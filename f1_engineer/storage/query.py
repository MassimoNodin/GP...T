from __future__ import annotations

import json
import hashlib
import io
import math
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

import pyarrow.compute as pc
import pyarrow.parquet as pq

from .database import Database
from .participant_context import (
    MAX_PLAYER_PARTICIPANT_CONTEXT_BYTES,
    load_attempt_player_participant_context,
)
from .car_setup_context import (
    MAX_PLAYER_CAR_SETUP_CONTEXT_BYTES,
    load_attempt_player_car_setup_context,
)
from .parquet import (
    MAX_OBSERVATION_CHUNK_ROWS,
    ROW_GROUP_SIZE,
    OBSERVATION_SCHEMA_VERSION,
    SUPPORTED_TRACE_SCHEMA_VERSIONS,
    read_trace,
    sha256_file,
)


MAX_OBSERVATION_PREVIEW_CHUNKS = 256
MAX_OBSERVATION_PREVIEW_MANIFEST_BYTES = 16 * 1024
MAX_OBSERVATION_PREVIEW_MANIFEST_TOTAL_BYTES = 4 * 1024 * 1024
MAX_OBSERVATION_SESSION_METRICS_BYTES = 1024 * 1024
MAX_OBSERVATION_CAPTURE_COMPLETION_BYTES = 64 * 1024
MAX_OBSERVATION_PREVIEW_CHUNK_BYTES = 16 * 1024 * 1024
MAX_OBSERVATION_PREVIEW_SOURCE_BYTES = 64 * 1024 * 1024
MAX_OBSERVATION_PREVIEW_ROW_GROUP_BYTES = 16 * 1024 * 1024
MAX_OBSERVATION_PREVIEW_ROW_GROUPS = 1024
MAX_OBSERVATION_PREVIEW_ROWS_READ = 4_194_304
MAX_OBSERVATION_PREVIEW_PATH_BYTES = 512
MAX_OBSERVATION_PREVIEW_SHA256_BYTES = 64


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

ENGINEER_ATTEMPT_METADATA_LIMITS = {
    "attempt_reasons_bytes": 8_192,
    "context_bytes": 16_384,
    "timing_evidence_bytes": 32_768,
    "capture_completion_bytes": 8_192,
    "processing_metrics_bytes": 65_536,
    "player_participant_context_bytes": MAX_PLAYER_PARTICIPANT_CONTEXT_BYTES,
    "player_car_setup_context_bytes": MAX_PLAYER_CAR_SETUP_CONTEXT_BYTES,
}


def _quality_json_shape_within_bounds(value: object) -> bool:
    stack = [(value, 0)]
    remaining_nodes = 250_000
    while stack:
        current, depth = stack.pop()
        remaining_nodes -= 1
        if remaining_nodes < 0 or depth > 16:
            return False
        if current is None or isinstance(current, bool):
            continue
        if isinstance(current, str):
            if len(current) > 4_096:
                return False
            continue
        if isinstance(current, int):
            continue
        if isinstance(current, float):
            if not math.isfinite(current):
                return False
            continue
        if isinstance(current, (list, tuple)):
            if len(current) > 2_048:
                return False
            stack.extend((item, depth + 1) for item in current)
            continue
        if isinstance(current, dict):
            if len(current) > 512:
                return False
            for key, item in current.items():
                if not isinstance(key, str) or len(key) > 128:
                    return False
                stack.append((item, depth + 1))
            continue
        return False
    return True


def _quality_context_scalar(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, str):
        return len(value) <= 4_096
    if isinstance(value, int):
        try:
            return math.isfinite(float(value))
        except OverflowError:
            return False
    return isinstance(value, float) and math.isfinite(value)


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
    superseded: bool | None = None
    lifecycle_assessed: bool = False
    timing_evidence: Mapping[str, object] | None = None
    source_sample_count: int | None = None
    player_participant_context: Mapping[str, object] | None = None
    player_car_setup_context: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class AttemptTraceResourceEstimate:
    attempt_key: str
    run_id: str
    session_uid: str
    car_index: int
    attempt_number: int
    disposition: str
    lap_time_ms: int | None
    game_valid: bool | None
    start_observed: bool
    pit_encountered: bool
    superseded: bool | None
    lifecycle_assessed: bool
    exclusion_reasons: tuple[str, ...]
    trace_ready: bool
    trace_row_count: int | None
    trace_sha256: str | None
    trace_schema_version: int | None
    trace_size_bytes: int | None
    context_segment_count: int
    context_bytes: int
    context_segments: tuple[tuple[int, Mapping[str, object] | None], ...]


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
    superseded: bool | None = None
    lifecycle_assessed: bool = False
    trace_size_bytes: int | None = None
    context_segment_count: int | None = None
    context_bytes: int | None = None


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


@dataclass(frozen=True, slots=True)
class StoredAttemptCaptureEvidence:
    capture_complete: bool
    capture_completion: object | None
    processing_quality: Mapping[str, object]


class AttemptTraceReadLimitError(ValueError):
    """Raised when a bounded trace consumer refuses an oversized artifact."""

    def __init__(self, limit_kind: str) -> None:
        self.limit_kind = limit_kind
        super().__init__(f"attempt_trace_{limit_kind}_limit_exceeded")


class AttemptInventoryReadLimitError(ValueError):
    """Raised when bounded reference inventory metadata exceeds its read limits."""

    def __init__(self, limit_kind: str) -> None:
        self.limit_kind = limit_kind
        super().__init__(f"attempt_inventory_{limit_kind}_limit_exceeded")


class AttemptQualityReadLimitError(ValueError):
    """Raised when one-attempt quality metadata exceeds its read bounds."""

    def __init__(self, limit_kind: str) -> None:
        self.limit_kind = limit_kind
        super().__init__(f"attempt_quality_source_{limit_kind}_limit_exceeded")


@contextmanager
def _query_connection(
    database_path: str | Path, connection: sqlite3.Connection | None = None
) -> Iterator[sqlite3.Connection]:
    if connection is not None:
        yield connection
        return
    with Database(Path(database_path), read_only=True) as db:
        yield db.connection


