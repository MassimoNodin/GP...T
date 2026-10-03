from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from .comparison import DeltaTime
from .continuity import session_time_discontinuity
from .distance_support import (
    has_connected_window_support,
    has_connected_window_support_arrays,
    requested_window_coverage,
    requested_window_coverage_arrays,
    sample_at_distance,
)
from .resampling import ResampledTrace, ResamplingConfig, TraceSample


MAX_INTERVAL_EVALUATION_WORK = 4_000_000


@dataclass(frozen=True, slots=True)
class IntervalDeltaEvaluation:
    start_delta_s: float | None
    end_delta_s: float | None
    target_time_coverage: float
    reference_time_coverage: float
    shared_time_coverage: float
    target_resampled_time_connected: bool
    reference_resampled_time_connected: bool
    shared_delta_time_connected: bool
    target_source_session_time_connected: bool
    reference_source_session_time_connected: bool
    interval_connected_supported_time: bool
    delta_change_s: float | None
    status: str
    unavailable_reasons: tuple[str, ...]

    @property
    def unavailable_reason(self) -> str | None:
        if self.interval_connected_supported_time:
            return None
        if self.status == "unsupported_boundary":
            return "unsupported_boundary_evidence"
        if self.status == "unsupported_source_chronology":
            return "disconnected_source_session_time"
        return "unsupported_or_disconnected_interior_time_evidence"


def evaluate_interval_delta(
    target_samples: Sequence[TraceSample],
    reference_samples: Sequence[TraceSample],
    target_resampled: ResampledTrace,
    reference_resampled: ResampledTrace,
    delta: DeltaTime,
    start_m: float,
    end_m: float,
    *,
    config: ResamplingConfig,
) -> IntervalDeltaEvaluation:
    """Evaluate a distance interval under the shared connected-time contract."""
    shared_excluded_spans = (
        *target_resampled.excluded_spans,
        *reference_resampled.excluded_spans,
    )
    start_delta_s = sample_at_distance(
        target_resampled.distance_m,
        delta.values_s,
        delta.mask,
        start_m,
        step_m=config.grid_step_m,
        channel="time_s",
        excluded_spans=shared_excluded_spans,
    )
    end_delta_s = sample_at_distance(
        target_resampled.distance_m,
        delta.values_s,
        delta.mask,
        end_m,
        step_m=config.grid_step_m,
        channel="time_s",
        excluded_spans=shared_excluded_spans,
    )
    target_coverage = requested_window_coverage(
        target_resampled, start_m, end_m, config.grid_step_m, channel="time_s"
    )
    reference_coverage = requested_window_coverage(
        reference_resampled, start_m, end_m, config.grid_step_m, channel="time_s"
    )
    shared_coverage = requested_window_coverage_arrays(
        target_resampled.distance_m,
        delta.values_s,
        delta.mask,
        shared_excluded_spans,
        start_m,
        end_m,
        config.grid_step_m,
        channel="time_s",
    )
    target_grid_connected = has_connected_window_support(
        target_resampled, start_m, end_m, config.grid_step_m, channel="time_s"
    )
    reference_grid_connected = has_connected_window_support(
        reference_resampled, start_m, end_m, config.grid_step_m, channel="time_s"
    )
    delta_grid_connected = has_connected_window_support_arrays(
        target_resampled.distance_m,
        delta.values_s,
        delta.mask,
        shared_excluded_spans,
        start_m,
        end_m,
        config.grid_step_m,
        channel="time_s",
    )
    target_source_connected = has_connected_source_session_time(
        target_samples, start_m, end_m, max_gap_s=config.max_bracket_time_s
    )
    reference_source_connected = has_connected_source_session_time(
        reference_samples, start_m, end_m, max_gap_s=config.max_bracket_time_s
    )

    reasons: list[str] = []
    if start_delta_s is None:
        reasons.append("entry_boundary_unsupported")
    if end_delta_s is None:
        reasons.append("exit_boundary_unsupported")
    if not target_source_connected:
        reasons.append("target_source_session_time_disconnected")
    if not reference_source_connected:
        reasons.append("reference_source_session_time_disconnected")
    if not target_grid_connected:
        reasons.append("target_resampled_time_disconnected")
    if not reference_grid_connected:
        reasons.append("reference_resampled_time_disconnected")
    if not delta_grid_connected:
        reasons.append("shared_delta_time_disconnected")

    connected = (
        start_delta_s is not None
        and end_delta_s is not None
        and target_grid_connected
        and reference_grid_connected
        and delta_grid_connected
        and target_source_connected
        and reference_source_connected
    )
    if start_delta_s is None or end_delta_s is None:
        status = "unsupported_boundary"
    elif not target_source_connected or not reference_source_connected:
        status = "unsupported_source_chronology"
    elif not target_grid_connected or not reference_grid_connected or not delta_grid_connected:
        status = "unsupported_interior"
    else:
        status = "supported"

    return IntervalDeltaEvaluation(
        start_delta_s=start_delta_s,
        end_delta_s=end_delta_s,
        target_time_coverage=target_coverage,
        reference_time_coverage=reference_coverage,
        shared_time_coverage=shared_coverage,
        target_resampled_time_connected=target_grid_connected,
        reference_resampled_time_connected=reference_grid_connected,
        shared_delta_time_connected=delta_grid_connected,
        target_source_session_time_connected=target_source_connected,
        reference_source_session_time_connected=reference_source_connected,
        interval_connected_supported_time=connected,
        delta_change_s=(end_delta_s - start_delta_s if connected else None),
        status=status,
        unavailable_reasons=tuple(reasons),
    )


