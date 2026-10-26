import json
import sqlite3
import struct

import pytest

from f1_engineer.analysis.session_comparison import compare_session_laps
from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.evidence import EvidenceStore, EvidenceUnavailable
from f1_engineer.processing.detail_demand import DetailCommand, DetailDemandQueue
from tests.helpers import make_datagram
from tests.test_car_lap_inventory import _participants_body
from tests.test_lap_tracking import SESSION_UID, _session_packet, LAP_RECORD


POSITIONS = [5, 4, 6, 1, 2, 3, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]


def evidence_packets(*, frames=26):
    sequence = 0
    session = _session_packet(race=True)
    yield session
    sequence += 1
    yield make_datagram(packet_id=4, session_uid=SESSION_UID, frame=50, session_time=0,
                        body=_participants_body(), sequence=sequence)
    for frame in range(1, frames + 1):
        frame_id = 49 + frame
        sequence += 1
        lap_number = 1 + (frame - 1) // 10
        in_lap = (frame - 1) % 10
        distance = float(in_lap * 500)
        records = []
        for car, position in enumerate(POSITIONS):
            fields = (90_000 if lap_number > 1 else 0, in_lap * 500,
                      0, 0, 0, 0, 0, 0, 0, 0,
                      distance, distance, 0.0, position, lap_number, 0, 0,
                      0, 0, 0, 0, 0, 0, 0, 1, 1, 2, False, 0, 0, False, 0.0, 0)
            records.append(LAP_RECORD.pack(*fields))
        yield make_datagram(packet_id=2, session_uid=SESSION_UID, frame=frame_id,
                            session_time=frame / 60, body=b"".join(records) + bytes((0, 255)),
                            sequence=sequence)


def run_generation(path, profile, source):
    store = EvidenceStore(path)
    coordinator = SessionCoordinator(store, source, detail_profile=profile)
    return store, coordinator


def _matching_attempt(store, *, role, car, lap_number=2):
    session = store.sessions()[0]["id"]
    return session, next(item for item in store.attempts(session)
                         if item["role"] == role and item["payload"]["car_index"] == car
                         and item["payload"]["lap_number"] == lap_number)


def test_E01_full_readiness_chunks_records_unchanged_and_detail_is_additive(tmp_path):
    store, coordinator = run_generation(tmp_path / "full.sqlite3", "full", "e01")
    for raw in evidence_packets():
        coordinator.ingest(raw)
    session, attempt = _matching_attempt(store, role="opponent", car=1)
    metadata, records = store.evidence(attempt["id"], session=session)
    assert attempt["readiness"]["state"] == "published"
    assert attempt["detail"] == {"profile": "full", "state": "available", "reason": None}
    assert len(attempt["manifest"]["chunks"]) > 0 and records
    assert metadata["id"] == attempt["id"]
    coordinator.close()


def test_E02_suppressed_attempt_is_deferred_and_raw_journal_matches_full(tmp_path):
    full, full_coordinator = run_generation(tmp_path / "full.sqlite3", "full", "e02-full")
    demand, demand_coordinator = run_generation(tmp_path / "demand.sqlite3", "demand_v1", "e02-demand")
    packets = tuple(evidence_packets())
    for raw in packets:
        full_coordinator.ingest(raw)
        demand_coordinator.ingest(raw)
    _, full_attempt = _matching_attempt(full, role="opponent", car=3)
    session, deferred = _matching_attempt(demand, role="opponent", car=3)
    assert full_attempt["readiness"]["state"] == "published"
    assert deferred["readiness"]["state"] == "deferred"
    assert deferred["detail"]["reason"] == "trace_not_selected"
    assert deferred["rows"] == 0 and deferred["manifest"]["chunks"] == []
    with pytest.raises(EvidenceUnavailable, match="trace_not_selected"):
        demand.evidence(deferred["id"], session=session)
    with full.connect() as left, demand.connect() as right:
        hashes_left = [row[0] for row in left.execute("SELECT metadata FROM journal WHERE kind='datagram' ORDER BY sequence")]
        hashes_right = [row[0] for row in right.execute("SELECT metadata FROM journal WHERE kind='datagram' ORDER BY sequence")]
    assert hashes_left == hashes_right
    full_coordinator.close()
    demand_coordinator.close()


