from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence

from ..storage.query import (
    TRAJECTORY_TRACE_COLUMNS,
    AttemptTraceReadLimitError,
    StoredAttemptTrace,
    load_attempt_trace,
)
from ..tracks.geometry import (
    GeometryModel,
    project_sample,
    unsupported_projection_intervals,
)
from ..tracks.geometry_loader import load_geometry_model
from .source_limits import (
    ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
    ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
    ANALYSIS_SOURCE_TRACE_BYTE_LIMIT,
    ANALYSIS_SOURCE_TRACE_ROW_LIMIT,
)
from .trajectory import (
    TrajectoryPreviewUnavailable,
    build_observed_trajectory,
)


TRAJECTORY_PROJECTION_POINT_LIMIT = 2_000
TRAJECTORY_PROJECTION_RUN_LIMIT = 256
TRAJECTORY_PROJECTION_EXAMPLE_LIMIT = 20
TRAJECTORY_PROJECTION_WORK_LIMIT = 4_000_000
TRACK_LENGTH_TOLERANCE_M = 1.0


class TrajectoryProjectionUnavailable(ValueError):
    """A source trace cannot be projected within explicit evidence limits."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def project_attempt_trajectory(
    database_path: str | Path,
    attempt_key: str,
    geometry_path: str | Path,
    *,
    declared_layout_id: str,
) -> dict[str, object]:
    """Project one checksummed attempt against explicitly selected geometry."""
    try:
        model = load_geometry_model(geometry_path)
    except ValueError as exc:
        raise TrajectoryProjectionUnavailable("geometry_artifact_invalid") from exc
    if not isinstance(declared_layout_id, str) or not declared_layout_id.strip():
        raise TrajectoryProjectionUnavailable("layout_assertion_required")
    if declared_layout_id != model.layout_id:
        raise TrajectoryProjectionUnavailable("layout_assertion_mismatch")

    try:
        attempt = load_attempt_trace(
            database_path,
            attempt_key,
            columns=TRAJECTORY_TRACE_COLUMNS,
            max_trace_bytes=ANALYSIS_SOURCE_TRACE_BYTE_LIMIT,
            max_trace_rows=ANALYSIS_SOURCE_TRACE_ROW_LIMIT,
            max_context_segments=ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
            max_context_bytes=ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
        )
    except AttemptTraceReadLimitError as exc:
        raise TrajectoryProjectionUnavailable(
            f"trajectory_projection_source_{exc.limit_kind}_limit_exceeded"
        ) from exc
    if attempt is None:
        raise TrajectoryProjectionUnavailable("trajectory_attempt_unavailable")

    source = _source_identity(attempt)
    geometry = _geometry_identity(model)
    context, context_reasons = _stable_context(attempt)
    base = _base_document(
        source=source,
        geometry=geometry,
        declared_layout_id=declared_layout_id,
        context=context,
    )
    if context_reasons:
        return _unavailable_document(base, context_reasons)

    assert context is not None
    context_reasons = _geometry_context_reasons(model, context)
    if context_reasons:
        return _unavailable_document(base, context_reasons)

    unsupported_intervals = unsupported_projection_intervals(model)
    anchor_bracket_count = sum(
        len(segment.anchors) - 1 for segment in model.segments
    )
    base["geometry_support_preflight"] = {
        "anchor_brackets_checked": anchor_bracket_count,
        "unsupported_anchor_interval_count": len(unsupported_intervals),
        "example_limit": TRAJECTORY_PROJECTION_EXAMPLE_LIMIT,
        "unsupported_intervals": [
            {
                "segment_id": interval.segment_id,
                "start_distance_m": interval.start_distance_m,
                "end_distance_m": interval.end_distance_m,
                "reason": interval.reason,
            }
            for interval in unsupported_intervals[:TRAJECTORY_PROJECTION_EXAMPLE_LIMIT]
        ],
        "omitted_count": max(
            0, len(unsupported_intervals) - TRAJECTORY_PROJECTION_EXAMPLE_LIMIT
        ),
    }
    if unsupported_intervals:
        return _unavailable_document(base, ["geometry_anchor_interval_unusable"])

    estimated_work = _estimated_lookup_work(len(attempt.samples), model)
    base["work"] = {
        "estimated_segment_anchor_lookup_operations": estimated_work,
        "geometry_anchor_preflight_operations": anchor_bracket_count,
        "limit": TRAJECTORY_PROJECTION_WORK_LIMIT,
        "estimation": (
            "geometry_anchor_brackets + source_rows * "
            "(2 * geometry_segment_count + ceil(log2(max_segment_anchors + 1)) + 1)"
        ),
    }
    if estimated_work > TRAJECTORY_PROJECTION_WORK_LIMIT:
        return _unavailable_document(base, ["projection_work_limit_exceeded"])

    trajectory = build_observed_trajectory(
        attempt_key=attempt.attempt_key,
        run_id=attempt.run_id,
        session_uid=attempt.session_uid,
        car_index=attempt.car_index,
        disposition=attempt.disposition,
        lap_time_ms=attempt.lap_time_ms,
        game_valid=attempt.game_valid,
        reference_eligible=attempt.reference_eligible,
        exclusion_reasons=attempt.exclusion_reasons,
        trace_sha256=attempt.trace_sha256,
        trace_schema_version=attempt.trace_schema_version,
        context_segments=attempt.context_segments,
        samples=attempt.samples,
    )
    source_segments = trajectory.get("segments")
    source_breaks = trajectory.get("breaks")
    unsupported_source = trajectory.get("unsupported_samples")
    coverage = trajectory.get("coverage")
    if not all(isinstance(value, list) for value in (source_segments, source_breaks, unsupported_source)):
        raise ValueError("trajectory source evidence is malformed")
    if not isinstance(coverage, Mapping):
        raise ValueError("trajectory source coverage is malformed")

    if len(source_segments) > TRAJECTORY_PROJECTION_RUN_LIMIT:
        return _unavailable_document(base, ["trajectory_projection_too_fragmented"])

    reason_counts: Counter[str] = Counter()
    for item in unsupported_source:
        if isinstance(item, Mapping) and isinstance(item.get("reason"), str):
            reason_counts[item["reason"]] += 1

    projected_runs: list[dict[str, object]] = []
    geometry_gaps: list[dict[str, object]] = []
    geometry_boundaries: list[dict[str, object]] = []
    geometry_gap_count = 0
    geometry_boundary_count = 0
    projected_point_count = 0
    projection_unsupported_count = 0

    for segment_index, source_segment in enumerate(source_segments):
        if not isinstance(source_segment, Mapping):
            raise ValueError("trajectory source segment is malformed")
        source_points = source_segment.get("points")
        break_reasons = source_segment.get("break_before_reasons")
        if (
            not isinstance(source_points, list)
            or any(not isinstance(point, Mapping) for point in source_points)
            or not isinstance(break_reasons, list)
            or any(not isinstance(reason, str) for reason in break_reasons)
        ):
            raise ValueError("trajectory source segment evidence is malformed")

        current_points: list[dict[str, object]] = []
        pending_break_reasons = list(break_reasons)
        current_break_reasons: list[str] = []
        pending_gap: dict[str, object] | None = None

        def finish_run() -> None:
            nonlocal current_points, current_break_reasons, projected_runs
            if not current_points:
                return
            if len(projected_runs) >= TRAJECTORY_PROJECTION_RUN_LIMIT:
                raise TrajectoryProjectionUnavailable(
                    "trajectory_projection_too_fragmented"
                )
            projected_runs.append(
                {
                    "run_index": len(projected_runs),
                    "source_segment_index": segment_index,
                    "break_before_reasons": current_break_reasons,
                    "source_sample_count": len(current_points),
                    "start_anchor": current_points[0]["source_anchor"],
                    "end_anchor": current_points[-1]["source_anchor"],
                    "points": current_points,
                }
            )
            current_points = []
            current_break_reasons = []

        def finish_gap(end_point: Mapping[str, object] | None) -> None:
            nonlocal pending_gap, geometry_gap_count
            if pending_gap is None:
                return
            if end_point is not None:
                pending_gap["end_anchor"] = _source_anchor(end_point)
            if len(geometry_gaps) < TRAJECTORY_PROJECTION_EXAMPLE_LIMIT:
                geometry_gaps.append(dict(pending_gap))
            geometry_gap_count += 1
            pending_gap = None

        for point in source_points:
            position = point.get("world_position_m")
            if not isinstance(position, Mapping):
                raise ValueError("trajectory source point position is malformed")
            projected = project_sample(
                model,
                packet_format=int(context["packet_format"]),
                track_id=int(context["track_id"]),
                layout_id=declared_layout_id,
                game_distance_m=_finite_number(point.get("lap_distance_m")),
                world_x_m=_finite_number(position.get("x")),
                world_y_m=_finite_number(position.get("y")),
                world_z_m=_finite_number(position.get("z")),
            )
            if projected.get("status") != "supported":
                reason = projected.get("reason")
                reason_code = reason if isinstance(reason, str) else "projection_unavailable"
                reason_counts[reason_code] += 1
                projection_unsupported_count += 1
                finish_run()
                if pending_gap is None:
                    pending_gap = {
                        "start_anchor": _source_anchor(point),
                        "end_anchor": None,
                        "sample_count": 0,
                        "reason_counts": {},
                    }
                pending_gap["sample_count"] = int(pending_gap["sample_count"]) + 1
                pending_gap["end_anchor"] = _source_anchor(point)
                gap_reasons = pending_gap["reason_counts"]
                assert isinstance(gap_reasons, dict)
                gap_reasons[reason_code] = int(gap_reasons.get(reason_code, 0)) + 1
                if "unsupported_geometry_span" not in pending_break_reasons:
                    pending_break_reasons.append("unsupported_geometry_span")
                continue

            if pending_gap is not None:
                finish_gap(point)
            segment_id = projected.get("segment_id")
            if current_points and current_points[-1].get("geometry_segment_id") != segment_id:
                previous_point = current_points[-1]
                previous_segment_id = previous_point.get("geometry_segment_id")
                finish_run()
                geometry_boundary_count += 1
                if len(geometry_boundaries) < TRAJECTORY_PROJECTION_EXAMPLE_LIMIT:
                    geometry_boundaries.append(
                        {
                            "reason": "geometry_segment_transition_between_source_samples",
                            "from_segment_id": previous_segment_id,
                            "to_segment_id": segment_id,
                            "before_anchor": previous_point["source_anchor"],
                            "after_anchor": _source_anchor(point),
                        }
                    )
                if "geometry_segment_boundary" not in pending_break_reasons:
                    pending_break_reasons.append("geometry_segment_boundary")
            if not current_points:
                current_break_reasons = pending_break_reasons or [
                    "continuous_source_segment"
                ]
                pending_break_reasons = []
            projected_point = {
                "source_anchor": _source_anchor(point),
                "geometry_segment_id": projected.get("segment_id"),
                "geometry_distance_m": projected.get("geometry_distance_m"),
                "reference_position_m": projected.get("reference_position_m"),
                "local_tangent_xz": projected.get("local_tangent_xz"),
                "longitudinal_residual_m": projected.get("longitudinal_residual_m"),
                "lateral_residual_m": projected.get("lateral_residual_m"),
                "vertical_residual_m": projected.get("vertical_residual_m"),
                "geometry_evidence_status": projected.get("geometry_evidence_status"),
            }
            current_points.append(projected_point)
            projected_point_count += 1

        finish_run()
        finish_gap(None)

    quotas = _allocate_projection_points(
        [run["points"] for run in projected_runs], TRAJECTORY_PROJECTION_POINT_LIMIT
    )
    rendered_runs: list[dict[str, object]] = []
    for run, quota in zip(projected_runs, quotas):
        points = run["points"]
        indexes = _evenly_spaced_indices(len(points), quota)
        rendered_runs.append(
            {
                key: value
                for key, value in run.items()
                if key != "points"
            }
            | {
                "rendered_point_count": len(indexes),
                "points": [points[index] for index in indexes],
            }
        )

    unsupported_count = len(unsupported_source) + projection_unsupported_count
    projected_status = "projected" if projected_point_count else "unavailable"
    final = {
        **base,
        "status": projected_status,
        "reasons": [] if projected_point_count else ["no_supported_projection_samples"],
        "coverage": {
            "source_sample_count": len(attempt.samples),
            "source_position_sample_count": int(coverage.get("position_sample_count", 0)),
            "source_unsupported_sample_count": len(unsupported_source),
            "projected_supported_sample_count": projected_point_count,
            "projected_unsupported_sample_count": projection_unsupported_count,
            "unsupported_sample_count": unsupported_count,
            "projected_sample_coverage": (
                projected_point_count / len(attempt.samples) if attempt.samples else 0.0
            ),
            "source_segment_count": len(source_segments),
            "source_discontinuity_count": len(source_breaks),
            "projection_gap_count": geometry_gap_count,
            "geometry_segment_transition_count": geometry_boundary_count,
            "projected_run_count": len(projected_runs),
            "rendered_run_count": len(rendered_runs),
            "rendered_point_count": sum(quotas),
            "omitted_projected_point_count": projected_point_count - sum(quotas),
            "support_reason_counts": dict(sorted(reason_counts.items())),
            "observed_lap_distance_range_m": coverage.get("observed_lap_distance_range_m"),
        },
        "limits": {
            "source_trace_bytes": ANALYSIS_SOURCE_TRACE_BYTE_LIMIT,
            "source_trace_rows": ANALYSIS_SOURCE_TRACE_ROW_LIMIT,
            "source_context_segments": ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
            "source_context_bytes": ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
            "rendered_points": TRAJECTORY_PROJECTION_POINT_LIMIT,
            "continuous_runs": TRAJECTORY_PROJECTION_RUN_LIMIT,
            "examples": TRAJECTORY_PROJECTION_EXAMPLE_LIMIT,
            "thinning_method": "deterministic_even_spacing_with_run_endpoints",
        },
        "source_continuity": {
            "policy": trajectory.get("continuity_policy"),
            "break_examples": source_breaks[:TRAJECTORY_PROJECTION_EXAMPLE_LIMIT],
            "break_examples_omitted_count": max(
                0, len(source_breaks) - TRAJECTORY_PROJECTION_EXAMPLE_LIMIT
            ),
            "unsupported_source_examples": unsupported_source[
                :TRAJECTORY_PROJECTION_EXAMPLE_LIMIT
            ],
            "unsupported_source_examples_omitted_count": max(
                0, len(unsupported_source) - TRAJECTORY_PROJECTION_EXAMPLE_LIMIT
            ),
        },
        "geometry_gaps": {
            "example_limit": TRAJECTORY_PROJECTION_EXAMPLE_LIMIT,
            "examples": geometry_gaps,
            "omitted_count": max(0, geometry_gap_count - len(geometry_gaps)),
        },
        "geometry_boundaries": {
            "example_limit": TRAJECTORY_PROJECTION_EXAMPLE_LIMIT,
            "examples": geometry_boundaries,
            "omitted_count": max(
                0, geometry_boundary_count - len(geometry_boundaries)
            ),
        },
        "runs": rendered_runs,
    }
    return final


def _base_document(
    *,
    source: dict[str, object],
    geometry: dict[str, object],
    declared_layout_id: str,
    context: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "artifact_kind": "diagnostic_trajectory_geometry_projection",
        "diagnostic_only": True,
        "coaching_eligible": False,
        "physical_verification_established": False,
        "residual_interpretation": "signed residuals against the selected geometry artifact",
        "source": source,
        "geometry": geometry,
        "layout_assertion": {
            "layout_id": declared_layout_id,
            "source": "caller_declared",
            "matches_geometry_artifact": declared_layout_id == geometry["layout_id"],
            "session_context_contains_layout_id": False,
        },
        "context": context,
        "units": {
            "position": "m",
            "distance": "m",
            "session_time": "s",
            "longitudinal_residual": "m",
            "lateral_residual": "m",
            "vertical_residual": "m",
        },
    }


def _unavailable_document(
    base: dict[str, object], reasons: Sequence[str]
) -> dict[str, object]:
    return {
        **base,
        "status": "unavailable",
        "reasons": sorted(set(reasons)),
        "coverage": {
            "source_sample_count": base["source"]["source_sample_count"],
            "projected_supported_sample_count": 0,
            "projected_unsupported_sample_count": None,
            "projected_sample_coverage": None,
            "projected_run_count": 0,
            "rendered_run_count": 0,
            "rendered_point_count": 0,
        },
        "runs": [],
    }


def _source_identity(attempt: StoredAttemptTrace) -> dict[str, object]:
    return {
        "attempt_key": attempt.attempt_key,
        "run_id": attempt.run_id,
        "session_uid": attempt.session_uid,
        "car_index": attempt.car_index,
        "attempt_number": attempt.attempt_number,
        "disposition": attempt.disposition,
        "lap_time_ms": attempt.lap_time_ms,
        "game_valid": attempt.game_valid,
        "reference_eligible": attempt.reference_eligible,
        "start_observed": attempt.start_observed,
        "pit_encountered": attempt.pit_encountered,
        "superseded": attempt.superseded,
        "lifecycle_assessed": attempt.lifecycle_assessed,
        "exclusion_reasons": list(attempt.exclusion_reasons),
        "trace_sha256": attempt.trace_sha256,
        "trace_schema_version": attempt.trace_schema_version,
        "source_sample_count": len(attempt.samples),
    }


def _geometry_identity(model: GeometryModel) -> dict[str, object]:
    return {
        "model_id": model.model_id,
        "revision": model.revision,
        "artifact_sha256": model.artifact_sha256,
        "packet_format": model.packet_format,
        "track_id": model.track_id,
        "track_name": model.track_name,
        "layout_id": model.layout_id,
        "lap_length_m": model.lap_length_m,
        "game_distance_origin_m": model.game_distance_origin_m,
        "coordinate_frame": model.coordinate_frame,
        "coordinate_units": model.coordinate_units,
        "lateral_sign_convention": model.lateral_sign_convention,
        "cyclic_seam_policy": model.cyclic_seam_policy,
        "role": model.role,
        "provenance": model.provenance,
        "geometry_validation": _validation_record(model.geometry_validation),
        "calibration_validation": _validation_record(model.calibration_validation),
    }


def _validation_record(evidence: object) -> dict[str, object]:
    return {
        "status": getattr(evidence, "status"),
        "reviewer": getattr(evidence, "reviewer"),
        "method": getattr(evidence, "method"),
        "evidence_reference": getattr(evidence, "evidence_reference"),
    }


def _stable_context(
    attempt: StoredAttemptTrace,
) -> tuple[dict[str, object] | None, list[str]]:
    if not attempt.context_segments:
        return None, ["session_context_unavailable"]
    contexts: list[dict[str, object]] = []
    for _, raw_context in attempt.context_segments:
        if not isinstance(raw_context, Mapping):
            return None, ["session_context_unavailable"]
        packet_format = raw_context.get("packet_format")
        track_id = raw_context.get("track_id")
        track_length = _finite_number(raw_context.get("track_length_m"))
        if (
            not isinstance(packet_format, int)
            or isinstance(packet_format, bool)
            or not isinstance(track_id, int)
            or isinstance(track_id, bool)
            or track_id < 0
            or track_length is None
            or track_length <= 0
        ):
            return None, ["session_track_identity_unavailable"]
        contexts.append(
            {
                "packet_format": packet_format,
                "track_id": track_id,
                "track_length_m": track_length,
                "track_name": (
                    raw_context.get("track_name")
                    if isinstance(raw_context.get("track_name"), str)
                    else None
                ),
            }
        )
    identity = tuple(
        (item["packet_format"], item["track_id"], item["track_length_m"])
        for item in contexts
    )
    if any(item != identity[0] for item in identity[1:]):
        return None, ["session_track_identity_changed"]
    first = contexts[0]
    mode_segments = []
    for (from_frame, raw_context) in attempt.context_segments:
        assert isinstance(raw_context, Mapping)
        mode_segments.append(
            {
                "from_frame_identifier": from_frame,
                "session_type_id": raw_context.get("session_type_id"),
                "session_type": raw_context.get("session_type"),
                "game_mode_id": raw_context.get("game_mode_id"),
                "game_mode": raw_context.get("game_mode"),
                "rule_set_id": raw_context.get("rule_set_id"),
                "rule_set": raw_context.get("rule_set"),
            }
        )
    return {
        **first,
        "stable": True,
        "context_segment_count": len(contexts),
        "layout_id": None,
        "mode_evidence_policy": "reported_only; does not enable or disable projection",
        "mode_segments": mode_segments,
    }, []


def _geometry_context_reasons(
    model: GeometryModel, context: Mapping[str, object]
) -> list[str]:
    reasons: list[str] = []
    if context["packet_format"] != model.packet_format:
        reasons.append("packet_format_mismatch")
    if context["track_id"] != model.track_id:
        reasons.append("track_id_mismatch")
    track_length = _finite_number(context.get("track_length_m"))
    if track_length is None:
        reasons.append("track_length_unavailable")
    elif not math.isclose(
        model.lap_length_m,
        track_length,
        rel_tol=0.0,
        abs_tol=TRACK_LENGTH_TOLERANCE_M,
    ):
        reasons.append("track_length_mismatch")
    return reasons


def _estimated_lookup_work(source_rows: int, model: GeometryModel) -> int:
    max_segment_anchors = max(len(segment.anchors) for segment in model.segments)
    anchor_search_depth = math.ceil(math.log2(max_segment_anchors + 1))
    geometry_anchor_brackets = sum(
        len(segment.anchors) - 1 for segment in model.segments
    )
    return geometry_anchor_brackets + source_rows * (
        2 * len(model.segments) + anchor_search_depth + 1
    )


def _source_anchor(point: Mapping[str, object] | None) -> dict[str, object]:
    if point is None:
        return {}
    position = point.get("world_position_m")
    if not isinstance(position, Mapping):
        raise ValueError("trajectory point position is malformed")
    return {
        "frame_identifier": point.get("frame_identifier"),
        "session_time_s": point.get("session_time_s"),
        "lap_distance_m": point.get("lap_distance_m"),
        "lap_time_ms": point.get("lap_time_ms"),
        "world_position_m": {
            axis: position.get(axis) for axis in ("x", "y", "z")
        },
    }


def _allocate_projection_points(
    point_lists: Sequence[Sequence[object]], point_limit: int
) -> list[int]:
    quotas = [min(2, len(points)) for points in point_lists]
    remaining = point_limit - sum(quotas)
    if remaining < 0:
        raise TrajectoryProjectionUnavailable("trajectory_projection_too_fragmented")
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


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None
