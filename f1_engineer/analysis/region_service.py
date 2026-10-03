from __future__ import annotations

import math
from pathlib import Path
from typing import Mapping

from ..storage.query import (
    ANALYSIS_TRACE_COLUMNS,
    AttemptTraceReadLimitError,
    StoredAttemptTrace,
    load_attempt_trace,
)
from ..tracks.model import MAX_TRACK_MODEL_REGIONS, TrackModel
from .corners import REGION_EVENT_EXAMPLE_LIMIT, analyze_attempt_regions
from .resampling import ResampledTrace, ResamplingConfig, TraceSample, resample_trace
from .service import TimeTrialContextError, require_track_model_compatible, stable_time_trial_context
from .trajectory import (
    REGION_POSITION_EVIDENCE_FRAGMENT_LIMIT,
    build_observed_trajectory,
    summarize_observed_position_window,
)
from .source_limits import (
    ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
    ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
    ANALYSIS_SOURCE_TRACE_BYTE_LIMIT,
    ANALYSIS_SOURCE_TRACE_ROW_LIMIT,
)


REGION_REPORT_SCHEMA_VERSION = 2
REGION_REPORT_GRID_POINT_LIMIT = 100_000
REGION_POSITION_TRACE_COLUMNS = [
    *ANALYSIS_TRACE_COLUMNS,
    "motion_available",
    "world_position_x_m",
    "world_position_y_m",
    "world_position_z_m",
]


class RegionReportUnavailable(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def load_attempt_region_report(
    database_path: str | Path,
    attempt_key: str,
    track_model: TrackModel,
    *,
    config: ResamplingConfig = ResamplingConfig(),
) -> dict[str, object] | None:
    """Load bounded, single-attempt observations for an explicitly selected model."""
    if len(track_model.corners) > MAX_TRACK_MODEL_REGIONS:
        raise RegionReportUnavailable("region_count_limit_exceeded")
    distance_grid = _bounded_track_grid(
        track_model.track_length_m,
        config.grid_step_m,
    )
    try:
        attempt = load_attempt_trace(
            database_path,
            attempt_key,
            columns=REGION_POSITION_TRACE_COLUMNS,
            max_trace_bytes=ANALYSIS_SOURCE_TRACE_BYTE_LIMIT,
            max_trace_rows=ANALYSIS_SOURCE_TRACE_ROW_LIMIT,
            max_context_segments=ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
            max_context_bytes=ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
        )
    except AttemptTraceReadLimitError as exc:
        raise RegionReportUnavailable(f"region_source_{exc.limit_kind}_limit_exceeded") from exc
    if attempt is None:
        return None

    try:
        context, _signature = stable_time_trial_context(
            attempt.context_segments, attempt.attempt_key
        )
    except TimeTrialContextError as exc:
        raise RegionReportUnavailable(exc.reason_code) from exc
    try:
        require_track_model_compatible(track_model, context)
    except ValueError as exc:
        raise RegionReportUnavailable("track_model_incompatible") from exc

    samples = tuple(TraceSample.from_record(row) for row in attempt.samples)
    resampled = resample_trace(
        samples,
        distance_grid,
        config,
        track_length_m=track_model.track_length_m,
    )
    observations = analyze_attempt_regions(
        samples,
        resampled,
        track_model,
        config=config,
        event_example_limit=REGION_EVENT_EXAMPLE_LIMIT,
    )
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
    for region in observations["regions"]:
        if not isinstance(region, dict):
            raise ValueError("region observation is malformed")
        window = region.get("analysis_window_m")
        if not isinstance(window, list) or len(window) != 2:
            raise ValueError("region analysis window is malformed")
        start_m, end_m = window
        if not isinstance(start_m, (int, float)) or not isinstance(end_m, (int, float)):
            raise ValueError("region analysis window is malformed")
        region["position_evidence"] = summarize_observed_position_window(
            trajectory,
            attempt.samples,
            float(start_m),
            float(end_m),
            trace_schema_version=attempt.trace_schema_version,
            fragment_limit=REGION_POSITION_EVIDENCE_FRAGMENT_LIMIT,
        )
    return {
        "schema_version": REGION_REPORT_SCHEMA_VERSION,
        "artifact_kind": "single_attempt_distance_region_observations",
        "diagnostic_only": True,
        "config": config.to_dict(),
        "source": _source_summary(attempt),
        **observations,
    }


def _bounded_track_grid(track_length_m: float, grid_step_m: float) -> tuple[float, ...]:
    if not math.isfinite(track_length_m) or track_length_m <= 0:
        raise RegionReportUnavailable("invalid_track_length")
    if not math.isfinite(grid_step_m) or grid_step_m <= 0:
        raise RegionReportUnavailable("invalid_grid_step")
    final_index = math.floor((track_length_m + 1e-9) / grid_step_m)
    point_count = final_index + 1
    if point_count > REGION_REPORT_GRID_POINT_LIMIT:
        raise RegionReportUnavailable("region_resampling_grid_limit_exceeded")
    return tuple(index * grid_step_m for index in range(point_count))


def _source_summary(attempt: StoredAttemptTrace) -> dict[str, object]:
    return {
        "attempt_key": attempt.attempt_key,
        "run_id": attempt.run_id,
        "session_uid": attempt.session_uid,
        "car_index": attempt.car_index,
        "attempt_number": attempt.attempt_number,
        "disposition": attempt.disposition,
        "lap_time_ms": attempt.lap_time_ms,
        "game_valid": attempt.game_valid,
        "reference_eligible": attempt.reference_eligible,
        "exclusion_reasons": list(attempt.exclusion_reasons),
        "trace_sha256": attempt.trace_sha256,
        "trace_schema_version": attempt.trace_schema_version,
        "source_sample_count": len(attempt.samples),
        "session_context_segments": [
            {
                "from_frame_identifier": frame,
                "context": dict(context) if isinstance(context, Mapping) else None,
            }
            for frame, context in attempt.context_segments
        ],
    }
