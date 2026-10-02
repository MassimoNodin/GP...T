from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Mapping, Sequence


ANALYSIS_VERSION = "distance-comparison-v2"
CHANNELS = ("time_s", "speed_mps", "throttle", "brake", "steering", "gear", "drs_active")
CONTINUOUS_CHANNELS = ("time_s", "speed_mps", "throttle", "brake", "steering")
_FRAME_MASK = 0xFFFFFFFF
_SERIAL_HALF_RANGE = 0x80000000
_GRID_EPSILON = 1e-7
_TIME_LIMIT_EPSILON_S = 1e-9


@dataclass(frozen=True, slots=True)
class TraceSample:
    frame_identifier: int
    distance_m: float | None
    time_s: float | None
    speed_mps: float | None
    throttle: float | None
    brake: float | None
    steering: float | None
    gear: int | None
    drs_active: bool | None
    session_time_s: float | None = None

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> TraceSample:
        frame = record.get("frame_identifier")
        clock = record.get("current_lap_time_ms")
        if not isinstance(frame, int) or isinstance(frame, bool) or not 0 <= frame <= _FRAME_MASK:
            raise ValueError("trace contains an invalid frame identifier")
        if not isinstance(clock, int) or isinstance(clock, bool):
            raise ValueError("trace contains an invalid lap clock")
        return cls(
            frame_identifier=frame,
            distance_m=_finite_optional(record.get("lap_distance_m")),
            time_s=clock / 1000.0 if clock >= 0 else None,
            speed_mps=_finite_optional(record.get("speed_mps")),
            throttle=_finite_optional(record.get("throttle")),
            brake=_finite_optional(record.get("brake")),
            steering=_finite_optional(record.get("steering")),
            gear=_integer_optional(record.get("gear")),
            drs_active=_boolean_optional(record.get("drs_active")),
            session_time_s=_finite_optional(record.get("session_time_s")),
        )


@dataclass(frozen=True, slots=True)
class ResamplingConfig:
    grid_step_m: float = 1.0
    max_bracket_time_s: float = 0.1
    max_bracket_distance_m: float = 25.0

    def __post_init__(self) -> None:
        for name, value in (
            ("grid_step_m", self.grid_step_m),
            ("max_bracket_time_s", self.max_bracket_time_s),
            ("max_bracket_distance_m", self.max_bracket_distance_m),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and greater than zero")

    def to_dict(self) -> dict[str, float]:
        return {
            "grid_step_m": self.grid_step_m,
            "max_bracket_time_s": self.max_bracket_time_s,
            "max_bracket_distance_m": self.max_bracket_distance_m,
        }


@dataclass(frozen=True, slots=True)
class ExcludedSpan:
    start_distance_m: float
    end_distance_m: float
    reason: str
    channel: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "start_distance_m": self.start_distance_m,
            "end_distance_m": self.end_distance_m,
            "reason": self.reason,
            "channel": self.channel,
        }


@dataclass(frozen=True, slots=True)
class ResampledTrace:
    distance_m: tuple[float, ...]
    values: Mapping[str, tuple[float | int | bool | None, ...]]
    masks: Mapping[str, tuple[bool, ...]]
    coverage: Mapping[str, float]
    excluded_spans: tuple[ExcludedSpan, ...]
    source_sample_count: int
    duplicate_frame_count: int
    duplicate_distance_count: int
    largest_adjacent_sample_distance_gap_m: float
    largest_adjacent_sample_time_gap_s: float
    largest_interpolation_gap_distance_m: float
    largest_interpolation_gap_time_s: float
    missing_channel_interpolation_count: Mapping[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "distance_m": list(self.distance_m),
            "values": {key: list(value) for key, value in self.values.items()},
            "masks": {key: list(value) for key, value in self.masks.items()},
            "coverage": dict(self.coverage),
            "excluded_spans": [span.to_dict() for span in self.excluded_spans],
            "source_sample_count": self.source_sample_count,
            "duplicate_frame_count": self.duplicate_frame_count,
            "duplicate_distance_count": self.duplicate_distance_count,
            "largest_adjacent_sample_distance_gap_m": self.largest_adjacent_sample_distance_gap_m,
            "largest_adjacent_sample_time_gap_s": self.largest_adjacent_sample_time_gap_s,
            "largest_interpolation_gap_distance_m": self.largest_interpolation_gap_distance_m,
            "largest_interpolation_gap_time_s": self.largest_interpolation_gap_time_s,
            "missing_channel_interpolation_count": dict(self.missing_channel_interpolation_count),
        }


