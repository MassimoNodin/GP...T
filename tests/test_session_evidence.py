from __future__ import annotations

import struct
import socket
import time
import json
import threading
from dataclasses import replace
import sqlite3

import pytest

from f1_engineer.analysis.session_comparison import compare_session_laps, measure_completed_laps
from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.evidence import EvidenceStore, EvidenceUnavailable
from f1_engineer.processing.runtime import LiveSessionRuntime
from f1_engineer.recording.capture import CaptureReader, CaptureWriter
from f1_engineer.api.app import create_app
from f1_engineer.storage.importer import import_capture, list_laps
from f1_engineer.storage.query import load_attempt_trace
from f1_engineer.storage.database import Database
from f1_engineer.processing.legacy import archived_session_page
from fastapi.testclient import TestClient
from tests.helpers import make_datagram
from tests.test_car_lap_inventory import _participants_body
from tests.test_lap_tracking import SESSION_UID, LAP_RECORD, _lap_body


TELEMETRY = struct.Struct("<HfffBbHBBH4H4B4BH4f4B")


def admitted_packets(*, frames: int = 50, car_count: int = 22, missing_telemetry: bool = False,
                     lap_frames: int = 20):
    sequence = 0
    packet_format = 2026 if car_count == 24 else 2025
    telemetry_struct = struct.Struct("<HfffBbHBBH4H4B4BB4f4B") if packet_format == 2026 else TELEMETRY
    yield make_datagram(packet_format=packet_format, packet_id=4, session_uid=SESSION_UID, frame=1, session_time=0,
                        body=_participants_body(active_count=car_count, slot_count=car_count, packet_format=packet_format), sequence=sequence)
    for frame in range(1, frames + 1):
        lap_number = 1 + (frame - 1) // lap_frames
        position = (frame - 1) % lap_frames
        distance = position * 5.0
        records = []
        for car in range(car_count):
            body = _lap_body(lap_number=lap_number, distance_m=distance,
                             current_lap_time_ms=position * 50,
                             last_lap_time_ms=lap_frames * 50 + car * 10, active_car_index=0)
            records.append(body[:LAP_RECORD.size])
        sequence += 1
        yield make_datagram(packet_format=packet_format, packet_id=2, session_uid=SESSION_UID, frame=frame,
                            session_time=frame * 0.05, body=b"".join(records) + bytes((0, 255)), sequence=sequence)
        if not missing_telemetry:
            telemetry = []
            for car in range(car_count):
                brake = 0.6 if 5 + car % 2 <= position <= 10 else 0.0
                telemetry.append(telemetry_struct.pack(100 + car, 0.5, 0.0, brake, 0, 4, 8000, 0, 50, 0,
                                                *([300] * 4), *([90] * 4), *([80] * 4), 100,
                                                *([22.0] * 4), *([0] * 4)))
            sequence += 1
            yield make_datagram(packet_format=packet_format, packet_id=6, session_uid=SESSION_UID, frame=frame,
                                session_time=frame * 0.05, body=b"".join(telemetry) + bytes((0, 0, 0)), sequence=sequence)


def completed_pair(store):
    session = store.sessions()[0]["id"]
    attempts = store.attempts(session)
    target = next(row for row in attempts if row["role"] == "player" and row["payload"]["lap_number"] == 2)
    reference = next(row for row in attempts if row["role"] == "opponent" and row["payload"]["car_index"] == 1
                     and row["payload"]["lap_number"] == 2)
    return session, target, reference


def test_completed_laps_are_committed_and_comparable_before_finish(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "live")
    for raw in admitted_packets():
        coordinator.ingest(raw)
    session, target, reference = completed_pair(store)
    assert store.sessions()[0]["lifecycle"] == "active"
    assert target["readiness"]["state"] == "published"
    assert reference["readiness"]["state"] == "published"
    report = compare_session_laps(store, session, target["id"], reference["id"])
    assert report["lap_time_difference_ms"] == -10
    assert report["channels"]["brake"]["coverage"] > 0.9
    assert any(zone["supported"] for zone in report["braking_zones"])
    coordinator.finish()
    assert store.report(report["id"]) == report
    assert store.sessions()[0]["lifecycle"] == "ended"


def test_binding_extent_never_shrinks_on_telemetry_only_outputs(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "binding-regression")
    prior_extents = {}
    for raw in admitted_packets():
        coordinator.ingest(raw)
        with store.connect() as database:
            for binding in database.execute("SELECT id,end_frame,payload FROM bindings"):
                assert binding["end_frame"] >= prior_extents.get(binding["id"], 0)
                assert json.loads(binding["payload"])["end_frame_ordinal_exclusive"] == binding["end_frame"]
                prior_extents[binding["id"]] = binding["end_frame"]
    session, target, reference = completed_pair(store)
    compare_session_laps(store, session, target["id"], reference["id"])
    coordinator.close()


