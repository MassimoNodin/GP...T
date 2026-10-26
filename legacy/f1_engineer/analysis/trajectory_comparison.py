from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .continuity import MAX_SESSION_TIME_GAP_S, session_time_discontinuity
from .trajectory_probe import MAX_PROBE_DISTANCE_BRACKET_SPAN_M


TRAJECTORY_COMPARISON_VERSION = "trajectory-comparison-preview-v1"
MAX_COMPARISON_PREVIEW_POINTS_PER_ATTEMPT = 2_000
MAX_COMPARISON_PREVIEW_SEGMENTS_PER_ATTEMPT = 256
_ALLOWED_POLICIES = {"time_trial", "practice_qualifying"}


class TrajectoryComparisonUnavailable(ValueError):
    """A paired path preview cannot be made from supported recorded evidence."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def build_trajectory_comparison_preview(
    target_preview: Mapping[str, object],
    reference_preview: Mapping[str, object],
    comparison: Mapping[str, object],
) -> dict[str, object]:
    """Build one bounded, common-coordinate view of two observed lap paths."""
    policy = comparison.get("comparison_policy")
    if policy not in _ALLOWED_POLICIES:
        raise TrajectoryComparisonUnavailable("unsupported_comparison_policy")

    target_evidence = _mapping(comparison.get("target"))
    reference_evidence = _mapping(comparison.get("reference"))
    if target_evidence is None or reference_evidence is None:
        raise TrajectoryComparisonUnavailable("trajectory_pair_provenance_unavailable")
    if not _same_scope(target_evidence, reference_evidence):
        raise TrajectoryComparisonUnavailable("trajectory_pair_scope_mismatch")

    target_path, target_bounds = _validated_path(target_preview, target_evidence)
    reference_path, reference_bounds = _validated_path(
        reference_preview, reference_evidence
    )
    all_bounds = (
        min(target_bounds[0], reference_bounds[0]),
        max(target_bounds[1], reference_bounds[1]),
        min(target_bounds[2], reference_bounds[2]),
        max(target_bounds[3], reference_bounds[3]),
    )
    target_probe = _mapping(target_path.get("position_probe"))
    reference_probe = _mapping(reference_path.get("position_probe"))
    position_probe: dict[str, object] | None = None
    if target_probe is not None or reference_probe is not None:
        if target_probe is None or reference_probe is None:
            raise TrajectoryComparisonUnavailable("trajectory_probe_pair_incomplete")
        target_distance = target_probe.get("requested_distance_m")
        reference_distance = reference_probe.get("requested_distance_m")
        if target_distance != reference_distance:
            raise TrajectoryComparisonUnavailable("trajectory_probe_distance_mismatch")
        track_length = _finite_number(comparison.get("track_length_m"))
        requested_distance = _finite_number(target_distance)
        distance_outside_track = requested_distance is not None and (
            track_length is None
            or requested_distance < 0.0
            or requested_distance > track_length
        )
        if distance_outside_track and (
            target_probe.get("status") == "available"
            or reference_probe.get("status") == "available"
        ):
            raise TrajectoryComparisonUnavailable(
                "trajectory_probe_distance_outside_track_range"
            )
        probe_positions = [
            _mapping(probe.get("position_world_xyz_m"))
            for probe in (target_probe, reference_probe)
            if probe.get("status") == "available"
        ]
        probe_coordinates = [
            (float(position["x"]), float(position["z"]))
            for position in probe_positions
            if position is not None
        ]
        if probe_coordinates:
            all_bounds = (
                min(all_bounds[0], *(point[0] for point in probe_coordinates)),
                max(all_bounds[1], *(point[0] for point in probe_coordinates)),
                min(all_bounds[2], *(point[1] for point in probe_coordinates)),
                max(all_bounds[3], *(point[1] for point in probe_coordinates)),
            )
        target_position = _mapping(target_probe.get("position_world_xyz_m"))
        reference_position = _mapping(reference_probe.get("position_world_xyz_m"))
        if target_probe.get("status") == reference_probe.get("status") == "available":
            if target_position is None or reference_position is None:
                raise TrajectoryComparisonUnavailable("trajectory_probe_positions_invalid")
            delta_x = float(target_position["x"]) - float(reference_position["x"])
            delta_z = float(target_position["z"]) - float(reference_position["z"])
            difference: dict[str, object] = {
                "status": "available",
                "world_x_m": delta_x,
                "world_z_m": delta_z,
                "horizontal_separation_m": math.hypot(delta_x, delta_z),
                "reason_code": None,
            }
        else:
            difference = {
                "status": "unavailable",
                "world_x_m": None,
                "world_z_m": None,
                "horizontal_separation_m": None,
                "reason_code": "one_or_both_positions_unavailable",
            }
        position_probe = {
            "schema_version": 1,
            "analysis_version": "paired-observed-position-probe-v1",
            "status": difference["status"],
            "requested_distance_m": target_distance,
            "target": target_probe,
            "reference": reference_probe,
            "difference": difference,
        }
    plot_bounds = _equal_scale_bounds(all_bounds)
    if any(
        not math.isfinite(value)
        for axis_bounds in plot_bounds.values()
        for value in axis_bounds
    ):
        raise TrajectoryComparisonUnavailable("trajectory_preview_coordinates_invalid")

    run_evidence = _mapping(comparison.get("processing_run_evidence")) or {}
    target_run = _mapping(run_evidence.get("target")) or {}
    reference_run = _mapping(run_evidence.get("reference")) or {}
    target_path["capture_evidence"] = _mapping(target_run.get("capture"))
    reference_path["capture_evidence"] = _mapping(reference_run.get("capture"))

    result = {
        "schema_version": 1,
        "analysis_version": TRAJECTORY_COMPARISON_VERSION,
        "artifact_kind": "observed_trajectory_comparison_preview",
        "status": "available",
        "comparison_policy": policy,
        "diagnostic_only": True,
        "is_centreline": False,
        "coordinate_projection": {
            "horizontal_axis": "world_x",
            "vertical_axis": "world_z",
            "units": "m",
            "orientation_claim": None,
            "equal_scale": True,
        },
        "plot_bounds_world_xz_m": plot_bounds,
        "paths": {"target": target_path, "reference": reference_path},
        "limits": {
            "points_per_attempt": MAX_COMPARISON_PREVIEW_POINTS_PER_ATTEMPT,
            "segments_per_attempt": MAX_COMPARISON_PREVIEW_SEGMENTS_PER_ATTEMPT,
            "combined_points": 2 * MAX_COMPARISON_PREVIEW_POINTS_PER_ATTEMPT,
        },
        "limits_applied": {
            "target_points": target_path["preview"]["rendered_point_count"],
            "reference_points": reference_path["preview"]["rendered_point_count"],
            "target_segments": target_path["preview"]["rendered_segment_count"],
            "reference_segments": reference_path["preview"]["rendered_segment_count"],
        },
    }
    if position_probe is not None:
        result["position_probe"] = position_probe
    return result


def _validated_path(
    preview: Mapping[str, object], attempt: Mapping[str, Any]
) -> tuple[dict[str, object], tuple[float, float, float, float]]:
    if (
        preview.get("schema_version") != 1
        or preview.get("artifact_kind") != "observed_driven_trajectory_preview"
        or preview.get("diagnostic_only") is not True
        or preview.get("is_centreline") is not False
    ):
        raise TrajectoryComparisonUnavailable("trajectory_preview_schema_unsupported")
    projection = _mapping(preview.get("coordinate_projection"))
    if (
        projection is None
        or projection.get("horizontal_axis") != "world_x"
        or projection.get("vertical_axis") != "world_z"
        or projection.get("units") != "m"
    ):
        raise TrajectoryComparisonUnavailable("trajectory_preview_projection_unsupported")

    source = _mapping(preview.get("source"))
    if source is None or not _source_matches_attempt(source, attempt):
        raise TrajectoryComparisonUnavailable("trajectory_pair_trace_provenance_mismatch")
    trace_schema = _integer(attempt.get("trace_schema_version"))
    if trace_schema is None or trace_schema < 2:
        raise TrajectoryComparisonUnavailable("motion_unavailable_for_trace_schema")

    segments = preview.get("segments")
    preview_summary = _mapping(preview.get("preview"))
    coverage = _mapping(preview.get("coverage"))
    if (
        not isinstance(segments, list)
        or preview_summary is None
        or coverage is None
        or len(segments) > MAX_COMPARISON_PREVIEW_SEGMENTS_PER_ATTEMPT
    ):
        raise TrajectoryComparisonUnavailable("trajectory_preview_limits_invalid")
    source_position_count = _integer(coverage.get("position_sample_count"))
    position_coverage = _finite_number(coverage.get("position_sample_coverage"))
    rendered_point_count = _integer(preview_summary.get("rendered_point_count"))
    rendered_segment_count = _integer(preview_summary.get("rendered_segment_count"))
    if (
        source_position_count is None
        or source_position_count < 0
        or position_coverage is None
        or not 0.0 <= position_coverage <= 1.0
        or rendered_point_count is None
        or rendered_point_count <= 0
        or rendered_point_count > MAX_COMPARISON_PREVIEW_POINTS_PER_ATTEMPT
        or rendered_segment_count != len(segments)
    ):
        raise TrajectoryComparisonUnavailable("trajectory_preview_limits_invalid")

    normalized_segments: list[dict[str, object]] = []
    points_count = 0
    min_x = math.inf
    max_x = -math.inf
    min_z = math.inf
    max_z = -math.inf
    for segment in segments:
        segment_mapping = _mapping(segment)
        points = segment_mapping.get("points") if segment_mapping is not None else None
        if (
            segment_mapping is None
            or not isinstance(points, list)
            or not points
        ):
            raise TrajectoryComparisonUnavailable("trajectory_preview_segments_invalid")
        safe_points: list[dict[str, object]] = []
        for point in points:
            point_mapping = _mapping(point)
            position = _mapping(point_mapping.get("world_position_m")) if point_mapping else None
            x = _finite_number(position.get("x")) if position else None
            z = _finite_number(position.get("z")) if position else None
            if x is None or z is None:
                raise TrajectoryComparisonUnavailable("trajectory_preview_points_invalid")
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_z = min(min_z, z)
            max_z = max(max_z, z)
            safe_points.append(dict(point_mapping))
        points_count += len(safe_points)
        normalized_segments.append(
            {
                "segment_index": segment_mapping.get("segment_index"),
                "break_before_reasons": _string_list(
                    segment_mapping.get("break_before_reasons")
                ),
                "sample_count": _integer(segment_mapping.get("sample_count")),
                "rendered_point_count": len(safe_points),
                "points": safe_points,
            }
        )
    if (
        points_count != rendered_point_count
        or points_count > MAX_COMPARISON_PREVIEW_POINTS_PER_ATTEMPT
    ):
        raise TrajectoryComparisonUnavailable("trajectory_preview_limits_invalid")
    if points_count == 0:
        raise TrajectoryComparisonUnavailable("no_observed_position_samples")

    path_source = {
        key: source.get(key)
        for key in (
            "attempt_key",
            "run_id",
            "session_uid",
            "car_index",
            "disposition",
            "lap_time_ms",
            "game_valid",
            "reference_eligible",
            "exclusion_reasons",
            "trace_sha256",
            "trace_schema_version",
        )
    }
    attempt_evidence = {
        key: attempt.get(key)
        for key in (
            "disposition",
            "lap_time_ms",
            "game_valid",
            "reference_eligible",
            "superseded",
            "lifecycle_assessed",
            "lifecycle_exclusions",
            "exclusion_reasons",
            "source_sample_count",
        )
    }
    position_probe = preview.get("position_probe")
    if position_probe is not None:
        position_probe = _validated_probe(position_probe, attempt)
    return (
        {
            "source": path_source,
            "attempt_evidence": attempt_evidence,
            "coverage": dict(coverage),
            "preview": dict(preview_summary),
            "segments": normalized_segments,
            "break_examples": _bounded_examples(preview.get("break_examples")),
            "unsupported_examples": _bounded_examples(
                preview.get("unsupported_examples")
            ),
            **({"position_probe": position_probe} if position_probe is not None else {}),
        },
        (min_x, max_x, min_z, max_z),
    )


def _validated_probe(
    value: object, attempt: Mapping[str, Any]
) -> dict[str, object]:
    probe = _mapping(value)
    source = _mapping(probe.get("source")) if probe is not None else None
    status = probe.get("status") if probe is not None else None
    if (
        probe is None
        or probe.get("schema_version") != 1
        or probe.get("analysis_version") != "observed-position-probe-v1"
        or status not in {"available", "unavailable"}
        or source is None
        or not _source_matches_attempt(source, attempt)
    ):
        raise TrajectoryComparisonUnavailable("trajectory_probe_provenance_invalid")
    distance = probe.get("requested_distance_m")
    if distance is not None and _finite_number(distance) is None:
        raise TrajectoryComparisonUnavailable("trajectory_probe_distance_invalid")
    anchors = probe.get("source_anchors")
    if not isinstance(anchors, list):
        raise TrajectoryComparisonUnavailable("trajectory_probe_anchors_invalid")
    if status == "unavailable":
        reason = probe.get("reason_code")
        if (
            not isinstance(reason, str)
            or not reason
            or anchors
            or probe.get("method") is not None
            or probe.get("segment_index") is not None
            or probe.get("interpolation_fraction") is not None
        ):
            raise TrajectoryComparisonUnavailable("trajectory_probe_result_invalid")
        if probe.get("position_world_xyz_m") is not None:
            raise TrajectoryComparisonUnavailable("trajectory_probe_result_invalid")
    else:
        position = _mapping(probe.get("position_world_xyz_m"))
        method = probe.get("method")
        segment_index = _integer(probe.get("segment_index"))
        fraction = _finite_number(probe.get("interpolation_fraction"))
        if (
            position is None
            or any(_finite_number(position.get(axis)) is None for axis in ("x", "y", "z"))
            or method not in {"exact_source_observation", "linear_interpolation"}
            or segment_index is None
            or segment_index < 0
            or fraction is None
            or not 0.0 <= fraction <= 1.0
            or _finite_number(distance) is None
            or len(anchors) != (1 if method == "exact_source_observation" else 2)
        ):
            raise TrajectoryComparisonUnavailable("trajectory_probe_result_invalid")
        normalized_anchors: list[Mapping[str, Any]] = []
        for anchor in anchors:
            anchor_mapping = _mapping(anchor)
            anchor_position = (
                _mapping(anchor_mapping.get("world_position_m"))
                if anchor_mapping is not None else None
            )
            if (
                anchor_mapping is None
                or not _valid_source_frame(anchor_mapping.get("frame_identifier"))
                or _finite_number(anchor_mapping.get("lap_distance_m")) is None
                or _finite_number(anchor_mapping.get("session_time_s")) is None
                or anchor_position is None
                or any(_finite_number(anchor_position.get(axis)) is None for axis in ("x", "y", "z"))
            ):
                raise TrajectoryComparisonUnavailable("trajectory_probe_anchors_invalid")
            normalized_anchors.append(anchor_mapping)
        probe_position = {
            axis: float(position[axis]) for axis in ("x", "y", "z")
        }
        requested_distance = float(distance)
        if method == "exact_source_observation":
            anchor = normalized_anchors[0]
            anchor_position = _mapping(anchor.get("world_position_m")) or {}
            if (
                float(anchor["lap_distance_m"]) != requested_distance
                or fraction != 0.0
                or any(
                    not math.isclose(
                        probe_position[axis],
                        float(anchor_position[axis]),
                        rel_tol=0.0,
                        abs_tol=1e-9,
                    )
                    for axis in ("x", "y", "z")
                )
            ):
                raise TrajectoryComparisonUnavailable("trajectory_probe_result_invalid")
        else:
            left, right = normalized_anchors
            left_distance = float(left["lap_distance_m"])
            right_distance = float(right["lap_distance_m"])
            left_time = float(left["session_time_s"])
            right_time = float(right["session_time_s"])
            left_frame = int(left["frame_identifier"])
            right_frame = int(right["frame_identifier"])
            distance_span = right_distance - left_distance
            expected_fraction = (
                (requested_distance - left_distance) / distance_span
                if distance_span > 0.0
                else -1.0
            )
            left_position = _mapping(left.get("world_position_m")) or {}
            right_position = _mapping(right.get("world_position_m")) or {}
            if (
                not left_distance < requested_distance < right_distance
                or distance_span > MAX_PROBE_DISTANCE_BRACKET_SPAN_M
                or ((right_frame - left_frame) & 0xFFFFFFFF) != 1
                or session_time_discontinuity(
                    left_time,
                    right_time,
                    max_gap_s=MAX_SESSION_TIME_GAP_S,
                ) is not None
                or not math.isclose(fraction, expected_fraction, rel_tol=0.0, abs_tol=1e-12)
                or any(
                    not math.isclose(
                        probe_position[axis],
                        float(left_position[axis])
                        + fraction * (float(right_position[axis]) - float(left_position[axis])),
                        rel_tol=1e-12,
                        abs_tol=1e-9,
                    )
                    for axis in ("x", "y", "z")
                )
            ):
                raise TrajectoryComparisonUnavailable("trajectory_probe_result_invalid")
    return dict(probe)


def _source_matches_attempt(source: Mapping[str, Any], attempt: Mapping[str, Any]) -> bool:
    if (
        not _nonempty_string(attempt.get("attempt_key"))
        or not _nonempty_string(attempt.get("run_id"))
        or not _nonempty_string(attempt.get("session_uid"))
        or _integer(attempt.get("car_index")) is None
        or _integer(attempt.get("car_index")) < 0
    ):
        return False
    for key in ("attempt_key", "run_id", "session_uid", "car_index", "trace_sha256"):
        if source.get(key) != attempt.get(key):
            return False
    checksum = source.get("trace_sha256")
    if not isinstance(checksum, str) or len(checksum) != 64:
        return False
    try:
        int(checksum, 16)
    except ValueError:
        return False
    return source.get("trace_schema_version") == attempt.get("trace_schema_version")


def _same_scope(target: Mapping[str, Any], reference: Mapping[str, Any]) -> bool:
    if (
        not _nonempty_string(target.get("run_id"))
        or not _nonempty_string(target.get("session_uid"))
        or _integer(target.get("car_index")) is None
        or _integer(target.get("car_index")) < 0
    ):
        return False
    return all(
        target.get(key) == reference.get(key)
        for key in ("run_id", "session_uid", "car_index")
    )


def _equal_scale_bounds(
    bounds: tuple[float, ...], *, padding_fraction: float = 0.04
) -> dict[str, list[float]]:
    min_x, max_x, min_z, max_z = bounds
    center_x = (min_x + max_x) / 2.0
    center_z = (min_z + max_z) / 2.0
    span = max(max_x - min_x, max_z - min_z, 2.0)
    half_span = span * (0.5 + padding_fraction)
    return {
        "world_x": [center_x - half_span, center_x + half_span],
        "world_z": [center_z - half_span, center_z + half_span],
    }


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _sequence(value: object) -> list[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return list(value)


def _bounded_examples(value: object, limit: int = 20) -> list[object]:
    return _sequence(value)[:limit]


def _string_list(value: object) -> list[str]:
    return [item for item in _sequence(value) if isinstance(item, str)]


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _valid_source_frame(value: object) -> bool:
    frame = _integer(value)
    return frame is not None and 0 <= frame <= 0xFFFFFFFF


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None
