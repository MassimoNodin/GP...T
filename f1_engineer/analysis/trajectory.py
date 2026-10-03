from __future__ import annotations

import math
from typing import Mapping, Sequence

from .continuity import MAX_SESSION_TIME_GAP_S, float32_ulp, session_time_discontinuity
from .source_limits import (
    ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT as TRAJECTORY_PREVIEW_SOURCE_CONTEXT_BYTE_LIMIT,
    ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT as TRAJECTORY_PREVIEW_SOURCE_CONTEXT_SEGMENT_LIMIT,
    ANALYSIS_SOURCE_TRACE_BYTE_LIMIT as TRAJECTORY_PREVIEW_SOURCE_BYTE_LIMIT,
    ANALYSIS_SOURCE_TRACE_ROW_LIMIT as TRAJECTORY_PREVIEW_SOURCE_ROW_LIMIT,
)


TRAJECTORY_SCHEMA_VERSION = 1
TRAJECTORY_PREVIEW_POINT_LIMIT = 2_000
TRAJECTORY_PREVIEW_SEGMENT_LIMIT = 256
TRAJECTORY_PREVIEW_EXAMPLE_LIMIT = 20
_PREVIEW_POINT_FIELDS = (
    "frame_identifier",
    "lap_distance_m",
    "session_time_s",
    "lap_time_s",
    "lap_time_ms",
    "world_position_m",
)
_FRAME_MASK = 0xFFFFFFFF
_SERIAL_HALF_RANGE = 0x80000000
_MAX_WORLD_POSITION_STEP_M = 25.0
_LAP_TIME_REGRESSION_TOLERANCE_S = 0.020
_LAP_DISTANCE_REGRESSION_TOLERANCE_M = 0.01


class TrajectoryPreviewUnavailable(ValueError):
    """A preview cannot preserve source continuity within its hard limits."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def build_observed_trajectory(
    *,
    attempt_key: str,
    run_id: str,
    session_uid: str,
    car_index: int,
    disposition: str,
    lap_time_ms: int | None,
    game_valid: bool | None,
    reference_eligible: bool,
    exclusion_reasons: Sequence[str],
    trace_sha256: str,
    trace_schema_version: int,
    context_segments: Sequence[tuple[int, Mapping[str, object] | None]],
    samples: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Export observed world-space samples without projecting them onto a track."""
    segments: list[dict[str, object]] = []
    breaks: list[dict[str, object]] = []
    unsupported_samples: list[dict[str, object]] = []
    current_points: list[dict[str, object]] = []
    current_break_reasons: list[str] = []
    previous_point: dict[str, object] | None = None
    position_sample_count = 0

    def finish_segment() -> None:
        nonlocal current_points, current_break_reasons
        if not current_points:
            return
        segments.append(
            {
                "segment_index": len(segments),
                "break_before_reasons": current_break_reasons,
                "sample_count": len(current_points),
                "start_anchor": _anchor(current_points[0]),
                "end_anchor": _anchor(current_points[-1]),
                "points": current_points,
            }
        )
        current_points = []
        current_break_reasons = []

    for sample in samples:
        point, missing_reason = _trajectory_point(sample)
        if point is None:
            finish_segment()
            anchor = _sample_anchor(sample)
            unsupported_samples.append({**anchor, "reason": missing_reason})
            breaks.append({**anchor, "reason": missing_reason})
            previous_point = None
            if missing_reason not in current_break_reasons:
                current_break_reasons.append(missing_reason)
            continue

        if previous_point is not None:
            continuity_reason = _continuity_break(previous_point, point)
            if continuity_reason is not None:
                finish_segment()
                breaks.append(
                    {
                        "reason": continuity_reason,
                        "after": _anchor(previous_point),
                        "before": _anchor(point),
                    }
                )
                current_break_reasons.append(continuity_reason)
        if not current_points and not current_break_reasons:
            current_break_reasons = ["start"] if not segments else ["continuous_segment"]
        current_points.append(point)
        previous_point = point
        position_sample_count += 1
    finish_segment()

    valid_distances = [
        float(point["lap_distance_m"])
        for segment in segments
        for point in segment["points"]
        if isinstance(point.get("lap_distance_m"), (int, float))
    ]
    total_samples = len(samples)
    return {
        "schema_version": TRAJECTORY_SCHEMA_VERSION,
        "artifact_kind": "observed_driven_trajectory",
        "diagnostic_only": True,
        "is_centreline": False,
        "source": {
            "attempt_key": attempt_key,
            "run_id": run_id,
            "session_uid": session_uid,
            "car_index": car_index,
            "disposition": disposition,
            "lap_time_ms": lap_time_ms,
            "game_valid": game_valid,
            "reference_eligible": reference_eligible,
            "exclusion_reasons": list(exclusion_reasons),
            "trace_sha256": trace_sha256,
            "trace_schema_version": trace_schema_version,
            "context_segments": [
                {
                    "from_frame_identifier": frame,
                    "context": dict(context) if context is not None else None,
                }
                for frame, context in context_segments
            ],
        },
        "units": {
            "world_position": "m",
            "world_velocity": "m/s",
            "world_forward": "dimensionless normalized direction",
            "world_right": "dimensionless normalized direction",
            "g_force": "g",
            "orientation": "rad",
            "lap_distance": "m",
            "session_time": "s",
            "lap_time": "s",
        },
        "continuity_policy": {
            "max_session_time_gap_s": MAX_SESSION_TIME_GAP_S,
            "session_time_float32_tolerance": "one ULP at the larger endpoint magnitude",
            "max_world_position_step_m": _MAX_WORLD_POSITION_STEP_M,
            "world_position_float32_tolerance": "sqrt(3) times one ULP at the largest component magnitude",
            "lap_time_regression_tolerance_ms": _LAP_TIME_REGRESSION_TOLERANCE_S * 1000,
            "lap_time_comparison_resolution": "integer source milliseconds",
            "lap_distance_regression_tolerance_m": _LAP_DISTANCE_REGRESSION_TOLERANCE_M,
            "session_time_regression_tolerance": "one ULP at the larger endpoint magnitude",
            "frame_delta": "exactly one, with uint32 wrap allowed",
        },
        "coverage": {
            "source_sample_count": total_samples,
            "position_sample_count": position_sample_count,
            "position_sample_coverage": (
                position_sample_count / total_samples if total_samples else 0.0
            ),
            "unsupported_sample_count": len(unsupported_samples),
            "segment_count": len(segments),
            "discontinuity_count": len(breaks),
            "observed_lap_distance_range_m": (
                [min(valid_distances), max(valid_distances)] if valid_distances else None
            ),
        },
        "segments": segments,
        "breaks": breaks,
        "unsupported_samples": unsupported_samples,
    }


