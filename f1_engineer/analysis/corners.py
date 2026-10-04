from __future__ import annotations

import math
from typing import Sequence

from ..tracks.model import CornerDefinition, MAX_TRACK_MODEL_REGIONS, TrackModel
from .comparison import DeltaTime
from .distance_support import (
    requested_window_coverage as _requested_window_coverage,
    sample_at_distance as _grid_value,
)
from .events import (
    ThresholdDetection,
    bounded_threshold_event_examples,
    detect_sustained_threshold_events,
)
from .interval_delta import evaluate_interval_delta
from .resampling import ResampledTrace, ResamplingConfig, TraceSample


CORNER_ANALYSIS_VERSION = "distance-regions-v2"
REGION_EVENT_EXAMPLE_LIMIT = 20
_EVENT_MINIMUM_DURATION_S = 0.1
_EVENT_THRESHOLDS = {
    "brake": 0.1,
    "steering_abs": 0.15,
    "throttle_10": 0.1,
    "throttle_50": 0.5,
    "throttle_90": 0.9,
    "throttle_99": 0.99,
}


def analyze_attempt_regions(
    samples: Sequence[TraceSample],
    resampled: ResampledTrace,
    track_model: TrackModel,
    *,
    config: ResamplingConfig,
    event_example_limit: int = REGION_EVENT_EXAMPLE_LIMIT,
) -> dict[str, object]:
    """Report observations in configured distance windows for one attempt."""
    if len(track_model.corners) > MAX_TRACK_MODEL_REGIONS:
        raise ValueError("region_count_limit_exceeded")
    if event_example_limit < 0:
        raise ValueError("event_example_limit must not be negative")
    regions = []
    for definition in track_model.corners:
        start_m = definition.start_distance_m + track_model.distance_origin_m
        end_m = definition.end_distance_m + track_model.distance_origin_m
        regions.append(
            {
                "identifier": definition.identifier,
                "label": definition.label,
                "analysis_window_m": [start_m, end_m],
                "definition_validation_status": track_model.validation_status,
                "direction": definition.direction,
                "complex_id": definition.complex_id,
                "observations": _analyze_attempt_region(
                    samples,
                    resampled,
                    definition,
                    start_m,
                    end_m,
                    config,
                    track_model.validation_status,
                    track_model.distance_origin_m,
                    track_model.track_length_m,
                    event_example_limit=event_example_limit,
                ),
            }
        )
    return {
        "analysis_version": CORNER_ANALYSIS_VERSION,
        "model": _model_summary(track_model),
        "layout_validation_status": "explicit_model_selected; layout absent from session packet",
        "diagnostic_only": True,
        "event_thresholds": {
            **_EVENT_THRESHOLDS,
            "minimum_duration_s": _EVENT_MINIMUM_DURATION_S,
            "maximum_gap_time_s": config.max_bracket_time_s,
            "maximum_gap_distance_m": config.max_bracket_distance_m,
        },
        "event_example_limit_per_channel_region": event_example_limit,
        "regions": regions,
    }


def analyze_corner_regions(
    target_samples: Sequence[TraceSample],
    reference_samples: Sequence[TraceSample],
    target_resampled: ResampledTrace,
    reference_resampled: ResampledTrace,
    delta: DeltaTime,
    track_model: TrackModel,
    *,
    config: ResamplingConfig,
    target_reference_eligible: bool,
    reference_reference_eligible: bool,
    event_example_limit: int | None = None,
) -> dict[str, object]:
    if len(track_model.corners) > MAX_TRACK_MODEL_REGIONS:
        raise ValueError("region_count_limit_exceeded")
    if event_example_limit is not None and event_example_limit < 0:
        raise ValueError("event_example_limit must not be negative")
    attempts_eligible = target_reference_eligible and reference_reference_eligible
    diagnostic_only = (
        track_model.validation_status != "validated" or not attempts_eligible
    )
    regions = []
    for definition in track_model.corners:
        start_m = definition.start_distance_m + track_model.distance_origin_m
        end_m = definition.end_distance_m + track_model.distance_origin_m
        regions.append(
            _compare_region(
                definition,
                track_model,
                start_m,
                end_m,
                target_samples,
                reference_samples,
                target_resampled,
                reference_resampled,
                delta,
                config,
                diagnostic_only=diagnostic_only,
                event_example_limit=event_example_limit,
            )
        )
    return {
        "analysis_version": CORNER_ANALYSIS_VERSION,
        "model": _model_summary(track_model),
        "layout_validation_status": "explicit_model_selected; layout absent from session packet",
        "attempts_reference_eligible": {
            "target": target_reference_eligible,
            "reference": reference_reference_eligible,
        },
        "diagnostic_only": diagnostic_only,
        "regions": regions,
    }


