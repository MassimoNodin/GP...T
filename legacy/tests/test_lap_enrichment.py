from __future__ import annotations

import sqlite3
import struct
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from f1_engineer.api.lap_enrichment_routes import register_lap_enrichment_routes
from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.evidence import EvidenceStore, EvidenceUnavailable
from f1_engineer.processing.lap_enrichment import (
    LapEnrichmentBusy,
    LapEnrichmentLimits,
    LapEnrichmentService,
)
from f1_engineer.udp.header import parse_header
from f1_engineer.udp.models import RawDatagram
from f1_engineer.udp.session_history import SESSION_HISTORY_BODY_SIZE
from tests.helpers import make_datagram
from tests.test_lap_tracking import LAP_RECORD, SESSION_UID
from tests.test_session_evidence import admitted_packets

STATUS_RECORD = struct.Struct("<5B3f2H2BH3Bb3fB3fB")
HISTORY_LAP = struct.Struct("<IHBHBHBB")
LAP_FRAMES = 20


def _with_sectors(raw: RawDatagram, sector1: int, sector2: int) -> RawDatagram:
    body = bytearray(raw.payload[29:])
    for car in range(22):
        offset = car * LAP_RECORD.size
        fields = list(LAP_RECORD.unpack_from(body, offset))
        fields[2], fields[4] = sector1, sector2
        LAP_RECORD.pack_into(body, offset, *fields)
    return RawDatagram(raw.sequence, raw.captured_at_ns, raw.monotonic_ns, raw.source_host,
                       raw.source_port, raw.payload[:29] + bytes(body))


def _status(frame: int, lap: int) -> RawDatagram:
    soft = lap <= 2
    actual, visual, age = (16, 16, lap - 1) if soft else (17, 17, lap - 3)
    other = STATUS_RECORD.pack(1, 0, 2, 54, 0, 12.5, 100.0, -1.25, 15_000, 4_000, 8, 1, 0,
                               18, 18, 7, -1, 900.0, 160.0, 3.5, 2, 10.0, 5.0, 7.0, 0)
    player = STATUS_RECORD.pack(1, 0, 2, 54, 0, 12.5, 100.0, -1.25, 15_000, 4_000, 8, 1, 0,
                                actual, visual, age, -1, 900.0, 160.0, 3.5, 2, 10.0, 5.0, 7.0, 0)
    return make_datagram(packet_id=7, session_uid=SESSION_UID, frame=frame, session_time=frame * 0.05,
                         body=player + other * 21)


def _history(frame: int, laps: list[tuple[int, int, int, int, int, int, int, int]],
             stints: list[tuple[int, int, int]]) -> RawDatagram:
    body = bytearray(SESSION_HISTORY_BODY_SIZE)
    body[:7] = bytes((0, len(laps), len(stints), 0, 1, 2, 3))
    for index, values in enumerate(laps):
        HISTORY_LAP.pack_into(body, 7 + index * HISTORY_LAP.size, *values)
    for index, values in enumerate(stints):
        struct.pack_into("<BBB", body, 7 + 100 * HISTORY_LAP.size + index * 3, *values)
    return make_datagram(packet_id=11, session_uid=SESSION_UID, frame=frame, session_time=frame * 0.05,
                         body=bytes(body))


def stream(*, frames: int = 100, with_history: bool = True, with_status: bool = True,
           history_lap_time: int = 1000):
    for raw in admitted_packets(frames=frames, lap_frames=LAP_FRAMES):
        header = parse_header(raw.payload)
        if header.packet_id == 2:
            raw = _with_sectors(raw, 300, 400)
        yield raw
        if header.packet_id != 2:
            continue
        frame = header.frame_identifier
        lap = 1 + (frame - 1) // LAP_FRAMES
        if with_status:
            yield _status(frame, lap)
        if with_history and frame == 45:
            yield _history(frame, [(1000, 300, 0, 400, 0, 300, 0, 15), (0, 0, 0, 0, 0, 0, 0, 0),
                                   (0, 300, 0, 0, 0, 0, 0, 0)], [(255, 16, 16)])
        if with_history and frame == frames - 1:
            yield _history(frame, [
                (history_lap_time, 300, 0, 400, 0, 300, 0, 15),
                (history_lap_time, 310, 0, 390, 0, 300, 0, 15),
                (history_lap_time, 300, 0, 400, 0, 0, 0, 15),
                (history_lap_time, 300, 0, 400, 0, 300, 0, 3),
                (0, 300, 0, 0, 0, 0, 0, 0),
            ], [(2, 16, 16), (255, 17, 17)])