def build_observed_trajectory_preview(
    trajectory: Mapping[str, object],
    *,
    point_limit: int = TRAJECTORY_PREVIEW_POINT_LIMIT,
    segment_limit: int = TRAJECTORY_PREVIEW_SEGMENT_LIMIT,
    example_limit: int = TRAJECTORY_PREVIEW_EXAMPLE_LIMIT,
) -> dict[str, object]:
    """Create a deterministic, bounded view while retaining source segmentation."""
    source_segments = trajectory.get("segments")
    if not isinstance(source_segments, list):
        raise ValueError("trajectory segments are malformed")
    if len(source_segments) > segment_limit:
        raise TrajectoryPreviewUnavailable("trajectory_preview_too_fragmented")

    segments: list[Mapping[str, object]] = [
        segment for segment in source_segments if isinstance(segment, Mapping)
    ]
    if len(segments) != len(source_segments):
        raise ValueError("trajectory segments are malformed")
    point_lists: list[list[Mapping[str, object]]] = []
    for segment in segments:
        points = segment.get("points")
        if not isinstance(points, list) or any(not isinstance(point, Mapping) for point in points):
            raise ValueError("trajectory segment points are malformed")
        point_lists.append(points)

    source_point_count = sum(len(points) for points in point_lists)
    if source_point_count == 0:
        raise TrajectoryPreviewUnavailable("no_observed_position_samples")
    if point_limit < sum(min(2, len(points)) for points in point_lists):
        raise TrajectoryPreviewUnavailable("trajectory_preview_too_fragmented")

    quotas = _allocate_preview_points(point_lists, point_limit)
    preview_segments: list[dict[str, object]] = []
    for segment, points, quota in zip(segments, point_lists, quotas):
        selected = [points[index] for index in _evenly_spaced_indices(len(points), quota)]
        preview_segments.append(
            {
                key: value for key, value in segment.items() if key != "points"
            }
            | {
                "rendered_point_count": len(selected),
                "points": [
                    {key: point[key] for key in _PREVIEW_POINT_FIELDS if key in point}
                    for point in selected
                ],
            }
        )

    source_breaks = trajectory.get("breaks")
    source_unsupported = trajectory.get("unsupported_samples")
    if not isinstance(source_breaks, list) or not isinstance(source_unsupported, list):
        raise ValueError("trajectory break evidence is malformed")
    break_examples = source_breaks[:example_limit]
    unsupported_examples = source_unsupported[:example_limit]

    coverage = trajectory.get("coverage")
    if not isinstance(coverage, Mapping):
        raise ValueError("trajectory coverage is malformed")
    return {
        "schema_version": TRAJECTORY_SCHEMA_VERSION,
        "artifact_kind": "observed_driven_trajectory_preview",
        "diagnostic_only": True,
        "is_centreline": False,
        "coordinate_projection": {
            "horizontal_axis": "world_x",
            "vertical_axis": "world_z",
            "units": "m",
            "orientation_claim": None,
        },
        "source": trajectory.get("source"),
        "units": trajectory.get("units"),
        "continuity_policy": trajectory.get("continuity_policy"),
        "coverage": dict(coverage),
        "segments": preview_segments,
        "break_examples": break_examples,
        "unsupported_examples": unsupported_examples,
        "preview": {
            "point_limit": point_limit,
            "source_position_point_count": source_point_count,
            "rendered_point_count": sum(quotas),
            "omitted_position_point_count": source_point_count - sum(quotas),
            "source_segment_count": len(segments),
            "rendered_segment_count": len(preview_segments),
            "segment_limit": segment_limit,
            "break_example_limit": example_limit,
            "break_examples_omitted_count": len(source_breaks) - len(break_examples),
            "unsupported_example_limit": example_limit,
            "unsupported_examples_omitted_count": len(source_unsupported)
            - len(unsupported_examples),
            "thinning_method": "deterministic_even_spacing_with_segment_endpoints",
        },
    }


