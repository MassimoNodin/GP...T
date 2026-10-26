from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .continuity import MAX_SESSION_TIME_GAP_S, session_time_discontinuity


TRAJECTORY_PROBE_VERSION = "observed-position-probe-v1"
MAX_PROBE_DISTANCE_BRACKET_SPAN_M = 25.0
_FRAME_MASK = 0xFFFFFFFF


def build_observed_position_probe(
    trajectory: Mapping[str, object],
    distance_m: float,
    *,
    track_length_m: float,
) -> dict[str, object]:
    """Find an exact or continuously bracketed world position at lap distance."""
    source = _mapping(trajectory.get("source"))
    source_identity = {
        key: source.get(key)
        for key in (
            "attempt_key",
            "run_id",
            "session_uid",
            "car_index",
            "trace_sha256",
            "trace_schema_version",
        )
    } if source is not None else None

    if not _finite(distance_m):
        return _unavailable(source_identity, None, "probe_distance_invalid")
    if not _finite(track_length_m) or track_length_m <= 0:
        return _unavailable(source_identity, distance_m, "probe_track_length_invalid")
    if distance_m < 0 or distance_m > track_length_m:
        return _unavailable(source_identity, distance_m, "probe_distance_outside_track_range")
    if source is None:
        return _unavailable(source_identity, distance_m, "probe_source_provenance_unavailable")
    schema_version = _integer(source.get("trace_schema_version"))
    if schema_version is None or schema_version < 2:
        return _unavailable(source_identity, distance_m, "motion_unavailable_for_trace_schema")

    segments = trajectory.get("segments")
    if not isinstance(segments, list):
        return _unavailable(source_identity, distance_m, "probe_source_segments_unavailable")

    exact_matches: list[tuple[int, Mapping[str, Any]]] = []
    brackets: list[tuple[int, Mapping[str, Any], Mapping[str, Any]]] = []
    descending_crossings: list[tuple[int, Mapping[str, Any], Mapping[str, Any]]] = []
    segment_ranges: list[tuple[float, float]] = []
    for segment in segments:
        segment_mapping = _mapping(segment)
        points = segment_mapping.get("points") if segment_mapping is not None else None
        segment_index = (
            _integer(segment_mapping.get("segment_index"))
            if segment_mapping is not None else None
        )
        if (
            segment_mapping is None
            or segment_index is None
            or not isinstance(points, list)
        ):
            return _unavailable(source_identity, distance_m, "probe_source_segments_invalid")
        normalized_points: list[Mapping[str, Any]] = []
        distances: list[float] = []
        for point in points:
            point_mapping = _mapping(point)
            point_distance = (
                _finite_number(point_mapping.get("lap_distance_m"))
                if point_mapping is not None else None
            )
            if point_mapping is None or point_distance is None:
                return _unavailable(source_identity, distance_m, "probe_source_points_invalid")
            normalized_points.append(point_mapping)
            distances.append(point_distance)
            if point_distance == distance_m:
                exact_matches.append((segment_index, point_mapping))
        if distances:
            segment_ranges.append((min(distances), max(distances)))
        for index in range(len(normalized_points) - 1):
            left_distance = distances[index]
            right_distance = distances[index + 1]
            if left_distance < distance_m < right_distance:
                brackets.append(
                    (segment_index, normalized_points[index], normalized_points[index + 1])
                )
            elif right_distance < distance_m < left_distance:
                descending_crossings.append(
                    (segment_index, normalized_points[index], normalized_points[index + 1])
                )

    candidate_count = (
        len(exact_matches) + len(brackets) + len(descending_crossings)
    )
    if candidate_count > 1:
        return _unavailable(source_identity, distance_m, "probe_distance_support_ambiguous")
    if not candidate_count:
        if not segment_ranges:
            reason = "no_observed_position_samples"
        elif any(low < distance_m < high for low, high in segment_ranges):
            reason = "probe_distance_support_repeated_or_nonmonotonic"
        elif any(low < distance_m for low, _ in segment_ranges) and any(
            high > distance_m for _, high in segment_ranges
        ):
            reason = "probe_crosses_trajectory_discontinuity"
        else:
            reason = "probe_distance_not_observed"
        return _unavailable(source_identity, distance_m, reason)
    if descending_crossings:
        return _unavailable(
            source_identity,
            distance_m,
            "probe_distance_support_repeated_or_nonmonotonic",
        )

    if exact_matches:
        segment_index, point = exact_matches[0]
        position = _world_position(point)
        if position is None:
            return _unavailable(source_identity, distance_m, "probe_world_position_invalid")
        anchor = _source_anchor(point)
        if anchor is None:
            return _unavailable(source_identity, distance_m, "probe_source_anchors_invalid")
        return {
            "schema_version": 1,
            "analysis_version": TRAJECTORY_PROBE_VERSION,
            "status": "available",
            "requested_distance_m": distance_m,
            "method": "exact_source_observation",
            "segment_index": segment_index,
            "interpolation_fraction": 0.0,
            "position_world_xyz_m": position,
            "source_anchors": [anchor],
            "source": source_identity,
            "reason_code": None,
        }

    segment_index, left, right = brackets[0]
    left_distance = _finite_number(left.get("lap_distance_m"))
    right_distance = _finite_number(right.get("lap_distance_m"))
    left_time = _finite_number(left.get("session_time_s"))
    right_time = _finite_number(right.get("session_time_s"))
    left_frame = _integer(left.get("frame_identifier"))
    right_frame = _integer(right.get("frame_identifier"))
    left_position = _world_position(left)
    right_position = _world_position(right)
    if (
        left_distance is None
        or right_distance is None
        or right_distance <= left_distance
        or left_time is None
        or right_time is None
        or left_frame is None
        or right_frame is None
        or not 0 <= left_frame <= _FRAME_MASK
        or not 0 <= right_frame <= _FRAME_MASK
    ):
        return _unavailable(source_identity, distance_m, "probe_source_anchors_invalid")
    if left_position is None or right_position is None:
        return _unavailable(source_identity, distance_m, "probe_world_position_invalid")
    distance_span = right_distance - left_distance
    if distance_span > MAX_PROBE_DISTANCE_BRACKET_SPAN_M:
        return _unavailable(source_identity, distance_m, "probe_distance_bracket_too_wide")
    frame_delta = (right_frame - left_frame) & _FRAME_MASK
    if frame_delta != 1:
        return _unavailable(source_identity, distance_m, "probe_frame_discontinuity")
    if session_time_discontinuity(
        left_time,
        right_time,
        max_gap_s=MAX_SESSION_TIME_GAP_S,
    ) is not None:
        return _unavailable(source_identity, distance_m, "probe_session_time_discontinuity")

    fraction = (distance_m - left_distance) / distance_span
    position = {
        axis: left_position[axis]
        + fraction * (right_position[axis] - left_position[axis])
        for axis in ("x", "y", "z")
    }
    left_anchor = _source_anchor(left)
    right_anchor = _source_anchor(right)
    if left_anchor is None or right_anchor is None:
        return _unavailable(source_identity, distance_m, "probe_source_anchors_invalid")
    return {
        "schema_version": 1,
        "analysis_version": TRAJECTORY_PROBE_VERSION,
        "status": "available",
        "requested_distance_m": distance_m,
        "method": "linear_interpolation",
        "segment_index": segment_index,
        "interpolation_fraction": fraction,
        "position_world_xyz_m": position,
        "source_anchors": [left_anchor, right_anchor],
        "source": source_identity,
        "reason_code": None,
    }


