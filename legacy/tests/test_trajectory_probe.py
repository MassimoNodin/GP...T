from __future__ import annotations

from types import SimpleNamespace

import pytest

from f1_engineer.analysis.trajectory import (
    build_observed_trajectory,
)
from f1_engineer.analysis.trajectory_probe import build_observed_position_probe
from f1_engineer.analysis import trajectory_service


def _point(
    frame: int,
    distance: float,
    *,
    time: float | None = None,
    x: float | None = None,
    y: float = 2.0,
    z: float | None = None,
) -> dict[str, object]:
    return {
        "frame_identifier": frame,
        "lap_distance_m": distance,
        "session_time_s": time if time is not None else 1.0 + frame * 0.02,
        "world_position_m": {
            "x": x if x is not None else distance * 2.0,
            "y": y,
            "z": z if z is not None else distance * 3.0,
        },
    }


def _trajectory(*segments: list[dict[str, object]], schema: int = 3) -> dict[str, object]:
    return {
        "source": {
            "attempt_key": "attempt:1",
            "run_id": "run-1",
            "session_uid": "42",
            "car_index": 0,
            "trace_sha256": "a" * 64,
            "trace_schema_version": schema,
        },
        "segments": [
            {"segment_index": index, "points": points}
            for index, points in enumerate(segments)
        ],
    }


def test_position_probe_uses_exact_source_observations_at_both_endpoints() -> None:
    trajectory = _trajectory([_point(1, 0.0), _point(2, 50.0), _point(3, 100.0)])

    start = build_observed_position_probe(trajectory, 0.0, track_length_m=100.0)
    finish = build_observed_position_probe(trajectory, 100.0, track_length_m=100.0)

    assert start["status"] == finish["status"] == "available"
    assert start["method"] == finish["method"] == "exact_source_observation"
    assert start["requested_distance_m"] == 0.0
    assert finish["requested_distance_m"] == 100.0
    assert start["position_world_xyz_m"] == {"x": 0.0, "y": 2.0, "z": 0.0}
    assert finish["position_world_xyz_m"] == {"x": 200.0, "y": 2.0, "z": 300.0}


def test_position_probe_interpolates_xyz_between_adjacent_source_samples() -> None:
    trajectory = _trajectory([_point(10, 10.0, time=5.0), _point(11, 20.0, time=5.02)])

    probe = build_observed_position_probe(trajectory, 12.5, track_length_m=100.0)

    assert probe["status"] == "available"
    assert probe["method"] == "linear_interpolation"
    assert probe["interpolation_fraction"] == pytest.approx(0.25)
    assert probe["position_world_xyz_m"] == {"x": 25.0, "y": 2.0, "z": 37.5}
    assert [item["frame_identifier"] for item in probe["source_anchors"]] == [10, 11]


def test_shared_bracket_endpoint_is_exact_and_not_ambiguous() -> None:
    trajectory = _trajectory(
        [_point(1, 10.0), _point(2, 20.0), _point(3, 30.0)]
    )

    probe = build_observed_position_probe(trajectory, 20.0, track_length_m=100.0)

    assert probe["status"] == "available"
    assert probe["method"] == "exact_source_observation"
    assert len(probe["source_anchors"]) == 1


def test_reversed_crossing_competes_with_increasing_crossing() -> None:
    trajectory = _trajectory(
        [
            _point(1, 10.005, time=2.0),
            _point(2, 10.000, time=2.02),
            _point(3, 10.006, time=2.04),
        ]
    )

    probe = build_observed_position_probe(trajectory, 10.002, track_length_m=100.0)

    assert probe["status"] == "unavailable"
    assert probe["reason_code"] == "probe_distance_support_ambiguous"


@pytest.mark.parametrize(
    "trajectory,distance,reason",
    [
        (
            _trajectory([_point(1, 10.0), _point(2, 10.0)]),
            10.0,
            "probe_distance_support_ambiguous",
        ),
        (
            _trajectory([_point(1, 0.0), _point(2, 20.0)], [_point(8, 10.0)]),
            10.0,
            "probe_distance_support_ambiguous",
        ),
        (
            _trajectory([_point(1, 0.0), _point(2, 5.0)], [_point(8, 15.0), _point(9, 20.0)]),
            10.0,
            "probe_crosses_trajectory_discontinuity",
        ),
        (
            _trajectory([_point(1, 0.0), _point(2, 30.0)]),
            10.0,
            "probe_distance_bracket_too_wide",
        ),
    ],
)
def test_position_probe_rejects_ambiguous_discontinuous_or_wide_support(
    trajectory: dict[str, object], distance: float, reason: str
) -> None:
    probe = build_observed_position_probe(trajectory, distance, track_length_m=100.0)
    assert probe["status"] == "unavailable"
    assert probe["reason_code"] == reason
    assert probe["position_world_xyz_m"] is None
    assert probe["source_anchors"] == []