def _allocate_preview_points(
    point_lists: Sequence[Sequence[object]], point_limit: int
) -> list[int]:
    quotas = [min(2, len(points)) for points in point_lists]
    remaining = point_limit - sum(quotas)
    capacities = [len(points) - quota for points, quota in zip(point_lists, quotas)]
    while remaining > 0 and any(capacities):
        total_capacity = sum(capacities)
        shares = [remaining * capacity / total_capacity for capacity in capacities]
        grants = [min(capacity, int(share)) for capacity, share in zip(capacities, shares)]
        granted = sum(grants)
        quotas = [quota + grant for quota, grant in zip(quotas, grants)]
        capacities = [capacity - grant for capacity, grant in zip(capacities, grants)]
        remaining -= granted
        if remaining <= 0 or not any(capacities):
            continue
        order = sorted(
            range(len(capacities)),
            key=lambda index: (-(shares[index] - int(shares[index])), index),
        )
        for index in order:
            if remaining == 0:
                break
            if capacities[index] > 0:
                quotas[index] += 1
                capacities[index] -= 1
                remaining -= 1
    return quotas


def _evenly_spaced_indices(point_count: int, retained_count: int) -> list[int]:
    if retained_count >= point_count:
        return list(range(point_count))
    if retained_count <= 1:
        return [0] if point_count else []
    last_index = point_count - 1
    denominator = retained_count - 1
    return [
        (2 * index * last_index + denominator) // (2 * denominator)
        for index in range(retained_count)
    ]


