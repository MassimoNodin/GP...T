from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .database import Database
from .parquet import SUPPORTED_TRACE_SCHEMA_VERSIONS, read_trace


ANALYSIS_TRACE_COLUMNS = [
    "frame_identifier",
    "session_time_s",
    "lap_distance_m",
    "current_lap_time_ms",
    "speed_mps",
    "throttle",
    "brake",
    "steering",
    "gear",
    "drs_active",
]
TRAJECTORY_TRACE_COLUMNS = [
    *ANALYSIS_TRACE_COLUMNS,
    "motion_available",
    "world_position_x_m",
    "world_position_y_m",
    "world_position_z_m",
    "world_velocity_x_mps",
    "world_velocity_y_mps",
    "world_velocity_z_mps",
    "world_forward_x",
    "world_forward_y",
    "world_forward_z",
    "world_right_x",
    "world_right_y",
    "world_right_z",
    "g_force_lateral",
    "g_force_longitudinal",
    "g_force_vertical",
    "yaw_rad",
    "pitch_rad",
    "roll_rad",
]


@dataclass(frozen=True, slots=True)
class StoredAttemptTrace:
    attempt_key: str
    run_id: str
    session_uid: str
    car_index: int
    disposition: str
    lap_time_ms: int | None
    game_valid: bool | None
    reference_eligible: bool
    exclusion_reasons: tuple[str, ...]
    trace_sha256: str
    trace_schema_version: int
    quality: Mapping[str, object]
    context_segments: tuple[tuple[int, Mapping[str, object] | None], ...]
    samples: tuple[Mapping[str, object], ...]
    attempt_number: int = 1
    start_observed: bool = True
    pit_encountered: bool = False


@dataclass(frozen=True, slots=True)
class StoredAttemptInventoryEntry:
    attempt_key: str
    run_id: str
    session_uid: str
    car_index: int
    attempt_number: int
    disposition: str
    lap_time_ms: int | None
    game_valid: bool | None
    reference_eligible: bool
    start_observed: bool
    pit_encountered: bool
    sample_count: int
    exclusion_reasons: tuple[str, ...]
    trace_ready: bool
    trace_row_count: int | None
    trace_sha256: str | None
    trace_schema_version: int | None
    quality: Mapping[str, object]
    context_segments: tuple[tuple[int, Mapping[str, object] | None], ...]


@dataclass(frozen=True, slots=True)
class StoredReferenceInventory:
    target_attempt_key: str
    run_id: str
    session_uid: str
    car_index: int
    target_attempt_number: int
    capture_complete: bool
    capture_completion: Mapping[str, object] | None
    processing_quality: Mapping[str, object]
    attempts: tuple[StoredAttemptInventoryEntry, ...]


class AttemptTraceReadLimitError(ValueError):
    """Raised when a bounded trace consumer refuses an oversized artifact."""

    def __init__(self, limit_kind: str) -> None:
        self.limit_kind = limit_kind
        super().__init__(f"attempt_trace_{limit_kind}_limit_exceeded")