def test_E03_partial_activation_and_reselection_do_not_stitch_laps(tmp_path):
    store, coordinator = run_generation(tmp_path / "partial.sqlite3", "demand_v1", "e03")
    queue = DetailDemandQueue()
    coordinator.detail_demand_queue = queue
    coordinator.pipeline.detail_profile = "demand_v1"
    packets = tuple(evidence_packets(frames=32))
    for raw in packets[:10]:
        coordinator.ingest(raw)
    session = store.sessions()[0]["id"]
    targets = queue.targets(session)
    target = next(item for item in targets if item["car_index"] == 3)
    command = DetailCommand("post", "midlap", session, target["binding_id"])
    assert queue.enqueue(command)[0] == 202
    coordinator.apply_detail_command(queue.pop())
    for raw in packets[10:22]:
        coordinator.ingest(raw)
    coordinator.apply_detail_command(DetailCommand("release", "midlap", session, target["binding_id"]))
    for raw in packets[22:]:
        coordinator.ingest(raw)
    attempts = store.attempts(session)
    car_three = [item for item in attempts if item["role"] == "opponent" and item["payload"]["car_index"] == 3]
    assert car_three and all(item["readiness"]["state"] == "deferred" for item in car_three)
    assert all(item["readiness"]["reason"] in {"trace_not_selected", "trace_selection_interrupted"}
               for item in car_three)
    coordinator.close()


def test_E04_gap_quarantine_keeps_precedence_and_saved_reports_immutable(tmp_path):
    store, coordinator = run_generation(tmp_path / "integrity.sqlite3", "demand_v1", "e04")
    for raw in evidence_packets():
        coordinator.ingest(raw)
    session, selected = _matching_attempt(store, role="opponent", car=1)
    _, target = _matching_attempt(store, role="player", car=0)
    report = compare_session_laps(store, session, target["id"], selected["id"])
    coordinator.gap("telemetry_silence")
    assert store.report(report["id"]) == report
    assert store.evidence(selected["id"])[0]["readiness"]["state"] == "published"
    coordinator.close()


@pytest.mark.parametrize("fault_stage", ["after_journal_commit", "before_publication_commit",
                                          "after_chunk_seal", "before_manifest_insert", "after_manifest_insert"])
def test_E05_fault_recovery_keeps_single_attempt_publication(tmp_path, fault_stage):
    path = tmp_path / f"recovery-{fault_stage}.sqlite3"
    store, coordinator = run_generation(path, "demand_v1", f"e05-{fault_stage}")
    packets = tuple(evidence_packets(frames=12))
    for raw in packets:
        coordinator.ingest(raw)
    fired = [False]
    def fail(stage):
        if stage == fault_stage and not fired[0]:
            fired[0] = True
            raise OSError("fault injection")
    coordinator.fault_injector = fail
    try:
        coordinator.finish()
    except OSError:
        pass
    coordinator.close()
    recovered = SessionCoordinator(store, f"e05-{fault_stage}", detail_profile="demand_v1")
    if not fired[0]:
        recovered.finish()
    session = store.sessions()[0]["id"]
    ids = [row["id"] for row in store.attempts(session)]
    assert len(ids) == len(set(ids))
    recovered.close()


def test_E06_profile_mismatch_fails_and_legacy_missing_profile_is_full(tmp_path):
    store, full = run_generation(tmp_path / "legacy.sqlite3", "full", "e06")
    full.close()
    with pytest.raises(EvidenceUnavailable, match="processing_profile_mismatch_requires_new_generation"):
        SessionCoordinator(store, "e06", detail_profile="demand_v1")
    with store.connect() as database:
        row = database.execute("SELECT sequence,payload FROM metadata WHERE kind='processor_config'").fetchone()
        config = json.loads(row["payload"])
        config.pop("detail_profile", None)
        config.pop("detail_policy_version", None)
        database.execute("UPDATE metadata SET payload=? WHERE sequence=? AND kind='processor_config'",
                         (json.dumps(config, sort_keys=True, separators=(",", ":")), row["sequence"]))
    legacy = SessionCoordinator(store, "e06", detail_profile="full")
    assert legacy.detail_profile == "full"
    assert legacy.pipeline.observation_rows_skipped_by_policy == 0
    legacy.close()
