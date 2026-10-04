from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..storage.query import (
    ANALYSIS_TRACE_COLUMNS,
    AttemptTraceReadLimitError,
    AttemptTraceResourceEstimate,
    StoredAttemptTrace,
    load_attempt_trace,
    load_attempt_trace_resource_estimates,
)
from ..storage.run_summaries import get_processing_run_summary
from ..tracks.model import MAX_TRACK_MODEL_REGIONS, CornerDefinition, TrackModel
from .comparison import calculate_delta_time
from .corners import CORNER_ANALYSIS_VERSION, REGION_EVENT_EXAMPLE_LIMIT, analyze_corner_regions
from .interval_delta import (
    MAX_INTERVAL_EVALUATION_WORK,
    reserve_interval_evaluation_work,
)
from .paired_region_debrief import (
    MAX_DIAGNOSTIC_DEBRIEF_REGIONS,
    build_diagnostic_region_debrief,
)
from .region_service import (
    REGION_RESAMPLING_WORK_LIMIT,
    _conservative_hard_block_count,
    _estimate_matches_attempt,
    _model_summary,
    _region_warnings,
    _source_summary,
)
from .resampling import (
    RESAMPLING_WORK_POLICY_VERSION,
    ResamplingConfig,
    TraceSample,
    estimate_resampling_work,
    resample_trace,
)
from .service import (
    ComparisonPolicy,
    TimeTrialContextError,
    require_track_model_compatible,
    stable_practice_qualifying_context,
    stable_time_trial_context,
    validate_comparison_pair_policy,
)
from .source_limits import (
    ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
    ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
    ANALYSIS_SOURCE_TRACE_BYTE_LIMIT,
    ANALYSIS_SOURCE_TRACE_ROW_LIMIT,
)


PAIRED_REGION_REPORT_SCHEMA_VERSION = 1
PAIRED_REGION_REPORT_VERSION = "paired-distance-region-observations-v1"
PAIRED_REGION_GRID_POINT_LIMIT = 100_000
PAIRED_REGION_RESAMPLING_WORK_LIMIT = REGION_RESAMPLING_WORK_LIMIT
PAIRED_REGION_ANALYSIS_WORK_POLICY_VERSION = "paired-region-weighted-work-v1"
PAIRED_REGION_ANALYSIS_WORK_LIMIT = 32_000_000
PAIRED_REGION_SOURCE_TRACE_BYTE_LIMIT = ANALYSIS_SOURCE_TRACE_BYTE_LIMIT
PAIRED_REGION_SOURCE_TRACE_ROW_LIMIT = ANALYSIS_SOURCE_TRACE_ROW_LIMIT