def load_attempt_trace(
    database_path: str | Path,
    attempt_key: str,
    *,
    columns: list[str] | None = None,
    max_trace_bytes: int | None = None,
    max_trace_rows: int | None = None,
    max_context_segments: int | None = None,
    max_context_bytes: int | None = None,
) -> StoredAttemptTrace | None:
    """Load a completed attempt after verifying its published Parquet trace."""
    database_path = Path(database_path)
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            """SELECT l.attempt_key, l.car_index, l.attempt_number,
                      l.disposition, l.lap_time_ms, l.start_observed, l.pit_encountered,
                      l.game_valid, l.reference_eligible, l.exclusion_reasons_json,
                      s.session_uid, s.run_id, t.relative_path, t.row_count,
                      t.sha256, t.quality_json, t.schema_version
                 FROM lap_attempts l JOIN sessions s USING(session_key)
                 JOIN processing_runs r USING(run_id)
                 JOIN telemetry_files t USING(attempt_key)
                WHERE l.attempt_key = ? AND t.ready = 1 AND r.status = 'complete'""",
            (attempt_key,),
        ).fetchone()
        if row is None:
            return None
        if max_trace_rows is not None and int(row["row_count"]) > max_trace_rows:
            raise AttemptTraceReadLimitError("rows")
        if max_context_segments is not None or max_context_bytes is not None:
            context_size = db.connection.execute(
                """SELECT COUNT(*) AS segment_count,
                          COALESCE(SUM(LENGTH(CAST(context_json AS BLOB))), 0) AS context_bytes
                     FROM lap_context_segments WHERE attempt_key = ?""",
                (attempt_key,),
            ).fetchone()
            if (
                max_context_segments is not None
                and int(context_size["segment_count"]) > max_context_segments
            ):
                raise AttemptTraceReadLimitError("context_segments")
            if (
                max_context_bytes is not None
                and int(context_size["context_bytes"]) > max_context_bytes
            ):
                raise AttemptTraceReadLimitError("context_bytes")
        context_rows = db.connection.execute(
            """SELECT from_frame_identifier, context_json
                 FROM lap_context_segments WHERE attempt_key = ? ORDER BY ordinal""",
            (attempt_key,),
        ).fetchall()

    relative_path = Path(row["relative_path"])
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError("stored trace path is invalid")
    root = database_path.parent.resolve()
    trace_path = (database_path.parent / relative_path).resolve()
    if not trace_path.is_relative_to(root):
        raise ValueError("stored trace path escapes the database directory")
    if not trace_path.is_file():
        raise ValueError("trace file is missing")
    if max_trace_bytes is not None and trace_path.stat().st_size > max_trace_bytes:
        raise AttemptTraceReadLimitError("bytes")
    if row["schema_version"] not in SUPPORTED_TRACE_SCHEMA_VERSIONS:
        raise ValueError(f"unsupported trace schema version {row['schema_version']}")

    if max_trace_bytes is None:
        trace_snapshot = trace_path.read_bytes()
    else:
        with trace_path.open("rb") as trace_stream:
            trace_snapshot = trace_stream.read(max_trace_bytes + 1)
        if len(trace_snapshot) > max_trace_bytes:
            raise AttemptTraceReadLimitError("bytes")
    if hashlib.sha256(trace_snapshot).hexdigest() != row["sha256"]:
        raise ValueError("trace file is missing or its checksum does not match SQLite")
    try:
        metadata, table = read_trace(
            trace_snapshot,
            columns=ANALYSIS_TRACE_COLUMNS if columns is None else columns,
            expected_schema_version=row["schema_version"],
            max_rows=max_trace_rows,
        )
    except ValueError as exc:
        if str(exc) == "trace_row_limit_exceeded":
            raise AttemptTraceReadLimitError("rows") from exc
        raise
    if metadata.num_rows != row["row_count"]:
        raise ValueError("trace row count does not match SQLite")

    contexts = tuple(
        (
            int(context_row["from_frame_identifier"]),
            json.loads(context_row["context_json"])
            if context_row["context_json"] is not None
            else None,
        )
        for context_row in context_rows
    )
    return StoredAttemptTrace(
        attempt_key=row["attempt_key"],
        run_id=row["run_id"],
        session_uid=row["session_uid"],
        car_index=row["car_index"],
        disposition=row["disposition"],
        lap_time_ms=row["lap_time_ms"],
        game_valid=None if row["game_valid"] is None else bool(row["game_valid"]),
        reference_eligible=bool(row["reference_eligible"]),
        exclusion_reasons=tuple(json.loads(row["exclusion_reasons_json"])),
        trace_sha256=row["sha256"],
        trace_schema_version=row["schema_version"],
        quality=json.loads(row["quality_json"]),
        context_segments=contexts,
        samples=tuple(table.to_pylist()),
        attempt_number=row["attempt_number"],
        start_observed=bool(row["start_observed"]),
        pit_encountered=bool(row["pit_encountered"]),
    )