def _unavailable(
    source: dict[str, object] | None,
    distance_m: float | None,
    reason_code: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "analysis_version": TRAJECTORY_PROBE_VERSION,
        "status": "unavailable",
        "requested_distance_m": distance_m,
        "method": None,
        "segment_index": None,
        "interpolation_fraction": None,
        "position_world_xyz_m": None,
        "source_anchors": [],
        "source": source,
        "reason_code": reason_code,
    }


def _source_anchor(point: Mapping[str, object]) -> dict[str, object] | None:
    frame = _integer(point.get("frame_identifier"))
    distance = _finite_number(point.get("lap_distance_m"))
    session_time = _finite_number(point.get("session_time_s"))
    position = _world_position(point)
    if (
        frame is None
        or not 0 <= frame <= _FRAME_MASK
        or distance is None
        or session_time is None
        or position is None
    ):
        return None
    return {
        "frame_identifier": frame,
        "lap_distance_m": distance,
        "session_time_s": session_time,
        "world_position_m": position,
    }


def _world_position(point: Mapping[str, object]) -> dict[str, float] | None:
    position = _mapping(point.get("world_position_m"))
    if position is None:
        return None
    values = {axis: _finite_number(position.get(axis)) for axis in ("x", "y", "z")}
    if any(value is None for value in values.values()):
        return None
    return {axis: float(value) for axis, value in values.items() if value is not None}


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _finite(value: object) -> bool:
    return _finite_number(value) is not None