def _model_summary(track_model: TrackModel) -> dict[str, object]:
    return {
        "model_id": track_model.model_id,
        "revision": track_model.revision,
        "packet_format": track_model.packet_format,
        "track_id": track_model.track_id,
        "track_name": track_model.track_name,
        "layout_id": track_model.layout_id,
        "track_length_m": track_model.track_length_m,
        "distance_origin_m": track_model.distance_origin_m,
        "validation_status": track_model.validation_status,
        "provenance": track_model.provenance,
    }


def _compare_region(
    definition: CornerDefinition,
    model: TrackModel,
    start_m: float,
    end_m: float,
    target_samples: Sequence[TraceSample],
    reference_samples: Sequence[TraceSample],
    target_resampled: ResampledTrace,
    reference_resampled: ResampledTrace,
    delta: DeltaTime,
    config: ResamplingConfig,
    *,
    diagnostic_only: bool,
    event_example_limit: int | None = None,
) -> dict[str, object]:
    target = _analyze_attempt_region(
        target_samples,
        target_resampled,
        definition,
        start_m,
        end_m,
        config,
        model.validation_status,
        model.distance_origin_m,
        model.track_length_m,
        event_example_limit=event_example_limit,
    )
    reference = _analyze_attempt_region(
        reference_samples,
        reference_resampled,
        definition,
        start_m,
        end_m,
        config,
        model.validation_status,
        model.distance_origin_m,
        model.track_length_m,
        event_example_limit=event_example_limit,
    )
    interval = evaluate_interval_delta(
        target_samples,
        reference_samples,
        target_resampled,
        reference_resampled,
        delta,
        start_m,
        end_m,
        config=config,
    )
    delta_change = interval.delta_change_s
    if interval.status == "supported":
        delta_status = "diagnostic_region_delta_change"
        if model.validation_status == "validated" and not diagnostic_only:
            delta_status = "supported_region_delta_change"
    elif interval.status == "unsupported_boundary":
        delta_status = "unsupported_entry_or_exit"
    elif interval.status == "unsupported_source_chronology":
        delta_status = "unsupported_source_chronology"
    else:
        delta_status = "unsupported_interior"
    target_minimum = target["minimum_speed"]
    reference_minimum = reference["minimum_speed"]
    target_exit_speeds = target["exit_speeds"]
    reference_exit_speeds = reference["exit_speeds"]
    assert isinstance(target_minimum, dict) and isinstance(reference_minimum, dict)
    assert isinstance(target_exit_speeds, list) and isinstance(reference_exit_speeds, list)
    differences: dict[str, object] = {
        "minimum_speed_kph": _difference(
            target_minimum.get("speed_kph"), reference_minimum.get("speed_kph")
        ),
        "region_delta_change_s": delta_change,
    }
    differences["exit_speed_kph"] = [
        {
            "offset_m": target_item["offset_m"],
            "target_minus_reference": _difference(
                target_item.get("speed_kph"), reference_item.get("speed_kph")
            ),
        }
        for target_item, reference_item in zip(target_exit_speeds, reference_exit_speeds)
    ]
    differences["braking_onset_distance_m"] = _event_distance_difference(
        target.get("braking"), reference.get("braking")
    )
    differences["throttle_50_distance_m"] = _event_distance_difference(
        target.get("throttle_pickup", {}).get("0.5"),
        reference.get("throttle_pickup", {}).get("0.5"),
    )
    return {
        "identifier": definition.identifier,
        "label": definition.label,
        "analysis_window_m": [start_m, end_m],
        "definition_validation_status": model.validation_status,
        "direction": definition.direction,
        "complex_id": definition.complex_id,
        "diagnostic_only": diagnostic_only,
        "target": target,
        "reference": reference,
        "differences": differences,
        "delta_change": {
            "status": delta_status,
            "entry_delta_s": interval.start_delta_s,
            "exit_delta_s": interval.end_delta_s,
            "delta_change_s": delta_change,
            "direction": "target_minus_reference_delta_at_exit_minus_entry",
            "target_time_coverage": interval.target_time_coverage,
            "reference_time_coverage": interval.reference_time_coverage,
            "shared_time_coverage": interval.shared_time_coverage,
            "target_resampled_time_connected": interval.target_resampled_time_connected,
            "reference_resampled_time_connected": interval.reference_resampled_time_connected,
            "shared_delta_time_connected": interval.shared_delta_time_connected,
            "target_source_session_time_connected": interval.target_source_session_time_connected,
            "reference_source_session_time_connected": interval.reference_source_session_time_connected,
            "interval_connected_supported_time": interval.interval_connected_supported_time,
            "unavailable_reason": interval.unavailable_reason,
            "unavailable_reasons": list(interval.unavailable_reasons),
        },
    }