def build(tmp_path, **options):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "live")
    try:
        for raw in stream(**options):
            coordinator.ingest(raw)
    finally:
        coordinator.close()
    return store, store.sessions()[0]["id"]


def by_lap(data):
    rows = {}
    for row in data["laps"]:
        if row["disposition"] == "completed":
            rows[row["lap_number"]] = row
    return rows


def test_sectors_and_tyres_are_derived_from_journal(tmp_path):
    store, session = build(tmp_path)
    data = LapEnrichmentService(store).laps(session)
    assert data["session_id"] == session
    laps = by_lap(data)
    assert sorted(laps) == [1, 2, 3, 4]
    assert laps[1]["sectors_ms"] == [300, 400, 300] and laps[1]["sectors_source"] == "session_history"
    assert laps[2]["sectors_ms"] == [310, 390, 300] and laps[2]["sectors_source"] == "session_history"
    # Session History reports no third sector for lap 3, so Lap Data fills it in.
    assert laps[3]["sectors_ms"] == [300, 400, 300] and laps[3]["sectors_source"] == "lap_data"
    assert all(row["sectors_reason"] is None for row in laps.values())
    assert laps[1]["lap_time_ms"] == 1000 and laps[1]["valid"] is True and laps[1]["pit"] is False
    assert laps[1]["tyre"]["visual_compound"] == 16 and laps[1]["tyre"]["age_laps"] == 0
    assert laps[2]["tyre"]["age_laps"] == 1 and laps[2]["tyre"]["source"] == "car_status"
    assert laps[3]["tyre"]["actual_compound"] == 17 and laps[3]["tyre"]["age_laps"] == 0
    assert laps[4]["tyre"]["age_laps"] == 1
    assert data["truncated"] is False
    assert [(s["visual_compound"], s["start_lap"], s["end_lap"], s["lap_count"]) for s in data["stints"]] == [
        (16, 1, 2, 2), (17, 3, 4, 2)]
    source = data["source"]
    assert source["committed_sequence"] > 0 and source["cache"] == "miss"
    assert source["packets"]["session_history"] == 2 and source["packets"]["car_status"] > 0


def test_lap_data_fallback_and_missing_tyre_reason(tmp_path):
    store, session = build(tmp_path, with_history=False, with_status=False)
    laps = by_lap(LapEnrichmentService(store).laps(session))
    assert laps[2]["sectors_ms"] == [300, 400, 300] and laps[2]["sectors_source"] == "lap_data"
    assert laps[2]["tyre"] is None and laps[2]["tyre_reason"] == "car_status_not_in_journal"


def test_history_without_matching_lap_time_is_rejected(tmp_path):
    store, session = build(tmp_path, with_status=False, history_lap_time=1234)
    laps = by_lap(LapEnrichmentService(store).laps(session))
    # Session History disagrees with the attempt, so the Lap Data fallback is used.
    assert laps[1]["sectors_source"] == "lap_data"
    # The tyre stint table from Session History backs up missing Car Status data.
    assert laps[1]["tyre"]["source"] == "session_history_stints" and laps[1]["tyre"]["age_laps"] is None
    assert laps[4]["tyre"]["visual_compound"] == 17


def test_results_are_cached_per_committed_sequence(tmp_path):
    store, session = build(tmp_path)
    service = LapEnrichmentService(store)
    first = service.laps(session)
    again = service.laps(session)
    assert again["source"]["cache"] == "hit" and again["laps"] == first["laps"]
    again["laps"].clear()
    assert service.laps(session)["laps"] == first["laps"]
    coordinator = SessionCoordinator(store, "live")
    try:
        for raw in admitted_packets(frames=1):
            pass
        coordinator.gap("test_gap")
    finally:
        coordinator.close()
    refreshed = service.laps(session)
    assert refreshed["source"]["cache"] == "miss"
    assert refreshed["source"]["committed_sequence"] > first["source"]["committed_sequence"]