def load_reference_inventory(
    database_path: str | Path, attempt_key: str
) -> StoredReferenceInventory | None:
    """Read run-scoped attempt/context metadata for deterministic reference selection."""
    database_path = Path(database_path)
    with Database(database_path, read_only=True) as db:
        target = db.connection.execute(
            """SELECT l.attempt_key, l.attempt_number, l.car_index,
                      s.run_id, s.session_uid
                 FROM lap_attempts l JOIN sessions s USING(session_key)
                 JOIN processing_runs r USING(run_id)
                WHERE l.attempt_key = ? AND r.status = 'complete'""",
            (attempt_key,),
        ).fetchone()
        if target is None:
            return None
        rows = db.connection.execute(
            """SELECT l.attempt_key, l.attempt_number, l.car_index, l.disposition,
                      l.lap_time_ms, l.game_valid, l.reference_eligible,
                      l.start_observed, l.pit_encountered, l.sample_count,
                      l.exclusion_reasons_json,
                      s.run_id, s.session_uid, t.ready, t.row_count, t.sha256, t.schema_version,
                      t.quality_json
                 FROM lap_attempts l JOIN sessions s USING(session_key)
                 JOIN processing_runs r USING(run_id)
                 LEFT JOIN telemetry_files t USING(attempt_key)
                WHERE s.run_id = ? AND s.session_uid = ? AND l.car_index = ?
                  AND r.status = 'complete'
                ORDER BY l.attempt_number""",
            (target["run_id"], target["session_uid"], target["car_index"]),
        ).fetchall()
        capture = db.connection.execute(
            """SELECT c.complete, c.completion_json, r.metrics_json
                 FROM processing_runs r JOIN captures c USING(capture_sha256)
                WHERE r.run_id = ?""",
            (target["run_id"],),
        ).fetchone()
        context_rows = db.connection.execute(
            """SELECT c.attempt_key, c.from_frame_identifier, c.context_json
                 FROM lap_context_segments c JOIN lap_attempts l USING(attempt_key)
                 JOIN sessions s USING(session_key)
                WHERE s.run_id = ? AND s.session_uid = ? AND l.car_index = ?
                ORDER BY c.attempt_key, c.ordinal""",
            (target["run_id"], target["session_uid"], target["car_index"]),
        ).fetchall()

    contexts_by_attempt: dict[str, list[tuple[int, Mapping[str, object] | None]]] = {}
    for context_row in context_rows:
        raw_context = (
            json.loads(context_row["context_json"])
            if context_row["context_json"] is not None
            else None
        )
        contexts_by_attempt.setdefault(context_row["attempt_key"], []).append(
            (int(context_row["from_frame_identifier"]), raw_context)
        )
    attempts = tuple(
        StoredAttemptInventoryEntry(
            attempt_key=row["attempt_key"],
            run_id=row["run_id"],
            session_uid=row["session_uid"],
            car_index=row["car_index"],
            attempt_number=row["attempt_number"],
            disposition=row["disposition"],
            lap_time_ms=row["lap_time_ms"],
            game_valid=None if row["game_valid"] is None else bool(row["game_valid"]),
            reference_eligible=bool(row["reference_eligible"]),
            start_observed=bool(row["start_observed"]),
            pit_encountered=bool(row["pit_encountered"]),
            sample_count=row["sample_count"],
            exclusion_reasons=tuple(json.loads(row["exclusion_reasons_json"])),
            trace_ready=bool(row["ready"]),
            trace_row_count=row["row_count"],
            trace_sha256=row["sha256"],
            trace_schema_version=row["schema_version"],
            quality=json.loads(row["quality_json"]) if row["quality_json"] else {},
            context_segments=tuple(contexts_by_attempt.get(row["attempt_key"], ())),
        )
        for row in rows
    )
    completion = (
        json.loads(capture["completion_json"])
        if capture is not None and capture["completion_json"]
        else None
    )
    metrics = json.loads(capture["metrics_json"]) if capture is not None and capture["metrics_json"] else {}
    processing_quality = metrics.get("capture_quality", {})
    return StoredReferenceInventory(
        target_attempt_key=target["attempt_key"],
        run_id=target["run_id"],
        session_uid=target["session_uid"],
        car_index=target["car_index"],
        target_attempt_number=target["attempt_number"],
        capture_complete=bool(capture["complete"]) if capture is not None else False,
        capture_completion=completion,
        processing_quality=(
            processing_quality if isinstance(processing_quality, Mapping) else {}
        ),
        attempts=attempts,
    )