def _trajectory_point(
    sample: Mapping[str, object],
) -> tuple[dict[str, object] | None, str]:
    frame = sample.get("frame_identifier")
    distance = _finite_number(sample.get("lap_distance_m"))
    session_time = _finite_number(sample.get("session_time_s"))
    position = tuple(
        _finite_number(sample.get(field))
        for field in ("world_position_x_m", "world_position_y_m", "world_position_z_m")
    )
    if not isinstance(frame, int) or isinstance(frame, bool):
        return None, "invalid_frame_anchor"
    if distance is None:
        return None, "missing_lap_distance"
    if session_time is None:
        return None, "missing_session_time_anchor"
    if session_time < 0:
        return None, "invalid_session_time_anchor"
    lap_time_ms_value = _finite_number(sample.get("current_lap_time_ms"))
    if lap_time_ms_value is None:
        return None, "missing_lap_time_anchor"
    if lap_time_ms_value < 0 or not lap_time_ms_value.is_integer():
        return None, "invalid_lap_time_anchor"
    if sample.get("motion_available") is not True or any(value is None for value in position):
        return None, "motion_position_unavailable"
    point: dict[str, object] = {
        "frame_identifier": frame,
        "lap_distance_m": distance,
        "session_time_s": session_time,
        "lap_time_s": lap_time_ms_value / 1000.0,
        "lap_time_ms": lap_time_ms_value,
        "world_position_m": {
            axis: value for axis, value in zip(("x", "y", "z"), position)
        },
    }
    for output, fields in (
        (
            "world_velocity_mps",
            ("world_velocity_x_mps", "world_velocity_y_mps", "world_velocity_z_mps"),
        ),
        ("world_forward", ("world_forward_x", "world_forward_y", "world_forward_z")),
        ("world_right", ("world_right_x", "world_right_y", "world_right_z")),
        (
            "g_force_g",
            ("g_force_lateral", "g_force_longitudinal", "g_force_vertical"),
        ),
    ):
        vector = tuple(_finite_number(sample.get(field)) for field in fields)
        point[output] = (
            {
                axis: value
                for axis, value in zip(
                    ("lateral", "longitudinal", "vertical")
                    if output == "g_force_g"
                    else ("x", "y", "z"),
                    vector,
                )
            }
            if all(value is not None for value in vector)
            else None
        )
    point["orientation_rad"] = {
        field.removesuffix("_rad"): _finite_number(sample.get(field))
        for field in ("yaw_rad", "pitch_rad", "roll_rad")
    }
    return point, ""


def _continuity_break(
    previous: Mapping[str, object], current: Mapping[str, object]
) -> str | None:
    previous_frame = int(previous["frame_identifier"])
    current_frame = int(current["frame_identifier"])
    frame_delta = (current_frame - previous_frame) & _FRAME_MASK
    if frame_delta != 1:
        return "frame_gap" if 1 < frame_delta < _SERIAL_HALF_RANGE else "frame_order_discontinuity"
    previous_time = float(previous["session_time_s"])
    current_time = float(current["session_time_s"])
    time_discontinuity = session_time_discontinuity(
        previous_time, current_time, max_gap_s=MAX_SESSION_TIME_GAP_S
    )
    if time_discontinuity is not None:
        return time_discontinuity
    previous_lap_time_ms = float(previous["lap_time_ms"])
    current_lap_time_ms = float(current["lap_time_ms"])
    if current_lap_time_ms < previous_lap_time_ms - _LAP_TIME_REGRESSION_TOLERANCE_S * 1000:
        return "lap_time_regression"
    previous_distance = float(previous["lap_distance_m"])
    current_distance = float(current["lap_distance_m"])
    if current_distance < previous_distance - _LAP_DISTANCE_REGRESSION_TOLERANCE_M:
        return "lap_distance_regression"
    previous_position = previous["world_position_m"]
    current_position = current["world_position_m"]
    position_step = math.dist(
        [float(previous_position[axis]) for axis in ("x", "y", "z")],
        [float(current_position[axis]) for axis in ("x", "y", "z")],
    )
    max_position_ulp = max(
        float32_ulp(float(position[axis]))
        for position in (previous_position, current_position)
        for axis in ("x", "y", "z")
    )
    position_tolerance = math.sqrt(3.0) * max_position_ulp
    if position_step > _MAX_WORLD_POSITION_STEP_M + position_tolerance:
        return "world_position_jump"
    return None


def _anchor(point: Mapping[str, object]) -> dict[str, object]:
    return {
        "frame_identifier": point.get("frame_identifier"),
        "lap_distance_m": point.get("lap_distance_m"),
        "session_time_s": point.get("session_time_s"),
        "lap_time_s": point.get("lap_time_s"),
        "lap_time_ms": point.get("lap_time_ms"),
    }


def _sample_anchor(sample: Mapping[str, object]) -> dict[str, object]:
    frame = sample.get("frame_identifier")
    lap_time_ms = _finite_number(sample.get("current_lap_time_ms"))
    if lap_time_ms is not None and lap_time_ms < 0:
        lap_time_ms = None
    return {
        "frame_identifier": frame if isinstance(frame, int) and not isinstance(frame, bool) else None,
        "lap_distance_m": _finite_number(sample.get("lap_distance_m")),
        "session_time_s": _finite_number(sample.get("session_time_s")),
        "lap_time_s": lap_time_ms / 1000.0 if lap_time_ms is not None else None,
        "lap_time_ms": lap_time_ms,
    }


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None