def test_cache_is_bounded(tmp_path):
    store, session = build(tmp_path)
    service = LapEnrichmentService(store, limits=LapEnrichmentLimits(cache_entries=1))
    service.laps(session)
    assert service.status()["cached_entries"] == 1


def test_scan_budget_truncates_and_reports_reason(tmp_path):
    store, session = build(tmp_path)
    service = LapEnrichmentService(store, limits=LapEnrichmentLimits(scan_rows=150))
    data = service.laps(session)
    assert data["truncated"] is True and data["source"]["truncation"] == "journal_row_budget_exceeded"
    laps = by_lap(data)
    assert laps[4]["sectors_ms"] is None
    assert laps[4]["sectors_reason"] == "journal_scan_budget_exceeded"
    assert laps[4]["tyre"] is None and laps[4]["tyre_reason"] == "journal_scan_budget_exceeded"


def test_time_budget_truncates_without_caching(tmp_path):
    store, session = build(tmp_path)
    ticks = iter(range(1000))
    service = LapEnrichmentService(store, limits=LapEnrichmentLimits(duration_s=1.0),
                                   clock=lambda: next(ticks) * 2.0)
    data = service.laps(session)
    assert data["truncated"] is True and data["source"]["truncation"] == "time_budget_exceeded"
    assert service.status()["cached_entries"] == 0


def test_row_cap_is_reported(tmp_path):
    store, session = build(tmp_path)
    data = LapEnrichmentService(store, limits=LapEnrichmentLimits(max_laps=2)).laps(session)
    assert len(data["laps"]) == 2 and data["truncated"] is True
    assert data["source"]["truncation"] == "attempt_row_budget_exceeded"


def test_derivation_never_writes_evidence(tmp_path):
    store, session = build(tmp_path)
    tables = ("journal", "attempts", "chunks", "dispositions", "metadata", "sessions", "generations", "bindings")

    def snapshot():
        with sqlite3.connect(store.path) as database:
            return {name: database.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in tables}

    before = snapshot()
    LapEnrichmentService(store).laps(session)
    assert snapshot() == before


def test_unknown_session_and_busy_admission(tmp_path):
    store, session = build(tmp_path)
    service = LapEnrichmentService(store, limits=LapEnrichmentLimits(concurrency=1))
    with pytest.raises(EvidenceUnavailable, match="session_not_found"):
        service.laps("0" * 32)
    service._admission.acquire()
    try:
        with pytest.raises(LapEnrichmentBusy):
            service.laps(session)
    finally:
        service._admission.release()


def test_route_contract(tmp_path):
    store, session = build(tmp_path)
    app = FastAPI()
    register_lap_enrichment_routes(app, evidence_store=store)
    client = TestClient(app)
    response = client.get(f"/api/v2/session-evidence/sessions/{session}/laps")
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {"session_id", "laps", "stints", "truncated", "source"}
    row = next(item for item in data["laps"] if item["disposition"] == "completed")
    assert {"revision_id", "lap_number", "lap_time_ms", "sectors_ms", "sectors_reason", "tyre", "pit",
            "valid"} <= set(row)
    assert data["stints"] and data["source"]["version"] == "lap-enrichment-v1"
    missing = client.get("/api/v2/session-evidence/sessions/nope/laps")
    assert missing.status_code == 404 and missing.json()["reason"] == "session_not_found"


def test_concurrent_requests_are_serialised_by_admission(tmp_path):
    store, session = build(tmp_path)
    service = LapEnrichmentService(store)
    results = []

    def request():
        try:
            results.append(service.laps(session)["session_id"])
        except LapEnrichmentBusy:
            results.append("busy")

    threads = [threading.Thread(target=request) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 4 and session in results