def _analyze_attempt_region(
    samples: Sequence[TraceSample],
    resampled: ResampledTrace,
    definition: CornerDefinition,
    start_m: float,
    end_m: float,
    config: ResamplingConfig,
    model_validation_status: str,
    distance_origin_m: float,
    track_length_m: float,
    *,
    event_example_limit: int | None = None,
) -> dict[str, object]:
    region_samples = [
        sample
        for sample in samples
        if sample.distance_m is not None and start_m <= sample.distance_m < end_m
    ]
    speed_coverage = _requested_window_coverage(
        resampled,
        start_m,
        end_m,
        config.grid_step_m,
        channel="speed_mps",
    )
    speed_samples = [
        sample
        for sample in region_samples
        if sample.speed_mps is not None and math.isfinite(sample.speed_mps)
    ]
    if not speed_samples:
        minimum_speed: dict[str, object] = {
            "status": "unavailable_no_speed_samples",
            "speed_kph": None,
            "distance_m": None,
            "supported_grid_coverage": speed_coverage,
        }
    else:
        observed_minimum = min(speed_samples, key=lambda sample: sample.speed_mps or 0.0)
        minimum_speed = {
            "status": "observed_minimum_complete_window"
            if speed_coverage >= 1.0
            else "observed_minimum_partial_window",
            "speed_kph": float(observed_minimum.speed_mps) * 3.6,
            "distance_m": observed_minimum.distance_m,
            "source_anchor": {
                "frame_identifier": observed_minimum.frame_identifier,
                "session_time_s": observed_minimum.session_time_s,
                "lap_distance_m": observed_minimum.distance_m,
            },
            "supported_grid_coverage": speed_coverage,
            "observed_sample_count": len(speed_samples),
        }

    brake_detection = _detect(
        samples,
        "brake",
        _EVENT_THRESHOLDS["brake"],
        _shift_window(definition.braking_search_window_m, distance_origin_m)
        if definition.braking_search_window_m is not None
        else None,
        config,
    )
    turn_in_detection = _detect(
        samples,
        "steering",
        _EVENT_THRESHOLDS["steering_abs"],
        _shift_window(definition.turn_in_search_window_m, distance_origin_m)
        if definition.turn_in_search_window_m is not None
        else None,
        config,
        absolute=True,
    )
    throttle_detections = {
        str(threshold): _detect(
            samples,
            "throttle",
            threshold,
            _shift_window(definition.throttle_pickup_window_m, distance_origin_m)
            if definition.throttle_pickup_window_m is not None
            else None,
            config,
        )
        for threshold in (0.1, 0.5, 0.9, 0.99)
    }

    apex = (
        {
            "status": "unconfigured",
            "distance_m": None,
            "definition_validation_status": model_validation_status,
        }
        if definition.nominal_apex_m is None
        else {
            "status": "draft_metadata_anchor"
            if model_validation_status == "draft"
            else "metadata_anchor",
            "distance_m": definition.nominal_apex_m + distance_origin_m,
            "definition_validation_status": model_validation_status,
        }
    )
    turn_in = _onset(
        turn_in_detection,
        "steering_onset_proxy",
        event_example_limit=event_example_limit,
    )
    turn_in["interpretation"] = (
        "absolute steering threshold; calibrated track-relative geometry unavailable"
    )

    return {
        "braking": _onset(
            brake_detection, "brake_onset", event_example_limit=event_example_limit
        ),
        "minimum_speed": minimum_speed,
        "turn_in_proxy": turn_in,
        "track_apex": apex,
        "driver_apex": {
            "status": "unavailable",
            "reason": "validated_track_relative_geometry_unavailable",
        },
        "event_channel_coverage": {
            channel: _requested_window_coverage(
                resampled,
                start_m,
                end_m,
                config.grid_step_m,
                channel=channel,
            )
            for channel in ("brake", "steering", "throttle")
        },
        "throttle_pickup": {
            threshold: _onset(
                detection,
                f"throttle_{threshold}_onset",
                event_example_limit=event_example_limit,
            )
            for threshold, detection in throttle_detections.items()
        },
        "exit_speeds": _exit_speeds(
            definition.exit_distance_m + distance_origin_m
            if definition.exit_distance_m is not None
            else None,
            resampled,
            config.grid_step_m,
            track_length_m=track_length_m,
        ),
    }