def has_connected_source_session_time(
    samples: Sequence[TraceSample],
    start_m: float,
    end_m: float,
    *,
    max_gap_s: float,
) -> bool:
    """Require continuous source session time across every raw edge in a window."""
    overlapping_edges = 0
    for previous, current in zip(samples, samples[1:]):
        previous_distance = previous.distance_m
        current_distance = current.distance_m
        if (
            previous_distance is None
            or current_distance is None
            or not math.isfinite(previous_distance)
            or not math.isfinite(current_distance)
        ):
            continue
        edge_start = min(previous_distance, current_distance)
        edge_end = max(previous_distance, current_distance)
        if edge_end <= start_m or edge_start >= end_m:
            continue
        overlapping_edges += 1
        previous_time = previous.session_time_s
        current_time = current.session_time_s
        if (
            previous_time is None
            or current_time is None
            or not math.isfinite(previous_time)
            or not math.isfinite(current_time)
            or previous_time < 0
            or current_time < 0
            or session_time_discontinuity(
                previous_time,
                current_time,
                max_gap_s=max_gap_s,
            )
            is not None
        ):
            return False
    return overlapping_edges > 0


def reserve_interval_evaluation_work(
    interval_count: int,
    target_samples: Sequence[TraceSample],
    reference_samples: Sequence[TraceSample],
    target_resampled: ResampledTrace,
    reference_resampled: ResampledTrace,
    delta: DeltaTime,
    *,
    limit: int = MAX_INTERVAL_EVALUATION_WORK,
) -> int:
    """Reserve conservative whole-input work before evaluating any interval."""
    if interval_count < 0 or limit < 0:
        raise ValueError("interval evaluation bounds must not be negative")
    units_per_interval = (
        len(target_samples)
        + len(reference_samples)
        + len(target_resampled.distance_m)
        + len(reference_resampled.distance_m)
        + len(delta.values_s)
        + len(target_resampled.excluded_spans)
        + len(reference_resampled.excluded_spans)
    )
    units = interval_count * units_per_interval
    if units > limit:
        raise ValueError("interval_evaluation_work_limit_exceeded")
    return units
