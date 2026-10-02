from __future__ import annotations

import pytest

from f1_engineer.analysis import service as service_module
from f1_engineer.analysis.events import detect_sustained_threshold_events
from f1_engineer.analysis.resampling import (
    ResamplingConfig,
    TraceSample,
    common_distance_grid,
    resample_trace,
)
from f1_engineer.storage.query import StoredAttemptTrace
from f1_engineer.tracks.model import CornerDefinition, TrackModel


def _sample(
    frame: int,
    distance: float,
    time_s: float,
    *,
    speed: float | None = None,
    throttle: float | None = 0.5,
    brake: float | None = 0.0,
    gear: int | None = 3,
    session_time_s: float | None = None,
) -> TraceSample:
    return TraceSample(
        frame_identifier=frame,
        distance_m=distance,
        time_s=time_s,
        speed_mps=speed,
        throttle=throttle,
        brake=brake,
        steering=0.0,
        gear=gear,
        drs_active=False,
        session_time_s=session_time_s,
    )


def test_common_grid_uses_shared_observed_distance_without_extrapolation() -> None:
    target = (_sample(1, 2.4, 0.0), _sample(2, 17.6, 1.0))
    reference = (_sample(1, 5.2, 0.0), _sample(2, 15.4, 1.0))

    grid = common_distance_grid(target, reference, track_length_m=20)

    assert grid[0] == 6.0
    assert grid[-1] == 15.0
    assert all(distance >= 5.2 and distance <= 15.4 for distance in grid)


def test_resampler_linearly_interpolates_and_uses_previous_discrete_value() -> None:
    samples = (
        _sample(1, 0.0, 0.0, speed=10.0, throttle=0.0, gear=2),
        _sample(2, 10.0, 0.1, speed=20.0, throttle=1.0, gear=4),
        _sample(3, 20.0, 0.2, speed=30.0, throttle=0.0, gear=5),
    )

    result = resample_trace(samples, (0, 5, 10, 15, 20))

    assert result.values["time_s"] == pytest.approx((0.0, 0.05, 0.1, 0.15, 0.2))
    assert result.values["speed_mps"] == pytest.approx((10.0, 15.0, 20.0, 25.0, 30.0))
    assert result.values["throttle"] == pytest.approx((0.0, 0.5, 1.0, 0.5, 0.0))
    assert result.values["gear"] == (2, 2, 4, 4, 5)
    assert result.values["drs_active"] == (False,) * 5
    assert result.coverage["time_s"] == 1.0


def test_exact_time_gap_limit_is_inclusive_for_interpolation_and_stationarity() -> None:
    interpolated = resample_trace(
        (
            _sample(1, 0.0, 0.3, speed=10.0),
            _sample(2, 10.0, 0.4, speed=20.0),
        ),
        (0, 5, 10),
    )

    assert interpolated.masks["time_s"] == (True, True, True)
    assert interpolated.values["time_s"][1] == pytest.approx(0.35)
    assert interpolated.values["speed_mps"][1] == pytest.approx(15.0)
    assert not any(span.reason == "interpolation_gap" for span in interpolated.excluded_spans)

    stationary = resample_trace(
        (
            _sample(1, 0.0, 0.3, speed=10.0),
            _sample(2, 0.0, 0.4, speed=11.0),
            _sample(3, 10.0, 0.5, speed=20.0),
        ),
        (0, 5, 10),
    )

    assert stationary.masks["speed_mps"][0] is True
    assert not any(span.reason == "stationary_distance" for span in stationary.excluded_spans)


def test_resampler_masks_large_gaps_and_missing_channels_independently() -> None:
    samples = (
        _sample(1, 0.0, 0.0, speed=10.0, throttle=0.2),
        _sample(2, 10.0, 0.1, speed=20.0, throttle=None),
        _sample(3, 40.0, 0.3, speed=30.0, throttle=0.8),
    )

    result = resample_trace(samples, (0, 5, 10, 20, 30, 40))

    assert result.masks["time_s"] == (True, True, True, False, False, True)
    assert result.values["throttle"][5] == 0.8
    assert result.masks["throttle"][1] is False
    assert any(span.reason == "interpolation_gap" for span in result.excluded_spans)


def test_stationary_lap_clock_only_removes_time_interpolation() -> None:
    samples = (
        _sample(1, 0.0, 0.0, speed=10.0),
        _sample(2, 5.0, 0.0, speed=20.0),
        _sample(3, 10.0, 0.1, speed=30.0),
    )

    result = resample_trace(samples, (0, 2, 5, 7, 10))

    assert result.masks["time_s"] == (True, False, True, True, True)
    assert result.values["speed_mps"] == (10.0, 14.0, 20.0, 24.0, 30.0)
    assert any(span.reason == "stationary_clock" for span in result.excluded_spans)