def _detect(
    samples: Sequence[TraceSample],
    channel: str,
    threshold: float,
    window: tuple[float, float] | None,
    config: ResamplingConfig,
    *,
    absolute: bool = False,
) -> ThresholdDetection:
    if window is None:
        return ThresholdDetection("unconfigured", (), 0, 0)
    return detect_sustained_threshold_events(
        list(samples),
        channel=channel,  # type: ignore[arg-type]
        threshold=threshold,
        search_window_m=window,
        minimum_duration_s=_EVENT_MINIMUM_DURATION_S,
        max_gap_time_s=config.max_bracket_time_s,
        max_gap_distance_m=config.max_bracket_distance_m,
        absolute=absolute,
    )


def _onset(
    detection: ThresholdDetection,
    event_name: str,
    *,
    event_example_limit: int | None = None,
) -> dict[str, object]:
    limit = (
        len(detection.sustained_events)
        if event_example_limit is None
        else event_example_limit
    )
    examples, event_count, events_truncated = bounded_threshold_event_examples(
        detection, limit
    )
    event_summary = {
        "events": examples,
        "event_count": event_count,
        "events_truncated": events_truncated,
        "rejected_short_event_count": detection.rejected_short_event_count,
        "unsupported_break_count": detection.unsupported_break_count,
    }
    event = next(
        (candidate for candidate in detection.sustained_events if not candidate.left_censored),
        None,
    )
    if event is not None:
        return {
            "status": "detected",
            "event_name": event_name,
            "distance_m": event.start_distance_m,
            "distance_bracket_m": list(event.start_distance_bracket_m)
            if event.start_distance_bracket_m is not None
            else None,
            "speed_mps": event.start_speed_mps,
            "duration_s": event.duration_s,
            "peak_value": event.peak_value,
            **event_summary,
        }
    status = detection.status
    reason = (
        "threshold_active_at_search_window_entrance"
        if status == "left_censored"
        else "no_sustained_threshold_event"
        if status == "no_sustained_event"
        else status
    )
    return {
        "status": status,
        "event_name": event_name,
        "distance_m": None,
        "distance_bracket_m": None,
        "reason": reason,
        **event_summary,
    }


def _exit_speeds(
    exit_distance_m: float | None,
    resampled: ResampledTrace,
    step_m: float,
    *,
    track_length_m: float | None,
) -> list[dict[str, object]]:
    if exit_distance_m is None:
        return []
    exit_distance = exit_distance_m
    values: list[dict[str, object]] = []
    for offset_m in (0.0, 25.0, 50.0, 100.0):
        distance = exit_distance + offset_m
        if track_length_m is not None and distance >= track_length_m:
            values.append({"offset_m": offset_m, "distance_m": distance, "status": "outside_track", "speed_kph": None})
            continue
        speed = _grid_value(
            resampled.distance_m,
            resampled.values["speed_mps"],
            resampled.masks["speed_mps"],
            distance,
            step_m=step_m,
            channel="speed_mps",
            excluded_spans=resampled.excluded_spans,
        )
        values.append(
            {
                "offset_m": offset_m,
                "distance_m": distance,
                "status": "supported" if speed is not None else "unsupported_or_outside_observed_range",
                "speed_kph": speed * 3.6 if speed is not None else None,
            }
        )
    return values


def _difference(target: object, reference: object) -> float | None:
    if isinstance(target, (int, float)) and isinstance(reference, (int, float)):
        return float(target) - float(reference)
    return None


def _event_distance_difference(target: object, reference: object) -> float | None:
    if not isinstance(target, dict) or not isinstance(reference, dict):
        return None
    return _difference(target.get("distance_m"), reference.get("distance_m"))


def _shift_window(
    window: tuple[float, float] | None, offset_m: float
) -> tuple[float, float] | None:
    if window is None:
        return None
    return window[0] + offset_m, window[1] + offset_m
