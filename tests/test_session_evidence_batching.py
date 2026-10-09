from __future__ import annotations

import socket
import time

import pytest

from f1_engineer.analysis.session_comparison import compare_session_laps
from f1_engineer.processing.coordinator import PUBLICATION_BATCH_ROWS, SessionCoordinator
from f1_engineer.processing.evidence import EvidenceStore, EvidenceUnavailable
from f1_engineer.processing.runtime import LiveSessionRuntime
from tests.helpers import make_datagram
from tests.test_session_evidence import SESSION_UID, admitted_packets, completed_pair


def normalized(store):
    session, target, reference = completed_pair(store)
    evidence = []
    after = ""
    while rows := store.attempts(session, after=after):
        for row in rows:
            metadata, records = store.evidence(row["id"])
            evidence.append((row["role"], row["payload"]["car_index"], row["payload"]["lap_number"],
                             metadata["payload"], metadata["readiness"], records))
        after = rows[-1]["id"]
    report = compare_session_laps(store, session, target["id"], reference["id"])
    return sorted(evidence, key=lambda row: row[:3]), {
        key: value for key, value in report.items() if key not in ("session", "target", "reference", "id")
    }


def journal_packets(coordinator, packets):
    for raw in packets:
        coordinator.journal(raw)
        if coordinator.pending_publication == PUBLICATION_BATCH_ROWS:
            coordinator.publish_pending()


def test_batched_publication_matches_serial_measurements_and_sealed_records(tmp_path):
    stores = [EvidenceStore(tmp_path / f"{mode}.sqlite3") for mode in ("serial", "batched")]
    coordinators = [SessionCoordinator(store, mode) for store, mode in zip(stores, ("serial", "batched"))]
    try:
        for raw in admitted_packets(frames=70):
            coordinators[0].ingest(raw)
            journal_packets(coordinators[1], [raw])
        coordinators[1].publish_pending()
        assert normalized(stores[0]) == normalized(stores[1])
        assert stores[1].sessions()[0]["lifecycle"] == "active"
        for coordinator in coordinators:
            coordinator.finish()
        assert normalized(stores[0]) == normalized(stores[1])
    finally:
        for coordinator in coordinators:
            coordinator.close()


def test_each_journal_commit_is_durable_before_batched_projection(tmp_path):
    store = EvidenceStore(tmp_path / "journal.sqlite3")
    coordinator = SessionCoordinator(store, "journal")
    statements = []
    coordinator._writer.set_trace_callback(statements.append)
    try:
        for index, raw in enumerate(admitted_packets()):
            if index == PUBLICATION_BATCH_ROWS:
                break
            coordinator.journal(raw)
            with store.connect() as database:
                assert database.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == index + 1
                assert database.execute("SELECT committed_sequence FROM generations").fetchone()[0] == 0
            assert store.sessions() == []
        with pytest.raises(EvidenceUnavailable, match="batch_budget"):
            coordinator.journal(next(admitted_packets()))
        assert coordinator.pending_publication == PUBLICATION_BATCH_ROWS
        assert sum(statement == "COMMIT" for statement in statements) == PUBLICATION_BATCH_ROWS
        coordinator.publish_pending()
        assert sum(statement == "COMMIT" for statement in statements) == PUBLICATION_BATCH_ROWS + 1
        assert coordinator.pending_publication == 0
    finally:
        coordinator.close()


@pytest.mark.parametrize("failpoint", ["after_journal_commit", "after_chunk_seal", "before_manifest_insert",
                                      "after_manifest_insert", "before_publication_commit"])