def test_prototype_schema_migration_preserves_session_identity(tmp_path):
    path = tmp_path / "prototype.sqlite3"
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, generation TEXT, uid TEXT, "
                         "lifecycle TEXT, acquisition TEXT, context TEXT)")
        database.execute("INSERT INTO sessions VALUES ('original','generation','42','ended','stopped',NULL)")
        database.execute("PRAGMA user_version=1")
    store = EvidenceStore(path)
    assert store.sessions()[0]["id"] == "original"
    assert store.sessions()[0]["occurrence"] == 1
    with store.connect() as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 2


def test_immutable_tables_reject_deletion(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "immutable")
    for raw in admitted_packets():
        coordinator.ingest(raw)
    session, target, reference = completed_pair(store)
    compare_session_laps(store, session, target["id"], reference["id"])
    for table in ("chunks", "attempts", "reports", "journal", "dispositions"):
        with store.connect() as database, pytest.raises(sqlite3.IntegrityError):
            database.execute(f"DELETE FROM {table}")
    coordinator.close()


def test_track_geometry_conflicts_cannot_produce_distance_measurements(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "geometry")
    for raw in admitted_packets():
        coordinator.ingest(raw)
    session, target, reference = completed_pair(store)
    class GeometryProvider:
        def evidence(self, attempt_id, *, session=None):
            attempt, rows = store.evidence(attempt_id, session=session)
            attempt["payload"]["context_segments"] = [{"context": {
                "track_id": 1 if attempt_id == target["id"] else 2, "track_length_m": 5000}}]
            return attempt, rows
    with pytest.raises(EvidenceUnavailable, match="incompatible_track_distance_regions"):
        measure_completed_laps(GeometryProvider(), session, target["id"], reference["id"])
    coordinator.close()


