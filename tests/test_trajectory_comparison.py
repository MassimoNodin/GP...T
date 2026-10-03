from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from f1_engineer.analysis.trajectory_comparison import (
    TrajectoryComparisonUnavailable,
    build_trajectory_comparison_preview,
)
from f1_engineer.analysis.trajectory import TrajectoryPreviewUnavailable
from f1_engineer.analysis import trajectory_comparison_service


def _attempt(attempt_key: str, checksum: str) -> dict[str, object]:
    return {
        "attempt_key": attempt_key,
        "run_id": "run",
        "session_uid": "42",
        "car_index": 0,
        "disposition": "completed",
        "lap_time_ms": 80_000,
        "game_valid": False,
        "reference_eligible": False,
        "superseded": False,
        "lifecycle_assessed": True,
        "lifecycle_exclusions": [],
        "exclusion_reasons": ["game_marked_invalid"],
        "trace_sha256": checksum,
        "trace_schema_version": 3,
        "source_sample_count": 4,
    }


def _preview(attempt: dict[str, object], *, x_offset: float) -> dict[str, object]:
    source = {
        key: attempt[key]
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
    points = [
        {
            "frame_identifier": 1,
            "lap_distance_m": 0.0,
            "session_time_s": 1.0,
            "lap_time_s": 0.0,
            "lap_time_ms": 0,
            "world_position_m": {"x": x_offset, "y": 0.0, "z": 0.0},
        },
        {
            "frame_identifier": 2,
            "lap_distance_m": 1.0,
            "session_time_s": 1.02,
            "lap_time_s": 0.02,
            "lap_time_ms": 20,
            "world_position_m": {"x": x_offset + 10.0, "y": 0.0, "z": 8.0},
        },
    ]
    return {
        "schema_version": 1,
        "artifact_kind": "observed_driven_trajectory_preview",
        "diagnostic_only": True,
        "is_centreline": False,
        "coordinate_projection": {
            "horizontal_axis": "world_x",
            "vertical_axis": "world_z",
            "units": "m",
            "orientation_claim": None,
        },
        "source": source,
        "coverage": {
            "source_sample_count": 4,
            "position_sample_count": 4,
            "position_sample_coverage": 1.0,
            "unsupported_sample_count": 0,
            "segment_count": 2,
            "discontinuity_count": 1,
            "observed_lap_distance_range_m": [0.0, 1.0],
        },
        "segments": [
            {
                "segment_index": 0,
                "break_before_reasons": ["start"],
                "sample_count": 2,
                "rendered_point_count": 1,
                "points": [points[0]],
            },
            {
                "segment_index": 1,
                "break_before_reasons": ["frame_gap"],
                "sample_count": 2,
                "rendered_point_count": 1,
                "points": [points[1]],
            },
        ],
        "break_examples": [{"reason": "frame_gap"}],
        "unsupported_examples": [],
        "preview": {
            "point_limit": 2_000,
            "source_position_point_count": 4,
            "rendered_point_count": 2,
            "omitted_position_point_count": 2,
            "source_segment_count": 2,
            "rendered_segment_count": 2,
            "segment_limit": 256,
            "break_example_limit": 20,
            "break_examples_omitted_count": 0,
            "unsupported_example_limit": 20,
            "unsupported_examples_omitted_count": 0,
            "thinning_method": "deterministic_even_spacing_with_segment_endpoints",
        },
    }


def _comparison() -> dict[str, object]:
    target = _attempt("run:42:0:2", "a" * 64)
    reference = _attempt("run:42:0:1", "b" * 64)
    return {
        "comparison_policy": "time_trial",
        "target": target,
        "reference": reference,
        "processing_run_evidence": {
            "target": {
                "capture": {
                    "sha256": "c" * 64,
                    "byte_size": 10_000,
                    "complete": False,
                    "footer_status": "incomplete",
                    "recording_counters": {"dropped_datagrams": 0},
                    "recording_observer_counters": {},
                }
            },
            "reference": {
                "capture": {
                    "sha256": "c" * 64,
                    "byte_size": 10_000,
                    "complete": False,
                    "footer_status": "incomplete",
                    "recording_counters": {"dropped_datagrams": 0},
                    "recording_observer_counters": {},
                }
            },
        },
    }


def _policy_attempt(attempt_key: str, *, run_id: str = "run") -> SimpleNamespace:
    context = {
        "packet_format": 2025,
        "track_id": 1,
        "track_name": "Test Circuit",
        "track_length_m": 5_000.0,
        "session_type": "time_trial",
        "game_mode": "time_trial",
        "rule_set": "time_trial",
        "formula_id": 0,
        "equal_car_performance_id": 0,
        "steering_assist_id": 0,
        "braking_assist_id": 0,
        "gearbox_assist_id": 0,
    }
    number = 2 if attempt_key.endswith(":2") else 1
    checksum = f"{number:064x}"
    return SimpleNamespace(
        attempt_key=attempt_key,
        run_id=run_id,
        session_uid="42",
        car_index=0,
        disposition="completed",
        lap_time_ms=80_000,
        game_valid=None,
        reference_eligible=False,
        exclusion_reasons=(),
        trace_sha256=checksum,
        trace_schema_version=3,
        quality={},
        context_segments=((0, context),),
        samples=(),
        attempt_number=number,
        start_observed=True,
        pit_encountered=False,
        superseded=False,
        lifecycle_assessed=True,
        timing_evidence=None,
        source_sample_count=4,
    )


def test_paired_preview_uses_one_equal_scale_and_preserves_path_breaks() -> None:
    comparison = _comparison()
    report = build_trajectory_comparison_preview(
        _preview(comparison["target"], x_offset=0.0),
        _preview(comparison["reference"], x_offset=100.0),
        comparison,
    )

    assert report["diagnostic_only"] is True
    assert report["is_centreline"] is False
    assert report["coordinate_projection"]["equal_scale"] is True
    bounds = report["plot_bounds_world_xz_m"]
    assert bounds["world_x"][1] - bounds["world_x"][0] == (
        bounds["world_z"][1] - bounds["world_z"][0]
    )
    assert len(report["paths"]["target"]["segments"]) == 2
    assert report["paths"]["target"]["segments"][1]["break_before_reasons"] == [
        "frame_gap"
    ]
    assert report["paths"]["target"]["preview"]["omitted_position_point_count"] == 2
    assert report["paths"]["target"]["capture_evidence"]["footer_status"] == "incomplete"
    assert report["paths"]["target"]["attempt_evidence"]["game_valid"] is False


def test_paired_preview_rejects_scope_checksum_schema_and_policy_mismatches() -> None:
    comparison = _comparison()
    target = _preview(comparison["target"], x_offset=0.0)
    reference = _preview(comparison["reference"], x_offset=100.0)

    wrong_scope = deepcopy(comparison)
    wrong_scope["reference"]["session_uid"] = "99"
    with pytest.raises(TrajectoryComparisonUnavailable, match="scope_mismatch"):
        build_trajectory_comparison_preview(target, reference, wrong_scope)

    wrong_checksum = deepcopy(reference)
    wrong_checksum["source"]["trace_sha256"] = "f" * 64
    with pytest.raises(TrajectoryComparisonUnavailable, match="provenance_mismatch"):
        build_trajectory_comparison_preview(target, wrong_checksum, comparison)

    legacy_attempt = deepcopy(comparison["target"])
    legacy_attempt["trace_schema_version"] = 1
    legacy_comparison = deepcopy(comparison)
    legacy_comparison["target"]["trace_schema_version"] = 1
    legacy = _preview(legacy_attempt, x_offset=0.0)
    with pytest.raises(TrajectoryComparisonUnavailable, match="motion_unavailable"):
        build_trajectory_comparison_preview(legacy, reference, legacy_comparison)

    unsupported = deepcopy(comparison)
    unsupported["comparison_policy"] = "race"
    with pytest.raises(TrajectoryComparisonUnavailable, match="unsupported_comparison_policy"):
        build_trajectory_comparison_preview(target, reference, unsupported)


def test_paired_preview_enforces_per_attempt_point_cap() -> None:
    comparison = _comparison()
    target = _preview(comparison["target"], x_offset=0.0)
    segment = target["segments"][0]
    segment["points"] = [
        {
            "world_position_m": {"x": float(index), "z": float(index), "y": 0.0}
        }
        for index in range(2_001)
    ]
    target["preview"]["rendered_point_count"] = 2_002
    with pytest.raises(TrajectoryComparisonUnavailable, match="limits_invalid"):
        build_trajectory_comparison_preview(
            target,
            _preview(comparison["reference"], x_offset=100.0),
            comparison,
        )


def test_comparison_service_validates_metadata_then_loads_bounded_pair(
    monkeypatch,
) -> None:
    target_metadata = _policy_attempt("run:42:0:2")
    reference_metadata = _policy_attempt("run:42:0:1")
    target_summary = _attempt(target_metadata.attempt_key, target_metadata.trace_sha256)
    reference_summary = _attempt(reference_metadata.attempt_key, reference_metadata.trace_sha256)
    target = _preview(target_summary, x_offset=0.0)
    reference = _preview(reference_summary, x_offset=100.0)
    loads: list[str] = []
    monkeypatch.setattr(
        trajectory_comparison_service,
        "load_attempt_policy_metadata",
        lambda _database, attempt_key, **_limits: (
            target_metadata
            if attempt_key == target_metadata.attempt_key
            else reference_metadata
        ),
    )
    monkeypatch.setattr(
        trajectory_comparison_service,
        "get_processing_run_summary",
        lambda *_args: {"capture": {"complete": True, "footer_status": "complete"}},
    )

    def load_preview(_database: object, attempt_key: str):
        loads.append(attempt_key)
        return target if attempt_key == target["source"]["attempt_key"] else reference

    monkeypatch.setattr(
        trajectory_comparison_service,
        "load_observed_trajectory_preview",
        load_preview,
    )
    report = trajectory_comparison_service.compare_observed_trajectories(
        "db.sqlite3", "run:42:0:2", "run:42:0:1", policy="time_trial"
    )

    assert report["status"] == "available"
    assert loads == ["run:42:0:2", "run:42:0:1"]
    assert report["paths"]["target"]["attempt_evidence"]["game_valid"] is None
    assert report["paths"]["target"]["capture_evidence"]["complete"] is True


def test_comparison_service_stops_on_bounded_source_failure_without_comparing(
    monkeypatch,
) -> None:
    target_metadata = _policy_attempt("run:42:0:2")
    reference_metadata = _policy_attempt("run:42:0:1")
    monkeypatch.setattr(
        trajectory_comparison_service,
        "load_attempt_policy_metadata",
        lambda _database, attempt_key, **_limits: (
            target_metadata
            if attempt_key == target_metadata.attempt_key
            else reference_metadata
        ),
    )
    preview_loads: list[str] = []

    def oversized_preview(_database: object, attempt_key: str):
        preview_loads.append(attempt_key)
        raise TrajectoryPreviewUnavailable("trajectory_source_bytes_limit_exceeded")

    monkeypatch.setattr(
        trajectory_comparison_service,
        "load_observed_trajectory_preview",
        oversized_preview,
    )
    monkeypatch.setattr(
        trajectory_comparison_service,
        "compare_attempts",
        lambda *_args, **_kwargs: pytest.fail("full comparison must not run"),
        raising=False,
    )

    with pytest.raises(TrajectoryPreviewUnavailable, match="source_bytes_limit"):
        trajectory_comparison_service.compare_observed_trajectories(
            "db.sqlite3", "run:42:0:2", "run:42:0:1", policy="time_trial"
        )
    assert preview_loads == ["run:42:0:2"]
