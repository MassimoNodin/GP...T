from __future__ import annotations

import struct

from f1_engineer.api.app import LapRecord
from f1_engineer.recording.capture import CaptureWriter
from f1_engineer.storage.importer import get_lap, import_capture, list_laps
from f1_engineer.storage.query import load_attempt_timing_evidence
from f1_engineer.storage.database import Database
from f1_engineer.storage.run_summaries import get_processing_run_detail
from f1_engineer.udp.header import HEADER_SIZE, parse_header
from tests.test_lap_tracking import SESSION_UID, _lap_packet, _session_packet
from tests.helpers import make_datagram


def _history_body() -> bytes:
    body = bytearray(1431)
    body[:7] = bytes((0, 1, 1, 0xFE, 0xFD, 0xFC, 0xFB))
    struct.pack_into("<IHBHBHBB", body, 7, 79_295, 27_446, 0, 17_987, 0, 33_861, 0, 0x06)
    struct.pack_into("<BBB", body, 7 + 100 * 14, 255, 3, 4)
    return bytes(body)


def _history(*, frame: int, sequence: int):
    return make_datagram(
        packet_id=11,
        session_uid=SESSION_UID,
        frame=frame,
        session_time=float(frame),
        body=_history_body(),
        sequence=sequence,
        player_car_index=0,
    )


def _session_packet_at_frame8():
    original = _session_packet()
    header = parse_header(original.payload)
    return make_datagram(
        packet_format=header.packet_format,
        packet_id=header.packet_id,
        packet_version=header.packet_version,
        session_uid=header.session_uid,
        frame=8,
        session_time=header.session_time,
        body=original.payload[HEADER_SIZE:],
        sequence=original.sequence,
    )


def test_import_persists_only_post_finalization_reported_timing(tmp_path) -> None:
    capture = tmp_path / "history.f1ecap"
    database = tmp_path / "state.sqlite3"
    packets = [
        _session_packet_at_frame8(),
        _lap_packet(frame=9, lap_number=1, distance_m=-0.5, session_time=10.0, sequence=2),
        _lap_packet(frame=10, lap_number=1, distance_m=0.5, session_time=10.1, sequence=3),
        _lap_packet(
            frame=11,
            lap_number=1,
            distance_m=5_100.0,
            current_lap_time_ms=79_000,
            session_time=89.0,
            sequence=4,
        ),
        _history(frame=12, sequence=5),
        _lap_packet(
            frame=12,
            lap_number=2,
            distance_m=0.5,
            last_lap_time_ms=79_295,
            session_time=89.1,
            sequence=6,
        ),
        _history(frame=13, sequence=7),
        *[
            make_datagram(
                packet_id=255,
                session_uid=SESSION_UID,
                frame=frame,
                session_time=float(frame),
                body=b"advance",
                sequence=frame + 10,
            )
            for frame in (14, 15, 16)
        ],
    ]
    with CaptureWriter(capture, {"fixture": "session-history"}) as writer:
        for packet in packets:
            writer.write(packet)

    imported = import_capture(capture, database)
    laps = list_laps(database, run_id=imported.run_id)
    assert imported.status == "complete"
    assert imported.session_history_packets_admitted == 2
    assert imported.session_history_packets_decoded == 2
    assert imported.session_history_matched_attempts == 1
    completed_laps = [lap for lap in laps if lap["disposition"] == "completed"]
    assert len(completed_laps) == 1
    lap = completed_laps[0]
    evidence = lap["timing_evidence"]
    assert evidence["status"] == "matched"
    assert evidence["sector1_time_ms"] == 27_446
    assert evidence["sector2_time_ms"] == 17_987
    assert evidence["sector3_time_ms"] == 33_861
    assert evidence["sector1_valid"] is True
    assert evidence["lap_valid"] is False
    assert evidence["source"]["capture_sequence"] == 7
    assert evidence["source"]["best_lap_time_lap_number"] == 0xFE

    attempt = get_lap(database, lap["attempt_key"])
    assert attempt is not None
    completion_ordinal = attempt["attempt"]["completion_frame_ordinal"]
    assert evidence["source"]["frame_ordinal"] > completion_ordinal
    assert evidence["provenance"] == {
        "attempt_key": lap["attempt_key"],
        "run_id": imported.run_id,
        "capture_sha256": imported.capture_sha256,
        "completion_frame_ordinal": completion_ordinal,
    }
    assert load_attempt_timing_evidence(database, lap["attempt_key"]) == evidence
    assert LapRecord.model_validate(lap).timing_evidence["status"] == "matched"
    detail = get_processing_run_detail(database, imported.run_id)
    assert detail is not None
    assert detail["attempts"]["items"][0]["timing_evidence"]["status"] == "matched"

    with Database(database) as db:
        with db.connection:
            db.connection.execute(
                "DELETE FROM attempt_timing_evidence WHERE attempt_key=?",
                (lap["attempt_key"],),
            )
    legacy = load_attempt_timing_evidence(database, lap["attempt_key"])
    assert legacy == {
        "status": "unavailable",
        "reasons": ["not_available_for_legacy_import"],
        "provenance": {
            "attempt_key": lap["attempt_key"],
            "run_id": imported.run_id,
            "capture_sha256": imported.capture_sha256,
            "completion_frame_ordinal": completion_ordinal,
        },
    }
