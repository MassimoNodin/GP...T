from __future__ import annotations

import struct
from dataclasses import replace

import pytest

from f1_engineer.analysis.comparison import calculate_delta_time
from f1_engineer.analysis.comparison_window import (
    DistanceWindow,
    analyze_comparison_window,
    optional_distance_window,
)
from f1_engineer.analysis.corners import analyze_attempt_regions, analyze_corner_regions
from f1_engineer.analysis.interval_delta import (
    has_connected_source_session_time,
    reserve_interval_evaluation_work,
)
from f1_engineer.analysis.resampling import ResamplingConfig, TraceSample, resample_trace
from f1_engineer.tracks.model import CornerDefinition, TrackModel


def _samples(
    *,
    gap: bool = False,
    target: bool = False,
    session_discontinuity: str | None = None,
) -> tuple[TraceSample, ...]:
    distances = [*range(0, 16), *range(25, 41)] if gap else list(range(41))
    result = []
    for index, distance in enumerate(distances):
        elapsed = distance * 0.05
        brake = 0.0
        throttle = 0.0
        if 11 <= distance <= 15:
            brake = 0.8 if target else 0.6
        if 26 <= distance <= 31:
            throttle = 0.6
        speed = 30.0 + distance
        if distance == (20 if target else 24):
            speed = 10.0
        if gap and distance >= 25:
            elapsed += 0.5
        session_elapsed = elapsed
        if session_discontinuity == "gap" and distance >= 20:
            session_elapsed += 0.5
        elif session_discontinuity == "rewind" and distance >= 20:
            session_elapsed -= 0.5
        result.append(
            TraceSample(
                frame_identifier=(0xFFFFFFE0 + index) & 0xFFFFFFFF,
                distance_m=float(distance),
                time_s=elapsed + (distance * 0.002 if target else 0.0),
                speed_mps=speed,
                throttle=throttle,
                brake=brake,
                steering=0.2 if 12 <= distance <= 17 else 0.0,
                gear=4,
                drs_active=False,
                session_time_s=session_elapsed,
            )
        )
    return tuple(result)


def _analyze(
    window: DistanceWindow,
    *,
    gap: bool = False,
    session_discontinuity: str | None = None,
):
    target_samples = _samples(
        gap=gap,
        target=True,
        session_discontinuity=session_discontinuity,
    )
    reference_samples = _samples(gap=gap, target=False)
    config = ResamplingConfig(grid_step_m=1.0)
    grid = tuple(float(value) for value in range(41))
    target_resampled = resample_trace(target_samples, grid, config)
    reference_resampled = resample_trace(reference_samples, grid, config)
    delta = calculate_delta_time(target_resampled, reference_resampled)
    return analyze_comparison_window(
        target_samples,
        reference_samples,
        target_resampled,
        reference_resampled,
        delta,
        window,
        config=config,
    )


def _analyze_corner_interval(
    start_m: float,
    end_m: float,
    *,
    gap: bool = False,
    session_discontinuity: str | None = None,
    target_samples: tuple[TraceSample, ...] | None = None,
    reference_samples: tuple[TraceSample, ...] | None = None,
):
    target_samples = target_samples or _samples(
        gap=gap,
        target=True,
        session_discontinuity=session_discontinuity,
    )
    reference_samples = reference_samples or _samples(gap=gap, target=False)
    config = ResamplingConfig(grid_step_m=1.0)
    grid = tuple(float(value) for value in range(41))
    target_resampled = resample_trace(target_samples, grid, config)
    reference_resampled = resample_trace(reference_samples, grid, config)
    delta = calculate_delta_time(target_resampled, reference_resampled)
    model = TrackModel(
        model_id="test-draft-interval",
        revision=1,
        packet_format=2025,
        track_id=0,
        track_name="Test",
        layout_id="test-layout",
        track_length_m=40.0,
        distance_origin_m=0.0,
        provenance="Synthetic connected-interval fixture",
        validation_status="draft",
        corners=(
            CornerDefinition(
                identifier="interval",
                label="Draft interval",
                start_distance_m=start_m,
                end_distance_m=end_m,
            ),
        ),
    )
    report = analyze_corner_regions(
        target_samples,
        reference_samples,
        target_resampled,
        reference_resampled,
        delta,
        model,
        config=config,
        target_reference_eligible=True,
        reference_reference_eligible=True,
    )
    return report["regions"][0]["delta_change"]