class PairedRegionReportUnavailable(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def compare_attempt_regions(
    database_path: str | Path,
    target_attempt_key: str,
    reference_attempt_key: str,
    track_model: TrackModel,
    *,
    model_metadata: Mapping[str, object] | None = None,
    policy: ComparisonPolicy | str = ComparisonPolicy.TIME_TRIAL,
    config: ResamplingConfig = ResamplingConfig(),
) -> dict[str, object] | None:
    """Compare two explicitly selected attempts across configured distance regions."""
    try:
        selected_policy = ComparisonPolicy(policy)
    except ValueError as exc:
        raise PairedRegionReportUnavailable("unsupported_comparison_policy") from exc
    if target_attempt_key == reference_attempt_key:
        raise PairedRegionReportUnavailable("target_and_reference_must_be_different")
    region_count = len(track_model.corners)
    if region_count > MAX_TRACK_MODEL_REGIONS:
        raise PairedRegionReportUnavailable("region_count_limit_exceeded")

    try:
        estimates = load_attempt_trace_resource_estimates(
            database_path,
            [target_attempt_key, reference_attempt_key],
            max_context_segments=ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
            max_context_bytes=ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
        )
    except AttemptTraceReadLimitError as exc:
        raise PairedRegionReportUnavailable(
            f"region_pair_source_{exc.limit_kind}_limit_exceeded"
        ) from exc
    estimate_by_key = {item.attempt_key: item for item in estimates}
    if len(estimates) != 2 or set(estimate_by_key) != {
        target_attempt_key,
        reference_attempt_key,
    }:
        return None
    target_estimate = estimate_by_key[target_attempt_key]
    reference_estimate = estimate_by_key[reference_attempt_key]
    _require_estimate(target_estimate)
    _require_estimate(reference_estimate)
    _require_aggregate_source_limits(estimates)
    _require_pair_scope(target_estimate, reference_estimate)

    target_policy_attempt = _policy_attempt(target_estimate)
    reference_policy_attempt = _policy_attempt(reference_estimate)
    try:
        target_context, reference_context = validate_comparison_pair_policy(
            target_policy_attempt,
            reference_policy_attempt,
            policy=selected_policy,
            config=config,
        )
    except TimeTrialContextError as exc:
        raise PairedRegionReportUnavailable(exc.reason_code) from exc
    except ValueError as exc:
        raise PairedRegionReportUnavailable(str(exc)) from exc
    try:
        require_track_model_compatible(track_model, target_context)
        require_track_model_compatible(track_model, reference_context)
    except ValueError as exc:
        raise PairedRegionReportUnavailable("track_model_incompatible") from exc

    grid = _bounded_pair_track_grid(track_model.track_length_m, config.grid_step_m)
    _require_pair_resampling_work(
        (target_estimate, reference_estimate),
        len(grid),
        config,
        hard_blocks=(0, 0),
    )

    target = _load_bounded_attempt(database_path, target_estimate)
    if target is None:
        raise PairedRegionReportUnavailable("attempt_metadata_changed_during_read")
    if target.context_segments != target_estimate.context_segments:
        raise PairedRegionReportUnavailable("attempt_context_changed_during_read")
    if not _estimate_matches_attempt(target_estimate, target):
        raise PairedRegionReportUnavailable("attempt_metadata_changed_during_read")

    reference = _load_bounded_attempt(
        database_path,
        reference_estimate,
        remaining_trace_bytes=PAIRED_REGION_SOURCE_TRACE_BYTE_LIMIT
        - (target_estimate.trace_size_bytes or 0),
        remaining_trace_rows=PAIRED_REGION_SOURCE_TRACE_ROW_LIMIT
        - (target_estimate.trace_row_count or 0),
        remaining_context_segments=ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT
        - target_estimate.context_segment_count,
        remaining_context_bytes=ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT
        - target_estimate.context_bytes,
    )
    if reference is None:
        raise PairedRegionReportUnavailable("attempt_metadata_changed_during_read")
    if reference.context_segments != reference_estimate.context_segments:
        raise PairedRegionReportUnavailable("attempt_context_changed_during_read")
    if not _estimate_matches_attempt(reference_estimate, reference):
        raise PairedRegionReportUnavailable("attempt_metadata_changed_during_read")
    if len(target.samples) + len(reference.samples) > PAIRED_REGION_SOURCE_TRACE_ROW_LIMIT:
        raise PairedRegionReportUnavailable("region_pair_source_rows_limit_exceeded")

    try:
        target_context, reference_context = validate_comparison_pair_policy(
            target, reference, policy=selected_policy, config=config
        )
    except TimeTrialContextError as exc:
        raise PairedRegionReportUnavailable(exc.reason_code) from exc
    except ValueError as exc:
        raise PairedRegionReportUnavailable(str(exc)) from exc
    try:
        require_track_model_compatible(track_model, target_context)
        require_track_model_compatible(track_model, reference_context)
    except ValueError as exc:
        raise PairedRegionReportUnavailable("track_model_incompatible") from exc

    target_samples = tuple(TraceSample.from_record(row) for row in target.samples)
    reference_samples = tuple(TraceSample.from_record(row) for row in reference.samples)
    target_hard_blocks = _conservative_hard_block_count(
        target_samples, track_model.track_length_m
    )
    reference_hard_blocks = _conservative_hard_block_count(
        reference_samples, track_model.track_length_m
    )
    resampling_work = _require_pair_resampling_work(
        (target_estimate, reference_estimate),
        len(grid),
        config,
        hard_blocks=(target_hard_blocks, reference_hard_blocks),
        source_counts=(len(target_samples), len(reference_samples)),
    )

    target_resampled = resample_trace(
        target_samples, grid, config, track_length_m=track_model.track_length_m
    )
    reference_resampled = resample_trace(
        reference_samples, grid, config, track_length_m=track_model.track_length_m
    )
    delta = calculate_delta_time(target_resampled, reference_resampled)
    region_work = _paired_region_analysis_work(
        region_count,
        len(target_samples),
        len(reference_samples),
        len(target_resampled.distance_m),
        len(reference_resampled.distance_m),
        len(delta.values_s),
        len(target_resampled.excluded_spans) + len(reference_resampled.excluded_spans),
    )
    if region_work > PAIRED_REGION_ANALYSIS_WORK_LIMIT:
        raise PairedRegionReportUnavailable("region_pair_analysis_work_limit_exceeded")
    try:
        interval_work = reserve_interval_evaluation_work(
            region_count,
            target_samples,
            reference_samples,
            target_resampled,
            reference_resampled,
            delta,
            limit=MAX_INTERVAL_EVALUATION_WORK,
        )
    except ValueError as exc:
        raise PairedRegionReportUnavailable(str(exc)) from exc

    observations = analyze_corner_regions(
        target_samples,
        reference_samples,
        target_resampled,
        reference_resampled,
        delta,
        track_model,
        config=config,
        target_reference_eligible=False,
        reference_reference_eligible=False,
        event_example_limit=REGION_EVENT_EXAMPLE_LIMIT,
    )
    run_summary = get_processing_run_summary(database_path, target.run_id)
    warnings = {
        "target": _region_warnings(target, selected_policy.value, run_summary),
        "reference": _region_warnings(reference, selected_policy.value, run_summary),
    }
    regions = _paired_regions(track_model, observations)
    return {
        "schema_version": PAIRED_REGION_REPORT_SCHEMA_VERSION,
        "analysis_version": PAIRED_REGION_REPORT_VERSION,
        "region_analysis_version": CORNER_ANALYSIS_VERSION,
        "artifact_kind": "paired_distance_region_observations",
        "status": "available",
        "comparison_policy": selected_policy.value,
        "diagnostic_only": True,
        "coaching_eligible": False,
        "ranking_eligible": False,
        "config": config.to_dict(),
        "policy_limitations": (
            [
                "fuel_load_uncontrolled",
                "tyre_condition_uncontrolled",
                "traffic_uncontrolled",
                "cooldown_intent_uncontrolled",
            ]
            if selected_policy is ComparisonPolicy.PRACTICE_QUALIFYING
            else []
        ),
        "track": {
            "packet_format": target_context["packet_format"],
            "track_id": target_context["track_id"],
            "track_name": target_context["track_name"],
            "track_length_m": target_context["track_length_m"],
            "layout_identity_status": "caller_declared",
        },
        "attempts": {
            "target": _source_summary(target, run_summary),
            "reference": _source_summary(reference, run_summary),
        },
        "warnings": warnings,
        "model": _model_summary(track_model, model_metadata),
        "regions": regions,
        "resource_policy": {
            "source": {
                "trace_bytes": {
                    "estimated": sum(item.trace_size_bytes or 0 for item in estimates),
                    "limit": PAIRED_REGION_SOURCE_TRACE_BYTE_LIMIT,
                },
                "trace_rows": {
                    "estimated": len(target.samples) + len(reference.samples),
                    "limit": PAIRED_REGION_SOURCE_TRACE_ROW_LIMIT,
                },
                "grid_points": {
                    "estimated": 2 * len(grid),
                    "limit": PAIRED_REGION_GRID_POINT_LIMIT,
                },
                "context_segments": {
                    "estimated": sum(item.context_segment_count for item in estimates),
                    "limit": ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
                },
                "context_bytes": {
                    "estimated": sum(item.context_bytes for item in estimates),
                    "limit": ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
                },
                "regions": {"estimated": region_count, "limit": MAX_TRACK_MODEL_REGIONS},
            },
            "resampling": {
                "version": RESAMPLING_WORK_POLICY_VERSION,
                "estimated_work": resampling_work,
                "limit": PAIRED_REGION_RESAMPLING_WORK_LIMIT,
                "status": "within_limit",
            },
            "interval_evaluation": {
                "version": "connected-interval-evaluation-v1",
                "estimated_work": interval_work,
                "limit": MAX_INTERVAL_EVALUATION_WORK,
                "status": "within_limit",
            },
            "region_analysis": {
                "version": PAIRED_REGION_ANALYSIS_WORK_POLICY_VERSION,
                "estimated_work": region_work,
                "limit": PAIRED_REGION_ANALYSIS_WORK_LIMIT,
                "status": "within_limit",
            },
            "event_examples_per_threshold_side_region": REGION_EVENT_EXAMPLE_LIMIT,
        },
    }


def _require_estimate(estimate: AttemptTraceResourceEstimate) -> None:
    if not estimate.trace_ready:
        raise PairedRegionReportUnavailable("attempt_trace_unavailable")
    if estimate.trace_row_count is None or estimate.trace_size_bytes is None:
        raise PairedRegionReportUnavailable("attempt_trace_unavailable")
    if estimate.trace_sha256 is None or estimate.trace_schema_version is None:
        raise PairedRegionReportUnavailable("attempt_trace_provenance_unavailable")


def _require_aggregate_source_limits(
    estimates: Sequence[AttemptTraceResourceEstimate],
) -> None:
    byte_count = sum(item.trace_size_bytes or 0 for item in estimates)
    row_count = sum(item.trace_row_count or 0 for item in estimates)
    context_segments = sum(item.context_segment_count for item in estimates)
    context_bytes = sum(item.context_bytes for item in estimates)
    if byte_count > PAIRED_REGION_SOURCE_TRACE_BYTE_LIMIT:
        raise PairedRegionReportUnavailable("region_pair_source_bytes_limit_exceeded")
    if row_count > PAIRED_REGION_SOURCE_TRACE_ROW_LIMIT:
        raise PairedRegionReportUnavailable("region_pair_source_rows_limit_exceeded")
    if context_segments > ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT:
        raise PairedRegionReportUnavailable(
            "region_pair_source_context_segments_limit_exceeded"
        )
    if context_bytes > ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT:
        raise PairedRegionReportUnavailable("region_pair_source_context_bytes_limit_exceeded")


def _require_pair_scope(
    target: AttemptTraceResourceEstimate,
    reference: AttemptTraceResourceEstimate,
) -> None:
    if target.run_id != reference.run_id:
        raise PairedRegionReportUnavailable("region_pair_must_share_processing_run")
    if target.session_uid != reference.session_uid:
        raise PairedRegionReportUnavailable("region_pair_must_share_session")
    if target.car_index != reference.car_index:
        raise PairedRegionReportUnavailable("region_pair_must_share_player")


def _policy_attempt(estimate: AttemptTraceResourceEstimate) -> StoredAttemptTrace:
    return StoredAttemptTrace(
        attempt_key=estimate.attempt_key,
        run_id=estimate.run_id,
        session_uid=estimate.session_uid,
        car_index=estimate.car_index,
        disposition=estimate.disposition,
        lap_time_ms=estimate.lap_time_ms,
        game_valid=estimate.game_valid,
        reference_eligible=False,
        exclusion_reasons=estimate.exclusion_reasons,
        trace_sha256=estimate.trace_sha256 or "",
        trace_schema_version=estimate.trace_schema_version or 1,
        quality={},
        context_segments=estimate.context_segments,
        samples=(),
        attempt_number=estimate.attempt_number,
        start_observed=estimate.start_observed,
        pit_encountered=estimate.pit_encountered,
        superseded=estimate.superseded,
        lifecycle_assessed=estimate.lifecycle_assessed,
        source_sample_count=estimate.trace_row_count,
    )


def _load_bounded_attempt(
    database_path: str | Path,
    estimate: AttemptTraceResourceEstimate,
    *,
    remaining_trace_bytes: int = PAIRED_REGION_SOURCE_TRACE_BYTE_LIMIT,
    remaining_trace_rows: int = PAIRED_REGION_SOURCE_TRACE_ROW_LIMIT,
    remaining_context_segments: int = ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
    remaining_context_bytes: int = ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
) -> StoredAttemptTrace | None:
    if (
        estimate.trace_size_bytes is None
        or estimate.trace_row_count is None
        or estimate.trace_size_bytes > remaining_trace_bytes
        or estimate.trace_row_count > remaining_trace_rows
        or estimate.context_segment_count > remaining_context_segments
        or estimate.context_bytes > remaining_context_bytes
    ):
        raise PairedRegionReportUnavailable("region_pair_source_reservation_exceeded")
    try:
        return load_attempt_trace(
            database_path,
            estimate.attempt_key,
            columns=ANALYSIS_TRACE_COLUMNS,
            max_trace_bytes=min(remaining_trace_bytes, estimate.trace_size_bytes),
            max_trace_rows=min(remaining_trace_rows, estimate.trace_row_count),
            max_context_segments=min(
                remaining_context_segments, estimate.context_segment_count
            ),
            max_context_bytes=min(remaining_context_bytes, estimate.context_bytes),
        )
    except AttemptTraceReadLimitError as exc:
        raise PairedRegionReportUnavailable(
            f"region_pair_source_{exc.limit_kind}_limit_exceeded"
        ) from exc


def _require_pair_resampling_work(
    estimates: tuple[AttemptTraceResourceEstimate, AttemptTraceResourceEstimate],
    grid_count: int,
    config: ResamplingConfig,
    *,
    hard_blocks: tuple[int, int],
    source_counts: tuple[int, int] | None = None,
) -> int:
    if source_counts is None:
        source_counts = (
            estimates[0].trace_row_count or 0,
            estimates[1].trace_row_count or 0,
        )
    work = sum(
        estimate_resampling_work(
            source_count,
            grid_count,
            config,
            hard_block_count=hard_block_count,
        )
        for source_count, hard_block_count in zip(source_counts, hard_blocks)
    )
    if work > PAIRED_REGION_RESAMPLING_WORK_LIMIT:
        raise PairedRegionReportUnavailable("region_pair_resampling_work_limit_exceeded")
    return work


def _bounded_pair_track_grid(
    track_length_m: float, grid_step_m: float
) -> tuple[float, ...]:
    if not math.isfinite(track_length_m) or track_length_m <= 0:
        raise PairedRegionReportUnavailable("invalid_track_length")
    if not math.isfinite(grid_step_m) or grid_step_m <= 0:
        raise PairedRegionReportUnavailable("invalid_grid_step")
    final_index = math.floor((track_length_m + 1e-9) / grid_step_m)
    point_count = final_index + 1
    if point_count * 2 > PAIRED_REGION_GRID_POINT_LIMIT:
        raise PairedRegionReportUnavailable("region_pair_grid_limit_exceeded")
    return tuple(index * grid_step_m for index in range(point_count))


def _paired_region_analysis_work(
    region_count: int,
    target_source_count: int,
    reference_source_count: int,
    target_grid_count: int,
    reference_grid_count: int,
    delta_grid_count: int,
    excluded_span_count: int,
) -> int:
    largest = max(
        target_grid_count,
        reference_grid_count,
        delta_grid_count,
        excluded_span_count,
    )
    levels = math.ceil(math.log2(largest + 1))
    return region_count * (
        40 * (target_source_count + reference_source_count)
        + 16
        * (
            target_grid_count
            + reference_grid_count
            + delta_grid_count
            + excluded_span_count
        )
        * levels
    )


def _paired_regions(
    model: TrackModel,
    analysis: Mapping[str, object],
) -> list[dict[str, object]]:
    raw_regions = analysis.get("regions")
    if not isinstance(raw_regions, Sequence) or isinstance(raw_regions, (str, bytes)):
        raise PairedRegionReportUnavailable("region_analysis_malformed")
    if len(raw_regions) != len(model.corners):
        raise PairedRegionReportUnavailable("region_analysis_model_mismatch")
    if len(raw_regions) > MAX_DIAGNOSTIC_DEBRIEF_REGIONS:
        raise PairedRegionReportUnavailable("diagnostic_region_debrief_region_limit_exceeded")
    paired: list[dict[str, object]] = []
    for definition, raw_region in zip(model.corners, raw_regions):
        if not isinstance(raw_region, Mapping):
            raise PairedRegionReportUnavailable("region_analysis_malformed")
        region = dict(raw_region)
        target = region.get("target")
        reference = region.get("reference")
        if not isinstance(target, Mapping) or not isinstance(reference, Mapping):
            raise PairedRegionReportUnavailable("region_analysis_malformed")
        region["diagnostic_only"] = True
        region["coaching_eligible"] = False
        region["ranking_eligible"] = False
        region.pop("differences", None)
        start_m, end_m = definition.start_distance_m + model.distance_origin_m, (
            definition.end_distance_m + model.distance_origin_m
        )
        region["configured_windows_m"] = {
            "analysis": [start_m, end_m],
            "braking_search": _shift_window(
                definition.braking_search_window_m, model.distance_origin_m
            ),
            "turn_in_search": _shift_window(
                definition.turn_in_search_window_m, model.distance_origin_m
            ),
            "throttle_pickup_search": _shift_window(
                definition.throttle_pickup_window_m, model.distance_origin_m
            ),
            "exit_distance_m": (
                definition.exit_distance_m + model.distance_origin_m
                if definition.exit_distance_m is not None
                else None
            ),
        }
        supported_differences = _supported_differences(
            definition,
            target,
            reference,
            region.get("delta_change"),
            model.distance_origin_m,
            start_m,
            end_m,
        )
        region["supported_differences"] = supported_differences
        region["debrief"] = build_diagnostic_region_debrief(
            supported_differences,
            region["configured_windows_m"],
        )
        paired.append(region)
    return paired


def _supported_differences(
    definition: CornerDefinition,
    target: Mapping[str, object],
    reference: Mapping[str, object],
    delta_change: object,
    origin_m: float,
    start_m: float,
    end_m: float,
) -> dict[str, dict[str, object]]:
    return {
        "connected_interval_time": _interval_difference(delta_change),
        "minimum_speed": _minimum_speed_difference(target, reference, start_m, end_m),
        "brake_10_percent_onset": _event_difference(
            target,
            reference,
            field="braking",
            channel="brake",
            threshold=0.1,
            search_window=_shift_window(definition.braking_search_window_m, origin_m),
        ),
        "throttle_50_percent_onset": _event_difference(
            target,
            reference,
            field="throttle_50",
            channel="throttle",
            threshold=0.5,
            search_window=_shift_window(definition.throttle_pickup_window_m, origin_m),
        ),
        "exit_speed": _exit_speed_difference(
            target,
            reference,
            definition.exit_distance_m + origin_m
            if definition.exit_distance_m is not None
            else None,
        ),
    }


def _interval_difference(raw: object) -> dict[str, object]:
    delta = _mapping(raw)
    if delta is None:
        return _unavailable("interval_delta_unavailable")
    entry = _finite_number(delta.get("entry_delta_s"))
    exit_value = _finite_number(delta.get("exit_delta_s"))
    change = _finite_number(delta.get("delta_change_s"))
    if delta.get("interval_connected_supported_time") is True and change is not None:
        return {
            "status": "supported",
            "value": change,
            "unit": "s",
            "direction": "target_minus_reference_end_delta_minus_start_delta",
            "entry_delta_s": entry,
            "exit_delta_s": exit_value,
            "support": dict(delta),
            "unavailable_reason": None,
        }
    return {
        **_unavailable(str(delta.get("unavailable_reason") or "interval_time_disconnected")),
        "entry_delta_s": entry,
        "exit_delta_s": exit_value,
        "support": dict(delta),
    }


def _minimum_speed_difference(
    target: Mapping[str, object],
    reference: Mapping[str, object],
    start_m: float,
    end_m: float,
) -> dict[str, object]:
    target_minimum = _mapping(target.get("minimum_speed"))
    reference_minimum = _mapping(reference.get("minimum_speed"))
    if target_minimum is None or reference_minimum is None:
        return _unavailable("minimum_speed_unavailable")
    for side, item in (("target", target_minimum), ("reference", reference_minimum)):
        if item.get("status") != "observed_minimum_complete_window":
            return _unavailable(f"{side}_minimum_speed_window_incomplete")
        coverage = _finite_number(item.get("supported_grid_coverage"))
        if coverage is None or not math.isclose(coverage, 1.0, rel_tol=0.0, abs_tol=1e-9):
            return _unavailable(f"{side}_minimum_speed_coverage_incomplete")
    target_value = _finite_number(target_minimum.get("speed_kph"))
    reference_value = _finite_number(reference_minimum.get("speed_kph"))
    target_anchor = _valid_source_anchor(target_minimum.get("source_anchor"), start_m, end_m)
    reference_anchor = _valid_source_anchor(
        reference_minimum.get("source_anchor"), start_m, end_m
    )
    if target_value is None or reference_value is None:
        return _unavailable("minimum_speed_value_unavailable")
    if target_anchor is None or reference_anchor is None:
        return _unavailable("minimum_speed_source_anchor_unavailable")
    return {
        "status": "supported",
        "target_value": target_value,
        "reference_value": reference_value,
        "value": target_value - reference_value,
        "unit": "km/h",
        "direction": "target_minus_reference",
        "target_anchor": target_anchor,
        "reference_anchor": reference_anchor,
        "unavailable_reason": None,
    }


def _event_difference(
    target: Mapping[str, object],
    reference: Mapping[str, object],
    *,
    field: str,
    channel: str,
    threshold: float,
    search_window: tuple[float, float] | None,
) -> dict[str, object]:
    if search_window is None:
        return _unavailable("search_window_unconfigured")
    event_fields = {
        "braking": field == "braking",
        "throttle": field == "throttle_50",
    }
    if not any(event_fields.values()):
        return _unavailable("event_field_unsupported")
    examples: dict[str, Mapping[str, object]] = {}
    for side, observation in (("target", target), ("reference", reference)):
        coverage = _mapping(observation.get("event_channel_coverage"))
        channel_coverage = _finite_number(coverage.get(channel)) if coverage else None
        if channel_coverage is None or not math.isclose(
            channel_coverage, 1.0, rel_tol=0.0, abs_tol=1e-9
        ):
            return _unavailable(f"{side}_{channel}_coverage_incomplete")
        if field == "braking":
            detection = _mapping(observation.get("braking"))
        else:
            throttle = _mapping(observation.get("throttle_pickup"))
            detection = _mapping(throttle.get("0.5")) if throttle else None
        reason = _event_support_reason(detection, channel, threshold, search_window)
        if reason is not None:
            return _unavailable(f"{side}_{reason}")
        assert detection is not None
        events = detection.get("events")
        assert isinstance(events, list) and isinstance(events[0], Mapping)
        examples[side] = events[0]

    target_event = examples["target"]
    reference_event = examples["reference"]
    target_bracket = _bracket(target_event.get("start_distance_bracket_m"), search_window)
    reference_bracket = _bracket(
        reference_event.get("start_distance_bracket_m"), search_window
    )
    if target_bracket is None or reference_bracket is None:
        return _unavailable("event_onset_bracket_unavailable")
    difference = (
        target_bracket[0] - reference_bracket[1],
        target_bracket[1] - reference_bracket[0],
    )
    return {
        "status": "supported",
        "channel": channel,
        "threshold": threshold,
        "target_start_bracket_m": list(target_bracket),
        "reference_start_bracket_m": list(reference_bracket),
        "target_minus_reference_start_bracket_m": list(difference),
        "right_censored": {
            "target": target_event.get("right_censored"),
            "reference": reference_event.get("right_censored"),
        },
        "unit": "m",
        "direction": "target_minus_reference",
        "unavailable_reason": None,
    }


def _event_support_reason(
    detection: Mapping[str, object] | None,
    channel: str,
    threshold: float,
    search_window: tuple[float, float],
) -> str | None:
    if detection is None or detection.get("status") != "detected":
        return "threshold_event_unavailable"
    events = detection.get("events")
    if (
        detection.get("event_count") != 1
        or detection.get("events_truncated") is not False
        or detection.get("rejected_short_event_count") != 0
        or detection.get("unsupported_break_count") != 0
        or not isinstance(events, list)
        or len(events) != 1
        or not isinstance(events[0], Mapping)
    ):
        return "threshold_event_not_unique_and_supported"
    event = events[0]
    onset = _finite_number(event.get("start_distance_m"))
    if (
        event.get("channel") != channel
        or not _close_number(event.get("threshold"), threshold)
        or event.get("left_censored") is not False
        or not isinstance(event.get("right_censored"), bool)
        or _finite_number(event.get("start_session_time_s")) is None
        or onset is None
        or not search_window[0] <= onset < search_window[1]
        or _bracket(event.get("start_distance_bracket_m"), search_window) is None
    ):
        return "threshold_event_onset_bracket_unavailable"
    return None


def _exit_speed_difference(
    target: Mapping[str, object],
    reference: Mapping[str, object],
    distance_m: float | None,
) -> dict[str, object]:
    if distance_m is None:
        return _unavailable("exit_distance_unconfigured")
    values: dict[str, float] = {}
    for side, observation in (("target", target), ("reference", reference)):
        exits = observation.get("exit_speeds")
        if not isinstance(exits, list):
            return _unavailable(f"{side}_exit_speed_unavailable")
        match = next(
            (
                item
                for item in exits
                if isinstance(item, Mapping)
                and item.get("offset_m") == 0.0
                and item.get("distance_m") == distance_m
            ),
            None,
        )
        if match is None or match.get("status") != "supported":
            return _unavailable(f"{side}_exit_speed_unsupported_at_configured_anchor")
        value = _finite_number(match.get("speed_kph"))
        if value is None:
            return _unavailable(f"{side}_exit_speed_unavailable")
        values[side] = value
    return {
        "status": "supported",
        "distance_m": distance_m,
        "target_value": values["target"],
        "reference_value": values["reference"],
        "value": values["target"] - values["reference"],
        "unit": "km/h",
        "direction": "target_minus_reference",
        "unavailable_reason": None,
    }


def _valid_source_anchor(
    value: object, start_m: float, end_m: float
) -> dict[str, object] | None:
    anchor = _mapping(value)
    if anchor is None:
        return None
    frame = anchor.get("frame_identifier")
    session_time = _finite_number(anchor.get("session_time_s"))
    distance = _finite_number(anchor.get("lap_distance_m"))
    if (
        isinstance(frame, bool)
        or not isinstance(frame, int)
        or session_time is None
        or session_time < 0
        or distance is None
        or not start_m <= distance < end_m
    ):
        return None
    return {
        "frame_identifier": frame,
        "session_time_s": session_time,
        "lap_distance_m": distance,
    }


def _bracket(value: object, window: tuple[float, float]) -> tuple[float, float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        return None
    lower = _finite_number(value[0])
    upper = _finite_number(value[1])
    if (
        lower is None
        or upper is None
        or lower > upper
        or lower < window[0]
        or upper > window[1]
    ):
        return None
    return lower, upper


def _shift_window(
    value: tuple[float, float] | None, offset_m: float
) -> tuple[float, float] | None:
    if value is None:
        return None
    return (value[0] + offset_m, value[1] + offset_m)


def _unavailable(reason: str) -> dict[str, object]:
    return {
        "status": "unavailable",
        "value": None,
        "unavailable_reason": reason,
    }


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _close_number(value: object, expected: float) -> bool:
    number = _finite_number(value)
    return number is not None and math.isclose(number, expected, rel_tol=0.0, abs_tol=1e-9)
