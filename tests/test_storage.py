from __future__ import annotations

import sqlite3
from io import BytesIO
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from f1_engineer.recording.capture import CaptureWriter
from f1_engineer.storage import importer as importer_module
from f1_engineer.storage import query as query_module
from f1_engineer.storage.database import Database
from f1_engineer.storage.importer import get_lap, import_capture, list_laps, list_sessions
from f1_engineer.storage.lock import ImportRunLock
from f1_engineer.storage.query import (
    TRAJECTORY_TRACE_COLUMNS,
    AttemptTraceReadLimitError,
    load_attempt_policy_metadata,
    load_attempt_trace,
    load_reference_inventory,
)
from f1_engineer.storage.parquet import (
    TRACE_SCHEMA_V1,
    TRACE_SCHEMA_V2,
    TRACE_SCHEMA_VERSION,
    read_trace,
)
from f1_engineer.udp.car_telemetry import _F1_25_CAR_TELEMETRY_V1_CAR
from f1_engineer.udp.participants import _PARTICIPANT_PREFIX
from tests.helpers import make_datagram
from tests.test_lap_tracking import SESSION_UID, _lap_body


SESSION_FIXTURE = Path(__file__).parent / "fixtures" / "f1_25_session_packet_v1.bin"


def _participants_body() -> bytes:
    records = []
    for index in range(22):
        name = b"Driver One" if index == 0 else b""
        fields = (
            0, 7, 0, 3, 1, 44, 8, name.ljust(32, b"\0"), 1, 1, 99, 0, 3
        ) + (0,) * 12
        records.append(_PARTICIPANT_PREFIX.pack(*fields))
    return bytes((1,)) + b"".join(records)


def _telemetry_body() -> bytes:
    car = _F1_25_CAR_TELEMETRY_V1_CAR.pack(
        200, 0.8, -0.25, 0.1, 20, 5, 12_000, 1, 90, 0x1234,
        *(400 + index for index in range(4)),
        91, 92, 93, 94,
        101, 102, 103, 104,
        95,
        23.1, 23.2, 23.3, 23.4,
        0, 1, 2, 3,
    )
    return car * 22 + bytes((2, 255, 6))


def _lap_datagram(frame: int, sequence: int, distance: float):
    return make_datagram(
        packet_id=2,
        session_uid=SESSION_UID,
        frame=frame,
        session_time=frame / 60,
        body=_lap_body(lap_number=1, distance_m=distance, current_lap_time_ms=frame * 10),
        sequence=sequence,
    )