def common_distance_grid(
    target: Sequence[TraceSample],
    reference: Sequence[TraceSample],
    *,
    track_length_m: float,
    step_m: float = 1.0,
) -> tuple[float, ...]:
    if not math.isfinite(track_length_m) or track_length_m <= 0:
        raise ValueError("track length must be finite and greater than zero")
    if not math.isfinite(step_m) or step_m <= 0:
        raise ValueError("grid step must be finite and greater than zero")
    target_distances = _valid_distances(target, track_length_m)
    reference_distances = _valid_distances(reference, track_length_m)
    if not target_distances or not reference_distances:
        raise ValueError("both attempts need valid lap-distance samples")
    start = max(min(target_distances), min(reference_distances), 0.0)
    end = min(max(target_distances), max(reference_distances), track_length_m)
    first_index = math.ceil((start - _GRID_EPSILON) / step_m)
    last_index = math.floor((end + _GRID_EPSILON) / step_m)
    count = last_index - first_index + 1
    if count <= 0:
        raise ValueError("attempts have no shared observed distance range on the grid")
    if count > 1_000_000:
        raise ValueError("distance grid exceeds the 1,000,000 point safety limit")
    return tuple(index * step_m for index in range(first_index, last_index + 1))


def resample_trace(
    samples: Sequence[TraceSample],
    distance_grid_m: Sequence[float],
    config: ResamplingConfig = ResamplingConfig(),
    *,
    track_length_m: float | None = None,
) -> ResampledTrace:
    grid = tuple(float(distance) for distance in distance_grid_m)
    if not grid or any(not math.isfinite(value) for value in grid):
        raise ValueError("distance grid must contain finite points")
    if any(right <= left for left, right in zip(grid, grid[1:])):
        raise ValueError("distance grid must be strictly increasing")
    if track_length_m is not None and (
        not math.isfinite(track_length_m) or track_length_m <= 0
    ):
        raise ValueError("track length must be finite and greater than zero")

    ordered, duplicate_frames = _validate_chronology(samples)
    segments: list[list[TraceSample]] = []
    current: list[TraceSample] = []
    spans: list[ExcludedSpan] = []
    hard_blocks: list[ExcludedSpan] = []
    missing_channel_interpolation_count = {channel: 0 for channel in CHANNELS}
    largest_adjacent_distance_gap = 0.0
    largest_adjacent_time_gap = 0.0
    largest_interpolation_distance_gap = 0.0
    largest_interpolation_time_gap = 0.0
    pending_break: tuple[float | None, str] | None = None
    for sample in ordered:
        invalid_reason: str | None = None
        if sample.distance_m is None:
            invalid_reason = "missing_distance"
        elif sample.distance_m < 0 or (
            track_length_m is not None and sample.distance_m > track_length_m
        ):
            invalid_reason = "distance_out_of_bounds"
        elif sample.time_s is None:
            invalid_reason = "missing_lap_clock"
        if invalid_reason is not None:
            if current:
                previous_distance = current[-1].distance_m
                assert previous_distance is not None
                segments.append(current)
                current = []
                if invalid_reason == "distance_out_of_bounds" and sample.distance_m is not None:
                    spans.append(_span(previous_distance, sample.distance_m, invalid_reason))
                    pending_break = (sample.distance_m, invalid_reason)
                else:
                    pending_break = (previous_distance, invalid_reason)
            elif pending_break is None:
                pending_break = (None, invalid_reason)
            continue
        assert sample.distance_m is not None and sample.time_s is not None
        if pending_break is not None:
            start_distance, reason = pending_break
            if start_distance is not None:
                spans.append(_span(start_distance, sample.distance_m, reason))
            pending_break = None
        if current:
            previous = current[-1]
            assert previous.distance_m is not None and previous.time_s is not None
            if sample.time_s < previous.time_s:
                span = _span(previous.distance_m, sample.distance_m, "lap_clock_regression")
                spans.append(span)
                hard_blocks.append(span)
                segments.append(current)
                current = [sample]
                continue
            if sample.distance_m < previous.distance_m:
                span = _span(previous.distance_m, sample.distance_m, "distance_regression")
                spans.append(span)
                hard_blocks.append(span)
                segments.append(current)
                current = [sample]
                continue
        current.append(sample)
    if current:
        segments.append(current)

    candidates: dict[str, list[list[float | int | bool]]] = {
        channel: [[] for _ in grid] for channel in CHANNELS
    }
    duplicate_distances = 0
    for segment in segments:
        points: list[TraceSample] = []
        index = 0
        while index < len(segment):
            group = [segment[index]]
            index += 1
            while (
                index < len(segment)
                and segment[index].distance_m == group[0].distance_m
            ):
                group.append(segment[index])
                index += 1
            duplicate_distances += len(group) - 1
            chosen = group[-1]
            assert chosen.distance_m is not None and chosen.time_s is not None
            if len(group) > 1 and _exceeds_limit(
                group[-1].time_s - group[0].time_s,
                config.max_bracket_time_s,
                _TIME_LIMIT_EPSILON_S,
            ):
                span = ExcludedSpan(
                    chosen.distance_m,
                    chosen.distance_m,
                    "stationary_distance",
                )
                spans.append(span)
                hard_blocks.append(span)
            points.append(chosen)

        for point in points:
            assert point.distance_m is not None
            position = bisect.bisect_left(grid, point.distance_m - _GRID_EPSILON)
            while position < len(grid) and abs(grid[position] - point.distance_m) <= _GRID_EPSILON:
                for channel in CHANNELS:
                    value = _channel_value(point, channel)
                    if value is not None:
                        candidates[channel][position].append(value)
                position += 1

        for previous, following in zip(points, points[1:]):
            assert previous.distance_m is not None and following.distance_m is not None
            assert previous.time_s is not None and following.time_s is not None
            distance_gap = following.distance_m - previous.distance_m
            time_gap = following.time_s - previous.time_s
            largest_adjacent_distance_gap = max(largest_adjacent_distance_gap, distance_gap)
            largest_adjacent_time_gap = max(largest_adjacent_time_gap, time_gap)
            if distance_gap <= 0:
                span = _span(previous.distance_m, following.distance_m, "stationary_distance")
                spans.append(span)
                hard_blocks.append(span)
                continue
            if time_gap <= 0:
                span = _span(previous.distance_m, following.distance_m, "stationary_clock")
                spans.append(span)
            if (
                distance_gap > config.max_bracket_distance_m
                or _exceeds_limit(
                    time_gap,
                    config.max_bracket_time_s,
                    _TIME_LIMIT_EPSILON_S,
                )
            ):
                spans.append(_span(previous.distance_m, following.distance_m, "interpolation_gap"))
                largest_interpolation_distance_gap = max(
                    largest_interpolation_distance_gap, distance_gap
                )
                largest_interpolation_time_gap = max(
                    largest_interpolation_time_gap, time_gap
                )
                continue

            first = bisect.bisect_right(grid, previous.distance_m + _GRID_EPSILON)
            last = bisect.bisect_left(grid, following.distance_m - _GRID_EPSILON)
            for position in range(first, last):
                ratio = (grid[position] - previous.distance_m) / distance_gap
                for channel in CHANNELS:
                    if channel == "time_s" and time_gap <= 0:
                        continue
                    left_value = _channel_value(previous, channel)
                    if channel in CONTINUOUS_CHANNELS:
                        right_value = _channel_value(following, channel)
                        if left_value is None or right_value is None:
                            missing_channel_interpolation_count[channel] += 1
                            continue
                        value = float(left_value) + (float(right_value) - float(left_value)) * ratio
                    else:
                        if left_value is None:
                            missing_channel_interpolation_count[channel] += 1
                            continue
                        value = left_value
                    candidates[channel][position].append(value)
            for channel in CHANNELS:
                if channel == "time_s":
                    continue
                if (
                    _channel_value(previous, channel) is None
                    or (
                        channel in CONTINUOUS_CHANNELS
                        and _channel_value(following, channel) is None
                    )
                ):
                    # Preserve the unsupported raw bracket even when the analysis
                    # grid has no interior point inside it.
                    spans.append(
                        ExcludedSpan(
                            previous.distance_m,
                            following.distance_m,
                            "channel_missing",
                            channel,
                        )
                    )

    values: dict[str, tuple[float | int | bool | None, ...]] = {}
    masks: dict[str, tuple[bool, ...]] = {}
    coverage: dict[str, float] = {}
    for channel in CHANNELS:
        channel_values: list[float | int | bool | None] = []
        channel_masks: list[bool] = []
        for position, options in enumerate(candidates[channel]):
            distance = grid[position]
            blocked = any(
                (span.channel is None or span.channel == channel)
                and span.start_distance_m - _GRID_EPSILON
                <= distance
                <= span.end_distance_m + _GRID_EPSILON
                and span.reason
                in {"distance_regression", "lap_clock_regression", "stationary_distance"}
                for span in hard_blocks
            )
            if blocked or not options:
                channel_values.append(None)
                channel_masks.append(False)
            elif len(options) > 1:
                span = ExcludedSpan(distance, distance, "ambiguous_overlapping_segments", channel)
                spans.append(span)
                channel_values.append(None)
                channel_masks.append(False)
            else:
                channel_values.append(options[-1])
                channel_masks.append(True)
        values[channel] = tuple(channel_values)
        masks[channel] = tuple(channel_masks)
        coverage[channel] = sum(channel_masks) / len(grid)

    return ResampledTrace(
        distance_m=grid,
        values=values,
        masks=masks,
        coverage=coverage,
        excluded_spans=tuple(_unique_spans(spans)),
        source_sample_count=len(samples),
        duplicate_frame_count=duplicate_frames,
        duplicate_distance_count=duplicate_distances,
        largest_adjacent_sample_distance_gap_m=largest_adjacent_distance_gap,
        largest_adjacent_sample_time_gap_s=largest_adjacent_time_gap,
        largest_interpolation_gap_distance_m=largest_interpolation_distance_gap,
        largest_interpolation_gap_time_s=largest_interpolation_time_gap,
        missing_channel_interpolation_count=missing_channel_interpolation_count,
    )