def load_attempt_capture_quality_evidence(
    database_path: str | Path, attempt_key: str
) -> StoredAttemptCaptureEvidence | None:
    """Read bounded footer and capture counters for one attempt's owning run."""
    with Database(Path(database_path), read_only=True) as db:
        row = db.connection.execute(
            """SELECT c.complete AS capture_complete,
                      CASE WHEN LENGTH(CAST(c.completion_json AS BLOB)) <= ?
                           THEN c.completion_json END AS completion_json,
                      LENGTH(CAST(c.completion_json AS BLOB)) AS completion_bytes,
                      CASE WHEN LENGTH(CAST(r.metrics_json AS BLOB)) <= ?
                           THEN r.metrics_json END AS metrics_json,
                      LENGTH(CAST(r.metrics_json AS BLOB)) AS metrics_bytes
                 FROM lap_attempts l JOIN sessions s USING(session_key)
                 JOIN processing_runs r USING(run_id)
                 LEFT JOIN captures c USING(capture_sha256)
                WHERE l.attempt_key = ? AND r.status = 'complete'""",
            (
                MAX_OBSERVATION_CAPTURE_COMPLETION_BYTES,
                MAX_OBSERVATION_SESSION_METRICS_BYTES,
                attempt_key,
            ),
        ).fetchone()
    if row is None:
        return None
    completion_bytes = row["completion_bytes"]
    if (
        completion_bytes is not None
        and int(completion_bytes) > MAX_OBSERVATION_CAPTURE_COMPLETION_BYTES
    ):
        raise AttemptQualityReadLimitError("completion_bytes")
    metrics_bytes = row["metrics_bytes"]
    if (
        metrics_bytes is not None
        and int(metrics_bytes) > MAX_OBSERVATION_SESSION_METRICS_BYTES
    ):
        raise AttemptQualityReadLimitError("processing_metrics_bytes")

    completion_raw = row["completion_json"]
    if completion_raw is None:
        completion: object | None = None
    else:
        try:
            completion = json.loads(completion_raw)
        except (TypeError, ValueError, RecursionError):
            completion = "invalid_json"

    metrics_raw = row["metrics_json"]
    if metrics_raw is None:
        capture_quality: Mapping[str, object] = {}
    else:
        try:
            metrics = json.loads(metrics_raw)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("attempt_quality_processing_metrics_invalid") from exc
        if not isinstance(metrics, dict):
            raise ValueError("attempt_quality_processing_metrics_invalid")
        if not _quality_json_shape_within_bounds(metrics):
            raise ValueError("attempt_quality_processing_metrics_invalid")
        value = metrics.get("capture_quality", {})
        capture_quality = value if isinstance(value, dict) else {}

    return StoredAttemptCaptureEvidence(
        capture_complete=bool(row["capture_complete"]),
        capture_completion=completion,
        processing_quality=capture_quality,
    )


def load_attempt_draft_authoring_snapshot(
    database_path: str | Path,
    attempt_key: str,
    *,
    max_context_segments: int,
    max_context_bytes: int,
) -> tuple[StoredAttemptTrace | None, dict[str, object] | None]:
    """Load attempt policy and provenance metadata from one bounded DB snapshot."""
    with Database(Path(database_path), read_only=True) as db:
        db.connection.execute("BEGIN")
        attempt = load_attempt_policy_metadata(
            database_path,
            attempt_key,
            max_context_segments=max_context_segments,
            max_context_bytes=max_context_bytes,
            _connection=db.connection,
        )
        if attempt is None:
            return None, None
        metadata = load_attempt_engineer_summary_metadata(
            database_path, attempt_key, _connection=db.connection
        )
    return attempt, metadata


def load_attempt_trace(
    database_path: str | Path,
    attempt_key: str,
    *,
    columns: list[str] | None = None,
    max_trace_bytes: int | None = None,
    max_trace_rows: int | None = None,
    max_context_segments: int | None = None,
    max_context_bytes: int | None = None,
    max_attempt_metadata_bytes: int | None = None,
) -> StoredAttemptTrace | None:
    """Load a completed attempt after verifying its published Parquet trace."""
    database_path = Path(database_path)
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            """SELECT l.attempt_key, l.car_index, l.attempt_number,
                      l.disposition, l.lap_time_ms, l.start_observed, l.pit_encountered,
                      l.game_valid, l.reference_eligible,
                      CASE WHEN LENGTH(CAST(l.exclusion_reasons_json AS BLOB)) <= ?
                           THEN l.exclusion_reasons_json END AS exclusion_reasons_json,
                      LENGTH(CAST(l.exclusion_reasons_json AS BLOB)) AS exclusion_reasons_bytes,
                      l.superseded, l.lifecycle_assessed,
                      s.session_uid, s.run_id, t.relative_path, t.row_count,
                      t.sha256,
                      CASE WHEN LENGTH(CAST(t.quality_json AS BLOB)) <= ?
                           THEN t.quality_json END AS quality_json,
                      LENGTH(CAST(t.quality_json AS BLOB)) AS quality_bytes,
                      t.schema_version,
                      CASE WHEN LENGTH(CAST(e.evidence_json AS BLOB)) <= ?
                           THEN e.evidence_json END AS timing_evidence_json,
                      LENGTH(CAST(e.evidence_json AS BLOB)) AS timing_evidence_bytes
                 FROM lap_attempts l JOIN sessions s USING(session_key)
                 JOIN processing_runs r USING(run_id)
                 JOIN telemetry_files t USING(attempt_key)
                 LEFT JOIN attempt_timing_evidence e USING(attempt_key)
                WHERE l.attempt_key = ? AND t.ready = 1 AND r.status = 'complete'""",
            (
                max_attempt_metadata_bytes
                if max_attempt_metadata_bytes is not None
                else 9223372036854775807,
                max_attempt_metadata_bytes
                if max_attempt_metadata_bytes is not None
                else 9223372036854775807,
                max_attempt_metadata_bytes
                if max_attempt_metadata_bytes is not None
                else 9223372036854775807,
                attempt_key,
            ),
        ).fetchone()
        if row is None:
            return None
        if max_attempt_metadata_bytes is not None and any(
            row[key] is not None and int(row[key]) > max_attempt_metadata_bytes
            for key in (
                "exclusion_reasons_bytes",
                "quality_bytes",
                "timing_evidence_bytes",
            )
        ):
            raise AttemptTraceReadLimitError("metadata_bytes")
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
        player_participant_context = load_attempt_player_participant_context(
            db.connection, attempt_key
        )
        player_car_setup_context = load_attempt_player_car_setup_context(
            db.connection, attempt_key
        )

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

    def decode_context(raw: object) -> Mapping[str, object] | None:
        if raw is None:
            return None
        try:
            context = json.loads(raw)
        except (TypeError, ValueError, RecursionError):
            if max_context_bytes is not None:
                return None
            raise
        if max_context_bytes is not None:
            if (
                not isinstance(context, dict)
                or len(context) > 32
                or any(
                    not isinstance(key, str)
                    or len(key) > 128
                    or not _quality_context_scalar(value)
                    or (isinstance(value, str) and len(value) > 4_096)
                    for key, value in context.items()
                )
            ):
                return None
        return context

    contexts = tuple(
        (
            int(context_row["from_frame_identifier"]),
            decode_context(context_row["context_json"]),
        )
        for context_row in context_rows
    )
    try:
        raw_exclusion_reasons = json.loads(row["exclusion_reasons_json"])
        quality = json.loads(row["quality_json"])
        timing_evidence = (
            json.loads(row["timing_evidence_json"])
            if row["timing_evidence_json"]
            else {
                "status": "unavailable",
                "reasons": ["not_available_for_legacy_import"],
            }
        )
    except (TypeError, ValueError, RecursionError) as exc:
        if max_attempt_metadata_bytes is not None:
            raise AttemptTraceReadLimitError("metadata_json") from exc
        raise
    if max_attempt_metadata_bytes is not None and (
        not isinstance(raw_exclusion_reasons, list)
        or any(not isinstance(reason, str) for reason in raw_exclusion_reasons)
        or not isinstance(quality, dict)
        or not isinstance(timing_evidence, dict)
    ):
        raise AttemptTraceReadLimitError("metadata_shape")
    exclusion_reasons = tuple(raw_exclusion_reasons)
    if max_attempt_metadata_bytes is not None and any(
        not _quality_json_shape_within_bounds(value)
        for value in (exclusion_reasons, quality, timing_evidence)
    ):
        raise AttemptTraceReadLimitError("metadata_shape")
    return StoredAttemptTrace(
        attempt_key=row["attempt_key"],
        run_id=row["run_id"],
        session_uid=row["session_uid"],
        car_index=row["car_index"],
        disposition=row["disposition"],
        lap_time_ms=row["lap_time_ms"],
        game_valid=None if row["game_valid"] is None else bool(row["game_valid"]),
        reference_eligible=bool(row["reference_eligible"]),
        exclusion_reasons=exclusion_reasons,
        trace_sha256=row["sha256"],
        trace_schema_version=row["schema_version"],
        quality=quality,
        context_segments=contexts,
        samples=tuple(table.to_pylist()),
        attempt_number=row["attempt_number"],
        start_observed=bool(row["start_observed"]),
        pit_encountered=bool(row["pit_encountered"]),
        superseded=None if row["superseded"] is None else bool(row["superseded"]),
        lifecycle_assessed=bool(row["lifecycle_assessed"]),
        timing_evidence=timing_evidence,
        source_sample_count=int(row["row_count"]),
        player_participant_context=player_participant_context,
        player_car_setup_context=player_car_setup_context,
    )