def test_invalid_distance_breaks_interpolation_and_track_end_is_not_extrapolated() -> None:
    samples = (
        _sample(1, 0.0, 0.0, speed=10.0),
        _sample(2, 5.0, 0.05, speed=20.0),
        _sample(3, None, 0.06, speed=22.0),
        _sample(4, 10.0, 0.1, speed=30.0),
        _sample(5, 15.0, 0.15, speed=40.0),
    )

    result = resample_trace(samples, (0, 5, 7, 10, 12), track_length_m=12)

    assert result.masks["speed_mps"] == (True, True, False, True, False)
    assert any(span.reason == "missing_distance" for span in result.excluded_spans)
    assert any(span.reason == "distance_out_of_bounds" for span in result.excluded_spans)


def test_resampler_excludes_regressions_without_sorting_the_trace() -> None:
    samples = (
        _sample(1, 0.0, 0.0, speed=10.0),
        _sample(2, 10.0, 0.1, speed=20.0),
        _sample(3, 5.0, 0.2, speed=15.0),
        _sample(4, 15.0, 0.3, speed=25.0),
    )

    result = resample_trace(samples, tuple(float(distance) for distance in range(16)))

    assert result.values["speed_mps"][4] == 14.0
    assert result.values["speed_mps"][6:11] == (None, None, None, None, None)
    assert result.values["speed_mps"][11] == 21.0
    assert any(span.reason == "distance_regression" for span in result.excluded_spans)


def test_resampler_collapses_exact_duplicate_frames_and_reports_stationarity() -> None:
    first = _sample(1, 0.0, 0.0, speed=0.0)
    duplicate_frame = first
    samples = (
        first,
        duplicate_frame,
        _sample(2, 5.0, 0.05, speed=10.0),
        _sample(3, 5.0, 0.25, speed=10.0),
        _sample(4, 10.0, 0.30, speed=20.0),
    )

    result = resample_trace(samples, (0, 5, 10))

    assert result.duplicate_frame_count == 1
    assert result.duplicate_distance_count == 1
    assert result.masks["time_s"] == (True, False, True)
    assert any(span.reason == "stationary_distance" for span in result.excluded_spans)


def test_resampler_rejects_out_of_order_frames_instead_of_sorting() -> None:
    samples = (_sample(10, 0.0, 0.0), _sample(9, 10.0, 0.1))

    with pytest.raises(ValueError, match="not in chronological order"):
        resample_trace(samples, (0, 5, 10))


def test_sustained_event_uses_session_time_and_reports_distance_brackets() -> None:
    samples = (
        _sample(1, 0.0, 0.0, speed=50.0, brake=0.0, session_time_s=10.0),
        _sample(2, 1.0, 0.0, speed=49.0, brake=0.2, session_time_s=10.05),
        _sample(3, 2.0, 0.0, speed=48.0, brake=0.8, session_time_s=10.10),
        _sample(4, 3.0, 0.01, speed=47.0, brake=0.7, session_time_s=10.15),
        _sample(5, 4.0, 0.01, speed=46.0, brake=0.0, session_time_s=10.20),
    )

    result = detect_sustained_threshold_events(
        samples,
        channel="brake",
        threshold=0.1,
        search_window_m=(0.0, 5.0),
        minimum_duration_s=0.1,
    )

    assert result.status == "detected"
    assert len(result.sustained_events) == 1
    event = result.sustained_events[0]
    assert event.start_distance_m == 1.0
    assert event.start_distance_bracket_m == (0.0, 1.0)
    assert event.end_distance_m == 3.0
    assert event.end_distance_bracket_m == (3.0, 4.0)
    assert event.duration_s == pytest.approx(0.1)
    assert event.peak_value == 0.8
    assert not event.left_censored
    assert not event.right_censored


def test_threshold_detector_preserves_multiple_applications_and_censoring() -> None:
    samples = (
        _sample(1, 0.0, 0.0, throttle=0.0, session_time_s=1.00),
        _sample(2, 1.0, 0.01, throttle=0.2, session_time_s=1.05),
        _sample(3, 2.0, 0.02, throttle=0.0, session_time_s=1.10),
        _sample(4, 3.0, 0.03, throttle=0.2, session_time_s=1.15),
        _sample(5, 4.0, 0.04, throttle=0.3, session_time_s=1.20),
        _sample(6, 5.0, 0.05, throttle=0.4, session_time_s=1.25),
        _sample(7, 6.0, 0.06, throttle=0.0, session_time_s=1.30),
    )

    result = detect_sustained_threshold_events(
        samples,
        channel="throttle",
        threshold=0.1,
        search_window_m=(0.0, 7.0),
        minimum_duration_s=0.1,
    )

    assert len(result.sustained_events) == 1
    assert result.rejected_short_event_count == 1
    assert result.sustained_events[0].start_distance_m == 3.0

    censored = detect_sustained_threshold_events(
        samples,
        channel="throttle",
        threshold=0.1,
        search_window_m=(4.0, 6.0),
        minimum_duration_s=0.05,
    )

    assert censored.status == "left_censored"
    assert censored.sustained_events[0].left_censored


