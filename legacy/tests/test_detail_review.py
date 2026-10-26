import asyncio
import json
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

import f1_engineer.processing.runtime as runtime_module
from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.detail_demand import DetailCommand, DetailDemandQueue
from f1_engineer.processing.detail_policy import select_detail
from f1_engineer.processing.evidence import EvidenceStore
from f1_engineer.processing.runtime import LiveSessionRuntime
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.models import PacketId
from tests.helpers import make_datagram
from tests.test_detail_demands import SESSION, BINDINGS, target_queue, post, apply_post
from tests.test_detail_evidence import evidence_packets, _matching_attempt
from tests.test_lap_tracking import LAP_RECORD, SESSION_UID
from tests.test_detail_policy import frame, lap
from tests.test_session_evidence import admitted_packets


@pytest.mark.parametrize("profile", ["full", "demand_v1"])
def test_runtime_uses_sqlite_owner_and_preserves_batching(tmp_path, monkeypatch, profile):
    packets = list(evidence_packets(frames=12))
    runtime = LiveSessionRuntime(EvidenceStore(tmp_path / "runtime.sqlite3"),
                                 host="127.0.0.1", port=20777,
                                 detail_profile=profile, publication_batch_size=8,
                                 publication_interval_s=0.1)

    class FakeSource:
        independent_receiver = True
        kernel_receive_drops = 0
        receive_buffer_bytes = 1024
        pending_count = 0
        stats = SimpleNamespace(received=0, dropped=0, socket_errors=0)

        def __init__(self, *args, **kwargs):
            pass

        async def open(self):
            pass

        async def receive_batch(self, available):
            await asyncio.sleep(0)
            if packets:
                self.stats.received += 1
                return [packets.pop(0)]
            runtime._stop.set()
            await asyncio.sleep(1)

        def close(self):
            pass

        def drain_pending(self):
            return []

    monkeypatch.setattr(runtime_module, "UDPSource", FakeSource)
    monkeypatch.setattr(runtime_module, "IsolatedUDPSource", FakeSource)
    asyncio.run(runtime._listen())
    assert runtime.state == "stopped"
    assert runtime.journaled == runtime.processed == 14
    assert runtime.max_publication_batch_size == 8
    assert runtime.status()["detail_processing"]["session_id"] is not None


def test_runtime_status_does_not_expose_mutable_cached_selection(tmp_path):
    runtime = LiveSessionRuntime(EvidenceStore(tmp_path / "snapshot.sqlite3"), host="127.0.0.1", port=20777)
    runtime._detail_status_cache["selected"] = [{"car_index": 0, "reasons": ["player"]}]
    returned = runtime.status()["detail_processing"]
    returned["selected"][0]["reasons"].clear()
    assert runtime.status()["detail_processing"]["selected"][0]["reasons"] == ["player"]
    demand = LiveSessionRuntime(runtime.store, host="127.0.0.1", port=20777, detail_profile="demand_v1")
    assert demand.enqueue_detail_command(DetailCommand("post", "stopped", SESSION, BINDINGS[0]))[0] == 409


def test_unknown_player_identity_suppresses_automatic_neighbors():
    selected = select_detail(frame([lap(), lap(), lap()], positions=[2, 1, 3], valid={1, 2}))
    assert selected.cars == (0,)


def test_queue_owner_revalidates_identity_and_queued_commands_preserve_order():
    queue = target_queue()
    post(queue)
    apply_post(queue)
    conflict = queue.apply(DetailCommand("post", "task_1", SESSION, BINDINGS[1]),
                           effective_sequence=8, target_current=True)
    assert conflict.binding_id == BINDINGS[0]
    assert conflict.last_command_reason == "detail_task_identity_conflict"
    post(queue)
    queue.enqueue(DetailCommand("release", "task_1", SESSION, BINDINGS[0]))
    post(queue)
    assert queue.counts()[1] == 3
    for sequence in range(9, 12):
        queue.apply(queue.pop(), effective_sequence=sequence, target_current=True)
    assert queue.get("task_1").state == "active"
    assert queue.get("task_1").activation_ordinal == 2


def test_release_without_retained_snapshot_is_safe():
    queue = DetailDemandQueue()
    result = queue.apply(DetailCommand("release", "evicted", SESSION, BINDINGS[0]),
                         effective_sequence=3, target_current=False)
    assert result.state == "released"


