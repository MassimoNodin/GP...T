from __future__ import annotations

import bisect
import math
from typing import Sequence

from ..tracks.model import CornerDefinition, TrackModel
from .comparison import DeltaTime
from .events import ThresholdDetection, detect_sustained_threshold_events
from .resampling import ExcludedSpan, ResampledTrace, ResamplingConfig, TraceSample


CORNER_ANALYSIS_VERSION = "distance-regions-v1"
_EVENT_THRESHOLDS = {
    "brake": 0.1,
    "steering_abs": 0.15,
    "throttle_10": 0.1,
    "throttle_50": 0.5,
    "throttle_90": 0.9,
    "throttle_99": 0.99,
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
) -> dict[str, object]:
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
            )
        )
    return {
        "analysis_version": CORNER_ANALYSIS_VERSION,
        "model": {
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
        },
        "layout_validation_status": "explicit_model_selected; layout absent from session packet",
        "attempts_reference_eligible": {
            "target": target_reference_eligible,
            "reference": reference_reference_eligible,
        },
        "diagnostic_only": diagnostic_only,
        "regions": regions,
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
    )
    entry_delta = _grid_value(
        target_resampled.distance_m,
        delta.values_s,
        delta.mask,
        start_m,
        step_m=config.grid_step_m,
        channel="time_s",
        excluded_spans=(
            *target_resampled.excluded_spans,
            *reference_resampled.excluded_spans,
        ),
    )
    exit_delta = _grid_value(
        target_resampled.distance_m,
        delta.values_s,
        delta.mask,
        end_m,
        step_m=config.grid_step_m,
        channel="time_s",
        excluded_spans=(
            *target_resampled.excluded_spans,
            *reference_resampled.excluded_spans,
        ),
    )
    delta_change = None
    delta_status = "unsupported_entry_or_exit"
    if entry_delta is not None and exit_delta is not None:
        delta_change = exit_delta - entry_delta
        delta_status = "diagnostic_region_delta_change"
        if model.validation_status == "validated" and not diagnostic_only:
            delta_status = "supported_region_delta_change"
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
            "entry_delta_s": entry_delta,
            "exit_delta_s": exit_delta,
            "delta_change_s": delta_change,
            "direction": "target_minus_reference_delta_at_exit_minus_entry",
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
    turn_in = _onset(turn_in_detection, "steering_onset_proxy")
    turn_in["interpretation"] = (
        "absolute steering threshold; calibrated track-relative geometry unavailable"
    )

    return {
        "braking": _onset(brake_detection, "brake_onset"),
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
            threshold: _onset(detection, f"throttle_{threshold}_onset")
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
        minimum_duration_s=0.1,
        max_gap_time_s=config.max_bracket_time_s,
        max_gap_distance_m=config.max_bracket_distance_m,
        absolute=absolute,
    )


def _onset(detection: ThresholdDetection, event_name: str) -> dict[str, object]:
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
            "events": [candidate.to_dict() for candidate in detection.sustained_events],
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
        "events": [candidate.to_dict() for candidate in detection.sustained_events],
        "rejected_short_event_count": detection.rejected_short_event_count,
        "unsupported_break_count": detection.unsupported_break_count,
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


def _grid_value(
    grid: Sequence[float],
    values: Sequence[float | int | bool | None],
    mask: Sequence[bool],
    distance_m: float,
    *,
    step_m: float = 1.0,
    channel: str | None = None,
    excluded_spans: Sequence[ExcludedSpan] = (),
) -> float | None:
    if not grid or not math.isfinite(distance_m):
        return None
    position = bisect.bisect_left(grid, distance_m)
    if position < len(grid) and math.isclose(grid[position], distance_m, abs_tol=1e-7):
        if mask[position] and values[position] is not None:
            return float(values[position])
        return None
    if position == 0 or position >= len(grid):
        return None
    left = position - 1
    right = position
    if (
        not mask[left]
        or not mask[right]
        or values[left] is None
        or values[right] is None
        or not math.isclose(grid[right] - grid[left], step_m, abs_tol=1e-7)
    ):
        return None
    for span in excluded_spans:
        if span.channel is not None and span.channel != channel:
            continue
        if span.reason == "stationary_clock" and channel != "time_s":
            continue
        if (
            span.start_distance_m <= grid[right] + 1e-7
            and span.end_distance_m >= grid[left] - 1e-7
        ):
            return None
    ratio = (distance_m - grid[left]) / (grid[right] - grid[left])
    return float(values[left]) + (float(values[right]) - float(values[left])) * ratio


def _requested_window_coverage(
    resampled: ResampledTrace,
    start_m: float,
    end_m: float,
    step_m: float,
    *,
    channel: str,
) -> float:
    first_index = math.ceil((start_m - 1e-7) / step_m)
    stop_index = math.ceil((end_m - 1e-7) / step_m)
    expected = range(first_index, stop_index)
    expected_count = len(expected)
    if expected_count == 0:
        return 0.0
    supported_count = 0
    for grid_index in expected:
        distance = grid_index * step_m
        position = bisect.bisect_left(resampled.distance_m, distance)
        if (
            position < len(resampled.distance_m)
            and math.isclose(resampled.distance_m[position], distance, abs_tol=1e-7)
            and resampled.masks[channel][position]
        ):
            supported_count += 1
    grid_coverage = supported_count / expected_count
    excluded_intervals = sorted(
        (
            max(start_m, span.start_distance_m),
            min(end_m, span.end_distance_m),
        )
        for span in resampled.excluded_spans
        if (span.channel is None or span.channel == channel)
        and not (span.reason == "stationary_clock" and channel != "time_s")
        and span.end_distance_m > start_m
        and span.start_distance_m < end_m
    )
    supported_positions = [
        index
        for index, distance in enumerate(resampled.distance_m)
        if start_m - 1e-7 <= distance <= end_m + 1e-7
        and resampled.masks[channel][index]
        and resampled.values[channel][index] is not None
    ]
    start_supported = _grid_value(
        resampled.distance_m,
        resampled.values[channel],
        resampled.masks[channel],
        start_m,
        step_m=step_m,
        channel=channel,
        excluded_spans=resampled.excluded_spans,
    ) is not None
    end_supported = _grid_value(
        resampled.distance_m,
        resampled.values[channel],
        resampled.masks[channel],
        end_m,
        step_m=step_m,
        channel=channel,
        excluded_spans=resampled.excluded_spans,
    ) is not None
    if not supported_positions:
        excluded_intervals.append((start_m, end_m))
    else:
        if not start_supported:
            first_supported_m = resampled.distance_m[supported_positions[0]]
            if first_supported_m > start_m:
                excluded_intervals.append((start_m, min(first_supported_m, end_m)))
        if not end_supported:
            last_supported_m = resampled.distance_m[supported_positions[-1]]
            if last_supported_m < end_m:
                excluded_intervals.append((max(last_supported_m, start_m), end_m))
    excluded_intervals.sort()
    excluded_length = 0.0
    current_start: float | None = None
    current_end: float | None = None
    for interval_start, interval_end in excluded_intervals:
        if current_start is None:
            current_start, current_end = interval_start, interval_end
        elif current_end is not None and interval_start <= current_end:
            current_end = max(current_end, interval_end)
        else:
            assert current_end is not None
            excluded_length += current_end - current_start
            current_start, current_end = interval_start, interval_end
    if current_start is not None and current_end is not None:
        excluded_length += current_end - current_start
    distance_coverage = max(0.0, 1.0 - excluded_length / (end_m - start_m))
    return min(grid_coverage, distance_coverage)


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