def test_position_probe_allows_uint32_frame_wrap_and_rejects_nonadjacent_frames() -> None:
    wrap = _trajectory(
        [_point(0xFFFFFFFF, 10.0, time=5.0), _point(0, 20.0, time=5.02)]
    )
    skipped = _trajectory([_point(10, 10.0), _point(12, 20.0)])

    assert build_observed_position_probe(wrap, 15.0, track_length_m=100.0)["status"] == "available"
    rejected = build_observed_position_probe(skipped, 15.0, track_length_m=100.0)
    assert rejected["reason_code"] == "probe_frame_discontinuity"


def test_position_probe_rejects_session_gap_missing_motion_and_legacy_schema() -> None:
    session_gap = _trajectory(
        [_point(1, 10.0, time=2.0), _point(2, 20.0, time=2.2)]
    )
    missing_motion = _trajectory(
        [
            _point(1, 10.0),
            {**_point(2, 20.0), "world_position_m": {"x": 40.0, "z": 60.0}},
        ]
    )
    legacy = _trajectory([_point(1, 10.0), _point(2, 20.0)], schema=1)

    assert build_observed_position_probe(session_gap, 15.0, track_length_m=100.0)["reason_code"] == "probe_session_time_discontinuity"
    assert build_observed_position_probe(missing_motion, 15.0, track_length_m=100.0)["reason_code"] == "probe_world_position_invalid"
    assert build_observed_position_probe(legacy, 15.0, track_length_m=100.0)["reason_code"] == "motion_unavailable_for_trace_schema"


@pytest.mark.parametrize(
    "distance,track_length,reason",
    [
        (-1.0, 100.0, "probe_distance_outside_track_range"),
        (101.0, 100.0, "probe_distance_outside_track_range"),
        (float("nan"), 100.0, "probe_distance_invalid"),
        (10.0, 0.0, "probe_track_length_invalid"),
    ],
)
def test_position_probe_rejects_invalid_or_out_of_range_distances(
    distance: float, track_length: float, reason: str
) -> None:
    probe = build_observed_position_probe(
        _trajectory([_point(1, 0.0), _point(2, 20.0)]),
        distance,
        track_length_m=track_length,
    )
    assert probe["status"] == "unavailable"
    assert probe["reason_code"] == reason


def test_probe_is_sampled_from_full_source_before_preview_thinning(monkeypatch) -> None:
    samples = [
        {
            "frame_identifier": index,
            "session_time_s": 1.0 + index * 0.02,
            "lap_distance_m": float(index),
            "current_lap_time_ms": index * 20,
            "motion_available": True,
            "world_position_x_m": float(index),
            "world_position_y_m": 2.0,
            "world_position_z_m": float(index * 2),
        }
        for index in range(2_401)
    ]
    attempt = SimpleNamespace(
        attempt_key="attempt:1",
        run_id="run-1",
        session_uid="42",
        car_index=0,
        disposition="completed",
        lap_time_ms=80_000,
        game_valid=True,
        reference_eligible=True,
        exclusion_reasons=(),
        trace_sha256="a" * 64,
        trace_schema_version=3,
        context_segments=(),
        samples=samples,
    )
    monkeypatch.setattr(trajectory_service, "load_attempt_trace", lambda *_args, **_kwargs: attempt)

    preview = trajectory_service.load_observed_trajectory_preview(
        "unused.sqlite3", "attempt:1", position_probe_m=3.0, track_length_m=3_000.0
    )

    assert preview is not None
    assert preview["preview"]["rendered_point_count"] <= 2_000
    assert preview["position_probe"]["status"] == "available"
    assert preview["position_probe"]["position_world_xyz_m"] == {
        "x": 3.0,
        "y": 2.0,
        "z": 6.0,
    }
    assert all(
        point["lap_distance_m"] != 3.0
        for segment in preview["segments"]
        for point in segment["points"]
    )