def test_unpublished_batch_recovers_once_after_failure(tmp_path, failpoint):
    stores = [EvidenceStore(tmp_path / f"{mode}.sqlite3") for mode in ("control", "failure")]
    packets = list(admitted_packets(frames=70))
    control = SessionCoordinator(stores[0], "control")
    failed = SessionCoordinator(stores[1], "failure")
    try:
        for raw in packets:
            control.ingest(raw)
        journal_packets(failed, packets[:64])
        before = stores[1].attempts(stores[1].sessions()[0]["id"])
        admitted = 64
        if failpoint == "after_journal_commit":
            def journal_failure(stage):
                if stage == failpoint:
                    raise OSError(stage)
            failed.fault_injector = journal_failure
            with pytest.raises(OSError):
                failed.journal(packets[64])
            admitted = 65
        else:
            for raw in packets[64:96]:
                failed.journal(raw)
            admitted = 96

            def inject(stage):
                if stage == failpoint:
                    assert stores[1].attempts(stores[1].sessions()[0]["id"]) == before
                    raise OSError(stage)

            failed.fault_injector = inject
            with pytest.raises(OSError):
                failed.publish_pending()
        assert stores[1].attempts(stores[1].sessions()[0]["id"]) == before
        with pytest.raises(EvidenceUnavailable, match="requires_recovery"):
            failed.publish_pending()
        failed.close()
        failed = SessionCoordinator(stores[1], "failure")
        assert failed.pending_publication == 0
        journal_packets(failed, packets[admitted:])
        failed.publish_pending()
        assert normalized(stores[0]) == normalized(stores[1])
        previous = stores[1].attempts(stores[1].sessions()[0]["id"])
        failed.close()
        failed = SessionCoordinator(stores[1], "failure")
        assert stores[1].attempts(stores[1].sessions()[0]["id"]) == previous
    finally:
        control.close()
        failed.close()


def test_close_without_publication_recovers_durable_multirow_tail(tmp_path):
    store = EvidenceStore(tmp_path / "tail.sqlite3")
    coordinator = SessionCoordinator(store, "tail")
    for raw in list(admitted_packets())[:20]:
        coordinator.journal(raw)
    assert not store.sessions()
    coordinator.close()
    recovered = SessionCoordinator(store, "tail")
    try:
        assert recovered.sequence == recovered.admitted_sequence == 20
        assert store.sessions()
        with store.connect() as database:
            assert database.execute("SELECT committed_sequence FROM generations").fetchone()[0] == 20
    finally:
        recovered.close()


def test_successful_batch_reader_sees_only_previous_checkpoint(tmp_path):
    store = EvidenceStore(tmp_path / "read.sqlite3")
    coordinator = SessionCoordinator(store, "read")
    packets = list(admitted_packets())
    try:
        journal_packets(coordinator, packets[:64])
        session = store.sessions()[0]["id"]
        previous = store.attempts(session)
        for raw in packets[64:96]:
            coordinator.journal(raw)

        def read_before_commit():
            assert store.attempts(session) == previous
            with store.connect() as database:
                assert database.execute("SELECT committed_sequence FROM generations").fetchone()[0] == 64
                assert database.execute("SELECT MAX(sequence) FROM journal").fetchone()[0] == 96

        coordinator.before_commit = read_before_commit
        coordinator.publish_pending()
        assert len(store.attempts(session)) > len(previous)
        with store.connect() as database:
            assert database.execute("SELECT committed_sequence FROM generations").fetchone()[0] == 96
    finally:
        coordinator.close()


def test_batched_missing_channels_are_not_invented(tmp_path):
    store = EvidenceStore(tmp_path / "missing.sqlite3")
    coordinator = SessionCoordinator(store, "missing")
    try:
        journal_packets(coordinator, admitted_packets(missing_telemetry=True))
        coordinator.publish_pending()
        session, target, reference = completed_pair(store)
        report = compare_session_laps(store, session, target["id"], reference["id"])
        assert report["channels"]["brake"]["coverage"] == 0
        assert report["braking_zones"] == []
    finally:
        coordinator.close()


def test_offline_replay_harness_preserves_gaps_and_evidence(tmp_path):
    from scripts.benchmark_session_publication import replay

    source = EvidenceStore(tmp_path / "source.sqlite3")
    coordinator = SessionCoordinator(source, "source")
    try:
        for index, raw in enumerate(admitted_packets()):
            if index == 40:
                coordinator.gap("real_fixture_loss")
            coordinator.ingest(raw)
        coordinator.finish()
    finally:
        coordinator.close()
    result = replay(source.path, tmp_path / "replays")
    assert result["parity"]
    assert result["gaps_preserved"]


