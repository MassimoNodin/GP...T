from __future__ import annotations

import struct

import pytest

from f1_engineer.analysis.trajectory import (
    TrajectoryPreviewUnavailable,
    build_observed_trajectory,
    build_observed_trajectory_preview,
    summarize_observed_position_window,
)


def _sample(frame: int, distance: float, *, available: bool = True) -> dict[str, object]:
    return {
        "frame_identifier": frame,
        "session_time_s": 10.0 + frame / 60,
        "lap_distance_m": distance,
        "current_lap_time_ms": frame * 10,
        "motion_available": available,
        "world_position_x_m": float(frame) if available else None,
        "world_position_y_m": 2.0 if available else None,
        "world_position_z_m": 3.0 if available else None,
        "world_velocity_x_mps": 1.0 if available else None,
        "world_velocity_y_mps": 2.0 if available else None,
        "world_velocity_z_mps": 3.0 if available else None,
        "world_forward_x": 1.0 if available else None,
        "world_forward_y": 0.0 if available else None,
        "world_forward_z": 0.0 if available else None,
        "world_right_x": 0.0 if available else None,
        "world_right_y": 1.0 if available else None,
        "world_right_z": 0.0 if available else None,
        "g_force_lateral": 0.0 if available else None,
        "g_force_longitudinal": 0.0 if available else None,
        "g_force_vertical": 1.0 if available else None,
        "yaw_rad": 0.0 if available else None,
        "pitch_rad": 0.0 if available else None,
        "roll_rad": 0.0 if available else None,
    }


def _trajectory(samples: tuple[dict[str, object], ...], *, schema_version: int = 3):
    return build_observed_trajectory(
        attempt_key="run:42:0:1",
        run_id="run",
        session_uid="42",
        car_index=0,
        disposition="completed",
        lap_time_ms=80_000,
        game_valid=False,
        reference_eligible=False,
        exclusion_reasons=("game_marked_invalid",),
        trace_sha256="a" * 64,
        trace_schema_version=schema_version,
        context_segments=((1, {"track_name": "Melbourne"}),),
        samples=samples,
    )


def test_position_window_uses_half_open_bounds_and_preserves_source_segments() -> None:
    samples = (
        _sample(10, 0.0),
        _sample(11, 10.0),
        _sample(12, 20.0),
        _sample(14, 20.0),
        _sample(15, 30.0),
        _sample(16, 40.0),
    )
    trajectory = _trajectory(samples)

    evidence = summarize_observed_position_window(
        trajectory, samples, 10.0, 40.0, trace_schema_version=3
    )

    assert evidence["boundary"] == "inclusive_start_exclusive_end"
    assert evidence["source_sample_count_in_window"] == 4
    assert evidence["source_position_sample_count"] == 4
    fragments = evidence["fragments"]
    assert [fragment["sample_count"] for fragment in fragments] == [2, 2]
    assert fragments[0]["clipped_at_window_start"] is True
    assert fragments[0]["break_before_reasons"] == []
    assert fragments[1]["break_before_reasons"] == ["frame_gap"]
    assert fragments[0]["start_anchor"]["frame_identifier"] == 11
    assert fragments[0]["start_anchor"]["world_position_m"] == {"x": 11.0, "y": 2.0, "z": 3.0}
    assert fragments[1]["start_anchor"]["lap_distance_m"] == 20.0
    assert fragments[1]["end_anchor"]["frame_identifier"] == 15


def test_position_window_reports_singleton_fragments_and_legacy_motion_unavailability() -> None:
    samples = (_sample(10, 4.0), _sample(12, 5.0))
    trajectory = _trajectory(samples)
    evidence = summarize_observed_position_window(
        trajectory, samples, 5.0, 6.0, trace_schema_version=3
    )

    assert evidence["source_position_sample_count"] == 1
    fragment = evidence["fragments"][0]
    assert fragment["sample_count"] == 1
    assert fragment["break_before_reasons"] == ["frame_gap"]
    assert fragment["start_anchor"] == fragment["end_anchor"]

    legacy = summarize_observed_position_window(
        trajectory, samples, 4.0, 6.0, trace_schema_version=1
    )
    assert legacy["status"] == "motion_unavailable_for_trace_schema"
    assert legacy["source_sample_count_in_window"] == 2
    assert legacy["source_position_sample_count"] is None
    assert legacy["fragments"] == []


