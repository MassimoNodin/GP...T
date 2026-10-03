from __future__ import annotations

import pytest

from f1_engineer.analysis.corners import (
    _grid_value,
    _requested_window_coverage,
    analyze_attempt_regions,
)
from f1_engineer.analysis.events import detect_sustained_threshold_events
from f1_engineer.analysis.resampling import ResamplingConfig, TraceSample, resample_trace
from f1_engineer.tracks.model import CornerDefinition, TrackModel


def _sample(
    frame: int,
    distance: float,
    session_time: float,
    *,
    lap_time: float,
    speed: float,
    throttle: float | None = 0.0,
) -> TraceSample:
    return TraceSample(
        frame_identifier=frame,
        distance_m=distance,
        time_s=lap_time,
        speed_mps=speed,
        throttle=throttle,
        brake=0.0,
        steering=0.0,
        gear=3,
        drs_active=False,
        session_time_s=session_time,
    )


def test_event_at_exact_search_window_entrance_is_left_censored() -> None:
    samples = (
        TraceSample(9, 9.0, 0.0, 20.0, 0.0, 0.0, 0.0, 3, False, 1.00),
        TraceSample(10, 10.0, 0.05, 19.0, 0.0, 0.2, 0.0, 3, False, 1.05),
        TraceSample(11, 11.0, 0.10, 18.0, 0.0, 0.3, 0.0, 3, False, 1.10),
        TraceSample(12, 12.0, 0.15, 17.0, 0.0, 0.4, 0.0, 3, False, 1.15),
    )

    result = detect_sustained_threshold_events(
        samples,
        channel="brake",
        threshold=0.1,
        search_window_m=(10.0, 20.0),
        minimum_duration_s=0.1,
    )

    assert result.status == "left_censored"
    assert result.sustained_events[0].left_censored
    assert result.sustained_events[0].start_distance_bracket_m is None


def test_region_coverage_counts_the_unobserved_parts_of_the_window() -> None:
    samples = (
        _sample(1, 50.0, 1.0, lap_time=1.0, speed=30.0),
        _sample(2, 55.0, 1.05, lap_time=1.05, speed=31.0),
        _sample(3, 60.0, 1.10, lap_time=1.10, speed=32.0),
    )
    config = ResamplingConfig(grid_step_m=5.0)
    resampled = resample_trace(samples, (50.0, 55.0, 60.0), config)

    coverage = _requested_window_coverage(
        resampled, 0.0, 100.0, config.grid_step_m, channel="speed_mps"
    )

    assert coverage == pytest.approx(0.10)


def test_region_coverage_includes_an_unsupported_requested_tail() -> None:
    samples = (
        _sample(1, 0.0, 1.0, lap_time=0.0, speed=30.0),
        _sample(2, 10.0, 1.1, lap_time=0.1, speed=31.0),
    )
    config = ResamplingConfig(grid_step_m=10.0)
    resampled = resample_trace(samples, (0.0, 10.0), config)

    coverage = _requested_window_coverage(
        resampled, 0.0, 20.0, config.grid_step_m, channel="speed_mps"
    )

    assert coverage == pytest.approx(0.5)


def test_missing_channel_bracket_is_preserved_without_interior_grid_points() -> None:
    samples = (
        _sample(1, 3.0, 1.00, lap_time=0.00, speed=30.0),
        _sample(2, 4.25, 1.02, lap_time=0.02, speed=31.0),
        _sample(3, 4.50, 1.03, lap_time=0.03, speed=32.0, throttle=None),
        _sample(4, 4.75, 1.04, lap_time=0.04, speed=33.0),
        _sample(5, 6.0, 1.06, lap_time=0.06, speed=34.0),
    )
    resampled = resample_trace(samples, (3.0, 4.0, 5.0, 6.0))

    assert any(
        span.channel == "throttle"
        and span.reason == "channel_missing"
        and span.start_distance_m == pytest.approx(4.25)
        and span.end_distance_m == pytest.approx(4.5)
        for span in resampled.excluded_spans
    )
    assert _grid_value(
        resampled.distance_m,
        resampled.values["throttle"],
        resampled.masks["throttle"],
        4.5,
        channel="throttle",
        excluded_spans=resampled.excluded_spans,
    ) is None


def test_boundary_sampling_does_not_interpolate_across_an_excluded_gap() -> None:
    samples = (
        _sample(1, 0.0, 1.0, lap_time=0.0, speed=10.0),
        _sample(2, 10.0, 1.2, lap_time=0.2, speed=20.0),
    )
    config = ResamplingConfig(grid_step_m=10.0, max_bracket_time_s=0.1)
    resampled = resample_trace(samples, (0.0, 10.0), config)

    assert resampled.masks["time_s"] == (True, True)
    assert _grid_value(
        resampled.distance_m,
        resampled.values["time_s"],
        resampled.masks["time_s"],
        5.0,
        step_m=10.0,
    ) == pytest.approx(0.1)
    assert _grid_value(
        resampled.distance_m,
        resampled.values["time_s"],
        resampled.masks["time_s"],
        5.0,
        step_m=10.0,
        channel="time_s",
        excluded_spans=resampled.excluded_spans,
    ) is None
    assert _grid_value(
        resampled.distance_m,
        resampled.values["speed_mps"],
        resampled.masks["speed_mps"],
        5.0,
        step_m=10.0,
        channel="speed_mps",
        excluded_spans=resampled.excluded_spans,
    ) is None


def test_standalone_region_observations_cap_examples_but_keep_onset_and_count() -> None:
    throttle_values = (0.0, 0.2, 0.2, 0.2, 0.0, 0.0, 0.2, 0.2, 0.2, 0.0, 0.0, 0.0, 0.0)
    samples = tuple(
        TraceSample(
            frame_identifier=index + 1,
            distance_m=float(index),
            time_s=index * 0.05,
            speed_mps=20.0 + index,
            throttle=throttle,
            brake=0.0,
            steering=0.0,
            gear=3,
            drs_active=False,
            session_time_s=1.0 + index * 0.05,
        )
        for index, throttle in enumerate(throttle_values)
    )
    config = ResamplingConfig()
    resampled = resample_trace(samples, tuple(float(index) for index in range(13)), config)
    model = TrackModel(
        model_id="test-region-v1",
        revision=1,
        packet_format=2025,
        track_id=0,
        track_name="Melbourne",
        layout_id="synthetic",
        track_length_m=12,
        distance_origin_m=0,
        provenance="synthetic test window",
        validation_status="draft",
        corners=(
            CornerDefinition(
                identifier="window-1",
                label="Draft window 1",
                start_distance_m=0,
                end_distance_m=12,
                throttle_pickup_window_m=(0, 12),
            ),
        ),
    )

    result = analyze_attempt_regions(
        samples,
        resampled,
        model,
        config=config,
        event_example_limit=1,
    )

    throttle = result["regions"][0]["observations"]["throttle_pickup"]["0.1"]
    assert throttle["status"] == "detected"
    assert throttle["distance_m"] == pytest.approx(1.0)
    assert throttle["event_count"] == 2
    assert len(throttle["events"]) == 1
    assert throttle["events_truncated"] is True
    assert result["diagnostic_only"] is True
