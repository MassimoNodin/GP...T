from __future__ import annotations

import struct

from f1_engineer.api.app import LapRecord
from f1_engineer.recording.capture import CaptureWriter
from f1_engineer.storage.importer import import_capture, list_laps
from f1_engineer.storage.query import load_attempt_trace
from f1_engineer.storage.run_summaries import (
    get_processing_run_detail,
    get_processing_run_summary,
    list_processing_run_lifecycle_events,
)
from tests.helpers import make_datagram
from tests.test_lap_tracking import SESSION_UID, _lap_packet


def _event(code: bytes, *, frame: int, time_s: float, sequence: int):
    if code == b"FLBK":
        details = struct.pack("<If", 12, 20.0) + b"\x00" * 4
    else:
        details = b"\x01" * 12
    return make_datagram(
        packet_id=3,
        session_uid=SESSION_UID,
        frame=frame,
        session_time=time_s,
        body=code + details,
        sequence=sequence,
    )


def test_import_persists_event_payloads_and_marks_rewound_attempts(tmp_path) -> None:
    capture = tmp_path / "flashback.f1ecap"
    database = tmp_path / "state.sqlite3"
    packets = [
        _event(b"SSTA", frame=9, time_s=9.8, sequence=1),
        _lap_packet(
            frame=10,
            lap_number=1,
            distance_m=-0.5,
            session_time=10.0,
            sequence=2,
        ),
        _lap_packet(
            frame=11,
            lap_number=1,
            distance_m=0.5,
            session_time=10.1,
            sequence=3,
        ),
        _event(b"SEND", frame=15, time_s=50.0, sequence=4),
        _lap_packet(
            frame=20,
            lap_number=1,
            distance_m=5_000.0,
            current_lap_time_ms=79_000,
            session_time=89.0,
            sequence=5,
        ),
        _lap_packet(
            frame=21,
            lap_number=2,
            distance_m=0.5,
            last_lap_time_ms=79_500,
            session_time=89.1,
            sequence=6,
        ),
        _event(b"FLBK", frame=22, time_s=90.0, sequence=7),
        _lap_packet(
            frame=22,
            lap_number=2,
            distance_m=100.0,
            current_lap_time_ms=10_000,
            session_time=90.0,
            sequence=8,
        ),
        make_datagram(
            packet_id=255,
            session_uid=SESSION_UID,
            frame=23,
            session_time=20.1,
            sequence=9,
        ),
    ]
    with CaptureWriter(capture, {"fixture": "lifecycle"}) as writer:
        for packet in packets:
            writer.write(packet)

    imported = import_capture(capture, database)
    laps = list_laps(database, run_id=imported.run_id)
    events = list_processing_run_lifecycle_events(database, imported.run_id)
    detail = get_processing_run_detail(database, imported.run_id)
    summary = get_processing_run_summary(database, imported.run_id)

    assert imported.status == "complete"
    assert imported.event_packets == 3
    assert imported.lifecycle_events == 3
    assert [lap["disposition"] for lap in laps] == ["completed", "abandoned"]
    assert laps[0]["superseded"] is True
    assert laps[0]["lifecycle_assessed"] is True
    assert laps[0]["reference_eligible"] is False
    assert "superseded_by_flashback" in laps[0]["exclusion_reasons"]
    api_lap = LapRecord.model_validate(laps[0])
    assert api_lap.superseded is True
    assert api_lap.lifecycle_assessed is True
    assert "superseded_by_flashback" in api_lap.exclusion_reasons
    assert laps[1]["superseded"] is False
    assert [event["event_code"] for event in events["items"]] == ["SSTA", "SEND", "FLBK"]
    assert events["items"][2]["target_game_frame_identifier"] == 12
    assert events["items"][2]["current_overall_frame_identifier"] == 22
    assert events["items"][2]["details_hex"] == (
        struct.pack("<If", 12, 20.0) + b"\x00" * 4
    ).hex()
    assert events["items"][2]["details_length_bytes"] == 12
    assert events["items"][2]["details_truncated"] is False
    assert detail is not None and detail["lifecycle_events"]["total"] == 3
    assert summary is not None
    assert summary["totals"]["flashback_event_count"] == 1
    assert summary["processing"]["lifecycle_evidence"]["event_code_counts"] == {
        "FLBK": 1,
        "SEND": 1,
        "SSTA": 1,
    }
    loaded = load_attempt_trace(database, laps[0]["attempt_key"])
    assert loaded is not None and loaded.superseded is True
