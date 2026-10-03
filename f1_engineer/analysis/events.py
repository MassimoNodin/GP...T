from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .continuity import float32_ulp, session_time_discontinuity
from .resampling import TraceSample


EventChannel = Literal["brake", "throttle", "steering"]


@dataclass(frozen=True, slots=True)
class ThresholdEvent:
    channel: EventChannel
    threshold: float
    start_distance_m: float
    start_distance_bracket_m: tuple[float, float] | None
    end_distance_m: float
    end_distance_bracket_m: tuple[float, float] | None
    start_session_time_s: float
    end_session_time_s: float
    duration_s: float
    peak_value: float
    start_speed_mps: float | None
    left_censored: bool
    right_censored: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "threshold": self.threshold,
            "start_distance_m": self.start_distance_m,
            "start_distance_bracket_m": list(self.start_distance_bracket_m)
            if self.start_distance_bracket_m is not None
            else None,
            "end_distance_m": self.end_distance_m,
            "end_distance_bracket_m": list(self.end_distance_bracket_m)
            if self.end_distance_bracket_m is not None
            else None,
            "start_session_time_s": self.start_session_time_s,
            "end_session_time_s": self.end_session_time_s,
            "duration_s": self.duration_s,
            "peak_value": self.peak_value,
            "start_speed_mps": self.start_speed_mps,
            "left_censored": self.left_censored,
            "right_censored": self.right_censored,
        }


@dataclass(frozen=True, slots=True)
class ThresholdDetection:
    status: str
    sustained_events: tuple[ThresholdEvent, ...]
    rejected_short_event_count: int
    unsupported_break_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "sustained_events": [event.to_dict() for event in self.sustained_events],
            "rejected_short_event_count": self.rejected_short_event_count,
            "unsupported_break_count": self.unsupported_break_count,
        }