def load_attempt_trace_resource_estimates(
    database_path: str | Path,
    attempt_keys: tuple[str, ...] | list[str],
    *,
    max_context_segments: int,
    max_context_bytes: int,
) -> tuple[AttemptTraceResourceEstimate, ...]:
    """Read selected trace/context sizes before a bounded multi-trace analysis."""
    if not attempt_keys or len(set(attempt_keys)) != len(attempt_keys):
        raise ValueError("attempt_keys_must_be_nonempty_and_unique")
    for name, limit in (
        ("max_context_segments", max_context_segments),
        ("max_context_bytes", max_context_bytes),
    ):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError(f"{name} must be a non-negative integer")

    placeholders = ",".join("?" for _ in attempt_keys)
    with Database(database_path, read_only=True) as db:
        rows = db.connection.execute(
            f"""SELECT l.attempt_key, l.attempt_number, l.car_index, l.disposition,
                      l.lap_time_ms, l.game_valid, l.start_observed, l.pit_encountered,
                      l.superseded, l.lifecycle_assessed, l.exclusion_reasons_json,
                      s.run_id, s.session_uid, t.ready, t.row_count, t.sha256,
                      t.schema_version, t.relative_path,
                      (SELECT COUNT(*) FROM lap_context_segments c
                        WHERE c.attempt_key = l.attempt_key) AS context_segment_count,
                      (SELECT COALESCE(SUM(LENGTH(CAST(c.context_json AS BLOB))), 0)
                         FROM lap_context_segments c
                        WHERE c.attempt_key = l.attempt_key) AS context_bytes
                 FROM lap_attempts l JOIN sessions s USING(session_key)
                 JOIN processing_runs r USING(run_id)
                 LEFT JOIN telemetry_files t USING(attempt_key)
                WHERE l.attempt_key IN ({placeholders}) AND r.status = 'complete'""",
            tuple(attempt_keys),
        ).fetchall()
        row_by_key = {str(row["attempt_key"]): row for row in rows}
        present_keys = [key for key in attempt_keys if key in row_by_key]
        if not present_keys:
            return ()
        aggregate_segments = sum(
            int(row_by_key[key]["context_segment_count"]) for key in present_keys
        )
        aggregate_context_bytes = sum(
            int(row_by_key[key]["context_bytes"]) for key in present_keys
        )
        if aggregate_segments > max_context_segments:
            raise AttemptTraceReadLimitError("context_segments")
        if aggregate_context_bytes > max_context_bytes:
            raise AttemptTraceReadLimitError("context_bytes")
        present_placeholders = ",".join("?" for _ in present_keys)
        context_rows = db.connection.execute(
            f"""SELECT attempt_key, from_frame_identifier, context_json
                   FROM lap_context_segments
                  WHERE attempt_key IN ({present_placeholders})
                  ORDER BY attempt_key, ordinal""",
            tuple(present_keys),
        ).fetchall()

    contexts_by_attempt: dict[
        str, list[tuple[int, Mapping[str, object] | None]]
    ] = {}
    for context_row in context_rows:
        value = (
            json.loads(context_row["context_json"])
            if context_row["context_json"] is not None
            else None
        )
        context = value if isinstance(value, Mapping) else None
        contexts_by_attempt.setdefault(str(context_row["attempt_key"]), []).append(
            (int(context_row["from_frame_identifier"]), context)
        )

    database_path = Path(database_path)
    estimates: dict[str, AttemptTraceResourceEstimate] = {}
    for key in present_keys:
        row = row_by_key[key]
        estimates[key] = AttemptTraceResourceEstimate(
            attempt_key=key,
            run_id=str(row["run_id"]),
            session_uid=str(row["session_uid"]),
            car_index=int(row["car_index"]),
            attempt_number=int(row["attempt_number"]),
            disposition=str(row["disposition"]),
            lap_time_ms=(
                int(row["lap_time_ms"]) if row["lap_time_ms"] is not None else None
            ),
            game_valid=(
                None if row["game_valid"] is None else bool(row["game_valid"])
            ),
            start_observed=bool(row["start_observed"]),
            pit_encountered=bool(row["pit_encountered"]),
            superseded=(
                None if row["superseded"] is None else bool(row["superseded"])
            ),
            lifecycle_assessed=bool(row["lifecycle_assessed"]),
            exclusion_reasons=tuple(json.loads(row["exclusion_reasons_json"])),
            trace_ready=bool(row["ready"]),
            trace_row_count=(
                int(row["row_count"]) if row["row_count"] is not None else None
            ),
            trace_sha256=(str(row["sha256"]) if row["sha256"] is not None else None),
            trace_schema_version=(
                int(row["schema_version"])
                if row["schema_version"] is not None
                else None
            ),
            trace_size_bytes=(
                _trace_file_size(database_path, row["relative_path"])
                if bool(row["ready"])
                else None
            ),
            context_segment_count=int(row["context_segment_count"]),
            context_bytes=int(row["context_bytes"]),
            context_segments=tuple(contexts_by_attempt.get(key, ())),
        )
    return tuple(estimates[key] for key in attempt_keys if key in estimates)


