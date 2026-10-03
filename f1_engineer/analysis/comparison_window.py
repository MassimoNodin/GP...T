from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .comparison import DeltaTime
from .distance_support import requested_window_coverage
from .events import (
    EventChannel,
    bounded_threshold_event_examples,
    detect_sustained_threshold_events,
)
from .interval_delta import (
    evaluate_interval_delta,
    has_connected_source_session_time,
)
from .resampling import ExcludedSpan, ResampledTrace, ResamplingConfig, TraceSample


COMPARISON_WINDOW_VERSION = "comparison-distance-window-v1"
WINDOW_EVENT_MINIMUM_DURATION_S = 0.1
WINDOW_EVENT_EXAMPLE_LIMIT = 20
WINDOW_EVENT_THRESHOLDS: tuple[tuple[str, EventChannel, float, bool], ...] = (
    ("brake_10_percent", "brake", 0.1, False),
    ("steering_absolute_15_percent", "steering", 0.15, True),
    ("throttle_10_percent", "throttle", 0.1, False),
    ("throttle_50_percent", "throttle", 0.5, False),
    ("throttle_90_percent", "throttle", 0.9, False),
    ("throttle_99_percent", "throttle", 0.99, False),
)
WINDOW_COVERAGE_CHANNELS = ("time_s", "speed_mps", "brake", "steering", "throttle")
WINDOW_EXCLUDED_SPAN_EXAMPLE_LIMIT = 20


@dataclass(frozen=True, slots=True)
class DistanceWindow:
    start_m: float
    end_m: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.start_m, bool)
            or not isinstance(self.start_m, (int, float))
            or isinstance(self.end_m, bool)
            or not isinstance(self.end_m, (int, float))
            or not math.isfinite(self.start_m)
            or not math.isfinite(self.end_m)
            or self.start_m < 0
            or self.start_m >= self.end_m
        ):
            raise ValueError("comparison_window_invalid_bounds")

    def validate_track_length(self, track_length_m: float) -> None:
        if (
            isinstance(track_length_m, bool)
            or not isinstance(track_length_m, (int, float))
            or not math.isfinite(track_length_m)
            or track_length_m <= 0
            or self.end_m > track_length_m
        ):
            raise ValueError("comparison_window_exceeds_track_length")

    def to_dict(self) -> dict[str, float]:
        return {"start_m": float(self.start_m), "end_m": float(self.end_m)}


def optional_distance_window(
    start_m: float | str | None,
    end_m: float | str | None,
) -> DistanceWindow | None:
    start_missing = start_m is None or (
        isinstance(start_m, str) and not start_m.strip()
    )
    end_missing = end_m is None or (isinstance(end_m, str) and not end_m.strip())
    if start_missing and end_missing:
        return None
    if start_missing != end_missing:
        raise ValueError("comparison_window_bounds_must_be_selected_together")
    try:
        return DistanceWindow(float(start_m), float(end_m))
    except (TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, ValueError) and str(exc) == "comparison_window_invalid_bounds":
            raise
        raise ValueError("comparison_window_invalid_bounds") from exc