def test_shutdown_rejects_pending_renewals_and_clears_targets():
    queue = target_queue()
    post(queue)
    apply_post(queue)
    post(queue)
    queue.reject_queued_on_shutdown()
    assert queue.counts() == (0, 0)
    assert queue.get("task_1").last_command_state == "rejected"
    assert queue.targets(SESSION) is None


def activate_car(coordinator, car=3):
    queue = coordinator.detail_demand_queue
    session = coordinator.store.sessions()[0]["id"]
    target = next(item for item in queue.targets(session) if item["car_index"] == car)
    command = DetailCommand("post", "review", session, target["binding_id"])
    assert queue.enqueue(command)[0] == 202
    coordinator.apply_detail_command(queue.pop())
    assert queue.get("review").state == "active"
    return command


def evidence_signature(store):
    session = store.sessions()[0]["id"]
    return sorted((item["payload"]["car_index"], item["payload"]["lap_number"],
                   item["payload"]["attempt_id"], item["rows"],
                   item["readiness"]["state"], item["readiness"]["reason"],
                   tuple(item["manifest"]["chunks"]))
                  for item in store.attempts(session))


@pytest.mark.parametrize("stage", ["after_journal_commit", "before_publication_commit",
                                  "after_chunk_seal", "after_manifest_insert"])
def test_committed_task_and_unpublished_tail_recover_identical_evidence(tmp_path, stage):
    packets = tuple(evidence_packets(frames=32))
    expected = EvidenceStore(tmp_path / "expected.sqlite3")
    actual = EvidenceStore(tmp_path / "actual.sqlite3")
    baseline = SessionCoordinator(expected, "review", detail_profile="demand_v1")
    subject = SessionCoordinator(actual, "review", detail_profile="demand_v1")
    for coordinator in (baseline, subject):
        for raw in packets[:10]:
            coordinator.ingest(raw)
        activate_car(coordinator)
        for raw in packets[10:20]:
            coordinator.ingest(raw)
    baseline.journal_batch(packets[20:])
    baseline.publish_pending()
    expected_signature = evidence_signature(expected)
    baseline.close()

    def fail(observed):
        if observed == stage:
            raise OSError("review crash")

    subject.fault_injector = fail
    with pytest.raises(OSError, match="review crash"):
        subject.journal_batch(packets[20:])
        subject.publish_pending()
    subject.close()
    recovered_queue = DetailDemandQueue(lambda: 1000000.0)
    recovered = SessionCoordinator(actual, "review", detail_profile="demand_v1",
                                   detail_demand_queue=recovered_queue)
    try:
        assert evidence_signature(actual) == expected_signature
        assert recovered_queue.active_tasks() == ()
        assert recovered_queue.get("review").reason == "detail_acquisition_interrupted"
        assert recovered.pipeline.detail_demands == ()
    finally:
        recovered.close()


def test_detail_mutation_flushes_admitted_packets_before_effective_sequence(tmp_path):
    store = EvidenceStore(tmp_path / "barrier.sqlite3")
    coordinator = SessionCoordinator(store, "barrier", detail_profile="demand_v1")
    packets = tuple(evidence_packets(frames=12))
    try:
        for raw in packets[:10]:
            coordinator.ingest(raw)
        coordinator.journal_batch(packets[10:12])
        prior = coordinator.admitted_sequence
        activate_car(coordinator)
        assert coordinator.pending_publication == 0
        assert coordinator.detail_demand_queue.get("review").effective_sequence == prior + 1
        with store.connect() as database:
            assert database.execute("SELECT committed_sequence FROM generations").fetchone()[0] == prior + 1
    finally:
        coordinator.close()


def sparse_packets():
    decoder = PacketDecoder()
    telemetry_body = next(raw.payload[29:] for raw in admitted_packets(frames=1)
                          if decoder.decode(raw).packet_kind == PacketId.CAR_TELEMETRY)
    sequence = 0
    for raw in evidence_packets(frames=32):
        packet = decoder.decode(raw)
        header = packet.header
        expanded_frame = header.overall_frame_identifier * 2
        yield make_datagram(packet_id=header.packet_id, session_uid=header.session_uid,
                            frame=expanded_frame, session_time=header.session_time,
                            body=raw.payload[29:], sequence=sequence)
        sequence += 1
        if packet.packet_kind == PacketId.LAP_DATA:
            yield make_datagram(packet_id=6, session_uid=header.session_uid,
                                frame=expanded_frame + 1, session_time=header.session_time + 0.001,
                                body=telemetry_body, sequence=sequence)
            sequence += 1