def load_attempt_policy_metadata(
    database_path: str | Path,
    attempt_key: str,
    *,
    max_context_segments: int | None = None,
    max_context_bytes: int | None = None,
    _connection: sqlite3.Connection | None = None,
) -> StoredAttemptTrace | None:
    """Load bounded SQLite evidence needed to validate a comparison policy.

    This deliberately does not read or decode the attempt's Parquet trace. Any
    subsequent telemetry preview must load and verify that trace independently.
    """
    with _query_connection(database_path, _connection) as connection:
        row = connection.execute(
            """SELECT l.attempt_key, l.car_index, l.attempt_number,
                      l.disposition, l.lap_time_ms, l.start_observed, l.pit_encountered,
                      l.game_valid, l.reference_eligible, l.exclusion_reasons_json,
                      l.superseded, l.lifecycle_assessed,
                      s.session_uid, s.run_id, t.row_count, t.sha256,
                      t.quality_json, t.schema_version,
                      e.evidence_json AS timing_evidence_json
                 FROM lap_attempts l JOIN sessions s USING(session_key)
                 JOIN processing_runs r USING(run_id)
                 JOIN telemetry_files t USING(attempt_key)
                 LEFT JOIN attempt_timing_evidence e USING(attempt_key)
                WHERE l.attempt_key = ? AND t.ready = 1 AND r.status = 'complete'""",
            (attempt_key,),
        ).fetchone()
        if row is None:
            return None
        if max_context_segments is not None or max_context_bytes is not None:
            context_size = connection.execute(
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
        context_rows = connection.execute(
            """SELECT from_frame_identifier, context_json
                 FROM lap_context_segments WHERE attempt_key = ? ORDER BY ordinal""",
            (attempt_key,),
        ).fetchall()

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
        samples=(),
        attempt_number=row["attempt_number"],
        start_observed=bool(row["start_observed"]),
        pit_encountered=bool(row["pit_encountered"]),
        superseded=None if row["superseded"] is None else bool(row["superseded"]),
        lifecycle_assessed=bool(row["lifecycle_assessed"]),
        timing_evidence=(
            json.loads(row["timing_evidence_json"])
            if row["timing_evidence_json"]
            else {
                "status": "unavailable",
                "reasons": ["not_available_for_legacy_import"],
            }
        ),
        source_sample_count=int(row["row_count"]),
    )


def load_attempt_timing_evidence(
    database_path: str | Path, attempt_key: str
) -> dict[str, object] | None:
    """Read reported Session History timing without opening a Parquet trace."""
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            """SELECT e.evidence_json, l.attempt_json, l.attempt_key,
                      s.run_id, r.capture_sha256
                 FROM lap_attempts l
                 JOIN sessions s USING(session_key)
                 JOIN processing_runs r USING(run_id)
                 LEFT JOIN attempt_timing_evidence e USING(attempt_key)
                WHERE l.attempt_key = ? AND r.status = 'complete'""",
            (attempt_key,),
        ).fetchone()
        if row is None:
            return None
        if row["evidence_json"]:
            value = json.loads(row["evidence_json"])
            if not isinstance(value, dict):
                return None
        else:
            value = {
                "status": "unavailable",
                "reasons": ["not_available_for_legacy_import"],
            }

        attempt = json.loads(row["attempt_json"])
        provenance = value.get("provenance")
        provenance = dict(provenance) if isinstance(provenance, dict) else {}
        provenance.setdefault("attempt_key", row["attempt_key"])
        provenance.setdefault("run_id", row["run_id"])
        provenance.setdefault("capture_sha256", row["capture_sha256"])
        provenance.setdefault(
            "completion_frame_ordinal",
            attempt.get("completion_frame_ordinal")
            if isinstance(attempt, dict)
            else None,
        )
        value["provenance"] = provenance
        return value


def load_attempt_engineer_summary_metadata(
    database_path: str | Path,
    attempt_key: str,
    *,
    _connection: sqlite3.Connection | None = None,
) -> dict[str, object] | None:
    """Load bounded SQLite-only evidence for a deterministic attempt summary.

    This function never resolves a stored trace path or opens Parquet. Large JSON
    columns are omitted before SQLite returns them and are marked as truncated.
    """
    limits = ENGINEER_ATTEMPT_METADATA_LIMITS
    with _query_connection(database_path, _connection) as connection:
        row = connection.execute(
            """SELECT l.attempt_key, l.attempt_number, l.lap_number,
                      l.disposition, l.lap_time_ms, l.game_valid,
                      l.start_observed, l.pit_encountered, l.superseded,
                      l.lifecycle_assessed, l.reference_eligible,
                      CASE WHEN LENGTH(CAST(l.exclusion_reasons_json AS BLOB)) <= ?
                           THEN l.exclusion_reasons_json END AS exclusion_reasons_json,
                      LENGTH(CAST(l.exclusion_reasons_json AS BLOB)) AS exclusion_reasons_bytes,
                      s.run_id, s.session_uid, l.car_index, s.packet_format,
                      r.status AS processing_status, r.pipeline_version,
                      r.capture_sha256, c.byte_size AS capture_byte_size, c.complete AS capture_complete,
                      CASE WHEN LENGTH(CAST(c.completion_json AS BLOB)) <= ?
                           THEN c.completion_json END AS completion_json,
                      LENGTH(CAST(c.completion_json AS BLOB)) AS completion_bytes,
                      CASE WHEN LENGTH(CAST(r.metrics_json AS BLOB)) <= ?
                           THEN r.metrics_json END AS metrics_json,
                      LENGTH(CAST(r.metrics_json AS BLOB)) AS processing_metrics_bytes,
                      t.ready AS trace_ready, t.row_count AS trace_row_count,
                      t.sha256 AS trace_sha256, t.schema_version AS trace_schema_version,
                      e.status AS timing_status,
                      CASE WHEN LENGTH(CAST(e.evidence_json AS BLOB)) <= ?
                           THEN e.evidence_json END AS timing_evidence_json,
                      LENGTH(CAST(e.evidence_json AS BLOB)) AS timing_evidence_bytes,
                      (SELECT c.context_json FROM lap_context_segments c
                        WHERE c.attempt_key = l.attempt_key AND c.ordinal = 0
                          AND LENGTH(CAST(c.context_json AS BLOB)) <= ?)
                        AS initial_context_json,
                      (SELECT LENGTH(CAST(c.context_json AS BLOB))
                         FROM lap_context_segments c
                        WHERE c.attempt_key = l.attempt_key AND c.ordinal = 0)
                        AS initial_context_bytes,
                      CASE WHEN LENGTH(CAST(s.context_json AS BLOB)) <= ?
                           THEN s.context_json END AS latest_context_json,
                      LENGTH(CAST(s.context_json AS BLOB)) AS latest_context_bytes,
                      (SELECT COUNT(*) FROM lap_context_segments c
                        WHERE c.attempt_key = l.attempt_key) AS context_segment_count
                 FROM lap_attempts l
                 JOIN sessions s USING(session_key)
                 JOIN processing_runs r USING(run_id)
                 LEFT JOIN captures c USING(capture_sha256)
                 LEFT JOIN telemetry_files t USING(attempt_key)
                 LEFT JOIN attempt_timing_evidence e USING(attempt_key)
                WHERE l.attempt_key = ?""",
            (
                limits["attempt_reasons_bytes"],
                limits["capture_completion_bytes"],
                limits["processing_metrics_bytes"],
                limits["timing_evidence_bytes"],
                limits["context_bytes"],
                limits["context_bytes"],
                attempt_key,
            ),
        ).fetchone()
        player_participant_context = (
            load_attempt_player_participant_context(
                connection, attempt_key
            )
            if row is not None
            else None
        )
        player_car_setup_context = (
            load_attempt_player_car_setup_context(connection, attempt_key)
            if row is not None
            else None
        )
    if row is None:
        return None

    def json_value(raw: object, expected: type) -> object | None:
        if not isinstance(raw, str):
            return None
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, expected) else None

    reasons = json_value(row["exclusion_reasons_json"], list)
    context = json_value(row["initial_context_json"], dict)
    latest_context = json_value(row["latest_context_json"], dict)
    completion = json_value(row["completion_json"], dict)
    metrics = json_value(row["metrics_json"], dict)
    timing = json_value(row["timing_evidence_json"], dict)
    if timing is None:
        timing = {
            "status": row["timing_status"] or "unavailable",
            "reasons": [
                "timing_evidence_exceeds_metadata_limit"
                if row["timing_evidence_bytes"] is not None
                and int(row["timing_evidence_bytes"]) > limits["timing_evidence_bytes"]
                else "not_available_for_legacy_import"
            ],
        }

    capture_complete = row["capture_complete"]
    return {
        "attempt": {
            "attempt_key": row["attempt_key"],
            "attempt_number": row["attempt_number"],
            "lap_number": row["lap_number"],
            "disposition": row["disposition"],
            "lap_time_ms": row["lap_time_ms"],
            "game_valid": None if row["game_valid"] is None else bool(row["game_valid"]),
            "start_observed": bool(row["start_observed"]),
            "pit_encountered": bool(row["pit_encountered"]),
            "superseded": None if row["superseded"] is None else bool(row["superseded"]),
            "lifecycle_assessed": bool(row["lifecycle_assessed"]),
            "reference_eligible": bool(row["reference_eligible"]),
            "exclusion_reasons": reasons if reasons is not None else [],
        },
        "scope": {
            "run_id": row["run_id"],
            "session_uid": row["session_uid"],
            "car_index": row["car_index"],
            "packet_format": row["packet_format"],
        },
        "context": context,
        "latest_context": latest_context,
        "context_segment_count": int(row["context_segment_count"] or 0),
        "processing": {
            "status": row["processing_status"],
            "pipeline_version": row["pipeline_version"],
            "metrics": metrics,
        },
        "capture": {
            "sha256": row["capture_sha256"],
            "byte_size": row["capture_byte_size"],
            "complete": None if capture_complete is None else bool(capture_complete),
            "completion": completion,
        },
        "trace_metadata": {
            "ready": None if row["trace_ready"] is None else bool(row["trace_ready"]),
            "row_count": row["trace_row_count"],
            "sha256": row["trace_sha256"],
            "schema_version": row["trace_schema_version"],
            "checksum_verified": False,
        },
        "timing_evidence": timing,
        "player_participant_context": player_participant_context,
        "player_car_setup_context": player_car_setup_context,
        "metadata_limits": {
            "attempt_reasons_truncated": row["exclusion_reasons_bytes"] is not None
            and int(row["exclusion_reasons_bytes"]) > limits["attempt_reasons_bytes"],
            "context_truncated": row["initial_context_bytes"] is not None
            and int(row["initial_context_bytes"]) > limits["context_bytes"],
            "latest_context_truncated": row["latest_context_bytes"] is not None
            and int(row["latest_context_bytes"]) > limits["context_bytes"],
            "timing_evidence_truncated": row["timing_evidence_bytes"] is not None
            and int(row["timing_evidence_bytes"]) > limits["timing_evidence_bytes"],
            "capture_completion_truncated": row["completion_bytes"] is not None
            and int(row["completion_bytes"]) > limits["capture_completion_bytes"],
            "processing_metrics_truncated": row["processing_metrics_bytes"] is not None
            and int(row["processing_metrics_bytes"]) > limits["processing_metrics_bytes"],
            "player_participant_context_truncated": bool(
                player_participant_context
                and player_participant_context.get("status") == "incomplete"
                and "participant_observation_history_truncated"
                in player_participant_context.get("reasons", [])
            ),
            "player_car_setup_context_truncated": bool(
                player_car_setup_context
                and player_car_setup_context.get("status") == "incomplete"
                and any(
                    reason in player_car_setup_context.get("reasons", [])
                    for reason in (
                        "setup_observation_history_truncated",
                        "run_setup_observation_history_truncated",
                    )
                )
            ),
        },
    }


