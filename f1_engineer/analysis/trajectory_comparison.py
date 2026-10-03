from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


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

    return {
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
        },
        (min_x, max_x, min_z, max_z),
    )


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


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None