def _validate_chronology(
    samples: Sequence[TraceSample],
) -> tuple[list[TraceSample], int]:
    ordered: list[TraceSample] = []
    duplicate_frames = 0
    for sample in samples:
        if not 0 <= sample.frame_identifier <= _FRAME_MASK:
            raise ValueError("trace contains an invalid frame identifier")
        if ordered:
            previous = ordered[-1]
            distance = (sample.frame_identifier - previous.frame_identifier) & _FRAME_MASK
            if distance == 0 and sample == previous:
                duplicate_frames += 1
                continue
            if distance == 0 or distance >= _SERIAL_HALF_RANGE:
                raise ValueError("trace frame identifiers are not in chronological order")
        ordered.append(sample)
    return ordered, duplicate_frames


def _valid_distances(samples: Sequence[TraceSample], track_length_m: float) -> list[float]:
    return [
        sample.distance_m
        for sample in samples
        if sample.distance_m is not None
        and math.isfinite(sample.distance_m)
        and 0 <= sample.distance_m <= track_length_m
    ]


def _channel_value(
    sample: TraceSample, channel: str
) -> float | int | bool | None:
    return getattr(sample, channel)


def _span(left: float, right: float, reason: str) -> ExcludedSpan:
    return ExcludedSpan(min(left, right), max(left, right), reason)


def _exceeds_limit(value: float, limit: float, tolerance: float) -> bool:
    return value > limit and not math.isclose(value, limit, rel_tol=0.0, abs_tol=tolerance)


def _unique_spans(spans: Sequence[ExcludedSpan]) -> list[ExcludedSpan]:
    unique: dict[tuple[float, float, str, str | None], ExcludedSpan] = {}
    for span in spans:
        key = (
            span.start_distance_m,
            span.end_distance_m,
            span.reason,
            span.channel,
        )
        unique[key] = span
    return sorted(
        unique.values(),
        key=lambda span: (
            span.start_distance_m,
            span.end_distance_m,
            span.reason,
            span.channel or "",
        ),
    )


def _finite_optional(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer_optional(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number == value else None


def _boolean_optional(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