def test_import_writes_idempotent_sqlite_inventory_and_parquet_trace(
    tmp_path, monkeypatch
) -> None:
    capture_path = tmp_path / "synthetic.f1ecap"
    database_path = tmp_path / "state" / "f1.sqlite3"
    session_body = SESSION_FIXTURE.read_bytes()[29:]
    packets = [
        make_datagram(
            packet_id=1,
            session_uid=SESSION_UID,
            frame=10,
            body=session_body,
            sequence=0,
        ),
        make_datagram(
            packet_id=4,
            session_uid=SESSION_UID,
            frame=10,
            body=_participants_body(),
            sequence=1,
        ),
        _lap_datagram(10, 2, 10.0),
        make_datagram(
            packet_id=6,
            session_uid=SESSION_UID,
            frame=10,
            body=_telemetry_body(),
            sequence=3,
        ),
        _lap_datagram(13, 4, 25.0),  # Deliberately missing this telemetry family.
        _lap_datagram(16, 5, 50.0),
        make_datagram(
            packet_id=6,
            session_uid=SESSION_UID,
            frame=16,
            body=_telemetry_body(),
            sequence=6,
        ),
    ]
    with CaptureWriter(capture_path, {"fixture": "storage-test"}) as writer:
        for packet in packets:
            writer.write(packet)

    imported = import_capture(capture_path, database_path)

    assert imported.status == "complete"
    assert imported.already_imported is False
    assert imported.attempts == 1
    assert imported.samples == 3
    assert imported.import_late_packets_ignored == 0
    assert imported.import_frame_overflow_packets_dropped == 0
    assert imported.missing_car_telemetry_samples == 1
    assert imported.missing_car_telemetry_frames == ((SESSION_UID, 13),)
    assert imported.participant_packets == 1
    assert imported.motion_packets == 0
    assert imported.player_motion_samples == 3
    assert imported.missing_player_motion_samples == 3
    assert imported.car_status_packets == 0
    assert imported.missing_player_car_status_samples == 3
    assert len(list_sessions(database_path)) == 1
    session_record = list_sessions(database_path)[0]
    assert session_record["capture_sha256"] == imported.capture_sha256
    assert session_record["pipeline_version"] == importer_module.PIPELINE_VERSION
    assert session_record["started_at_utc"]
    assert session_record["finished_at_utc"]
    laps = list_laps(database_path)
    assert len(laps) == 1
    assert laps[0]["disposition"] == "partial"
    assert laps[0]["quality"]["missing_car_telemetry_count"] == 1
    assert laps[0]["quality"]["missing_motion_count"] == 3

    stored_attempt = load_attempt_trace(database_path, laps[0]["attempt_key"])
    assert stored_attempt is not None
    assert len(stored_attempt.samples) == 3
    assert stored_attempt.attempt_number == 1
    assert stored_attempt.start_observed is False
    assert stored_attempt.pit_encountered is False
    assert stored_attempt.trace_sha256
    assert stored_attempt.trace_schema_version == TRACE_SCHEMA_VERSION == 4
    assert stored_attempt.context_segments[0][1]["game_mode"] == "time_trial"
    monkeypatch.setattr(
        query_module,
        "read_trace",
        lambda *_args, **_kwargs: pytest.fail("policy metadata must not decode Parquet"),
    )
    policy_metadata = load_attempt_policy_metadata(
        database_path,
        laps[0]["attempt_key"],
        max_context_segments=1,
    )
    assert policy_metadata is not None
    assert policy_metadata.samples == ()
    assert policy_metadata.source_sample_count == len(stored_attempt.samples)
    assert policy_metadata.trace_sha256 == stored_attempt.trace_sha256
    with pytest.raises(
        AttemptTraceReadLimitError,
        match="context_segments_limit_exceeded",
    ):
        load_attempt_policy_metadata(
            database_path,
            laps[0]["attempt_key"],
            max_context_segments=0,
        )
    monkeypatch.setattr(query_module, "read_trace", read_trace)
    with pytest.raises(AttemptTraceReadLimitError, match="rows_limit_exceeded"):
        load_attempt_trace(
            database_path,
            laps[0]["attempt_key"],
            max_trace_rows=2,
        )
    with pytest.raises(AttemptTraceReadLimitError, match="bytes_limit_exceeded"):
        load_attempt_trace(
            database_path,
            laps[0]["attempt_key"],
            max_trace_bytes=1,
        )
    with pytest.raises(AttemptTraceReadLimitError, match="context_segments_limit_exceeded"):
        load_attempt_trace(
            database_path,
            laps[0]["attempt_key"],
            max_context_segments=0,
        )
    inventory = load_reference_inventory(database_path, laps[0]["attempt_key"])
    assert inventory is not None
    assert inventory.run_id == imported.run_id
    assert inventory.target_attempt_number == 1
    assert inventory.capture_complete is True
    assert inventory.capture_completion == {"status": "complete"}
    assert inventory.processing_quality["import_late_packets_ignored"] == 0
    assert inventory.processing_quality["import_frame_overflow_packets_dropped"] == 0
    assert len(inventory.attempts) == 1
    assert inventory.attempts[0].trace_row_count == 3
    with Database(database_path, read_only=True) as read_only_db:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            read_only_db.connection.execute("DELETE FROM sessions")
    trajectory_attempt = load_attempt_trace(
        database_path,
        laps[0]["attempt_key"],
        columns=TRAJECTORY_TRACE_COLUMNS,
    )
    assert trajectory_attempt is not None
    assert trajectory_attempt.samples[0]["motion_available"] is False
    assert trajectory_attempt.samples[0]["world_position_x_m"] is None
    status_attempt = load_attempt_trace(
        database_path,
        laps[0]["attempt_key"],
        columns=["car_status_available", "car_status_unavailable_reason"],
    )
    assert status_attempt is not None
    assert status_attempt.samples[0] == {
        "car_status_available": False,
        "car_status_unavailable_reason": "status_packet_missing",
    }

    details = get_lap(database_path, laps[0]["attempt_key"])
    assert details is not None
    trace_path = database_path.parent / details["trace_path"]
    original_bytes = trace_path.read_bytes()
    _, original_table = query_module.read_trace(original_bytes)
    speed_column = original_table.schema.get_field_index("speed_mps")
    altered_table = original_table.set_column(
        speed_column,
        original_table.schema.field(speed_column),
        pa.array([999.0] * original_table.num_rows, type=pa.float32()),
    )
    altered_buffer = BytesIO()
    pq.write_table(altered_table, altered_buffer, compression="zstd")
    altered_bytes = altered_buffer.getvalue()
    actual_read_trace = query_module.read_trace

    def replace_trace_before_parsing(
        snapshot, *, columns=None, expected_schema_version=None, max_rows=None
    ):
        trace_path.write_bytes(altered_bytes)
        return actual_read_trace(
            snapshot,
            columns=columns,
            expected_schema_version=expected_schema_version,
            max_rows=max_rows,
        )

    monkeypatch.setattr(query_module, "read_trace", replace_trace_before_parsing)
    try:
        raced_attempt = load_attempt_trace(database_path, laps[0]["attempt_key"])
    finally:
        trace_path.write_bytes(original_bytes)
    assert raced_attempt is not None
    assert raced_attempt.samples == stored_attempt.samples
    monkeypatch.setattr(query_module, "read_trace", actual_read_trace)

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE telemetry_files SET row_count = 1 WHERE attempt_key = ?",
            (laps[0]["attempt_key"],),
        )
    try:
        with pytest.raises(AttemptTraceReadLimitError, match="rows_limit_exceeded"):
            load_attempt_trace(
                database_path,
                laps[0]["attempt_key"],
                max_trace_rows=2,
            )
    finally:
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "UPDATE telemetry_files SET row_count = ? WHERE attempt_key = ?",
                (len(stored_attempt.samples), laps[0]["attempt_key"]),
            )

    assert details["trace_checksum_valid"] is True
    assert details["parquet_rows"] == 3
    assert details["first_sample"][0]["session_uid"] == str(SESSION_UID)
    assert details["first_sample"][0]["attempt_id"] == laps[0]["attempt_key"]
    assert details["last_sample"][0]["speed_mps"] > 55.5

    with sqlite3.connect(database_path) as connection:
        uid = connection.execute("SELECT session_uid FROM sessions").fetchone()[0]
        driver_rows = connection.execute("SELECT COUNT(*) FROM driver_snapshots").fetchone()[0]
    assert uid == str(SESSION_UID)  # EA UID exceeds SQLite's signed integer range.
    assert driver_rows == 22

    repeated = import_capture(capture_path, database_path)
    assert repeated.run_id == imported.run_id
    assert repeated.already_imported is True
    assert repeated.missing_car_telemetry_frames == ((SESSION_UID, 13),)
    assert len(list_laps(database_path)) == 1

    (database_path.parent / details["trace_path"]).unlink()
    repaired = import_capture(capture_path, database_path)
    assert repaired.already_imported is False
    assert repaired.run_id == imported.run_id
    assert len(list_laps(database_path)) == 1

    other_database = tmp_path / "state" / "other.sqlite3"
    other_import = import_capture(capture_path, other_database)
    other_lap = list_laps(other_database)[0]
    other_details = get_lap(other_database, other_lap["attempt_key"])
    assert other_import.run_id == imported.run_id
    assert other_details is not None
    assert other_details["trace_path"] != details["trace_path"]
    assert get_lap(database_path, laps[0]["attempt_key"]) is not None


