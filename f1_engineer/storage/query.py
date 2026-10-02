from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .database import Database
from .parquet import TRACE_SCHEMA_VERSION, read_trace


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


def load_attempt_trace(
    database_path: str | Path, attempt_key: str
) -> StoredAttemptTrace | None:
    """Load a completed attempt after verifying its published Parquet trace."""
    database_path = Path(database_path)
    with Database(database_path) as db:
        row = db.connection.execute(
            """SELECT l.attempt_key, l.car_index, l.disposition, l.lap_time_ms,
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
    if row["schema_version"] != TRACE_SCHEMA_VERSION:
        raise ValueError(f"unsupported trace schema version {row['schema_version']}")

    trace_snapshot = trace_path.read_bytes()
    if hashlib.sha256(trace_snapshot).hexdigest() != row["sha256"]:
        raise ValueError("trace file is missing or its checksum does not match SQLite")
    metadata, table = read_trace(trace_snapshot, columns=ANALYSIS_TRACE_COLUMNS)
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
    )