def test_threshold_detector_breaks_at_missing_values_and_large_gaps() -> None:
    samples = (
        _sample(1, 0.0, 0.0, throttle=0.3, session_time_s=0.0),
        _sample(2, 1.0, 0.01, throttle=0.4, session_time_s=0.05),
        _sample(3, 2.0, 0.02, throttle=None, session_time_s=0.10),
        _sample(4, 3.0, 0.03, throttle=0.4, session_time_s=0.25),
        _sample(5, 4.0, 0.04, throttle=0.5, session_time_s=0.30),
        _sample(6, 5.0, 0.05, throttle=0.6, session_time_s=0.35),
    )

    result = detect_sustained_threshold_events(
        samples,
        channel="throttle",
        threshold=0.1,
        search_window_m=(0.0, 6.0),
        minimum_duration_s=0.1,
        max_gap_time_s=0.1,
    )

    assert result.unsupported_break_count >= 2
    assert result.sustained_events
    assert result.sustained_events[0].left_censored


def _stored_attempt(
    attempt_key: str,
    times_ms: tuple[int, int, int],
    *,
    game_valid: bool | None = True,
    context: dict[str, object] | None = None,
    speed_mps: float = 30.0,
    throttle: float = 0.75,
    distances_m: tuple[float, float, float] = (0.0, 10.0, 20.0),
) -> StoredAttemptTrace:
    if context is None:
        context = {
            "packet_format": 2025,
            "track_id": 0,
            "track_name": "Melbourne",
            "track_length_m": 20,
            "weather_id": 0,
            "weather_name": "clear",
            "session_type": "time_trial",
            "game_mode": "time_trial",
            "rule_set": "time_trial",
            "formula_id": 0,
            "equal_car_performance_id": 0,
            "steering_assist_id": 0,
            "braking_assist_id": 0,
            "gearbox_assist_id": 1,
        }
    samples = tuple(
        {
            "frame_identifier": frame,
            "lap_distance_m": distance,
            "current_lap_time_ms": time_ms,
            "session_time_s": frame / 100,
            "speed_mps": speed_mps,
            "throttle": throttle,
            "brake": 0.0,
            "steering": 0.0,
            "gear": 4,
            "drs_active": False,
        }
        for frame, distance, time_ms in zip((10, 20, 30), distances_m, times_ms)
    )
    return StoredAttemptTrace(
        attempt_key=attempt_key,
        run_id="run",
        session_uid="42",
        car_index=0,
        disposition="completed",
        lap_time_ms=80_000 if attempt_key.startswith("target") else 79_900,
        game_valid=game_valid,
        reference_eligible=game_valid is True,
        exclusion_reasons=("game_marked_invalid",) if game_valid is False else (),
        trace_sha256=("a" if attempt_key.startswith("target") else "b") * 64,
        trace_schema_version=1,
        quality={"sample_count": 3},
        context_segments=((10, context),),
        samples=samples,
    )


def test_comparison_reports_absolute_delta_and_keeps_invalid_attempt_visible(monkeypatch) -> None:
    target = _stored_attempt("target-attempt", (100, 200, 300), game_valid=False)
    reference = _stored_attempt(
        "reference-attempt", (100, 150, 200), speed_mps=28.0, throttle=0.5
    )
    attempts = {target.attempt_key: target, reference.attempt_key: reference}
    monkeypatch.setattr(
        service_module,
        "load_attempt_trace",
        lambda _database, key: attempts.get(key),
    )

    result = service_module.compare_attempts("test.sqlite3", "target-attempt", "reference-attempt")

    assert result["official_lap_time_difference_s"] == 0.1
    assert result["delta_s"][10] == pytest.approx(0.05)
    assert result["delta_s"][-1] == pytest.approx(0.1)
    assert result["observed_range_delta"]["observed_range_change_s"] == pytest.approx(0.1)
    assert result["target"]["game_valid"] is False
    assert result["target"]["exclusion_reasons"] == ["game_marked_invalid"]
    assert result["target"]["reference_eligible"] is False
    assert result["target"]["session_context_segments"][0]["context"]["weather_name"] == "clear"
    assert result["channel_difference_direction"] == "target_minus_reference"
    assert result["quality"]["delta_time_coverage"] == 1.0
    assert result["channel_differences"]["speed_mps"]["values"][10] == 2.0
    assert result["channel_differences"]["throttle"]["values"][10] == 0.25