def analyze_comparison_window(
    target_samples: Sequence[TraceSample],
    reference_samples: Sequence[TraceSample],
    target_resampled: ResampledTrace,
    reference_resampled: ResampledTrace,
    delta: DeltaTime,
    window: DistanceWindow,
    *,
    config: ResamplingConfig,
) -> dict[str, object]:
    start_m, end_m = window.start_m, window.end_m
    target = _attempt_window_summary(target_samples, target_resampled, window, config)
    reference = _attempt_window_summary(
        reference_samples, reference_resampled, window, config
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
    boundary_start = _delta_boundary(start_m, interval.start_delta_s)
    boundary_end = _delta_boundary(end_m, interval.end_delta_s)
    delta_status = (
        "unsupported_interior"
        if interval.status == "unsupported_source_chronology"
        else interval.status
    )
    return {
        "schema_version": 1,
        "artifact_kind": "comparison_distance_window",
        "analysis_version": COMPARISON_WINDOW_VERSION,
        "diagnostic_only": True,
        "interval_convention": "[start_m, end_m)",
        "window_m": window.to_dict(),
        "config": {
            "grid_step_m": config.grid_step_m,
            "max_bracket_time_s": config.max_bracket_time_s,
            "max_bracket_distance_m": config.max_bracket_distance_m,
            "event_minimum_duration_s": WINDOW_EVENT_MINIMUM_DURATION_S,
            "event_example_limit_per_threshold_per_attempt": WINDOW_EVENT_EXAMPLE_LIMIT,
            "excluded_span_example_limit_per_attempt": WINDOW_EXCLUDED_SPAN_EXAMPLE_LIMIT,
        },
        "event_thresholds": {
            name: {"channel": channel, "threshold": threshold, "absolute": absolute}
            for name, channel, threshold, absolute in WINDOW_EVENT_THRESHOLDS
        },
        "target": target,
        "reference": reference,
        "delta": {
            "status": delta_status,
            "start_boundary": boundary_start,
            "end_boundary": boundary_end,
            "target_time_coverage": interval.target_time_coverage,
            "reference_time_coverage": interval.reference_time_coverage,
            "shared_time_coverage": interval.shared_time_coverage,
            "target_resampled_time_connected": interval.target_resampled_time_connected,
            "reference_resampled_time_connected": interval.reference_resampled_time_connected,
            "shared_delta_time_connected": interval.shared_delta_time_connected,
            "target_source_session_time_connected": interval.target_source_session_time_connected,
            "reference_source_session_time_connected": interval.reference_source_session_time_connected,
            "interval_connected_supported_time": interval.interval_connected_supported_time,
            "delta_change_s": interval.delta_change_s,
            "direction": "target_minus_reference_end_delta_minus_start_delta",
            "unavailable_reason": interval.unavailable_reason,
            "unavailable_reasons": list(interval.unavailable_reasons),
        },
    }


def _attempt_window_summary(
    samples: Sequence[TraceSample],
    resampled: ResampledTrace,
    window: DistanceWindow,
    config: ResamplingConfig,
) -> dict[str, object]:
    in_window = [
        sample
        for sample in samples
        if sample.distance_m is not None
        and window.start_m <= sample.distance_m < window.end_m
    ]
    speeds = [
        sample
        for sample in in_window
        if sample.speed_mps is not None and math.isfinite(sample.speed_mps)
    ]
    brakes = [
        sample
        for sample in in_window
        if sample.brake is not None and math.isfinite(sample.brake)
    ]
    minimum_speed = min(speeds, key=lambda sample: sample.speed_mps or 0.0) if speeds else None
    peak_brake = max(brakes, key=lambda sample: sample.brake or 0.0) if brakes else None
    events: dict[str, object] = {}
    for name, channel, threshold, absolute in WINDOW_EVENT_THRESHOLDS:
        detection = detect_sustained_threshold_events(
            list(samples),
            channel=channel,
            threshold=threshold,
            search_window_m=(window.start_m, window.end_m),
            minimum_duration_s=WINDOW_EVENT_MINIMUM_DURATION_S,
            max_gap_time_s=config.max_bracket_time_s,
            max_gap_distance_m=config.max_bracket_distance_m,
            absolute=absolute,
        )
        examples, event_count, truncated = bounded_threshold_event_examples(
            detection, WINDOW_EVENT_EXAMPLE_LIMIT
        )
        events[name] = {
            "status": detection.status,
            "threshold": threshold,
            "events": examples,
            "event_count": event_count,
            "events_truncated": truncated,
            "left_censored_event_count": sum(
                event.left_censored for event in detection.sustained_events
            ),
            "right_censored_event_count": sum(
                event.right_censored for event in detection.sustained_events
            ),
            "rejected_short_event_count": detection.rejected_short_event_count,
            "unsupported_break_count": detection.unsupported_break_count,
        }
    excluded_spans = [
        span
        for span in resampled.excluded_spans
        if span.end_distance_m > window.start_m
        and span.start_distance_m < window.end_m
    ]
    span_examples = [
        span.to_dict()
        for span in excluded_spans[:WINDOW_EXCLUDED_SPAN_EXAMPLE_LIMIT]
    ]
    return {
        "source_sample_count": len(in_window),
        "speed_sample_count": len(speeds),
        "brake_sample_count": len(brakes),
        "minimum_speed": (
            {
                "status": "observed",
                "speed_kph": float(minimum_speed.speed_mps) * 3.6,
                "anchor": _source_anchor(minimum_speed),
            }
            if minimum_speed is not None
            else {"status": "unavailable_no_speed_samples", "speed_kph": None, "anchor": None}
        ),
        "peak_brake": (
            {
                "status": "observed",
                "value": float(peak_brake.brake),
                "anchor": _source_anchor(peak_brake),
            }
            if peak_brake is not None
            else {"status": "unavailable_no_brake_samples", "value": None, "anchor": None}
        ),
        "coverage": {
            channel: requested_window_coverage(
                resampled,
                window.start_m,
                window.end_m,
                config.grid_step_m,
                channel=channel,
            )
            for channel in WINDOW_COVERAGE_CHANNELS
        },
        "threshold_events": events,
        "excluded_spans": {
            "count": len(excluded_spans),
            "examples": span_examples,
            "truncated": len(span_examples) < len(excluded_spans),
        },
    }


def _source_anchor(sample: TraceSample) -> dict[str, object | None]:
    return {
        "frame_identifier": sample.frame_identifier,
        "session_time_s": sample.session_time_s,
        "lap_distance_m": sample.distance_m,
    }


def _delta_boundary(distance_m: float, value_s: float | None) -> dict[str, object]:
    return {
        "status": "supported" if value_s is not None else "unsupported",
        "distance_m": distance_m,
        "target_minus_reference_s": value_s,
    }