def test_sparse_non_lap_frames_do_not_hide_partial_selection(tmp_path):
    store = EvidenceStore(tmp_path / "sparse.sqlite3")
    coordinator = SessionCoordinator(store, "sparse", detail_profile="demand_v1")
    packets = tuple(sparse_packets())
    try:
        for raw in packets[:33]:
            coordinator.ingest(raw)
        activate_car(coordinator)
        for raw in packets[33:]:
            coordinator.ingest(raw)
        _, partial = _matching_attempt(store, role="opponent", car=3)
        assert 0 < partial["rows"] < partial["payload"]["sample_count"]
        assert partial["readiness"]["state"] == "deferred"
        assert partial["detail"]["reason"] == "trace_selection_interrupted"
        assert len(coordinator.pipeline._detail_omitted_samples) <= 24
    finally:
        coordinator.close()


def test_demand_profile_24_slot_stream_preserves_whole_field_inventory(tmp_path):
    store = EvidenceStore(tmp_path / "24.sqlite3")
    coordinator = SessionCoordinator(store, "24", detail_profile="demand_v1")
    try:
        for raw in admitted_packets(frames=50, car_count=24):
            coordinator.ingest(raw)
        session = store.sessions()[0]["id"]
        attempts = store.attempts(session)
        completed = [item for item in attempts if item["payload"]["disposition"] == "completed"]
        assert {item["payload"]["car_index"] for item in completed} == set(range(24))
        assert all(item["readiness"]["state"] == "deferred" for item in completed if item["role"] == "opponent")
        assert all(item["rows"] == 0 for item in completed if item["role"] == "opponent")
        assert all(item["readiness"]["state"] == "published" for item in completed if item["role"] == "player")
    finally:
        coordinator.close()


def test_real_missing_rows_take_precedence_over_policy_deferral(tmp_path, monkeypatch):
    store = EvidenceStore(tmp_path / "missing.sqlite3")
    coordinator = SessionCoordinator(store, "missing", detail_profile="demand_v1")
    packets = tuple(evidence_packets(frames=32))
    original = coordinator._stage_observation

    def discard_selected(database, values):
        if values[2] != 3:
            original(database, values)

    try:
        for raw in packets[:17]:
            coordinator.ingest(raw)
        activate_car(coordinator)
        monkeypatch.setattr(coordinator, "_stage_observation", discard_selected)
        for raw in packets[17:]:
            coordinator.ingest(raw)
        _, partial = _matching_attempt(store, role="opponent", car=3)
        assert partial["readiness"]["state"] == "quarantined"
        assert partial["readiness"]["reason"] == "missing_observation_rows"
    finally:
        coordinator.close()


def test_flush_persists_every_selection_change_with_its_frame_binding(tmp_path):
    store = EvidenceStore(tmp_path / "changes.sqlite3")
    coordinator = SessionCoordinator(store, "changes", detail_profile="demand_v1")
    packets = list(evidence_packets(frames=32))
    try:
        for index in range(3):
            raw = packets[-3 + index]
            body = bytearray(raw.payload[29:])
            records = [list(LAP_RECORD.unpack_from(body, car * LAP_RECORD.size)) for car in range(22)]
            replacement = 3 + index
            records[1][13], records[replacement][13] = records[replacement][13], records[1][13]
            body = b"".join(LAP_RECORD.pack(*record) for record in records) + bytes((0, 255))
            packets[-3 + index] = replace(raw, payload=raw.payload[:29] + body)
        for raw in packets:
            coordinator.ingest(raw)
        coordinator.finish()
        with store.connect() as database:
            rows = database.execute(
                "SELECT ordinal,payload FROM metadata WHERE sequence=? AND kind='detail_selection' ORDER BY ordinal",
                (coordinator.sequence,),
            ).fetchall()
            assert len(rows) == 3
            assert [row["ordinal"] for row in rows] == [0, 1, 2]
            assert len({json.loads(row["payload"])["frame_ordinal"] for row in rows}) == 3
            assert all(item["binding_id"] for row in rows for item in json.loads(row["payload"])["selected"])
    finally:
        coordinator.close()