def test_trace_reader_adapts_v1_rows_with_unavailable_motion_fields() -> None:
    table = pa.Table.from_pylist(
        [{"frame_identifier": 7, "lap_distance_m": 125.5}],
        schema=TRACE_SCHEMA_V1,
    )
    encoded = BytesIO()
    pq.write_table(table, encoded, compression="zstd")

    metadata, loaded = read_trace(
        encoded.getvalue(),
        columns=[
            "frame_identifier",
            "lap_distance_m",
            "motion_available",
            "world_position_x_m",
        ],
        expected_schema_version=1,
    )

    assert metadata.num_rows == 1
    assert loaded.to_pylist() == [
        {
            "frame_identifier": 7,
            "lap_distance_m": 125.5,
            "motion_available": None,
            "world_position_x_m": None,
        }
    ]


def test_trace_reader_adapts_v2_rows_with_unavailable_car_status_fields() -> None:
    table = pa.Table.from_pylist(
        [{"frame_identifier": 9, "motion_available": True}],
        schema=TRACE_SCHEMA_V2,
    )
    encoded = BytesIO()
    pq.write_table(table, encoded, compression="zstd")

    metadata, loaded = read_trace(
        encoded.getvalue(),
        columns=["frame_identifier", "car_status_available", "fuel_in_tank_reported"],
        expected_schema_version=2,
    )

    assert metadata.num_rows == 1
    assert loaded.to_pylist() == [{
        "frame_identifier": 9,
        "car_status_available": None,
        "fuel_in_tank_reported": None,
    }]