def test_position_window_source_count_is_independent_of_thinned_preview_points() -> None:
    samples = tuple(_sample(frame, float(frame)) for frame in range(20))
    trajectory = _trajectory(samples)
    preview = build_observed_trajectory_preview(trajectory, point_limit=2)

    evidence = summarize_observed_position_window(
        trajectory, samples, 8.0, 10.0, trace_schema_version=3
    )
    retained = [
        point
        for segment in preview["segments"]
        for point in segment["points"]
        if 8.0 <= point["lap_distance_m"] < 10.0
    ]

    assert evidence["source_position_sample_count"] == 2
    assert evidence["source_sample_count_in_window"] == 2
    assert retained == []


def test_position_window_keeps_boundary_jitter_reentry_as_separate_fragments_after_thinning() -> None:
    # The 0.006 m dip is within the source continuity tolerance, but it falls
    # outside this region and must still separate the two highlighted runs.
    samples = (
        _sample(10, 100.004),
        _sample(11, 99.998),
        _sample(12, 100.003),
    )
    trajectory = _trajectory(samples)
    preview = build_observed_trajectory_preview(trajectory, point_limit=2)

    evidence = summarize_observed_position_window(
        trajectory, samples, 100.0, 101.0, trace_schema_version=3
    )

    assert len(trajectory["segments"]) == 1
    assert [point["frame_identifier"] for point in preview["segments"][0]["points"]] == [10, 12]
    assert evidence["source_position_sample_count"] == 2
    assert evidence["source_fragment_count"] == 2
    assert [fragment["sample_count"] for fragment in evidence["fragments"]] == [1, 1]
    assert [fragment["start_anchor"]["frame_identifier"] for fragment in evidence["fragments"]] == [10, 12]
    assert all(fragment["window_membership_break_before"] == (index == 1) for index, fragment in enumerate(evidence["fragments"]))
    assert all(fragment["window_membership_break_after"] == (index == 0) for index, fragment in enumerate(evidence["fragments"]))
    assert [fragment["clipped_at_window_start"] for fragment in evidence["fragments"]] == [False, True]
    assert [fragment["clipped_at_window_end"] for fragment in evidence["fragments"]] == [False, False]


def test_position_window_caps_anchor_fragments_without_dropping_source_counts() -> None:
    samples = tuple(
        _sample(frame, float(frame)) for frame in (0, 2, 4, 6)
    )
    trajectory = _trajectory(samples)

    evidence = summarize_observed_position_window(
        trajectory,
        samples,
        0.0,
        7.0,
        trace_schema_version=3,
        fragment_limit=1,
    )

    assert evidence["source_position_sample_count"] == 4
    assert evidence["source_fragment_count"] == 4
    assert len(evidence["fragments"]) == 1
    assert evidence["omitted_fragment_count"] == 3


def test_observed_trajectory_preserves_provenance_and_breaks_unsupported_spans() -> None:
    trajectory = build_observed_trajectory(
        attempt_key="run:42:0:1",
        run_id="run",
        session_uid="42",
        car_index=0,
        disposition="completed",
        lap_time_ms=80_000,
        game_valid=False,
        reference_eligible=False,
        exclusion_reasons=("game_marked_invalid",),
        trace_sha256="abc123",
        trace_schema_version=2,
        context_segments=((100, {"track_name": "Melbourne"}),),
        samples=(
            _sample(100, 0.0),
            _sample(101, 1.0),
            _sample(102, 2.0, available=False),
            _sample(103, 3.0),
            _sample(105, 5.0),
        ),
    )

    assert trajectory["schema_version"] == 1
    assert trajectory["artifact_kind"] == "observed_driven_trajectory"
    assert trajectory["diagnostic_only"] is True
    assert trajectory["is_centreline"] is False
    assert trajectory["source"]["trace_sha256"] == "abc123"
    assert trajectory["source"]["game_valid"] is False
    assert trajectory["source"]["context_segments"][0]["context"]["track_name"] == "Melbourne"
    assert trajectory["coverage"]["position_sample_count"] == 4
    assert trajectory["coverage"]["position_sample_coverage"] == 0.8
    assert trajectory["coverage"]["segment_count"] == 3
    assert trajectory["unsupported_samples"][0]["frame_identifier"] == 102
    assert trajectory["breaks"][1]["reason"] == "frame_gap"

    segments = trajectory["segments"]
    assert [segment["sample_count"] for segment in segments] == [2, 1, 1]
    assert segments[0]["break_before_reasons"] == ["start"]
    assert segments[1]["break_before_reasons"] == ["motion_position_unavailable"]
    assert segments[2]["break_before_reasons"] == ["frame_gap"]
    assert segments[1]["points"][0]["world_position_m"] == {
        "x": 103.0,
        "y": 2.0,
        "z": 3.0,
    }
    assert segments[0]["points"][0]["g_force_g"] == {
        "lateral": 0.0,
        "longitudinal": 0.0,
        "vertical": 1.0,
    }
    assert trajectory["continuity_policy"]["max_session_time_gap_s"] == 0.1