def detect_sustained_threshold_events(
    samples: tuple[TraceSample, ...] | list[TraceSample],
    *,
    channel: EventChannel,
    threshold: float,
    search_window_m: tuple[float, float],
    minimum_duration_s: float = 0.1,
    max_gap_time_s: float = 0.1,
    max_gap_distance_m: float = 25.0,
    absolute: bool = False,
) -> ThresholdDetection:
    """Find sustained sampled threshold episodes without bridging unsupported spans."""
    for name, value in (
        ("threshold", threshold),
        ("minimum duration", minimum_duration_s),
        ("maximum time gap", max_gap_time_s),
        ("maximum distance gap", max_gap_distance_m),
    ):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and non-negative")
    start_m, end_m = search_window_m
    if not math.isfinite(start_m) or not math.isfinite(end_m) or start_m >= end_m:
        raise ValueError("search window must be a finite increasing distance range")

    in_window_indices = [
        index
        for index, sample in enumerate(samples)
        if sample.distance_m is not None and start_m <= sample.distance_m < end_m
    ]
    if not in_window_indices:
        return ThresholdDetection("no_observations", (), 0, 0)
    in_window = [samples[index] for index in in_window_indices]

    first_index = in_window_indices[0]
    previous = samples[first_index - 1] if first_index > 0 else None
    previous_index = first_index - 1 if previous is not None else None
    previous_value = _value(previous, channel) if previous is not None else None
    previous_active = _active(previous_value, threshold, absolute)
    active_samples: list[TraceSample] = []
    left_censored = False
    bracket_start: tuple[float, float] | None = None
    events: list[ThresholdEvent] = []
    rejected_short = 0
    unsupported_breaks = 0

    def close_event(
        *,
        right_censored: bool,
        end_bracket: tuple[float, float] | None,
    ) -> None:
        nonlocal active_samples, left_censored, bracket_start, rejected_short
        if not active_samples:
            return
        first = active_samples[0]
        last = active_samples[-1]
        assert first.distance_m is not None and last.distance_m is not None
        assert first.session_time_s is not None and last.session_time_s is not None
        duration = max(0.0, last.session_time_s - first.session_time_s)
        duration_tolerance = max(
            float32_ulp(first.session_time_s),
            float32_ulp(last.session_time_s),
        )
        values = [
            value
            for sample in active_samples
            if (value := _value(sample, channel)) is not None
        ]
        sustained = duration + duration_tolerance >= minimum_duration_s
        if sustained:
            events.append(
                ThresholdEvent(
                    channel=channel,
                    threshold=threshold,
                    start_distance_m=first.distance_m,
                    start_distance_bracket_m=None if left_censored else bracket_start,
                    end_distance_m=last.distance_m,
                    end_distance_bracket_m=end_bracket,
                    start_session_time_s=first.session_time_s,
                    end_session_time_s=last.session_time_s,
                    duration_s=duration,
                    peak_value=max(abs(value) for value in values)
                    if absolute
                    else max(values),
                    start_speed_mps=first.speed_mps,
                    left_censored=left_censored,
                    right_censored=right_censored,
                )
            )
        else:
            rejected_short += 1
        active_samples = []
        left_censored = False
        bracket_start = None

    for index, sample in zip(in_window_indices, in_window):
        value = _value(sample, channel)
        connected = (
            previous is not None
            and previous_index is not None
            and index == previous_index + 1
            and _continuous(
            previous,
            sample,
            max_gap_time_s=max_gap_time_s,
            max_gap_distance_m=max_gap_distance_m,
            )
        )
        if previous is not None and not connected:
            if active_samples:
                close_event(right_censored=True, end_bracket=None)
            unsupported_breaks += 1

        if value is None or sample.session_time_s is None:
            if active_samples:
                close_event(right_censored=True, end_bracket=None)
            unsupported_breaks += 1
            previous = sample
            previous_value = None
            previous_active = False
            continue

        is_active = _active(value, threshold, absolute)
        if is_active:
            if not active_samples:
                # A live threshold at the search-window entrance has no observable onset.
                left_censored = (
                    previous is None
                    or not connected
                    or previous_value is None
                    or (previous.distance_m is not None and previous.distance_m < start_m and previous_active)
                    or (
                        index == first_index
                        and previous is not None
                        and previous.distance_m is not None
                        and sample.distance_m is not None
                        and previous.distance_m < start_m <= sample.distance_m
                    )
                )
                if connected and previous is not None and previous.distance_m is not None:
                    assert sample.distance_m is not None
                    bracket_start = (previous.distance_m, sample.distance_m)
                elif not left_censored:
                    assert sample.distance_m is not None
                    bracket_start = (start_m, sample.distance_m)
            active_samples.append(sample)
        elif active_samples:
            previous_active_sample = active_samples[-1]
            assert previous_active_sample.distance_m is not None and sample.distance_m is not None
            close_event(
                right_censored=False,
                end_bracket=(previous_active_sample.distance_m, sample.distance_m),
            )

        previous = sample
        previous_index = index
        previous_value = value
        previous_active = is_active

    if active_samples:
        close_event(right_censored=True, end_bracket=None)

    status = "detected" if any(not event.left_censored for event in events) else (
        "left_censored" if events else "no_sustained_event"
    )
    return ThresholdDetection(status, tuple(events), rejected_short, unsupported_breaks)


def _value(sample: TraceSample | None, channel: EventChannel) -> float | None:
    if sample is None:
        return None
    value = getattr(sample, channel)
    return float(value) if value is not None else None


def _active(value: float | None, threshold: float, absolute: bool) -> bool:
    if value is None:
        return False
    return (abs(value) if absolute else value) >= threshold


def _continuous(
    previous: TraceSample,
    current: TraceSample,
    *,
    max_gap_time_s: float,
    max_gap_distance_m: float,
) -> bool:
    if (
        previous.session_time_s is None
        or current.session_time_s is None
        or previous.distance_m is None
        or current.distance_m is None
        or not math.isfinite(previous.session_time_s)
        or not math.isfinite(current.session_time_s)
        or previous.session_time_s < 0
    ):
        return False
    session_time_reason = (
        session_time_discontinuity(
            previous.session_time_s,
            current.session_time_s,
            max_gap_s=max_gap_time_s,
        )
    )
    if (
        session_time_reason is not None
        or current.time_s is None
        or previous.time_s is None
        or current.time_s < previous.time_s
        or current.distance_m < previous.distance_m
    ):
        return False
    return current.distance_m - previous.distance_m <= max_gap_distance_m + 1e-7
