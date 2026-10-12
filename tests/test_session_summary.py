from __future__ import annotations

import json
import struct

import pytest
from fastapi.testclient import TestClient

from f1_engineer.api.app import create_app
from f1_engineer.processing.evidence import PROCESSOR_VERSION, EvidenceStore, EvidenceUnavailable
from f1_engineer.processing.session_summary import SessionSummaryService, SummaryLimits
from tests.helpers import make_datagram


def summary_store(tmp_path, *, capture_ns=1_790_000_000_000_000_000, first_packet=None):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    packets = [
        make_datagram(packet_id=3, body=b"SSTA" + bytes(12), session_time=0),
        make_datagram(packet_id=3, body=b"FTLP" + struct.pack("<Bf", 0, 90.5) + bytes(7), session_time=90.5),
        make_datagram(packet_id=3, body=b"SEND" + bytes(12), session_time=91),
        make_datagram(packet_id=3, body=b"SSTA" + bytes(12), session_time=0),
        make_datagram(packet_id=3, body=b"FTLP" + struct.pack("<Bf", 0, 88.0) + bytes(7), session_time=88),
    ]
    if first_packet is not None:
        packets[0] = first_packet
    context = json.dumps({"track_id": 0, "packet_format": 2025, "track_length_m": 5276})
    with store.connect() as database:
        database.execute("INSERT INTO generations VALUES ('generation','test',?,'active',5,0)",
                         (PROCESSOR_VERSION,))
        database.executemany("INSERT INTO sessions VALUES (?,'generation','10',?,'receiving',?,?)", [
            ("first", "ended", context, 1), ("second", "active", context, 2),
        ])
        for sequence, raw in enumerate(packets, 1):
            database.execute("INSERT INTO journal VALUES ('generation',?,'datagram',?,?)", (
                sequence, raw.payload, json.dumps({"captured_at_ns": capture_ns + sequence * 10**9}),
            ))
        for sequence, session in ((1, "first"), (4, "second")):
            database.execute("INSERT INTO metadata VALUES ('generation',?,'logical_session',0,?)",
                             (sequence, json.dumps({"session_id": session})))
    return store


def test_summary_separates_repeated_uid_occurrences(tmp_path):
    service = SessionSummaryService(summary_store(tmp_path))
    first = service.summary("first")
    second = service.summary("second")
    assert [event["kind"] for event in first["events"]] == ["session_started", "fastest_lap", "session_ended"]
    assert first["events"][1]["detail"]["lap_time_ms"] == 90_500
    assert second["events"][1]["detail"]["lap_time_ms"] == 88_000
    assert first["session_time"]["duration_s"] == 91
    assert first["ended_at_utc"] is not None
    assert second["ended_at_utc"] is None
    assert second["time_reason"] == "session_active"
    assert first["scan"]["last_sequence"] == 3
    assert second["scan"]["first_sequence"] == 4


def test_summary_cache_returns_copies_and_invalidates_committed_prefix(tmp_path):
    store = summary_store(tmp_path)
    service = SessionSummaryService(store)
    result = service.summary("second")
    result["events"].clear()
    assert len(service.summary("second")["events"]) == 2
    with store.connect() as database:
        database.execute("UPDATE generations SET committed_sequence=4")
    assert len(service.summary("second")["events"]) == 1


def test_summary_reports_bounded_partial_scan(tmp_path):
    result = SessionSummaryService(summary_store(tmp_path), limits=SummaryLimits(scan_rows=1)).summary("first")
    assert result["scan"]["stop_reason"] == "row_budget"
    assert not result["scan"]["complete"]
    assert result["totals"]["truncated"]
    assert result["ended_at_utc"] is None


def test_summary_unknown_session_and_busy_admission(tmp_path):
    service = SessionSummaryService(summary_store(tmp_path), limits=SummaryLimits(concurrent_scans=1))
    with pytest.raises(EvidenceUnavailable, match="session_not_found"):
        service.summary("missing")
    assert service._admission.acquire(blocking=False)
    try:
        with pytest.raises(EvidenceUnavailable, match="summary_busy"):
            service.summary("first")
    finally:
        service._admission.release()


def test_summary_full_profile_uses_recorded_restart_route(tmp_path):
    store = summary_store(tmp_path)
    with store.connect() as database:
        database.execute("DELETE FROM metadata WHERE kind='logical_session'")
        database.execute("INSERT INTO metadata VALUES ('generation',4,'input_route',0,?)",
                         (json.dumps({"restart_occurrence": True, "retired_input": False}),))
    service = SessionSummaryService(store)
    assert service.summary("first")["events"][1]["detail"]["lap_time_ms"] == 90_500
    assert service.summary("second")["events"][1]["detail"]["lap_time_ms"] == 88_000


def test_summary_omits_untrusted_capture_times(tmp_path):
    store = summary_store(tmp_path, capture_ns=0)
    result = SessionSummaryService(store).summary("first")
    assert result["started_at_utc"] is None and result["ended_at_utc"] is None
    assert result["time_reason"] == "captured_at_not_wall_clock"


def test_summary_decodes_player_lap_state_without_writes(tmp_path):
    from tests.test_lap_enrichment import build

    store, session = build(tmp_path)
    with store.connect() as database:
        before = database.execute("SELECT COUNT(*) FROM journal").fetchone()[0]
    result = SessionSummaryService(store).summary(session)
    assert result["player"]["last_lap_number"] is not None
    assert result["scan"]["complete"]
    with store.connect() as database:
        assert database.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == before


def test_summary_decodes_weather_from_session_packet(tmp_path):
    prefix = struct.pack("<BbbBHBbBHHBBBBBB", 0, 35, 23, 5, 5276, 18, 0, 0, 600, 600, 80, 0, 0, 0, 0, 0)
    packet = make_datagram(packet_id=1, body=prefix + bytes(724 - len(prefix)), session_time=0)
    result = SessionSummaryService(summary_store(tmp_path, first_packet=packet)).summary("first")
    assert result["weather"][0]["weather"] == "clear"
    assert result["weather"][0]["track_temperature_c"] == 35
    assert result["scheduled_duration_s"] == 600


def test_summary_route_registered_on_application(tmp_path):
    store = summary_store(tmp_path)
    app = create_app(tmp_path / "archive.sqlite3", evidence_database_path=store.path,
                     automatic_acquisition=False)
    with TestClient(app) as client:
        response = client.get("/api/v2/session-evidence/sessions/first/summary")
        assert response.status_code == 200
        assert response.json()["data"]["session"]["id"] == "first"
        assert client.get("/api/v2/session-evidence/sessions/missing/summary").status_code == 404