def test_format_change_invalidates_current_persisted_session_context(tmp_path) -> None:
    capture_path = tmp_path / "format-change.f1ecap"
    database_path = tmp_path / "format.sqlite3"
    session_body = SESSION_FIXTURE.read_bytes()[29:]
    with CaptureWriter(capture_path) as writer:
        writer.write(
            make_datagram(
                packet_id=1,
                session_uid=SESSION_UID,
                frame=10,
                body=session_body,
                sequence=0,
            )
        )
        writer.write(
            make_datagram(
                packet_format=2026,
                packet_id=255,
                session_uid=SESSION_UID,
                frame=20,
                body=b"new-format",
                sequence=1,
            )
        )

    import_capture(capture_path, database_path)

    session = list_sessions(database_path)[0]
    assert session["packet_format"] == 2026
    assert session["context"] is None
    with sqlite3.connect(database_path) as connection:
        invalidations = connection.execute(
            "SELECT effective_frame, reason FROM session_context_invalidations"
        ).fetchall()
        historical_contexts = connection.execute(
            "SELECT COUNT(*) FROM session_contexts"
        ).fetchone()[0]
    assert invalidations == [(20, "packet_format_changed")]
    assert historical_contexts == 1


def test_import_persists_historical_context_without_rolling_back_current_context(tmp_path) -> None:
    capture_path = tmp_path / "context-history.f1ecap"
    database_path = tmp_path / "context.sqlite3"
    body = SESSION_FIXTURE.read_bytes()[29:]
    changed_context = bytearray(body)
    changed_context[1] = 37
    with CaptureWriter(capture_path) as writer:
        for sequence, frame, session_body in (
            (0, 10, body),
            (1, 20, bytes(changed_context)),
            (2, 19, body),  # Reordered checkpoint, accepted as history only.
        ):
            writer.write(
                make_datagram(
                    packet_id=1,
                    session_uid=SESSION_UID,
                    frame=frame,
                    body=session_body,
                    sequence=sequence,
                )
            )

    import_capture(capture_path, database_path)

    session = list_sessions(database_path)[0]
    assert session["context"]["track_temperature_c"] == 37
    with sqlite3.connect(database_path) as connection:
        checkpoints = connection.execute(
            "SELECT effective_frame, context_json FROM session_contexts "
            "ORDER BY effective_frame"
        ).fetchall()
    assert [frame for frame, _ in checkpoints] == [10, 19, 20]


def test_import_run_lock_rejects_a_second_writer_and_releases_on_exit(tmp_path) -> None:
    lock_path = tmp_path / "run.lock"
    with ImportRunLock(lock_path):
        try:
            ImportRunLock(lock_path).__enter__()
        except ValueError as exc:
            assert "already running" in str(exc)
        else:
            raise AssertionError("second import lock unexpectedly acquired")

    with ImportRunLock(lock_path):
        pass


def test_import_fails_if_capture_changes_while_it_is_being_read(tmp_path, monkeypatch) -> None:
    capture_path = tmp_path / "changing.f1ecap"
    database_path = tmp_path / "changing.sqlite3"
    with CaptureWriter(capture_path):
        pass

    original_hash = importer_module._capture_hash
    hash_calls = 0

    def append_before_final_hash(path: Path) -> tuple[str, int]:
        nonlocal hash_calls
        hash_calls += 1
        if hash_calls == 2:
            with path.open("ab") as stream:
                stream.write(b"late recorder data")
        return original_hash(path)

    monkeypatch.setattr(importer_module, "_capture_hash", append_before_final_hash)

    with pytest.raises(ValueError, match="capture changed during import"):
        import_capture(capture_path, database_path)

    with sqlite3.connect(database_path) as connection:
        status = connection.execute("SELECT status FROM processing_runs").fetchone()[0]
    assert status == "failed"