def test_distance_window_reports_anchored_observations_and_connected_delta() -> None:
    result = _analyze(DistanceWindow(10.0, 30.0))

    assert result["schema_version"] == 1
    assert result["artifact_kind"] == "comparison_distance_window"
    assert result["interval_convention"] == "[start_m, end_m)"
    assert result["target"]["minimum_speed"] == {
        "status": "observed",
        "speed_kph": 36.0,
        "anchor": {
            "frame_identifier": (0xFFFFFFE0 + 20) & 0xFFFFFFFF,
            "session_time_s": 1.0,
            "lap_distance_m": 20.0,
        },
    }
    assert result["target"]["peak_brake"]["value"] == 0.8
    assert result["target"]["peak_brake"]["anchor"]["lap_distance_m"] == 11.0
    assert result["delta"]["start_boundary"]["target_minus_reference_s"] == pytest.approx(0.02)
    assert result["delta"]["end_boundary"]["target_minus_reference_s"] == pytest.approx(0.06)
    assert result["delta"]["delta_change_s"] == pytest.approx(0.04)
    assert result["delta"]["interval_connected_supported_time"] is True
    brake = result["target"]["threshold_events"]["brake_10_percent"]
    assert brake["event_count"] == 1
    assert brake["events"][0]["start_distance_m"] == 11.0
    assert brake["events"][0]["left_censored"] is False


def test_supported_boundaries_do_not_hide_an_unsupported_interior() -> None:
    result = _analyze(DistanceWindow(10.0, 30.0), gap=True)

    assert result["delta"]["start_boundary"]["status"] == "supported"
    assert result["delta"]["end_boundary"]["status"] == "supported"
    assert result["delta"]["interval_connected_supported_time"] is False
    assert result["delta"]["delta_change_s"] is None
    assert result["delta"]["status"] == "unsupported_interior"
    assert result["delta"]["shared_time_coverage"] < 1.0


@pytest.mark.parametrize("discontinuity", ["gap", "rewind"])
def test_window_delta_withholds_scalar_across_raw_session_time_discontinuity(
    discontinuity,
) -> None:
    result = _analyze(
        DistanceWindow(10.0, 30.0),
        session_discontinuity=discontinuity,
    )

    assert result["delta"]["start_boundary"]["status"] == "supported"
    assert result["delta"]["end_boundary"]["status"] == "supported"
    assert result["delta"]["target_source_session_time_connected"] is False
    assert result["delta"]["reference_source_session_time_connected"] is True
    assert result["delta"]["interval_connected_supported_time"] is False
    assert result["delta"]["delta_change_s"] is None
    assert result["delta"]["status"] == "unsupported_interior"
    assert (
        result["target"]["threshold_events"]["brake_10_percent"][
            "unsupported_break_count"
        ]
        > 0
    )


def test_window_reports_threshold_censoring_and_unsupported_requested_tail() -> None:
    censored = _analyze(DistanceWindow(11.0, 14.0))
    brake = censored["target"]["threshold_events"]["brake_10_percent"]
    assert brake["event_count"] == 1
    assert brake["left_censored_event_count"] == 1
    assert brake["right_censored_event_count"] == 1
    assert brake["events"][0]["left_censored"] is True
    assert brake["events"][0]["right_censored"] is True

    tail = _analyze(DistanceWindow(30.0, 45.0))
    assert tail["target"]["coverage"]["speed_mps"] < 1.0
    assert tail["delta"]["end_boundary"]["status"] == "unsupported"
    assert tail["delta"]["delta_change_s"] is None


def test_sub_grid_window_keeps_boundary_values_when_one_supported_cell_connects_it() -> None:
    result = _analyze(DistanceWindow(10.2, 10.8))

    assert result["delta"]["shared_time_coverage"] == 0.0
    assert result["delta"]["interval_connected_supported_time"] is True
    assert result["delta"]["delta_change_s"] == pytest.approx(0.0012)


