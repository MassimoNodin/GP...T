from __future__ import annotations

import math
from typing import Mapping, Sequence

from .continuity import MAX_SESSION_TIME_GAP_S, float32_ulp, session_time_discontinuity


TRAJECTORY_SCHEMA_VERSION = 1
_FRAME_MASK = 0xFFFFFFFF
_SERIAL_HALF_RANGE = 0x80000000
_MAX_WORLD_POSITION_STEP_M = 25.0
_LAP_TIME_REGRESSION_TOLERANCE_S = 0.020
_LAP_DISTANCE_REGRESSION_TOLERANCE_M = 0.01


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