def test_observed_trajectory_splits_large_time_and_position_jumps() -> None:
    cases = (
        ("session_time_s", 10.5, "session_time_gap"),
        ("world_position_x_m", 30.0, "world_position_jump"),
    )
    for field, value, expected_reason in cases:
        second = _sample(2, 2.0)
        second[field] = value
        trajectory = build_observed_trajectory(
            attempt_key="run:42:0:1",
            run_id="run",
            session_uid="42",
            car_index=0,
            disposition="completed",
            lap_time_ms=80_000,
            game_valid=True,
            reference_eligible=True,
            exclusion_reasons=(),
            trace_sha256="abc123",
            trace_schema_version=2,
            context_segments=(),
            samples=(_sample(1, 1.0), second),
        )

        assert trajectory["breaks"][0]["reason"] == expected_reason
        assert trajectory["coverage"]["segment_count"] == 2


def test_observed_trajectory_splits_lap_clock_rewind_but_tolerates_capture_jitter() -> None:
    first = _sample(1, 1.0)
    first["current_lap_time_ms"] = 1000
    jitter = _sample(2, 2.0)
    jitter["current_lap_time_ms"] = 988
    boundary = _sample(3, 3.0)
    boundary["current_lap_time_ms"] = 968
    rewind = _sample(4, 4.0)
    rewind["current_lap_time_ms"] = 947
    trajectory = build_observed_trajectory(
        attempt_key="run:42:0:1",
        run_id="run",
        session_uid="42",
        car_index=0,
        disposition="completed",
        lap_time_ms=80_000,
        game_valid=True,
        reference_eligible=True,
        exclusion_reasons=(),
        trace_sha256="abc123",
        trace_schema_version=2,
        context_segments=(),
        samples=(first, jitter, boundary, rewind),
    )

    assert trajectory["breaks"][0]["reason"] == "lap_time_regression"
    assert trajectory["coverage"]["segment_count"] == 2
    assert trajectory["segments"][0]["sample_count"] == 3


def test_observed_trajectory_float32_session_time_at_exact_gap_limit_is_continuous() -> None:
    float32 = lambda value: struct.unpack("<f", struct.pack("<f", value))[0]
    first = _sample(1, 1.0)
    first["session_time_s"] = float32(10.0)
    second = _sample(2, 2.0)
    second["session_time_s"] = float32(10.1)
    trajectory = build_observed_trajectory(
        attempt_key="run:42:0:1",
        run_id="run",
        session_uid="42",
        car_index=0,
        disposition="completed",
        lap_time_ms=80_000,
        game_valid=True,
        reference_eligible=True,
        exclusion_reasons=(),
        trace_sha256="abc123",
        trace_schema_version=2,
        context_segments=(),
        samples=(first, second),
    )

    assert trajectory["coverage"]["segment_count"] == 1
    assert trajectory["breaks"] == []


def test_observed_trajectory_requires_nonnegative_clock_anchors() -> None:
    invalid = _sample(1, 1.0)
    invalid["session_time_s"] = -1.0
    trajectory = build_observed_trajectory(
        attempt_key="run:42:0:1",
        run_id="run",
        session_uid="42",
        car_index=0,
        disposition="completed",
        lap_time_ms=80_000,
        game_valid=True,
        reference_eligible=True,
        exclusion_reasons=(),
        trace_sha256="abc123",
        trace_schema_version=2,
        context_segments=(),
        samples=(invalid,),
    )

    assert trajectory["segments"] == []
    assert trajectory["unsupported_samples"][0]["reason"] == "invalid_session_time_anchor"