def test_comparison_rejects_unknown_context_and_cross_mode_attempts(monkeypatch) -> None:
    target = _stored_attempt("target-attempt", (100, 200, 300), context=None)
    unknown = {**target.context_segments[0][1], "game_mode": None}
    target = _stored_attempt("target-attempt", (100, 200, 300), context=unknown)
    reference = _stored_attempt("reference-attempt", (100, 150, 200))
    attempts = {target.attempt_key: target, reference.attempt_key: reference}
    monkeypatch.setattr(
        service_module,
        "load_attempt_trace",
        lambda _database, key: attempts.get(key),
    )

    with pytest.raises(ValueError, match="unknown.*mode"):
        service_module.compare_attempts("test.sqlite3", "target-attempt", "reference-attempt")

    attempts[target.attempt_key] = _stored_attempt("target-attempt", (100, 200, 300))
    race = {**reference.context_segments[0][1], "session_type": "race"}
    attempts[reference.attempt_key] = _stored_attempt(
        "reference-attempt", (100, 150, 200), context=race
    )
    with pytest.raises(ValueError, match="not a known Time Trial"):
        service_module.compare_attempts("test.sqlite3", "target-attempt", "reference-attempt")


def test_corner_region_analysis_keeps_draft_and_invalid_laps_diagnostic(monkeypatch) -> None:
    target = _stored_attempt("target-attempt", (100, 200, 300), game_valid=False)
    reference = _stored_attempt("reference-attempt", (100, 150, 200), game_valid=False)
    attempts = {target.attempt_key: target, reference.attempt_key: reference}
    monkeypatch.setattr(
        service_module,
        "load_attempt_trace",
        lambda _database, key: attempts.get(key),
    )
    model = TrackModel(
        model_id="melbourne-draft-test",
        revision=1,
        packet_format=2025,
        track_id=0,
        track_name="Melbourne",
        layout_id="default",
        track_length_m=20,
        distance_origin_m=0,
        provenance="synthetic test region",
        validation_status="draft",
        corners=(
            CornerDefinition(
                identifier="region-1",
                label="Draft region 1",
                start_distance_m=0,
                end_distance_m=20,
                braking_search_window_m=(0, 20),
                turn_in_search_window_m=(0, 20),
                throttle_pickup_window_m=(0, 20),
                nominal_apex_m=10,
                exit_distance_m=10,
            ),
        ),
    )

    result = service_module.compare_attempts(
        "test.sqlite3",
        "target-attempt",
        "reference-attempt",
        track_model=model,
    )

    corner_analysis = result["corner_analysis"]
    assert corner_analysis["diagnostic_only"] is True
    assert corner_analysis["model"]["validation_status"] == "draft"
    region = corner_analysis["regions"][0]
    assert region["label"] == "Draft region 1"
    assert region["diagnostic_only"] is True
    assert region["target"]["minimum_speed"]["status"] == "observed_minimum_complete_window"
    assert region["target"]["track_apex"]["status"] == "draft_metadata_anchor"
    assert (
        region["target"]["driver_apex"]["reason"]
        == "validated_track_relative_geometry_unavailable"
    )
    assert region["target"]["event_channel_coverage"] == {
        "brake": 1.0,
        "steering": 1.0,
        "throttle": 1.0,
    }
    assert (
        region["target"]["turn_in_proxy"]["interpretation"]
        == "absolute steering threshold; calibrated track-relative geometry unavailable"
    )
    assert region["target"]["throttle_pickup"]["0.5"]["status"] == "left_censored"
    assert region["delta_change"]["delta_change_s"] == pytest.approx(0.1)

    attempts[target.attempt_key] = _stored_attempt(
        "target-attempt", (100, 200, 300), game_valid=True
    )
    attempts[reference.attempt_key] = _stored_attempt(
        "reference-attempt", (100, 150, 200), game_valid=True
    )
    eligible_result = service_module.compare_attempts(
        "test.sqlite3",
        "target-attempt",
        "reference-attempt",
        track_model=model,
    )
    assert eligible_result["corner_analysis"]["diagnostic_only"] is True
    assert eligible_result["corner_analysis"]["regions"][0]["diagnostic_only"] is True
    assert (
        eligible_result["corner_analysis"]["regions"][0]["delta_change"]["status"]
        == "diagnostic_region_delta_change"
    )

    attempts[target.attempt_key] = _stored_attempt(
        "target-attempt", (100, 200, 300), game_valid=False
    )
    attempts[reference.attempt_key] = _stored_attempt(
        "reference-attempt",
        (100, 200, 300),
        game_valid=False,
        distances_m=(0.0, 5.0, 10.0),
    )
    tail_unsupported = service_module.compare_attempts(
        "test.sqlite3",
        "target-attempt",
        "reference-attempt",
        track_model=model,
    )
    assert (
        tail_unsupported["corner_analysis"]["regions"][0]["reference"][
            "event_channel_coverage"
        ]["brake"]
        == pytest.approx(0.5)
    )
