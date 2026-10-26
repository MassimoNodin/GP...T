from __future__ import annotations

import math
from pathlib import Path
from typing import Mapping

from ..storage.query import (
    ANALYSIS_TRACE_COLUMNS,
    AttemptTraceReadLimitError,
    AttemptTraceResourceEstimate,
    StoredAttemptTrace,
    load_attempt_trace,
    load_attempt_trace_resource_estimates,
)
from ..storage.run_summaries import get_processing_run_summary
from ..tracks.model import MAX_TRACK_MODEL_REGIONS, TrackModel
from .corners import REGION_EVENT_EXAMPLE_LIMIT, analyze_attempt_regions
from .resampling import (
    RESAMPLING_WORK_POLICY_VERSION,
    ResamplingConfig,
    TraceSample,
    estimate_resampling_work,
    resample_trace,
)
from .service import (
    TimeTrialContextError,
    require_track_model_compatible,
    stable_practice_qualifying_context,
    stable_time_trial_context,
)
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


REGION_REPORT_SCHEMA_VERSION = 3
REGION_REPORT_GRID_POINT_LIMIT = 100_000
REGION_ANALYSIS_WORK_POLICY_VERSION = "single-attempt-region-weighted-work-v1"
REGION_ANALYSIS_WORK_LIMIT = 32_000_000
REGION_RESAMPLING_WORK_POLICY_VERSION = RESAMPLING_WORK_POLICY_VERSION
REGION_RESAMPLING_WORK_LIMIT = 16_000_000
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
    model_metadata: Mapping[str, object] | None = None,
    config: ResamplingConfig = ResamplingConfig(),
) -> dict[str, object] | None:
    """Load bounded, standalone observations for an explicitly selected model."""
    region_count = len(track_model.corners)
    if region_count > MAX_TRACK_MODEL_REGIONS:
        raise RegionReportUnavailable("region_count_limit_exceeded")

    try:
        estimates = load_attempt_trace_resource_estimates(
            database_path,
            [attempt_key],
            max_context_segments=ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
            max_context_bytes=ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
        )
    except AttemptTraceReadLimitError as exc:
        raise RegionReportUnavailable(f"region_source_{exc.limit_kind}_limit_exceeded") from exc
    if not estimates:
        return None
    estimate = estimates[0]
    _require_estimated_source_within_limits(estimate)

    try:
        context, context_mode = stable_region_context(
            estimate.context_segments, estimate.attempt_key
        )
    except TimeTrialContextError as exc:
        raise RegionReportUnavailable(exc.reason_code) from exc
    except ValueError as exc:
        if str(exc).startswith("practice_qualifying_"):
            raise RegionReportUnavailable(_practice_qualifying_reason(str(exc))) from exc
        raise
    try:
        require_track_model_compatible(track_model, context)
    except ValueError as exc:
        raise RegionReportUnavailable("track_model_incompatible") from exc

    distance_grid = _bounded_track_grid(track_model.track_length_m, config.grid_step_m)
    source_count = estimate.trace_row_count
    assert source_count is not None
    _require_region_resampling_work(
        source_count, len(distance_grid), config, hard_block_count=0
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
        raise RegionReportUnavailable("attempt_metadata_changed_during_read")
    if not _estimate_matches_attempt(estimate, attempt):
        raise RegionReportUnavailable("attempt_metadata_changed_during_read")

    samples = tuple(TraceSample.from_record(row) for row in attempt.samples)
    hard_block_count = _conservative_hard_block_count(samples, track_model.track_length_m)
    resampling_work = estimate_resampling_work(
        len(samples),
        len(distance_grid),
        config,
        hard_block_count=hard_block_count,
    )
    _enforce_work_limit(
        resampling_work,
        REGION_RESAMPLING_WORK_LIMIT,
        "region_resampling_work_limit_exceeded",
    )

    resampled = resample_trace(
        samples,
        distance_grid,
        config,
        track_length_m=track_model.track_length_m,
    )
    analysis_work = _region_analysis_work(
        region_count, len(samples), len(distance_grid), len(resampled.excluded_spans)
    )
    _enforce_work_limit(
        analysis_work,
        REGION_ANALYSIS_WORK_LIMIT,
        "region_analysis_work_limit_exceeded",
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

    run_summary = get_processing_run_summary(database_path, attempt.run_id)
    warnings = _region_warnings(attempt, context_mode, run_summary)
    return {
        "schema_version": REGION_REPORT_SCHEMA_VERSION,
        "artifact_kind": "single_attempt_distance_region_observations",
        "diagnostic_only": True,
        "context_mode": context_mode,
        "config": config.to_dict(),
        "resource_policy": {
            "resampling": {
                "version": REGION_RESAMPLING_WORK_POLICY_VERSION,
                "estimated_work": resampling_work,
                "limit": REGION_RESAMPLING_WORK_LIMIT,
                "status": "within_limit",
            },
            "region_analysis": {
                "version": REGION_ANALYSIS_WORK_POLICY_VERSION,
                "estimated_work": analysis_work,
                "limit": REGION_ANALYSIS_WORK_LIMIT,
                "status": "within_limit",
            },
        },
        "source": _source_summary(attempt, run_summary),
        "warnings": warnings,
        **observations,
        "model": _model_summary(track_model, model_metadata),
    }


def stable_region_context(
    context_segments: tuple[tuple[int, Mapping[str, object] | None], ...],
    attempt_key: str,
) -> tuple[Mapping[str, object], str]:
    if not context_segments:
        raise RegionReportUnavailable("unknown_context")
    first = context_segments[0][1]
    if first is None:
        raise RegionReportUnavailable("unknown_context")
    session_type = first.get("session_type")
    game_mode = first.get("game_mode")
    rule_set = first.get("rule_set")
    if (
        session_type == "time_trial"
        or game_mode == "time_trial"
        or rule_set == "time_trial"
    ):
        try:
            context, _signature = stable_time_trial_context(context_segments, attempt_key)
        except TimeTrialContextError:
            raise
        return context, "time_trial"
    is_practice_qualifying = (
        rule_set == "practice_qualifying"
        or (
            isinstance(session_type, str)
            and session_type.startswith(("practice_", "qualifying_", "sprint_shootout_"))
        )
    )
    if is_practice_qualifying:
        try:
            context, _signature = stable_practice_qualifying_context(
                context_segments, attempt_key
            )
        except ValueError:
            raise
        return context, "practice_qualifying"
    if any(value in (None, "unknown") for value in (session_type, game_mode, rule_set)):
        raise RegionReportUnavailable("unknown_mode")
    raise RegionReportUnavailable("unsupported_mode")


def _practice_qualifying_reason(reason: str) -> str:
    prefix = "practice_qualifying_"
    return reason[len(prefix) :] if reason.startswith(prefix) else reason


def _require_estimated_source_within_limits(estimate: AttemptTraceResourceEstimate) -> None:
    if not estimate.trace_ready:
        raise RegionReportUnavailable("attempt_trace_unavailable")
    if estimate.trace_row_count is None:
        raise RegionReportUnavailable("attempt_trace_unavailable")
    if estimate.trace_row_count > ANALYSIS_SOURCE_TRACE_ROW_LIMIT:
        raise RegionReportUnavailable("region_source_rows_limit_exceeded")
    if estimate.trace_size_bytes is None:
        raise RegionReportUnavailable("attempt_trace_unavailable")
    if estimate.trace_size_bytes > ANALYSIS_SOURCE_TRACE_BYTE_LIMIT:
        raise RegionReportUnavailable("region_source_bytes_limit_exceeded")


def _estimate_matches_attempt(
    estimate: AttemptTraceResourceEstimate, attempt: StoredAttemptTrace
) -> bool:
    return (
        estimate.attempt_key == attempt.attempt_key
        and estimate.run_id == attempt.run_id
        and estimate.session_uid == attempt.session_uid
        and estimate.car_index == attempt.car_index
        and estimate.attempt_number == attempt.attempt_number
        and estimate.disposition == attempt.disposition
        and estimate.lap_time_ms == attempt.lap_time_ms
        and estimate.game_valid == attempt.game_valid
        and estimate.superseded == attempt.superseded
        and estimate.lifecycle_assessed == attempt.lifecycle_assessed
        and estimate.exclusion_reasons == attempt.exclusion_reasons
        and estimate.trace_row_count == attempt.source_sample_count
        and estimate.trace_sha256 == attempt.trace_sha256
        and estimate.trace_schema_version == attempt.trace_schema_version
        and estimate.context_segments == attempt.context_segments
    )


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


def _conservative_hard_block_count(
    samples: tuple[TraceSample, ...], track_length_m: float
) -> int:
    count = 0
    for left, right in zip(samples, samples[1:]):
        if (
            left.distance_m is None
            or right.distance_m is None
            or left.time_s is None
            or right.time_s is None
            or not 0 <= left.distance_m <= track_length_m
            or not 0 <= right.distance_m <= track_length_m
        ):
            continue
        if right.time_s < left.time_s or right.distance_m <= left.distance_m:
            count += 1
    return count


def _require_region_resampling_work(
    source_count: int,
    grid_count: int,
    config: ResamplingConfig,
    *,
    hard_block_count: int,
) -> None:
    work = estimate_resampling_work(
        source_count,
        grid_count,
        config,
        hard_block_count=hard_block_count,
    )
    _enforce_work_limit(
        work,
        REGION_RESAMPLING_WORK_LIMIT,
        "region_resampling_work_limit_exceeded",
    )


def _region_analysis_work(
    region_count: int, source_count: int, grid_count: int, excluded_span_count: int
) -> int:
    largest_scan = max(grid_count, excluded_span_count)
    scan_levels = math.ceil(math.log2(largest_scan + 1))
    return region_count * (
        40 * source_count + 16 * (grid_count + excluded_span_count) * scan_levels
    )


def _enforce_work_limit(work: int, limit: int, reason: str) -> None:
    if work > limit:
        raise RegionReportUnavailable(reason)


def _model_summary(
    model: TrackModel, metadata: Mapping[str, object] | None
) -> dict[str, object]:
    supplied: dict[str, object] = {
        "model_id": model.model_id,
        "revision": model.revision,
        "packet_format": model.packet_format,
        "track_id": model.track_id,
        "track_name": model.track_name,
        "layout_id": model.layout_id,
        "track_length_m": model.track_length_m,
        "distance_origin_m": model.distance_origin_m,
        "validation_status": model.validation_status,
        "provenance": model.provenance,
        "region_count": len(model.corners),
        "origin": "unattributed" if metadata is None else "packaged",
        "content_sha256": None,
        "source_filename": None,
    }
    if metadata is not None:
        supplied.update(metadata)
    supplied["distance_origin_m"] = model.distance_origin_m
    supplied["layout_id"] = model.layout_id
    supplied["layout_identity_status"] = "caller_declared"
    return supplied


def _source_summary(
    attempt: StoredAttemptTrace, run_summary: Mapping[str, object] | None
) -> dict[str, object]:
    capture: Mapping[str, object] | None = None
    processing: Mapping[str, object] | None = None
    if isinstance(run_summary, Mapping):
        raw_capture = run_summary.get("capture")
        raw_processing = run_summary.get("processing")
        capture = raw_capture if isinstance(raw_capture, Mapping) else None
        processing = raw_processing if isinstance(raw_processing, Mapping) else None
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
        "start_observed": attempt.start_observed,
        "pit_encountered": attempt.pit_encountered,
        "superseded": attempt.superseded,
        "lifecycle_assessed": attempt.lifecycle_assessed,
        "exclusion_reasons": list(attempt.exclusion_reasons),
        "trace_sha256": attempt.trace_sha256,
        "trace_schema_version": attempt.trace_schema_version,
        "source_sample_count": len(attempt.samples),
        "capture": (
            {
                "sha256": capture.get("sha256"),
                "complete": capture.get("complete"),
                "footer_status": capture.get("footer_status"),
                "recording_counters": capture.get("recording_counters"),
            }
            if capture is not None
            else None
        ),
        "replay_counters": (
            processing.get("replay_counters") if processing is not None else None
        ),
        "session_context_segments": [
            {
                "from_frame_identifier": frame,
                "context": dict(context) if isinstance(context, Mapping) else None,
            }
            for frame, context in attempt.context_segments
        ],
    }


def _region_warnings(
    attempt: StoredAttemptTrace,
    context_mode: str,
    run_summary: Mapping[str, object] | None,
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if context_mode == "practice_qualifying":
        warnings.append({
            "code": "practice_qualifying_conditions_uncontrolled",
            "text": "Fuel, tyres, traffic, and cooldown intent are uncontrolled.",
        })
    if attempt.disposition != "completed":
        warnings.append({
            "code": "attempt_not_completed",
            "text": "This partial or abandoned attempt is shown as diagnostic evidence.",
        })
    if attempt.game_valid is False:
        warnings.append({"code": "game_invalid", "text": "The game marked this lap invalid."})
    elif attempt.game_valid is None:
        warnings.append({"code": "game_validity_unknown", "text": "Game validity is unknown."})
    if attempt.superseded is True:
        warnings.append({"code": "superseded", "text": "Lifecycle evidence supersedes this attempt."})
    if not attempt.lifecycle_assessed or attempt.superseded is None:
        warnings.append({"code": "lifecycle_unassessed", "text": "Lifecycle evidence is unassessed."})

    capture: Mapping[str, object] = {}
    processing: Mapping[str, object] = {}
    if isinstance(run_summary, Mapping):
        raw_capture = run_summary.get("capture")
        raw_processing = run_summary.get("processing")
        if isinstance(raw_capture, Mapping):
            capture = raw_capture
        if isinstance(raw_processing, Mapping):
            processing = raw_processing
    if capture.get("complete") is False:
        warnings.append({"code": "capture_incomplete", "text": "The source capture footer is incomplete."})
    elif capture.get("complete") is not True:
        warnings.append({"code": "capture_completion_unknown", "text": "Capture completion is unknown."})
    recording = capture.get("recording_counters")
    recording = recording if isinstance(recording, Mapping) else {}
    loss_values = [recording.get(key) for key in ("queue_dropped", "unpersisted_on_shutdown", "socket_errors")]
    known_loss = any(isinstance(value, int) and value > 0 for value in loss_values)
    unknown_loss = any(value is None for value in loss_values)
    if known_loss:
        text = "Recording loss or socket errors were reported."
        if unknown_loss:
            text += " Other loss counters are unknown."
        warnings.append({"code": "recording_loss_reported", "text": text})
    elif unknown_loss:
        warnings.append({"code": "recording_loss_counters_unknown", "text": "Some recording loss counters are unknown."})
    replay = processing.get("replay_counters")
    replay = replay if isinstance(replay, Mapping) else {}
    replay_values = [replay.get(key) for key in ("import_late_packets_ignored", "import_frame_overflow_packets_dropped")]
    known_replay = any(isinstance(value, int) and value > 0 for value in replay_values)
    unknown_replay = any(value is None for value in replay_values)
    if known_replay:
        text = "Replay excluded late or overflowed frames."
        if unknown_replay:
            text += " Other replay counters are unknown."
        warnings.append({"code": "replay_frame_exclusions_reported", "text": text})
    elif unknown_replay:
        warnings.append({"code": "replay_frame_counters_unknown", "text": "Some replay frame counters are unknown."})
    return warnings[:12]
