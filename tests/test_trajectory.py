from __future__ import annotations

import struct

from f1_engineer.analysis.trajectory import build_observed_trajectory


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