def test_api_defaults_to_automatic_acquisition_and_fences_socket_errors(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    app = create_app(tmp_path / "archive.sqlite3", recordings_root=tmp_path / "recordings",
                     recording_host="127.0.0.1", recording_port=port)
    with TestClient(app) as client:
        assert client.get("/api/v2/session-evidence/status").json()["state"] == "listening"
        runtime = app.state.live_session_runtime
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(next(admitted_packets()).payload, ("127.0.0.1", port))
        deadline = time.monotonic() + 5
        while runtime.processed < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert runtime.processed == 1
        runtime.source.stats.socket_errors += 1
        seen = False
        while time.monotonic() < deadline:
            with app.state.evidence_store.connect() as database:
                gaps = database.execute("SELECT payload FROM metadata WHERE kind='gap'").fetchall()
                seen = any(json.loads(row[0])["reason"] == "socket_error" for row in gaps)
            if seen:
                break
            time.sleep(0.02)
        assert seen
        assert client.get("/api/v2/session-evidence/status").json()["socket_errors"] == 1
        assert app.state.evidence_store.sessions()[0]["lifecycle"] == "interrupted"


def test_restart_rebuilds_without_duplicate_publications(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "live")
    packets = list(admitted_packets())
    for raw in packets[:60]:
        coordinator.ingest(raw)
    coordinator.close()
    recovered = SessionCoordinator(store, "live")
    assert recovered.generation == coordinator.generation
    for raw in packets[60:]:
        recovered.ingest(raw)
    session, target, reference = completed_pair(store)
    before = store.attempts(session)
    recovered.close()
    recovered = SessionCoordinator(store, "live")
    assert store.attempts(session) == before
    assert compare_session_laps(store, session, target["id"], reference["id"])["channels"]["speed_mps"]["coverage"] > 0.9


def test_transaction_failure_is_not_readable_and_recovers(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "live")
    packets = list(admitted_packets())
    for raw in packets[:80]:
        coordinator.ingest(raw)
    before = store.attempts(store.sessions()[0]["id"])
    def fail():
        assert store.attempts(store.sessions()[0]["id"]) == before
        raise OSError("injected publication failure")
    coordinator.before_commit = fail
    with pytest.raises(OSError):
        coordinator.ingest(packets[80])
    assert store.attempts(store.sessions()[0]["id"]) == before
    with pytest.raises(EvidenceUnavailable):
        coordinator.ingest(packets[81])
    coordinator.close()
    recovered = SessionCoordinator(store, "live")
    for raw in packets[81:]:
        recovered.ingest(raw)
    completed_pair(store)


def test_missing_channels_remain_missing_and_silence_is_not_end(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "live")
    for raw in admitted_packets(missing_telemetry=True):
        coordinator.ingest(raw)
    session, target, reference = completed_pair(store)
    report = compare_session_laps(store, session, target["id"], reference["id"])
    assert report["channels"]["brake"]["coverage"] == 0
    assert report["braking_zones"] == []
    coordinator.gap("telemetry_silence")
    assert store.sessions()[0]["lifecycle"] == "interrupted"
    assert store.report(report["id"]) == report


def test_capture_replay_matches_normalized_evidence_and_measurements(tmp_path):
    capture = tmp_path / "fixture.f1ecap"
    live_store = EvidenceStore(tmp_path / "live.sqlite3")
    replay_store = EvidenceStore(tmp_path / "replay.sqlite3")
    live = SessionCoordinator(live_store, "live")
    replay = SessionCoordinator(replay_store, "replay")
    with CaptureWriter(capture) as writer:
        for raw in admitted_packets():
            writer.write(raw)
            live.ingest(raw)
    with CaptureReader(capture) as reader:
        for raw in reader:
            replay.ingest(raw)
        assert reader.complete
    def normalized(store):
        session, target, reference = completed_pair(store)
        evidence = []
        for row in store.attempts(session):
            metadata, records = store.evidence(row["id"])
            evidence.append((row["role"], row["payload"]["car_index"], row["payload"]["lap_number"],
                             row["payload"], row["readiness"]["state"], records))
        report = compare_session_laps(store, session, target["id"], reference["id"])
        return sorted(evidence, key=lambda row: row[:3]), {key: value for key, value in report.items()
                                                        if key not in ("session", "target", "reference", "id")}
    assert normalized(live_store) == normalized(replay_store)
    live.finish()
    replay.finish()
    assert normalized(live_store) == normalized(replay_store)
    live.close()
    replay.close()


def test_flashback_supersedes_without_rewriting_saved_comparison(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "live")
    for raw in admitted_packets():
        coordinator.ingest(raw)
    session, target, reference = completed_pair(store)
    report = compare_session_laps(store, session, target["id"], reference["id"])
    coordinator.ingest(make_datagram(packet_id=3, session_uid=SESSION_UID, frame=51, session_time=2.55,
                                    body=b"FLBK" + struct.pack("<If", 25, 1.25) + bytes(4), sequence=101))
    for frame in range(52, 56):
        coordinator.ingest(make_datagram(packet_id=255, session_uid=SESSION_UID, frame=frame,
                                        session_time=2.55, sequence=frame + 100))
    assert store.evidence(target["id"])[0]["readiness"]["state"] == "superseded"
    assert store.report(report["id"]) == report
    with pytest.raises(EvidenceUnavailable, match="measurement_ready"):
        compare_session_laps(store, session, target["id"], reference["id"])
    coordinator.close()


def test_reconnect_requires_new_tenure_and_retired_uid_cannot_reactivate(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "live")
    for raw in admitted_packets(frames=30):
        coordinator.ingest(raw)
    old_drivers = {row["driver"] for row in store.attempts(store.sessions()[0]["id"])}
    coordinator.gap("disconnect")
    for raw in list(admitted_packets())[61:]:
        coordinator.ingest(raw)
    session = store.sessions()[0]["id"]
    rows = store.attempts(session)
    assert all(row["readiness"]["state"] == "quarantined" for row in rows if row["payload"]["start_frame_identifier"] >= 31)
    coordinator.ingest(make_datagram(packet_id=4, session_uid=SESSION_UID, frame=51, session_time=2.55,
                                    body=_participants_body(), sequence=200))
    for raw in admitted_packets(frames=90):
        if raw.payload and raw.sequence > 102:
            coordinator.ingest(raw)
    new_drivers = {row["driver"] for row in store.attempts(session) if row["payload"]["start_frame_identifier"] >= 51}
    assert not old_drivers.intersection(new_drivers - {None})
    coordinator.ingest(make_datagram(session_uid=999, frame=1, sequence=500))
    coordinator.ingest(make_datagram(session_uid=SESSION_UID, frame=100, sequence=501))
    sessions = store.sessions()
    assert next(row for row in sessions if row["uid"] == str(SESSION_UID))["lifecycle"] == "ended"
    assert coordinator.pipeline.sessions.current_session_uid == 999
    coordinator.close()


def test_source_lock_read_budget_and_checksum_integrity(tmp_path, monkeypatch):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "live")
    with pytest.raises(EvidenceUnavailable, match="already_owned"):
        SessionCoordinator(store, "live")
    for raw in admitted_packets():
        coordinator.ingest(raw)
    session, target, reference = completed_pair(store)
    with pytest.raises(EvidenceUnavailable, match="not_in_session"):
        store.evidence(target["id"], session="other-session")
    monkeypatch.setattr("f1_engineer.processing.evidence.MAX_READ_ROWS", 1)
    with pytest.raises(EvidenceUnavailable, match="budget"):
        store.evidence(target["id"])
    monkeypatch.undo()
    with store.connect() as database:
        with pytest.raises(Exception, match="immutable"):
            database.execute("UPDATE chunks SET payload='[]'")
        database.execute("DROP TRIGGER immutable_chunks")
        database.execute("UPDATE chunks SET payload='[]' WHERE hash=?", (target["manifest"]["chunks"][0],))
    with pytest.raises(EvidenceUnavailable, match="checksum"):
        store.evidence(target["id"])
    coordinator.close()


