from __future__ import annotations

import bisect
import math
from typing import Sequence

from .resampling import ExcludedSpan, ResampledTrace


def sample_at_distance(
    grid: Sequence[float],
    values: Sequence[float | int | bool | None],
    mask: Sequence[bool],
    distance_m: float,
    *,
    step_m: float = 1.0,
    channel: str | None = None,
    excluded_spans: Sequence[ExcludedSpan] = (),
) -> float | None:
    """Sample one supported grid channel without crossing excluded spans."""
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


def requested_window_coverage(
    resampled: ResampledTrace,
    start_m: float,
    end_m: float,
    step_m: float,
    *,
    channel: str,
) -> float:
    """Measure full requested-window support, including unsupported tails."""
    return requested_window_coverage_arrays(
        resampled.distance_m,
        resampled.values[channel],
        resampled.masks[channel],
        resampled.excluded_spans,
        start_m,
        end_m,
        step_m,
        channel=channel,
    )


def requested_window_coverage_arrays(
    grid: Sequence[float],
    values: Sequence[float | int | bool | None],
    mask: Sequence[bool],
    excluded_spans: Sequence[ExcludedSpan],
    start_m: float,
    end_m: float,
    step_m: float,
    *,
    channel: str,
) -> float:
    """Coverage implementation shared by stored and derived grid channels."""
    first_index = math.ceil((start_m - 1e-7) / step_m)
    stop_index = math.ceil((end_m - 1e-7) / step_m)
    expected = range(first_index, stop_index)
    expected_count = len(expected)
    if expected_count == 0:
        return 0.0
    supported_count = 0
    for grid_index in expected:
        distance = grid_index * step_m
        position = bisect.bisect_left(grid, distance)
        if (
            position < len(grid)
            and math.isclose(grid[position], distance, abs_tol=1e-7)
            and mask[position]
        ):
            supported_count += 1
    grid_coverage = supported_count / expected_count
    excluded_intervals = sorted(
        (
            max(start_m, span.start_distance_m),
            min(end_m, span.end_distance_m),
        )
        for span in excluded_spans
        if (span.channel is None or span.channel == channel)
        and not (span.reason == "stationary_clock" and channel != "time_s")
        and span.end_distance_m > start_m
        and span.start_distance_m < end_m
    )
    supported_positions = [
        index
        for index, distance in enumerate(grid)
        if start_m - 1e-7 <= distance <= end_m + 1e-7
        and mask[index]
        and values[index] is not None
    ]
    start_supported = sample_at_distance(
        grid,
        values,
        mask,
        start_m,
        step_m=step_m,
        channel=channel,
        excluded_spans=excluded_spans,
    ) is not None
    end_supported = sample_at_distance(
        grid,
        values,
        mask,
        end_m,
        step_m=step_m,
        channel=channel,
        excluded_spans=excluded_spans,
    ) is not None
    if not supported_positions:
        excluded_intervals.append((start_m, end_m))
    else:
        if not start_supported:
            first_supported_m = grid[supported_positions[0]]
            if first_supported_m > start_m:
                excluded_intervals.append((start_m, min(first_supported_m, end_m)))
        if not end_supported:
            last_supported_m = grid[supported_positions[-1]]
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


def has_connected_window_support(
    resampled: ResampledTrace,
    start_m: float,
    end_m: float,
    step_m: float,
    *,
    channel: str,
) -> bool:
    """Require supported boundaries, interior grid points and no break span."""
    return has_connected_window_support_arrays(
        resampled.distance_m,
        resampled.values[channel],
        resampled.masks[channel],
        resampled.excluded_spans,
        start_m,
        end_m,
        step_m,
        channel=channel,
    )


def has_connected_window_support_arrays(
    grid: Sequence[float],
    values: Sequence[float | int | bool | None],
    mask: Sequence[bool],
    excluded_spans: Sequence[ExcludedSpan],
    start_m: float,
    end_m: float,
    step_m: float,
    *,
    channel: str,
) -> bool:
    """Check connected support for a channel stored on a distance grid."""
    if sample_at_distance(
        grid,
        values,
        mask,
        start_m,
        step_m=step_m,
        channel=channel,
        excluded_spans=excluded_spans,
    ) is None or sample_at_distance(
        grid,
        values,
        mask,
        end_m,
        step_m=step_m,
        channel=channel,
        excluded_spans=excluded_spans,
    ) is None:
        return False
    if any(
        span.end_distance_m > start_m and span.start_distance_m < end_m
        and (span.channel is None or span.channel == channel)
        and not (span.reason == "stationary_clock" and channel != "time_s")
        for span in excluded_spans
    ):
        return False
    first_index = math.ceil((start_m - 1e-7) / step_m)
    stop_index = math.ceil((end_m - 1e-7) / step_m)
    for grid_index in range(first_index, stop_index):
        distance = grid_index * step_m
        position = bisect.bisect_left(grid, distance)
        if (
            position >= len(grid)
            or not math.isclose(grid[position], distance, abs_tol=1e-7)
            or not mask[position]
            or values[position] is None
        ):
            return False
    return True