def load_reference_inventory(
    database_path: str | Path,
    attempt_key: str,
    *,
    max_prior_attempts: int | None = None,
    include_after_target: bool = False,
    max_scope_attempts: int | None = None,
    max_context_segments: int | None = None,
    max_context_bytes: int | None = None,
) -> StoredReferenceInventory | None:
    """Read run-scoped attempt/context metadata for deterministic lap assessment."""
    if not isinstance(include_after_target, bool):
        raise ValueError("include_after_target must be a boolean")
    for name, limit in (
        ("max_prior_attempts", max_prior_attempts),
        ("max_scope_attempts", max_scope_attempts),
        ("max_context_segments", max_context_segments),
        ("max_context_bytes", max_context_bytes),
    ):
        if limit is not None and (
            not isinstance(limit, int) or isinstance(limit, bool) or limit < 0
        ):
            raise ValueError(f"{name} must be a non-negative integer")
    if max_scope_attempts is not None and max_scope_attempts < 1:
        raise ValueError("max_scope_attempts must be positive")
    if include_after_target and max_prior_attempts is not None:
        raise ValueError("max_prior_attempts cannot be combined with include_after_target")
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
        attempt_scope = "s.run_id = ? AND s.session_uid = ? AND l.car_index = ?"
        scope_values = (target["run_id"], target["session_uid"], target["car_index"])
        attempt_filter = (
            " AND l.attempt_number <= ?"
            if max_prior_attempts is not None and not include_after_target
            else ""
        )
        attempt_parameters = (
            (*scope_values, target["attempt_number"])
            if attempt_filter
            else scope_values
        )
        if max_scope_attempts is not None:
            scoped_count = db.connection.execute(
                f"""SELECT COUNT(*) AS attempt_count
                      FROM lap_attempts l JOIN sessions s USING(session_key)
                      JOIN processing_runs r USING(run_id)
                     WHERE {attempt_scope} AND r.status = 'complete'""",
                scope_values,
            ).fetchone()["attempt_count"]
            if int(scoped_count) > max_scope_attempts:
                raise AttemptInventoryReadLimitError("scope_attempts")
        if max_prior_attempts is not None and not include_after_target:
            prior_count = db.connection.execute(
                f"""SELECT COUNT(*) AS attempt_count
                      FROM lap_attempts l JOIN sessions s USING(session_key)
                      JOIN processing_runs r USING(run_id)
                     WHERE {attempt_scope} AND r.status = 'complete'
                       AND l.attempt_number < ?""",
                (*scope_values, target["attempt_number"]),
            ).fetchone()["attempt_count"]
            if int(prior_count) > max_prior_attempts:
                raise AttemptInventoryReadLimitError("prior_attempts")
            bounded_attempt_count = db.connection.execute(
                f"""SELECT COUNT(*) AS attempt_count
                      FROM lap_attempts l JOIN sessions s USING(session_key)
                      JOIN processing_runs r USING(run_id)
                     WHERE {attempt_scope} AND r.status = 'complete'
                       AND l.attempt_number <= ?""",
                (*scope_values, target["attempt_number"]),
            ).fetchone()["attempt_count"]
            if int(bounded_attempt_count) > max_prior_attempts + 1:
                raise AttemptInventoryReadLimitError("prior_attempts")
        if max_context_segments is not None or max_context_bytes is not None:
            context_row_limit = (
                max_context_segments + 1
                if max_context_segments is not None
                else max_context_bytes + 1
                if max_context_bytes is not None
                else 1
            )
            context_sizes = db.connection.execute(
                f"""SELECT LENGTH(CAST(c.context_json AS BLOB)) AS context_bytes
                     FROM lap_context_segments c JOIN lap_attempts l USING(attempt_key)
                     JOIN sessions s USING(session_key)
                     JOIN processing_runs r USING(run_id)
                    WHERE {attempt_scope} {attempt_filter} AND r.status = 'complete'
                    ORDER BY c.attempt_key, c.ordinal LIMIT ?""",
                (*attempt_parameters, context_row_limit),
            )
            segment_count = 0
            context_bytes = 0
            for context_row in context_sizes:
                segment_count += 1
                context_bytes += int(context_row["context_bytes"] or 0)
                if (
                    max_context_segments is not None
                    and segment_count > max_context_segments
                ):
                    raise AttemptInventoryReadLimitError("context_segments")
                if (
                    max_context_bytes is not None
                    and context_bytes > max_context_bytes
                ):
                    raise AttemptInventoryReadLimitError("context_bytes")
        rows = db.connection.execute(
            f"""SELECT l.attempt_key, l.attempt_number, l.car_index, l.disposition,
                      l.lap_time_ms, l.game_valid, l.reference_eligible,
                      l.start_observed, l.pit_encountered, l.sample_count,
                      l.exclusion_reasons_json, l.superseded, l.lifecycle_assessed,
                      s.run_id, s.session_uid, t.ready, t.row_count, t.sha256, t.schema_version,
                      t.quality_json, t.relative_path
                 FROM lap_attempts l JOIN sessions s USING(session_key)
                 JOIN processing_runs r USING(run_id)
                 LEFT JOIN telemetry_files t USING(attempt_key)
                WHERE s.run_id = ? AND s.session_uid = ? AND l.car_index = ?
                  {attempt_filter}
                  AND r.status = 'complete'
                ORDER BY l.attempt_number""",
            attempt_parameters,
        ).fetchall()
        capture = db.connection.execute(
            """SELECT c.complete, c.completion_json, r.metrics_json
                 FROM processing_runs r JOIN captures c USING(capture_sha256)
                WHERE r.run_id = ?""",
            (target["run_id"],),
        ).fetchone()
        context_rows = db.connection.execute(
            f"""SELECT c.attempt_key, c.from_frame_identifier, c.context_json
                 FROM lap_context_segments c JOIN lap_attempts l USING(attempt_key)
                 JOIN sessions s USING(session_key)
                WHERE s.run_id = ? AND s.session_uid = ? AND l.car_index = ?
                  {attempt_filter}
                ORDER BY c.attempt_key, c.ordinal""",
            attempt_parameters,
        ).fetchall()

    contexts_by_attempt: dict[str, list[tuple[int, Mapping[str, object] | None]]] = {}
    context_segment_counts: dict[str, int] = {}
    context_bytes_by_attempt: dict[str, int] = {}
    for context_row in context_rows:
        attempt_key = str(context_row["attempt_key"])
        raw_context = (
            json.loads(context_row["context_json"])
            if context_row["context_json"] is not None
            else None
        )
        contexts_by_attempt.setdefault(attempt_key, []).append(
            (int(context_row["from_frame_identifier"]), raw_context)
        )
        context_segment_counts[attempt_key] = context_segment_counts.get(attempt_key, 0) + 1
        context_bytes_by_attempt[attempt_key] = context_bytes_by_attempt.get(
            attempt_key, 0
        ) + (
            len(str(context_row["context_json"]).encode("utf-8"))
            if context_row["context_json"] is not None
            else 0
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
            superseded=None if row["superseded"] is None else bool(row["superseded"]),
            lifecycle_assessed=bool(row["lifecycle_assessed"]),
            trace_size_bytes=_trace_file_size(database_path, row["relative_path"]),
            context_segment_count=context_segment_counts.get(row["attempt_key"], 0),
            context_bytes=context_bytes_by_attempt.get(row["attempt_key"], 0),
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


def _trace_file_size(database_path: Path, relative_path_value: object) -> int | None:
    if not isinstance(relative_path_value, str):
        return None
    relative_path = Path(relative_path_value)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    root = database_path.parent.resolve()
    trace_path = (database_path.parent / relative_path).resolve()
    if not trace_path.is_relative_to(root) or not trace_path.is_file():
        return None
    return trace_path.stat().st_size


def _car_observation_archive_status(metrics_json: str | None) -> str:
    metrics = json.loads(metrics_json) if metrics_json else {}
    quality = metrics.get("capture_quality", {}) if isinstance(metrics, dict) else {}
    observation_count = (
        quality.get("car_observation_count") if isinstance(quality, dict) else None
    )
    if not isinstance(quality, dict) or "car_observation_count" not in quality:
        return "not_archived"
    if type(observation_count) is not int or observation_count < 0:
        return "unavailable"
    return "empty" if int(observation_count) == 0 else "available"


def list_car_observation_inventory(
    database_path: str | Path,
    run_id: str,
    session_uid: str | int,
    *,
    limit: int = 24,
    offset: int = 0,
) -> dict[str, object] | None:
    """Return observed slot coverage; counts do not assert opponent eligibility."""
    if not 1 <= limit <= 100 or not 0 <= offset <= 100_000:
        raise ValueError("car observation inventory page is out of range")
    database_path = Path(database_path)
    with Database(database_path, read_only=True) as db:
        session = db.connection.execute(
            """SELECT s.session_key, s.session_uid, r.status,
                      CASE WHEN length(CAST(r.metrics_json AS BLOB)) <= ?
                           THEN r.metrics_json ELSE NULL END AS metrics_json,
                      length(CAST(r.metrics_json AS BLOB)) AS metrics_json_bytes,
                      c.complete,
                      CASE WHEN length(CAST(c.completion_json AS BLOB)) <= ?
                           THEN c.completion_json ELSE NULL END AS completion_json,
                      length(CAST(c.completion_json AS BLOB)) AS completion_json_bytes
                 FROM sessions s JOIN processing_runs r USING(run_id)
                 JOIN captures c USING(capture_sha256)
                WHERE s.run_id=? AND s.session_uid=?""",
            (
                MAX_OBSERVATION_SESSION_METRICS_BYTES,
                MAX_OBSERVATION_CAPTURE_COMPLETION_BYTES,
                run_id,
                str(session_uid),
            ),
        ).fetchone()
        if session is None or session["status"] != "complete":
            return None
        if (
            session["metrics_json_bytes"] is not None
            and session["metrics_json_bytes"] > MAX_OBSERVATION_SESSION_METRICS_BYTES
        ):
            raise ValueError("observation_session_metrics_limit_exceeded")
        if (
            session["completion_json_bytes"] is not None
            and session["completion_json_bytes"] > MAX_OBSERVATION_CAPTURE_COMPLETION_BYTES
        ):
            raise ValueError("observation_capture_completion_limit_exceeded")
        total = int(
            db.connection.execute(
                "SELECT COUNT(DISTINCT car_index) FROM car_observation_slots WHERE session_key=?",
                (session["session_key"],),
            ).fetchone()[0]
        )
        rows = db.connection.execute(
            """SELECT car_index, SUM(observation_count) AS observation_count,
                      SUM(car_telemetry_count) AS car_telemetry_count,
                      SUM(motion_count) AS motion_count,
                      SUM(nonzero_speed_count) AS nonzero_speed_count,
                      SUM(header_player_count) AS header_player_count,
                      MIN(first_frame_ordinal) AS first_frame_ordinal,
                      MAX(last_frame_ordinal) AS last_frame_ordinal
                 FROM car_observation_slots WHERE session_key=?
                GROUP BY car_index ORDER BY car_index LIMIT ? OFFSET ?""",
            (session["session_key"], limit, offset),
        ).fetchall()
        participant_counts = {
            int(row["car_index"]): int(row["snapshot_count"])
            for row in db.connection.execute(
                """SELECT car_index, COUNT(*) AS snapshot_count FROM driver_snapshots
                     WHERE session_key=? GROUP BY car_index""",
                (session["session_key"],),
            ).fetchall()
        }
        metrics = json.loads(session["metrics_json"]) if session["metrics_json"] else {}
        quality = metrics.get("capture_quality", {}) if isinstance(metrics, dict) else {}
        archive_status = _car_observation_archive_status(session["metrics_json"])
        def assessed_counter(name: str) -> int | None:
            if not isinstance(quality, dict):
                return None
            value = quality.get(name)
            return value if type(value) is int and value >= 0 else None

        completion = (
            json.loads(session["completion_json"])
            if session["completion_json"]
            else {}
        )
    return {
        "run_id": run_id,
        "session_uid": str(session["session_uid"]),
        "status": "available",
        "archive_status": archive_status,
        "verification_scope": "session_car_observations",
        "opponent_eligibility": "not_assessed",
        "capture": {
            "complete": bool(session["complete"]),
            "footer_status": completion.get("status"),
        },
        "replay_quality": {
            "late_packets_ignored": assessed_counter("import_late_packets_ignored"),
            "frame_overflow_packets_dropped": assessed_counter(
                "import_frame_overflow_packets_dropped"
            ),
            "conflicting_observation_frames": assessed_counter(
                "car_observation_conflict_count"
            ),
        },
        "slots": {
            "limit": limit,
            "offset": offset,
            "total": total,
            "items": [
                {
                    "car_index": int(row["car_index"]),
                    "observation_count": int(row["observation_count"]),
                    "car_telemetry_count": int(row["car_telemetry_count"]),
                    "motion_count": int(row["motion_count"]),
                    "nonzero_speed_count": int(row["nonzero_speed_count"]),
                    "header_player_count": int(row["header_player_count"]),
                    "participant_snapshot_count": participant_counts.get(
                        int(row["car_index"]), 0
                    ),
                    "first_frame_ordinal": int(row["first_frame_ordinal"]),
                    "last_frame_ordinal": int(row["last_frame_ordinal"]),
                    "activity_evidence": (
                        "nonzero_speed_observed"
                        if int(row["nonzero_speed_count"]) > 0
                        else "no_nonzero_speed_observed"
                    ),
                }
                for row in rows
            ],
        },
    }


def load_car_observation_preview(
    database_path: str | Path,
    run_id: str,
    session_uid: str | int,
    car_index: int,
    *,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, object] | None:
    """Read a bounded per-slot preview from checksummed observation chunks."""
    if not 0 <= car_index <= 23:
        raise ValueError("car index is out of range")
    if not 1 <= limit <= 500 or not 0 <= offset <= 100_000:
        raise ValueError("car observation preview page is out of range")
    database_path = Path(database_path)
    with Database(database_path, read_only=True) as db:
        session = db.connection.execute(
            """SELECT s.session_key, s.session_uid, r.status,
                      CASE WHEN length(CAST(r.metrics_json AS BLOB)) <= ?
                           THEN r.metrics_json ELSE NULL END AS metrics_json,
                      length(CAST(r.metrics_json AS BLOB)) AS metrics_json_bytes,
                      c.complete,
                      CASE WHEN length(CAST(c.completion_json AS BLOB)) <= ?
                           THEN c.completion_json ELSE NULL END AS completion_json,
                      length(CAST(c.completion_json AS BLOB)) AS completion_json_bytes
                 FROM sessions s JOIN processing_runs r USING(run_id)
                 JOIN captures c USING(capture_sha256)
                WHERE s.run_id=? AND s.session_uid=?""",
            (
                MAX_OBSERVATION_SESSION_METRICS_BYTES,
                MAX_OBSERVATION_CAPTURE_COMPLETION_BYTES,
                run_id,
                str(session_uid),
            ),
        ).fetchone()
        if session is None or session["status"] != "complete":
            return None
        if (
            session["metrics_json_bytes"] is not None
            and session["metrics_json_bytes"] > MAX_OBSERVATION_SESSION_METRICS_BYTES
        ):
            raise ValueError("observation_session_metrics_limit_exceeded")
        if (
            session["completion_json_bytes"] is not None
            and session["completion_json_bytes"] > MAX_OBSERVATION_CAPTURE_COMPLETION_BYTES
        ):
            raise ValueError("observation_capture_completion_limit_exceeded")
        raw_chunks = db.connection.execute(
            """WITH candidate_chunks AS (
                     SELECT packet_format,lifecycle_epoch,chunk_ordinal,
                            CASE WHEN length(CAST(relative_path AS BLOB)) <= ?
                                 THEN relative_path ELSE NULL END AS relative_path,
                            length(CAST(relative_path AS BLOB)) AS relative_path_bytes,
                            schema_version,row_count,
                            CASE WHEN length(CAST(sha256 AS BLOB)) <= ?
                                 THEN sha256 ELSE NULL END AS sha256,
                            length(CAST(sha256 AS BLOB)) AS sha256_bytes,
                            quality_json,
                            length(CAST(quality_json AS BLOB)) AS manifest_bytes
                       FROM car_observation_chunks
                      WHERE session_key=? AND ready=1
                      ORDER BY lifecycle_epoch,chunk_ordinal,packet_format LIMIT ?
                 ), sized_chunks AS (
                     SELECT *, SUM(manifest_bytes) OVER() AS total_manifest_bytes
                       FROM candidate_chunks
                 )
                 SELECT packet_format,lifecycle_epoch,chunk_ordinal,relative_path,
                        relative_path_bytes,schema_version,row_count,sha256,sha256_bytes,manifest_bytes,
                        total_manifest_bytes,
                        CASE WHEN manifest_bytes <= ? AND total_manifest_bytes <= ?
                             THEN quality_json ELSE NULL END AS quality_json
                   FROM sized_chunks
                  ORDER BY lifecycle_epoch,chunk_ordinal,packet_format""",
            (
                MAX_OBSERVATION_PREVIEW_PATH_BYTES,
                MAX_OBSERVATION_PREVIEW_SHA256_BYTES,
                session["session_key"],
                MAX_OBSERVATION_PREVIEW_CHUNKS + 1,
                MAX_OBSERVATION_PREVIEW_MANIFEST_BYTES,
                MAX_OBSERVATION_PREVIEW_MANIFEST_TOTAL_BYTES,
            ),
        ).fetchall()
        if len(raw_chunks) > MAX_OBSERVATION_PREVIEW_CHUNKS:
            raise ValueError("observation_preview_chunk_limit_exceeded")
        if raw_chunks and raw_chunks[0]["total_manifest_bytes"] > MAX_OBSERVATION_PREVIEW_MANIFEST_TOTAL_BYTES:
            raise ValueError("observation_preview_manifest_total_limit_exceeded")
        chunks = []
        total = 0
        for chunk in raw_chunks:
            if (
                chunk["relative_path"] is None
                or chunk["relative_path_bytes"] > MAX_OBSERVATION_PREVIEW_PATH_BYTES
                or chunk["sha256"] is None
                or chunk["sha256_bytes"] != MAX_OBSERVATION_PREVIEW_SHA256_BYTES
            ):
                raise ValueError("observation_preview_manifest_invalid")
            raw_quality = chunk["quality_json"]
            if raw_quality is None or chunk["manifest_bytes"] > MAX_OBSERVATION_PREVIEW_MANIFEST_BYTES:
                raise ValueError("observation_preview_manifest_limit_exceeded")
            if len(raw_quality.encode("utf-8")) > MAX_OBSERVATION_PREVIEW_MANIFEST_BYTES:
                raise ValueError("observation_preview_manifest_limit_exceeded")
            quality = json.loads(raw_quality)
            if not isinstance(quality, dict) or not isinstance(quality.get("slots"), dict):
                raise ValueError("observation_preview_manifest_invalid")
            slot = quality.get("slots", {}).get(str(car_index), {})
            raw_count = slot.get("observation_count", 0)
            if type(raw_count) is not int or raw_count < 0:
                raise ValueError("observation_preview_manifest_invalid")
            row_count = int(chunk["row_count"])
            if not 0 <= row_count <= MAX_OBSERVATION_CHUNK_ROWS or raw_count > row_count:
                raise ValueError("observation_preview_manifest_invalid")
            if int(chunk["schema_version"]) != OBSERVATION_SCHEMA_VERSION:
                raise ValueError("observation_preview_schema_mismatch")
            count = raw_count
            if count:
                chunks.append((chunk, quality, count))
                total += count
        chunks.sort(
            key=lambda item: (
                int(item[1].get("first_frame_ordinal", 0) or 0),
                int(item[0]["lifecycle_epoch"]),
                int(item[0]["packet_format"]),
                int(item[0]["chunk_ordinal"]),
            )
        )
        completion = (
            json.loads(session["completion_json"])
            if session["completion_json"]
            else {}
        )

    root = database_path.parent.resolve()
    remaining_skip = offset
    selected_rows: list[dict[str, object]] = []
    selected_chunks: set[str] = set()
    source_bytes_read = 0
    row_groups_read = 0
    rows_read = 0
    columns = (
        "frame_identifier",
        "frame_ordinal",
        "session_time_s",
        "car_index",
        "header_player_car_index",
        "packet_format",
        "lifecycle_epoch",
        "lap_number",
        "lap_distance_m",
        "total_distance_m",
        "current_lap_time_ms",
        "speed_mps",
        "throttle",
        "brake",
        "steering",
        "gear",
        "engine_rpm",
        "drs_active",
        "car_telemetry_available",
        "car_telemetry_unavailable_reason",
        "motion_available",
        "motion_unavailable_reason",
        "world_position_x_m",
        "world_position_y_m",
        "world_position_z_m",
        "world_velocity_x_mps",
        "world_velocity_y_mps",
        "world_velocity_z_mps",
        "g_force_lateral",
        "g_force_longitudinal",
        "g_force_vertical",
        "context_json",
        "validation_flags",
    )
    for chunk, _, slot_count in chunks:
        if remaining_skip >= slot_count:
            remaining_skip -= slot_count
            continue
        relative = Path(chunk["relative_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("stored observation path is invalid")
        path = (database_path.parent / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("observation chunk is missing")
        try:
            with path.open("rb") as stream:
                source_size = os.fstat(stream.fileno()).st_size
                if source_size > MAX_OBSERVATION_PREVIEW_CHUNK_BYTES:
                    raise ValueError("observation_preview_chunk_bytes_limit_exceeded")
                if source_bytes_read + source_size > MAX_OBSERVATION_PREVIEW_SOURCE_BYTES:
                    raise ValueError("observation_preview_source_bytes_limit_exceeded")
                source_bytes = stream.read(MAX_OBSERVATION_PREVIEW_CHUNK_BYTES + 1)
        except OSError:
            raise
        if (
            len(source_bytes) != source_size
            or len(source_bytes) > MAX_OBSERVATION_PREVIEW_CHUNK_BYTES
        ):
            raise ValueError("observation_preview_chunk_changed_during_read")
        source_bytes_read += len(source_bytes)
        actual_hash = hashlib.sha256(source_bytes).hexdigest()
        if actual_hash != chunk["sha256"]:
            raise ValueError("observation chunk checksum does not match SQLite")
        parquet = pq.ParquetFile(io.BytesIO(source_bytes))
        metadata = parquet.metadata
        raw_version = (metadata.metadata or {}).get(b"observation_schema_version")
        if (
            raw_version is None
            or int(raw_version) != OBSERVATION_SCHEMA_VERSION
            or int(chunk["schema_version"]) != OBSERVATION_SCHEMA_VERSION
            or metadata.num_rows != int(chunk["row_count"])
            or metadata.num_rows > MAX_OBSERVATION_CHUNK_ROWS
            or metadata.num_row_groups
            > (MAX_OBSERVATION_CHUNK_ROWS + ROW_GROUP_SIZE - 1) // ROW_GROUP_SIZE
            or int(chunk["row_count"]) > MAX_OBSERVATION_CHUNK_ROWS
        ):
            raise ValueError("observation chunk schema or row count does not match SQLite")
        selected_chunks.add(str(relative))
        for row_group in range(metadata.num_row_groups):
            row_group_metadata = metadata.row_group(row_group)
            if row_groups_read >= MAX_OBSERVATION_PREVIEW_ROW_GROUPS:
                raise ValueError("observation_preview_row_group_limit_exceeded")
            if (
                row_group_metadata.num_rows > ROW_GROUP_SIZE
                or row_group_metadata.total_byte_size > MAX_OBSERVATION_PREVIEW_ROW_GROUP_BYTES
            ):
                raise ValueError("observation_preview_row_group_bounds_exceeded")
            if rows_read + row_group_metadata.num_rows > MAX_OBSERVATION_PREVIEW_ROWS_READ:
                raise ValueError("observation_preview_rows_limit_exceeded")
            row_groups_read += 1
            rows_read += row_group_metadata.num_rows
            table = parquet.read_row_group(row_group, columns=list(columns))
            filtered = table.filter(pc.equal(table["car_index"], car_index))
            if filtered.num_rows == 0:
                continue
            rows = filtered.to_pylist()
            if remaining_skip >= len(rows):
                remaining_skip -= len(rows)
                continue
            rows = rows[remaining_skip:]
            remaining_skip = 0
            selected_rows.extend(rows[: limit - len(selected_rows)])
            if len(selected_rows) >= limit:
                break
        if len(selected_rows) >= limit:
            break

    for row in selected_rows:
        context_raw = row.pop("context_json", None)
        row["context"] = json.loads(context_raw) if context_raw else None
    return {
        "run_id": run_id,
        "session_uid": str(session["session_uid"]),
        "car_index": car_index,
        "status": "available",
        "archive_status": _car_observation_archive_status(session["metrics_json"]),
        "verification_scope": "session_car_observations",
        "opponent_eligibility": "not_assessed",
        "capture": {
            "complete": bool(session["complete"]),
            "footer_status": completion.get("status"),
        },
        "observations": {
            "limit": limit,
            "offset": offset,
            "total": total,
            "returned": len(selected_rows),
            "source_chunks_read": len(selected_chunks),
            "items": selected_rows,
        },
    }
