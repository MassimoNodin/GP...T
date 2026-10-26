from __future__ import annotations

from pathlib import Path

from .trajectory import (
    TRAJECTORY_PREVIEW_SOURCE_BYTE_LIMIT,
    TRAJECTORY_PREVIEW_SOURCE_CONTEXT_BYTE_LIMIT,
    TRAJECTORY_PREVIEW_SOURCE_CONTEXT_SEGMENT_LIMIT,
    TRAJECTORY_PREVIEW_SOURCE_ROW_LIMIT,
    TrajectoryPreviewUnavailable,
    build_observed_trajectory,
    build_observed_trajectory_preview,
)
from .trajectory_probe import build_observed_position_probe
from ..storage.query import (
    TRAJECTORY_TRACE_COLUMNS,
    AttemptTraceReadLimitError,
    load_attempt_trace,
)


TRAJECTORY_PREVIEW_TRACE_COLUMNS = [
    "frame_identifier",
    "session_time_s",
    "lap_distance_m",
    "current_lap_time_ms",
    "motion_available",
    "world_position_x_m",
    "world_position_y_m",
    "world_position_z_m",
]


def load_observed_trajectory(
    database_path: str | Path, attempt_key: str
) -> dict[str, object] | None:
    """Load the full-fidelity trajectory artifact used by the CLI exporter."""
    attempt = load_attempt_trace(
        database_path,
        attempt_key,
        columns=TRAJECTORY_TRACE_COLUMNS,
    )
    if attempt is None:
        return None
    return build_observed_trajectory(
        attempt_key=attempt.attempt_key,
        run_id=attempt.run_id,
        session_uid=attempt.session_uid,
        car_index=attempt.car_index,
        disposition=attempt.disposition,
        lap_time_ms=attempt.lap_time_ms,
        game_valid=attempt.game_valid,
        reference_eligible=attempt.reference_eligible,
        exclusion_reasons=attempt.exclusion_reasons,
        trace_sha256=attempt.trace_sha256,
        trace_schema_version=attempt.trace_schema_version,
        context_segments=attempt.context_segments,
        samples=attempt.samples,
    )


def load_observed_trajectory_preview(
    database_path: str | Path,
    attempt_key: str,
    *,
    position_probe_m: float | None = None,
    track_length_m: float | None = None,
) -> dict[str, object] | None:
    """Load a bounded preview and optional source-based position probe."""
    try:
        attempt = load_attempt_trace(
            database_path,
            attempt_key,
            columns=TRAJECTORY_PREVIEW_TRACE_COLUMNS,
            max_trace_bytes=TRAJECTORY_PREVIEW_SOURCE_BYTE_LIMIT,
            max_trace_rows=TRAJECTORY_PREVIEW_SOURCE_ROW_LIMIT,
            max_context_segments=TRAJECTORY_PREVIEW_SOURCE_CONTEXT_SEGMENT_LIMIT,
            max_context_bytes=TRAJECTORY_PREVIEW_SOURCE_CONTEXT_BYTE_LIMIT,
        )
    except AttemptTraceReadLimitError as exc:
        raise TrajectoryPreviewUnavailable(
            f"trajectory_source_{exc.limit_kind}_limit_exceeded"
        ) from exc
    if attempt is None:
        return None
    if attempt.trace_schema_version == 1:
        raise TrajectoryPreviewUnavailable("motion_unavailable_for_trace_schema")

    trajectory = build_observed_trajectory(
        attempt_key=attempt.attempt_key,
        run_id=attempt.run_id,
        session_uid=attempt.session_uid,
        car_index=attempt.car_index,
        disposition=attempt.disposition,
        lap_time_ms=attempt.lap_time_ms,
        game_valid=attempt.game_valid,
        reference_eligible=attempt.reference_eligible,
        exclusion_reasons=attempt.exclusion_reasons,
        trace_sha256=attempt.trace_sha256,
        trace_schema_version=attempt.trace_schema_version,
        context_segments=attempt.context_segments,
        samples=attempt.samples,
    )
    preview = build_observed_trajectory_preview(trajectory)
    if position_probe_m is not None:
        if track_length_m is None:
            raise ValueError("position probe requires a known track length")
        preview["position_probe"] = build_observed_position_probe(
            trajectory,
            position_probe_m,
            track_length_m=track_length_m,
        )
    return preview