def test_corner_interval_uses_the_same_connected_delta_evaluator_as_numeric_window() -> None:
    result = _analyze(DistanceWindow(10.0, 30.0))
    region = _analyze_corner_interval(10.0, 30.0)
    wrapped_samples = _samples(target=True)

    assert wrapped_samples[31].frame_identifier == 0xFFFFFFFF
    assert wrapped_samples[32].frame_identifier == 0

    assert region["entry_delta_s"] == result["delta"]["start_boundary"][
        "target_minus_reference_s"
    ]
    assert region["exit_delta_s"] == result["delta"]["end_boundary"][
        "target_minus_reference_s"
    ]
    assert region["delta_change_s"] == result["delta"]["delta_change_s"]
    assert region["interval_connected_supported_time"] is True
    assert region["shared_time_coverage"] == result["delta"]["shared_time_coverage"]
    assert region["status"] == "diagnostic_region_delta_change"


def test_source_session_time_accepts_float32_rounding_at_the_gap_limit() -> None:
    float32 = lambda value: struct.unpack("<f", struct.pack("<f", value))[0]
    samples = (
        replace(_samples()[0], distance_m=0.0, session_time_s=float32(10.0)),
        replace(_samples()[1], distance_m=1.0, session_time_s=float32(10.1)),
    )

    assert has_connected_source_session_time(
        samples,
        0.0,
        1.0,
        max_gap_s=0.1,
    ) is True


@pytest.mark.parametrize(
    ("kwargs", "expected_reason"),
    [
        ({"gap": True}, "shared_delta_time_disconnected"),
        ({"session_discontinuity": "gap"}, "target_source_session_time_disconnected"),
        ({"session_discontinuity": "rewind"}, "target_source_session_time_disconnected"),
    ],
)
def test_corner_interval_withholds_change_across_disconnected_evidence(
    kwargs, expected_reason
) -> None:
    region = _analyze_corner_interval(10.0, 30.0, **kwargs)

    assert region["entry_delta_s"] is not None
    assert region["exit_delta_s"] is not None
    assert region["interval_connected_supported_time"] is False
    assert region["delta_change_s"] is None
    assert region["status"] in {
        "unsupported_source_chronology",
        "unsupported_interior",
    }
    assert expected_reason in region["unavailable_reasons"]


def test_corner_interval_rejects_a_sub_grid_missing_clock_bracket() -> None:
    base = _samples(target=True)
    inserted = TraceSample(
        frame_identifier=0,
        distance_m=14.5,
        time_s=None,
        speed_mps=44.5,
        throttle=0.0,
        brake=0.0,
        steering=0.0,
        gear=4,
        drs_active=False,
        session_time_s=0.725,
    )
    samples = [*base[:15], inserted, *base[15:]]
    target_samples = tuple(
        replace(sample, frame_identifier=index + 1)
        for index, sample in enumerate(samples)
    )
    region = _analyze_corner_interval(
        10.0,
        20.0,
        target_samples=target_samples,
        reference_samples=_samples(target=False),
    )

    assert region["entry_delta_s"] is not None
    assert region["exit_delta_s"] is not None
    assert region["interval_connected_supported_time"] is False
    assert region["delta_change_s"] is None
    assert "target_resampled_time_disconnected" in region["unavailable_reasons"]


def test_corner_interval_reports_unsupported_exit_boundary_and_keeps_entry_value() -> None:
    target_samples = _samples(target=True)[:26]
    reference_samples = _samples(target=False)[:26]
    region = _analyze_corner_interval(
        20.0,
        35.0,
        target_samples=target_samples,
        reference_samples=reference_samples,
    )

    assert region["entry_delta_s"] is not None
    assert region["exit_delta_s"] is None
    assert region["delta_change_s"] is None
    assert region["status"] == "unsupported_entry_or_exit"
    assert "exit_boundary_unsupported" in region["unavailable_reasons"]