def test_observed_trajectory_does_not_assert_geometry_for_v1_trace_samples() -> None:
    trajectory = build_observed_trajectory(
        attempt_key="legacy:42:0:1",
        run_id="legacy",
        session_uid="42",
        car_index=0,
        disposition="completed",
        lap_time_ms=80_000,
        game_valid=True,
        reference_eligible=True,
        exclusion_reasons=(),
        trace_sha256="v1hash",
        trace_schema_version=1,
        context_segments=(),
        samples=({
            "frame_identifier": 1,
            "session_time_s": 1.0,
            "lap_distance_m": 5.0,
            "current_lap_time_ms": 10,
            "motion_available": None,
            "world_position_x_m": None,
            "world_position_y_m": None,
            "world_position_z_m": None,
        },),
    )

    assert trajectory["segments"] == []
    assert trajectory["coverage"]["position_sample_coverage"] == 0.0
    assert trajectory["unsupported_samples"][0]["reason"] == "motion_position_unavailable"
    assert trajectory["is_centreline"] is False


def test_trajectory_preview_is_deterministic_bounded_and_preserves_segment_endpoints() -> None:
    samples = tuple(_sample(frame, float(frame)) for frame in range(1, 11))
    trajectory = build_observed_trajectory(
        attempt_key="run:42:0:1",
        run_id="run",
        session_uid="42",
        car_index=0,
        disposition="completed",
        lap_time_ms=80_000,
        game_valid=False,
        reference_eligible=False,
        exclusion_reasons=("game_marked_invalid",),
        trace_sha256="abc123",
        trace_schema_version=3,
        context_segments=(),
        samples=samples,
    )

    first = build_observed_trajectory_preview(trajectory, point_limit=4)
    second = build_observed_trajectory_preview(trajectory, point_limit=4)

    assert first == second
    assert first["artifact_kind"] == "observed_driven_trajectory_preview"
    assert first["coordinate_projection"] == {
        "horizontal_axis": "world_x",
        "vertical_axis": "world_z",
        "units": "m",
        "orientation_claim": None,
    }
    assert first["source"]["game_valid"] is False
    assert first["source"]["reference_eligible"] is False
    segment = first["segments"][0]
    assert segment["sample_count"] == 10
    assert segment["rendered_point_count"] == 4
    assert segment["points"][0]["frame_identifier"] == 1
    assert segment["points"][-1]["frame_identifier"] == 10
    assert first["preview"]["source_position_point_count"] == 10
    assert first["preview"]["rendered_point_count"] == 4
    assert first["preview"]["omitted_position_point_count"] == 6


def test_trajectory_preview_keeps_all_segments_and_abstains_when_too_fragmented() -> None:
    trajectory = build_observed_trajectory(
        attempt_key="run:42:0:1",
        run_id="run",
        session_uid="42",
        car_index=0,
        disposition="partial",
        lap_time_ms=None,
        game_valid=None,
        reference_eligible=False,
        exclusion_reasons=("partial_attempt",),
        trace_sha256="abc123",
        trace_schema_version=3,
        context_segments=(),
        samples=(
            _sample(1, 1.0),
            _sample(2, 2.0),
            _sample(3, 3.0, available=False),
            _sample(4, 4.0),
            _sample(5, 5.0),
            _sample(6, 6.0, available=False),
            _sample(7, 7.0),
        ),
    )

    preview = build_observed_trajectory_preview(trajectory, point_limit=5)
    assert len(preview["segments"]) == 3
    assert [segment["points"][0]["frame_identifier"] for segment in preview["segments"]] == [1, 4, 7]
    assert [segment["points"][-1]["frame_identifier"] for segment in preview["segments"]] == [2, 5, 7]
    assert preview["preview"]["rendered_point_count"] == 5
    with pytest.raises(TrajectoryPreviewUnavailable) as error:
        build_observed_trajectory_preview(trajectory, segment_limit=2)
    assert error.value.reason_code == "trajectory_preview_too_fragmented"


def test_trajectory_preview_explicitly_abstains_without_positions() -> None:
    trajectory = build_observed_trajectory(
        attempt_key="old:42:0:1",
        run_id="old",
        session_uid="42",
        car_index=0,
        disposition="completed",
        lap_time_ms=80_000,
        game_valid=True,
        reference_eligible=True,
        exclusion_reasons=(),
        trace_sha256="legacy",
        trace_schema_version=1,
        context_segments=(),
        samples=(_sample(1, 1.0, available=False),),
    )

    with pytest.raises(TrajectoryPreviewUnavailable) as error:
        build_observed_trajectory_preview(trajectory)
    assert error.value.reason_code == "no_observed_position_samples"