def test_runtime_binds_automatically_and_comparison_runs_during_udp(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    store = EvidenceStore(tmp_path / "live.sqlite3")
    runtime = LiveSessionRuntime(store, host="127.0.0.1", port=port, stale_after_s=0.2)
    runtime.start()
    assert runtime.state == "listening"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            packets = list(admitted_packets())
            for raw in packets:
                sender.sendto(raw.payload, ("127.0.0.1", port))
                time.sleep(0.015)
            deadline = time.monotonic() + 10
            while runtime.processed < len(packets) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert runtime.processed == len(packets), runtime.status()
            session, target, reference = completed_pair(store)
            report = compare_session_laps(store, session, target["id"], reference["id"])
            assert report["braking_zones"][0]["supported"]
            assert store.sessions()[0]["lifecycle"] == "active"
            time.sleep(0.4)
            assert store.sessions()[0]["lifecycle"] == "interrupted"
    finally:
        runtime.close()
    assert store.report(report["id"]) == report


def test_same_uid_authoritative_start_creates_new_occurrence(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "live")
    for raw in admitted_packets():
        coordinator.ingest(raw)
    original, target, reference = completed_pair(store)
    report = compare_session_laps(store, original, target["id"], reference["id"])
    coordinator.ingest(make_datagram(packet_id=3, session_uid=SESSION_UID, frame=51, session_time=2.55,
                                    body=b"SEND" + bytes(12), sequence=101))
    for frame in range(52, 56):
        coordinator.ingest(make_datagram(packet_id=255, session_uid=SESSION_UID, frame=frame, session_time=2.6, sequence=frame))
    assert store.sessions()[0]["lifecycle"] == "ended"
    coordinator.ingest(make_datagram(packet_id=3, session_uid=SESSION_UID, frame=1, session_time=0,
                                    body=b"SSTA" + bytes(12), sequence=110))
    assert len(store.sessions()) == 2
    assert sorted(row["occurrence"] for row in store.sessions()) == [1, 2]
    for raw in admitted_packets():
        coordinator.ingest(raw)
    assert store.report(report["id"]) == report
    sessions = store.sessions()
    coordinator.close()
    recovered = SessionCoordinator(store, "live")
    assert store.sessions() == sessions
    recovered.close()


def test_all_24_slots_have_owned_completed_evidence(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "24-car-fixture")
    for raw in admitted_packets(car_count=24):
        coordinator.ingest(raw)
    rows = store.attempts(store.sessions()[0]["id"])
    assert {row["payload"]["car_index"] for row in rows if row["payload"]["disposition"] == "completed"} == set(range(24))
    assert all(row["driver"] for row in rows)
    assert all(row["readiness"]["state"] == "published" for row in rows)
    coordinator.close()


def test_runtime_queue_pressure_is_bounded_and_journaled(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    store = EvidenceStore(tmp_path / "live.sqlite3")
    runtime = LiveSessionRuntime(store, host="127.0.0.1", port=port, queue_size=1)
    runtime.start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            for raw in admitted_packets(frames=100):
                sender.sendto(raw.payload, ("127.0.0.1", port))
            time.sleep(1)
            sender.sendto(make_datagram(session_uid=SESSION_UID, frame=101).payload, ("127.0.0.1", port))
            time.sleep(0.5)
        assert runtime.source.stats.dropped > 0
        assert runtime.max_queue_depth <= 1
        with store.connect() as database:
            gaps = [json.loads(row[0])["reason"] for row in database.execute("SELECT payload FROM metadata WHERE kind='gap'")]
        assert "queue_pressure_or_socket_gap" in gaps
    finally:
        runtime.close()


@pytest.mark.parametrize("failpoint", ["after_journal_commit", "after_chunk_seal", "before_manifest_insert",
                                      "after_manifest_insert", "before_publication_commit"])
def test_each_publication_stage_recovers_from_committed_journal(tmp_path, failpoint):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "failure-test")
    packets = list(admitted_packets())
    failed_at = None
    for index, raw in enumerate(packets):
        if index >= 80:
            def inject(stage):
                if stage == failpoint:
                    raise OSError("injected " + failpoint)
            coordinator.fault_injector = inject
        try:
            coordinator.ingest(raw)
        except OSError:
            failed_at = index
            break
    assert failed_at is not None
    with store.connect() as database:
        checkpoint = database.execute("SELECT committed_sequence FROM generations WHERE id=?", (coordinator.generation,)).fetchone()[0]
        assert checkpoint < coordinator.sequence
        assert database.execute("SELECT COUNT(*) FROM attempts WHERE published_sequence>?", (checkpoint,)).fetchone()[0] == 0
    coordinator.close()
    recovered = SessionCoordinator(store, "failure-test")
    for raw in packets[failed_at + 1:]:
        recovered.ingest(raw)
    session, target, reference = completed_pair(store)
    assert target["rows"] == target["payload"]["sample_count"]
    assert compare_session_laps(store, session, target["id"], reference["id"])["channels"]["brake"]["coverage"] > 0.9
    with store.connect() as database:
        assert database.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 44
    recovered.close()


def test_session_api_is_additive_bounded_and_preserves_legacy_trace_values(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "api-fixture")
    capture = tmp_path / "fixture.f1ecap"
    with CaptureWriter(capture) as writer:
        for raw in admitted_packets():
            writer.write(raw)
            coordinator.ingest(raw)
    session, target, reference = completed_pair(store)
    legacy_path = tmp_path / "archive.sqlite3"
    imported = import_capture(capture, legacy_path)
    legacy_lap = next(row for row in list_laps(legacy_path, run_id=imported.run_id) if row["lap_number"] == 2)
    trace = load_attempt_trace(legacy_path, legacy_lap["attempt_key"])
    _, rows = store.evidence(target["id"])
    assert trace is not None
    for live, archived in zip(rows, trace.samples, strict=True):
        for field in ("frame_identifier", "current_lap_time_ms", "lap_distance_m", "speed_mps", "brake", "throttle"):
            assert live[field] == pytest.approx(archived[field], abs=1e-5)
    app = create_app(legacy_path, recordings_root=tmp_path / "recordings", control_token="test-token",
                     automatic_acquisition=False, evidence_database_path=store.path)
    with TestClient(app) as client:
        assert client.get("/api/v2/session-evidence/status").json()["state"] == "disabled"
        assert client.get("/api/v2/session-evidence/sessions?limit=101").status_code == 422
        assert client.get(f"/api/v2/session-evidence/sessions/{session}/attempts/{target['id']}").json()["data"]["samples"] == rows
        url = f"/api/v2/session-evidence/sessions/{session}/compare?target={target['id']}&reference={reference['id']}"
        assert client.post(url).status_code == 403
        response = client.post(url, headers={"Authorization": "Bearer test-token"})
        assert response.status_code == 200
        report = response.json()["data"]
        assert client.get(f"/api/v2/session-evidence/comparisons/{report['id']}").json()["data"] == report
        assert len(client.get("/api/v2/session-evidence/legacy-sessions").json()["data"]) == 1
    with Database(legacy_path) as database:
        database.connection.execute("UPDATE processing_runs SET status='failed' WHERE run_id=?", (imported.run_id,))
        database.connection.commit()
    assert archived_session_page(legacy_path) == []
    assert load_attempt_trace(legacy_path, legacy_lap["attempt_key"]) is None
    coordinator.close()


def test_journal_corruption_is_rejected_and_zero_uid_is_not_a_session(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "journal")
    coordinator.ingest(make_datagram(session_uid=0))
    assert store.sessions() == []
    for raw in admitted_packets(frames=10):
        coordinator.ingest(raw)
    coordinator.close()
    with store.connect() as database:
        database.execute("DROP TRIGGER immutable_journal")
        database.execute("UPDATE journal SET payload=x'00' WHERE sequence=2")
    with pytest.raises(EvidenceUnavailable, match="journal_checksum"):
        SessionCoordinator(store, "journal")