def test_interval_work_reservation_counts_sources_grids_and_excluded_spans() -> None:
    target_samples = _samples(target=True)
    reference_samples = _samples(target=False)
    config = ResamplingConfig(grid_step_m=1.0)
    grid = tuple(float(value) for value in range(41))
    target_resampled = resample_trace(target_samples, grid, config)
    reference_resampled = resample_trace(reference_samples, grid, config)
    delta = calculate_delta_time(target_resampled, reference_resampled)
    expected = (
        len(target_samples)
        + len(reference_samples)
        + len(target_resampled.distance_m)
        + len(reference_resampled.distance_m)
        + len(delta.values_s)
        + len(target_resampled.excluded_spans)
        + len(reference_resampled.excluded_spans)
    )

    assert reserve_interval_evaluation_work(
        1,
        target_samples,
        reference_samples,
        target_resampled,
        reference_resampled,
        delta,
        limit=expected,
    ) == expected
    with pytest.raises(ValueError, match="interval_evaluation_work_limit_exceeded"):
        reserve_interval_evaluation_work(
            1,
            target_samples,
            reference_samples,
            target_resampled,
            reference_resampled,
            delta,
            limit=expected - 1,
        )


def test_window_observations_match_shared_draft_region_measurements() -> None:
    samples = _samples(target=True)
    config = ResamplingConfig(grid_step_m=1.0)
    grid = tuple(float(value) for value in range(41))
    resampled = resample_trace(samples, grid, config)
    model = TrackModel(
        model_id="test-draft-window",
        revision=1,
        packet_format=2025,
        track_id=0,
        track_name="Test",
        layout_id="test-layout",
        track_length_m=40.0,
        distance_origin_m=0.0,
        provenance="Synthetic unvalidated window",
        validation_status="draft",
        corners=(
            CornerDefinition(
                identifier="window",
                label="Draft window",
                start_distance_m=10.0,
                end_distance_m=30.0,
                braking_search_window_m=(10.0, 30.0),
                turn_in_search_window_m=(10.0, 30.0),
                throttle_pickup_window_m=(10.0, 30.0),
            ),
        ),
    )

    report = analyze_attempt_regions(
        samples,
        resampled,
        model,
        config=config,
    )
    region = report["regions"][0]["observations"]
    comparison = _analyze(DistanceWindow(10.0, 30.0))

    assert (
        comparison["target"]["minimum_speed"]["speed_kph"]
        == region["minimum_speed"]["speed_kph"]
    )
    assert (
        comparison["target"]["coverage"]["speed_mps"]
        == region["minimum_speed"]["supported_grid_coverage"]
    )
    assert (
        comparison["target"]["threshold_events"]["brake_10_percent"]["events"]
        == region["braking"]["events"]
    )


def test_window_caps_each_threshold_example_list_and_keeps_full_event_count() -> None:
    samples = tuple(
        TraceSample(
            frame_identifier=(0xFFFFFF80 + distance) & 0xFFFFFFFF,
            distance_m=float(distance),
            time_s=distance * 0.05,
            speed_mps=30.0,
            throttle=(
                0.2
                if any(start <= distance < start + 3 for start in range(2, 198, 7))
                else 0.0
            ),
            brake=0.0,
            steering=0.0,
            gear=3,
            drs_active=False,
            session_time_s=distance * 0.05,
        )
        for distance in range(200)
    )
    config = ResamplingConfig(grid_step_m=1.0)
    grid = tuple(float(value) for value in range(200))
    resampled = resample_trace(samples, grid, config)
    delta = calculate_delta_time(resampled, resampled)

    result = analyze_comparison_window(
        samples,
        samples,
        resampled,
        resampled,
        delta,
        DistanceWindow(0.0, 200.0),
        config=config,
    )
    episodes = result["target"]["threshold_events"]["throttle_10_percent"]

    assert episodes["event_count"] == 28
    assert len(episodes["events"]) == 20
    assert episodes["events_truncated"] is True


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (10.0, None, "bounds_must_be_selected_together"),
        (None, 20.0, "bounds_must_be_selected_together"),
        ("nan", "10", "invalid_bounds"),
        (-1, 10, "invalid_bounds"),
        (10, 10, "invalid_bounds"),
    ],
)
def test_optional_distance_window_rejects_incomplete_and_invalid_bounds(
    start, end, message
) -> None:
    with pytest.raises(ValueError, match=message):
        optional_distance_window(start, end)


def test_optional_distance_window_keeps_no_window_and_track_limit_explicit() -> None:
    assert optional_distance_window(None, None) is None
    window = optional_distance_window("0", "20.5")
    assert window is not None
    assert window.to_dict() == {"start_m": 0.0, "end_m": 20.5}
    window.validate_track_length(20.5)
    with pytest.raises(ValueError, match="exceeds_track_length"):
        window.validate_track_length(20.0)