def test_delayed_end_start_and_retired_uid_routing_replays_exactly(tmp_path):
    store = EvidenceStore(tmp_path / "routing.sqlite3")
    coordinator = SessionCoordinator(store, "routing")
    try:
        journal_packets(coordinator, admitted_packets())
        coordinator.publish_pending()
        events = [make_datagram(packet_id=3, session_uid=SESSION_UID, frame=51, session_time=2.55,
                                body=b"SEND" + bytes(12), sequence=101)]
        events.extend(make_datagram(packet_id=255, session_uid=SESSION_UID, frame=frame,
                                    session_time=2.6, sequence=frame + 100) for frame in range(52, 56))
        events.extend([
            make_datagram(packet_id=3, session_uid=SESSION_UID, frame=1, session_time=0,
                          body=b"SSTA" + bytes(12), sequence=160),
            make_datagram(session_uid=999, frame=1, sequence=161),
            make_datagram(packet_id=3, session_uid=SESSION_UID, frame=2, session_time=0,
                          body=b"SSTA" + bytes(12), sequence=162),
            make_datagram(session_uid=0, frame=3, sequence=163),
        ])
        for raw in events:
            coordinator.journal(raw)
        coordinator.publish_pending()
        sessions = store.sessions()
        assert sorted(row["occurrence"] for row in sessions if row["uid"] == str(SESSION_UID)) == [1, 2]
        assert next(row for row in sessions if row["uid"] == "999")["lifecycle"] == "active"
        assert all(row["lifecycle"] == "ended" for row in sessions if row["uid"] == str(SESSION_UID))
        with store.connect() as database:
            assert database.execute("SELECT COUNT(*) FROM metadata WHERE kind='input_route'").fetchone()[0] >= 3
    finally:
        coordinator.close()
    recovered = SessionCoordinator(store, "routing")
    try:
        assert store.sessions() == sessions
        recovered.ingest(make_datagram(session_uid=SESSION_UID, frame=3, sequence=164))
        assert store.sessions() == sessions
    finally:
        recovered.close()


def test_real_gap_flushes_preceding_batch_and_fences_ownership(tmp_path):
    store = EvidenceStore(tmp_path / "gap.sqlite3")
    coordinator = SessionCoordinator(store, "gap")
    try:
        for raw in list(admitted_packets())[:15]:
            coordinator.journal(raw)
        coordinator.gap("actual_test_loss")
        assert coordinator.pending_publication == 0
        assert store.sessions()[0]["lifecycle"] == "interrupted"
        assert not coordinator.pipeline.car_lap_inventory.active_tenures()
        with store.connect() as database:
            assert database.execute("SELECT COUNT(*) FROM metadata WHERE kind='gap'").fetchone()[0] == 1
    finally:
        coordinator.close()


def test_runtime_flushes_low_rate_batch_before_silence_and_shutdown(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    store = EvidenceStore(tmp_path / "low-rate.sqlite3")
    runtime = LiveSessionRuntime(store, host="127.0.0.1", port=port, stale_after_s=0.3)
    runtime.start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            for raw in list(admitted_packets())[:3]:
                sender.sendto(raw.payload, ("127.0.0.1", port))
            deadline = time.monotonic() + 3
            while runtime.processed < 3 and time.monotonic() < deadline:
                time.sleep(0.005)
        assert runtime.processed == runtime.journaled == 3
        assert runtime.status()["pending_publication"] == 0
        assert store.sessions()[0]["lifecycle"] == "active"
        assert 1 <= runtime.max_publication_batch_size <= 3
        assert runtime.last_journal_s > 0
        assert runtime.last_publication_s > 0
        time.sleep(0.4)
        assert store.sessions()[0]["lifecycle"] == "interrupted"
        assert runtime.last_gap_s > 0
    finally:
        runtime.close()
    assert runtime.state == "stopped"
    with store.connect() as database:
        assert database.execute("SELECT MAX(sequence) FROM journal").fetchone()[0] == database.execute(
            "SELECT committed_sequence FROM generations").fetchone()[0]