def test_task_snapshot_waits_for_publication_commit(tmp_path):
    store = EvidenceStore(tmp_path / "commit.sqlite3")
    coordinator = SessionCoordinator(store, "commit", detail_profile="demand_v1")
    started = threading.Event()
    completed = threading.Event()
    results = []
    readers = []

    def read_task():
        started.set()
        results.append(coordinator.detail_demand_queue.get("review"))
        completed.set()

    def before_commit():
        reader = threading.Thread(target=read_task)
        readers.append(reader)
        reader.start()
        assert started.wait(1)
        assert not completed.wait(0.02)

    try:
        for raw in tuple(evidence_packets(frames=12))[:10]:
            coordinator.ingest(raw)
        coordinator.before_commit = before_commit
        activate_car(coordinator)
        assert completed.wait(1)
        assert results[0].state == "active"
        with store.connect() as database:
            assert database.execute("SELECT committed_sequence FROM generations").fetchone()[0] == results[0].effective_sequence
    finally:
        for reader in readers:
            reader.join(timeout=1)
        coordinator.close()


def test_replay_of_same_uid_occurrences_keeps_tasks_pinned_and_counters_monotonic(tmp_path):
    store = EvidenceStore(tmp_path / "occurrences.sqlite3")
    coordinator = SessionCoordinator(store, "occurrences", detail_profile="demand_v1")
    for raw in admitted_packets(frames=30):
        coordinator.ingest(raw)
    created_before_restart = coordinator.pipeline.observation_rows_created
    coordinator.ingest(make_datagram(packet_id=3, session_uid=SESSION_UID, frame=31,
                                    session_time=1.55, body=b"SEND" + bytes(12)))
    for frame_id in range(32, 37):
        coordinator.ingest(make_datagram(packet_id=255, session_uid=SESSION_UID, frame=frame_id,
                                        session_time=1.6))
    coordinator.ingest(make_datagram(packet_id=3, session_uid=SESSION_UID, frame=1,
                                    session_time=0, body=b"SSTA" + bytes(12)))
    second = tuple(admitted_packets(frames=30))
    for raw in second[:14]:
        coordinator.ingest(raw)
    assert coordinator.pipeline.observation_rows_created > created_before_restart
    session = next(item["id"] for item in store.sessions() if item["occurrence"] == 2)
    target = next(item for item in coordinator.detail_demand_queue.targets(session) if item["car_index"] == 3)
    command = DetailCommand("post", "review", session, target["binding_id"])
    coordinator.detail_demand_queue.enqueue(command)
    coordinator.apply_detail_command(coordinator.detail_demand_queue.pop())
    for raw in second[14:]:
        coordinator.ingest(raw)
    expected_count = coordinator.pipeline.observation_rows_created
    expected_signature = evidence_signature(store)
    coordinator.close()
    recovered = SessionCoordinator(store, "occurrences", detail_profile="demand_v1")
    try:
        assert recovered.pipeline.observation_rows_created == expected_count
        assert evidence_signature(store) == expected_signature
        assert recovered.detail_demand_queue.get("review").session_id == session
        assert recovered.detail_demand_queue.active_tasks() == ()
    finally:
        recovered.close()


def test_idle_gap_clears_selection_and_target_snapshots(tmp_path):
    store = EvidenceStore(tmp_path / "idle.sqlite3")
    coordinator = SessionCoordinator(store, "idle", detail_profile="demand_v1")
    try:
        for raw in evidence_packets(frames=12):
            coordinator.ingest(raw)
        session = store.sessions()[0]["id"]
        activate_car(coordinator)
        assert coordinator.detail_demand_queue.targets(session)
        coordinator.gap("telemetry_silence")
        assert coordinator.detail_status()["selected"] == []
        assert coordinator.detail_demand_queue.targets(session) == ()
        coordinator.gap("listener_stopped")
        assert coordinator.detail_demand_queue.targets(session) == ()
        assert coordinator.detail_demand_queue.active_tasks() == ()
    finally:
        coordinator.close()
