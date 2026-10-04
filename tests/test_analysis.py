from __future__ import annotations

import struct
from dataclasses import replace

import pytest

from f1_engineer.analysis import service as service_module
from f1_engineer.analysis.comparison_window import DistanceWindow
from f1_engineer.analysis.corners import analyze_attempt_regions
from f1_engineer.analysis.events import detect_sustained_threshold_events
from f1_engineer.analysis import quality as quality_module
from f1_engineer.analysis.resampling import (
    ExcludedSpan,
    ResamplingConfig,
    TraceSample,
    _hard_block_grid_masks,
    common_distance_grid,
    estimate_resampling_work,
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


def test_indexed_hard_block_masks_match_the_inclusive_bruteforce_rule() -> None:
    grid = tuple(float(distance) for distance in range(8))
    blocks = (
        ExcludedSpan(2.0, 5.0, "distance_regression"),
        ExcludedSpan(3.0, 4.0, "stationary_distance"),
        ExcludedSpan(1.0, 2.0, "lap_clock_regression", "brake"),
        ExcludedSpan(6.0, 6.0, "stationary_distance", "throttle"),
        ExcludedSpan(0.0, 7.0, "interpolation_gap"),
        ExcludedSpan(0.0, 7.0, "distance_regression", "unknown_channel"),
    )

    global_mask, channel_masks = _hard_block_grid_masks(grid, blocks)

    for channel in ("time_s", "speed_mps", "brake", "throttle"):
        actual = tuple(
            global_mask[index]
            or (
                channel in channel_masks
                and channel_masks[channel][index]
            )
            for index in range(len(grid))
        )
        expected = tuple(
            any(
                (block.channel is None or block.channel == channel)
                and block.start_distance_m - 1e-7 <= distance <= block.end_distance_m + 1e-7
                and block.reason
                in {"distance_regression", "lap_clock_regression", "stationary_distance"}
                for block in blocks
            )
            for distance in grid
        )
        assert actual == expected


def test_indexed_hard_block_ranges_preserve_epsilon_endpoints_and_zero_width_blocks() -> None:
    grid = tuple(float(distance) for distance in range(5))
    blocks = (
        ExcludedSpan(2.0 + 5e-8, 2.0 + 5e-8, "stationary_distance"),
        ExcludedSpan(3.0 + 2e-7, 3.0 + 2e-7, "distance_regression"),
    )

    global_mask, channel_masks = _hard_block_grid_masks(grid, blocks)

    assert global_mask == (False, False, True, False, False)
    assert channel_masks == {}


def test_indexed_masks_bound_work_for_highly_fragmented_support() -> None:
    grid = tuple(float(distance) for distance in range(1000))
    blocks = tuple(
        ExcludedSpan(
            float(index % 1000),
            float(index % 1000),
            "distance_regression",
            "brake",
        )
        for index in range(10_000)
    )

    global_mask, channel_masks = _hard_block_grid_masks(grid, blocks)
    estimated_work = estimate_resampling_work(
        10_000,
        len(grid),
        ResamplingConfig(),
        hard_block_count=len(blocks),
    )

    assert not any(global_mask)
    assert channel_masks["brake"] == (True,) * len(grid)
    assert estimated_work < 16_000_000


def test_indexed_resampling_work_estimator_matches_policy_formula() -> None:
    assert estimate_resampling_work(
        21,
        21,
        ResamplingConfig(),
        hard_block_count=3,
    ) == 7833


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


@pytest.mark.parametrize(("start", "end"), [(10.0, 10.1), (64.0, 64.1)])
def test_threshold_event_uses_float32_tolerance_at_exact_time_boundaries(
    start: float, end: float
) -> None:
    encode_float32 = lambda value: struct.unpack("<f", struct.pack("<f", value))[0]
    samples = (
        _sample(
            1,
            0.0,
            0.0,
            throttle=0.2,
            session_time_s=encode_float32(start),
        ),
        _sample(
            2,
            1.0,
            0.1,
            throttle=0.2,
            session_time_s=encode_float32(end),
        ),
    )

    result = detect_sustained_threshold_events(
        samples,
        channel="throttle",
        threshold=0.1,
        search_window_m=(0.0, 2.0),
        minimum_duration_s=0.1,
    )

    assert len(result.sustained_events) == 1
    assert result.rejected_short_event_count == 0
    assert result.sustained_events[0].duration_s == pytest.approx(0.1, abs=2e-6)


def _stored_attempt(
    attempt_key: str,
    times_ms: tuple[int, int, int],
    *,
    game_valid: bool | None = True,
    context: dict[str, object] | None = None,
    speed_mps: float = 30.0,
    throttle: float = 0.75,
    distances_m: tuple[float, float, float] = (0.0, 10.0, 20.0),
    run_id: str = "run",
    session_uid: str = "42",
    car_index: int = 0,
    lap_time_ms: int | None = None,
    start_observed: bool = True,
    pit_encountered: bool = False,
    disposition: str = "completed",
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
        run_id=run_id,
        session_uid=session_uid,
        car_index=car_index,
        disposition=disposition,
        lap_time_ms=(
            lap_time_ms
            if lap_time_ms is not None
            else 80_000 if attempt_key.startswith("target") else 79_900
        ),
        game_valid=game_valid,
        reference_eligible=game_valid is True,
        exclusion_reasons=("game_marked_invalid",) if game_valid is False else (),
        superseded=False,
        lifecycle_assessed=True,
        trace_sha256=("a" if attempt_key.startswith("target") else "b") * 64,
        trace_schema_version=1,
        quality={"sample_count": 3},
        context_segments=((10, context),),
        samples=samples,
        start_observed=start_observed,
        pit_encountered=pit_encountered,
    )


def _practice_context(**overrides: object) -> dict[str, object]:
    context = {
        "packet_format": 2025,
        "track_id": 2,
        "track_name": "Shanghai",
        "track_length_m": 20,
        "weather_id": 1,
        "weather_name": "light_cloud",
        "session_type": "practice_1",
        "game_mode": "driver_career_25",
        "rule_set": "practice_qualifying",
        "formula_id": 0,
        "equal_car_performance_id": 0,
        "steering_assist_id": 0,
        "braking_assist_id": 0,
        "gearbox_assist_id": 1,
    }
    context.update(overrides)
    return context


def test_practice_qualifying_comparison_is_explicit_bounded_and_diagnostic(
    monkeypatch, tmp_path
) -> None:
    target = _stored_attempt(
        "target-practice",
        (100, 200, 300),
        context=_practice_context(),
        game_valid=False,
        lap_time_ms=80_100,
    )
    reference = _stored_attempt(
        "reference-practice",
        (100, 150, 200),
        context=_practice_context(),
        lap_time_ms=79_900,
    )
    attempts = {target.attempt_key: target, reference.attempt_key: reference}
    load_calls: list[dict[str, object]] = []
    evidence_calls: list[str] = []
    database_path = tmp_path / "analysis.sqlite3"
    database_path.touch()
    run_evidence = {
        "run_id": "run",
        "capture": {"complete": False, "footer_status": "incomplete"},
        "processing": {"replay_counters": None},
    }

    def load(_database, key, **kwargs):
        load_calls.append(kwargs)
        return attempts.get(key)

    monkeypatch.setattr(service_module, "load_attempt_trace", load)
    monkeypatch.setattr(
        service_module,
        "get_processing_run_summary",
        lambda _database, run_id: evidence_calls.append(run_id) or run_evidence,
    )
    result = service_module.compare_attempts(
        database_path,
        target.attempt_key,
        reference.attempt_key,
        policy=service_module.ComparisonPolicy.PRACTICE_QUALIFYING,
    )

    assert result["comparison_policy"] == "practice_qualifying"
    assert result["comparison_policy_version"] == "pq-same-session-diagnostic-v1"
    assert result["diagnostic_only"] is True
    assert result["policy_limitations"] == [
        "fuel_load_uncontrolled",
        "tyre_condition_uncontrolled",
        "traffic_uncontrolled",
        "cooldown_intent_uncontrolled",
    ]
    assert result["official_lap_time_difference_s"] == pytest.approx(0.2)
    brief = result["comparison_brief"]
    assert brief["analysis_version"] == "comparison-brief-v1"
    assert brief["status"] == "available"
    assert "Target was 0.200 s slower" in brief["text"]
    assert result["corner_comparison_brief"]["status"] == "abstained"
    assert result["corner_comparison_brief"]["gate_reasons"] == [
        "unsupported_comparison_policy"
    ]
    assert result["target"]["trace_sha256"] == "a" * 64
    assert result["reference"]["trace_sha256"] == "b" * 64
    assert result["target"]["game_valid"] is False
    assert result["processing_run_evidence"] == {
        "target": run_evidence,
        "reference": run_evidence,
    }
    assert evidence_calls == ["run"]
    assert len(load_calls) == 2
    assert all(call["max_trace_rows"] == 100_000 for call in load_calls)


@pytest.mark.parametrize(
    ("target_updates", "reference_updates", "message"),
    [
        ({"run_id": "other"}, {}, "must_share_processing_run"),
        ({}, {"session_uid": "other"}, "must_share_session"),
        ({}, {"car_index": 1}, "must_share_player"),
    ],
)
def test_practice_qualifying_comparison_requires_same_run_session_and_player(
    monkeypatch, target_updates, reference_updates, message
) -> None:
    target = _stored_attempt("target-practice", (100, 200, 300), context=_practice_context())
    reference = _stored_attempt("reference-practice", (100, 150, 200), context=_practice_context())
    target = replace(target, **target_updates)
    reference = replace(reference, **reference_updates)
    attempts = {target.attempt_key: target, reference.attempt_key: reference}
    monkeypatch.setattr(
        service_module,
        "load_attempt_trace",
        lambda _database, key, **_kwargs: attempts.get(key),
    )
    with pytest.raises(ValueError, match=message):
        service_module.compare_attempts(
            "test.sqlite3",
            target.attempt_key,
            reference.attempt_key,
            policy="practice_qualifying",
            distance_window=DistanceWindow(0.0, 20.0),
        )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"lap_time_ms": 0}, "positive_lap_time"),
        ({"start_observed": False}, "start_unobserved"),
        ({"pit_encountered": True}, "encountered_pit"),
        ({"disposition": "partial"}, "only completed laps"),
    ],
)
def test_practice_qualifying_comparison_requires_completed_started_non_pit_laps(
    monkeypatch, updates, message
) -> None:
    target = _stored_attempt("target-practice", (100, 200, 300), context=_practice_context())
    reference = _stored_attempt("reference-practice", (100, 150, 200), context=_practice_context())
    target = replace(target, **updates)
    attempts = {target.attempt_key: target, reference.attempt_key: reference}
    monkeypatch.setattr(
        service_module,
        "load_attempt_trace",
        lambda _database, key, **_kwargs: attempts.get(key),
    )
    with pytest.raises(ValueError, match=message):
        service_module.compare_attempts(
            "test.sqlite3",
            target.attempt_key,
            reference.attempt_key,
            policy="practice_qualifying",
            distance_window=DistanceWindow(0.0, 20.0),
        )


@pytest.mark.parametrize(
    "context",
    [
        _practice_context(session_type="race", rule_set="race"),
        _practice_context(session_type="unknown"),
        _practice_context(track_name=None),
        _practice_context(game_mode="time_trial"),
    ],
)
def test_practice_qualifying_comparison_rejects_race_unknown_and_incomplete_context(
    monkeypatch, context
) -> None:
    target = _stored_attempt("target-practice", (100, 200, 300), context=context)
    reference = _stored_attempt("reference-practice", (100, 150, 200), context=context)
    attempts = {target.attempt_key: target, reference.attempt_key: reference}
    monkeypatch.setattr(
        service_module,
        "load_attempt_trace",
        lambda _database, key, **_kwargs: attempts.get(key),
    )
    with pytest.raises(ValueError):
        service_module.compare_attempts(
            "test.sqlite3",
            target.attempt_key,
            reference.attempt_key,
            policy="practice_qualifying",
            distance_window=DistanceWindow(0.0, 20.0),
        )


@pytest.mark.parametrize(
    ("target_context_segments", "reference_context"),
    [
        (
            (
                (10, _practice_context()),
                (20, _practice_context(formula_id=1)),
            ),
            _practice_context(),
        ),
        (
            ((10, _practice_context(track_id=3, track_name="Melbourne")),),
            _practice_context(),
        ),
    ],
)
def test_practice_qualifying_comparison_rejects_changing_or_mismatched_context(
    monkeypatch, target_context_segments, reference_context
) -> None:
    target = _stored_attempt(
        "target-practice", (100, 200, 300), context=_practice_context()
    )
    target = replace(target, context_segments=target_context_segments)
    reference = _stored_attempt(
        "reference-practice", (100, 150, 200), context=reference_context
    )
    attempts = {target.attempt_key: target, reference.attempt_key: reference}
    monkeypatch.setattr(
        service_module,
        "load_attempt_trace",
        lambda _database, key, **_kwargs: attempts.get(key),
    )

    with pytest.raises(ValueError, match="context_changed|incompatible_context"):
        service_module.compare_attempts(
            "test.sqlite3",
            target.attempt_key,
            reference.attempt_key,
            policy="practice_qualifying",
            distance_window=DistanceWindow(0.0, 20.0),
        )


def test_practice_qualifying_comparison_rejects_regions_and_oversized_grid(monkeypatch) -> None:
    target = _stored_attempt("target-practice", (100, 200, 300), context=_practice_context())
    reference = _stored_attempt("reference-practice", (100, 150, 200), context=_practice_context())
    attempts = {target.attempt_key: target, reference.attempt_key: reference}
    monkeypatch.setattr(
        service_module,
        "load_attempt_trace",
        lambda _database, key, **_kwargs: attempts.get(key),
    )
    with pytest.raises(ValueError, match="region_analysis_unsupported"):
        service_module.compare_attempts(
            "test.sqlite3",
            target.attempt_key,
            reference.attempt_key,
            policy="practice_qualifying",
            track_model="unused.json",
        )
    with pytest.raises(ValueError, match="resampling_grid_limit_exceeded"):
        service_module.compare_attempts(
            "test.sqlite3",
            target.attempt_key,
            reference.attempt_key,
            policy="practice_qualifying",
            config=ResamplingConfig(grid_step_m=0.0001),
        )
    with pytest.raises(ValueError, match="resampling_grid_limit_exceeded"):
        service_module.compare_attempts(
            "test.sqlite3",
            target.attempt_key,
            reference.attempt_key,
            policy="practice_qualifying",
            config=ResamplingConfig(grid_step_m=5e-324),
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
        lambda _database, key, **_kwargs: attempts.get(key),
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
    assert "comparison_window" not in result


@pytest.mark.parametrize(
    ("superseded", "lifecycle_assessed", "reason"),
    [
        (True, True, "superseded_by_flashback"),
        (None, False, "lifecycle_evidence_unassessed"),
    ],
)
def test_time_trial_comparison_keeps_lifecycle_exclusions_visible(
    monkeypatch, superseded, lifecycle_assessed, reason
) -> None:
    target = _stored_attempt("target-attempt", (100, 200, 300))
    target = replace(
        target,
        reference_eligible=False,
        superseded=superseded,
        lifecycle_assessed=lifecycle_assessed,
        exclusion_reasons=(reason,) if superseded is True else (),
    )
    reference = _stored_attempt("reference-attempt", (100, 150, 200))
    attempts = {target.attempt_key: target, reference.attempt_key: reference}
    monkeypatch.setattr(
        service_module,
        "load_attempt_trace",
        lambda _database, key, **_kwargs: attempts.get(key),
    )

    result = service_module.compare_attempts(
        "unused.sqlite3", target.attempt_key, reference.attempt_key
    )

    assert result["diagnostic_only"] is True
    assert result["target"]["reference_eligible"] is False
    if superseded is True:
        assert reason in result["target"]["exclusion_reasons"]
    else:
        assert result["target"]["exclusion_reasons"] == []
    assert reason in result["target"]["lifecycle_exclusions"]


def test_practice_qualifying_superseded_pair_keeps_target_reference_status(monkeypatch) -> None:
    target = replace(
        _stored_attempt("target-practice", (100, 200, 300), context=_practice_context()),
        reference_eligible=False,
        superseded=True,
        lifecycle_assessed=True,
        exclusion_reasons=("superseded_by_flashback",),
    )
    reference = _stored_attempt(
        "reference-practice", (100, 150, 200), context=_practice_context()
    )
    attempts = {target.attempt_key: target, reference.attempt_key: reference}
    monkeypatch.setattr(
        service_module,
        "load_attempt_trace",
        lambda _database, key, **_kwargs: attempts.get(key),
    )

    result = service_module.compare_attempts(
        "unused.sqlite3",
        target.attempt_key,
        reference.attempt_key,
        policy="practice_qualifying",
    )

    assert result["diagnostic_only"] is True
    assert result["target"]["lifecycle_exclusions"] == ["superseded_by_flashback"]
    assert result["reference"]["lifecycle_exclusions"] == []


def test_time_trial_window_uses_bounded_source_reads_and_preserves_policy(monkeypatch):
    target = _stored_attempt("target-attempt", (100, 200, 300))
    reference = _stored_attempt("reference-attempt", (100, 150, 200))
    attempts = {target.attempt_key: target, reference.attempt_key: reference}
    load_calls: list[dict[str, object]] = []

    def load(_database, key, **kwargs):
        load_calls.append(kwargs)
        return attempts.get(key)

    monkeypatch.setattr(service_module, "load_attempt_trace", load)

    result = service_module.compare_attempts(
        "unused.sqlite3",
        target.attempt_key,
        reference.attempt_key,
        distance_window=DistanceWindow(0.0, 20.0),
    )

    window = result["comparison_window"]
    assert result["comparison_policy"] == "time_trial"
    assert result["diagnostic_only"] is False
    assert window["analysis_version"] == "comparison-distance-window-v1"
    assert window["window_m"] == {"start_m": 0.0, "end_m": 20.0}
    assert window["delta"]["delta_change_s"] == pytest.approx(0.1)
    brief = result["distance_window_brief"]
    assert brief["analysis_version"] == "distance-window-brief-v1"
    assert brief["coaching_eligible"] is False
    assert brief["provenance"]["target"]["trace_sha256"] == "a" * 64
    assert any(
        fact["kind"] == "connected_interval_time_difference"
        for fact in brief["facts"]
    )
    assert len(load_calls) == 2
    assert all(call["max_trace_rows"] == 100_000 for call in load_calls)
    assert all(call["max_trace_bytes"] == 64 * 1024 * 1024 for call in load_calls)


def test_time_trial_window_rejects_out_of_track_range_and_oversized_grid(
    monkeypatch,
) -> None:
    target = _stored_attempt("target-attempt", (100, 200, 300))
    reference = _stored_attempt("reference-attempt", (100, 150, 200))
    attempts = {target.attempt_key: target, reference.attempt_key: reference}
    monkeypatch.setattr(
        service_module,
        "load_attempt_trace",
        lambda _database, key, **_kwargs: attempts.get(key),
    )

    with pytest.raises(ValueError, match="comparison_window_exceeds_track_length"):
        service_module.compare_attempts(
            "unused.sqlite3",
            target.attempt_key,
            reference.attempt_key,
            distance_window=DistanceWindow(10.0, 21.0),
        )
    with pytest.raises(ValueError, match="comparison_window_resampling_grid_limit_exceeded"):
        service_module.compare_attempts(
            "unused.sqlite3",
            target.attempt_key,
            reference.attempt_key,
            config=ResamplingConfig(grid_step_m=0.0001),
            distance_window=DistanceWindow(10.0, 11.0),
        )


def test_comparison_condition_summary_matches_standalone_quality_semantics(
    monkeypatch,
) -> None:
    target = _stored_attempt("target-attempt", (100, 200, 300))
    reference = _stored_attempt("reference-attempt", (100, 150, 200))

    def with_conditions(attempt, fuel: tuple[float, float, float], tyre_age: int):
        samples = tuple(
            {
                **sample,
                "validation_flags": [],
                "car_status_available": True,
                "car_status_unavailable_reason": None,
                "fuel_in_tank_reported": value,
                "actual_tyre_compound": 20,
                "visual_tyre_compound": 17,
                "tyre_age_laps": tyre_age,
            }
            for sample, value in zip(attempt.samples, fuel)
        )
        context = {
            **attempt.context_segments[0][1],
            "track_temperature_c": 30,
            "air_temperature_c": 24,
        }
        return replace(
            attempt,
            trace_schema_version=3,
            samples=samples,
            context_segments=((10, context),),
        )

    target = with_conditions(target, (7.194, 6.5, 5.9), 1)
    reference = with_conditions(reference, (5.421, 5.1, 4.8), 2)
    attempts = {target.attempt_key: target, reference.attempt_key: reference}
    load_calls: list[dict[str, object]] = []

    def load(_database, key, **kwargs):
        load_calls.append(kwargs)
        return attempts.get(key)

    monkeypatch.setattr(service_module, "load_attempt_trace", load)

    result = service_module.compare_attempts(
        "unused.sqlite3", target.attempt_key, reference.attempt_key
    )

    assert result["observed_conditions"]["target"] == quality_module.summarize_observed_conditions(
        target.samples,
        trace_schema_version=target.trace_schema_version,
        context_segments=target.context_segments,
    )
    assert result["observed_conditions"]["reference"] == quality_module.summarize_observed_conditions(
        reference.samples,
        trace_schema_version=reference.trace_schema_version,
        context_segments=reference.context_segments,
    )
    assert result["observed_conditions"]["target"]["first_last_observed"][
        "fuel_in_tank_reported"
    ]["first"]["lap_distance_m"] == 0.0
    assert all("car_status_available" in call["columns"] for call in load_calls)


def test_comparison_rejects_unknown_context_and_cross_mode_attempts(monkeypatch) -> None:
    target = _stored_attempt("target-attempt", (100, 200, 300), context=None)
    unknown = {**target.context_segments[0][1], "game_mode": None}
    target = _stored_attempt("target-attempt", (100, 200, 300), context=unknown)
    reference = _stored_attempt("reference-attempt", (100, 150, 200))
    attempts = {target.attempt_key: target, reference.attempt_key: reference}
    monkeypatch.setattr(
        service_module,
        "load_attempt_trace",
        lambda _database, key, **_kwargs: attempts.get(key),
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

    load_calls: list[dict[str, object]] = []

    def load(_database, key, **kwargs):
        load_calls.append(kwargs)
        return attempts.get(key)

    monkeypatch.setattr(
        service_module,
        "load_attempt_trace",
        load,
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
    assert len(load_calls) == 2
    assert all(call["max_trace_rows"] == 100_000 for call in load_calls)
    assert all(call["max_trace_bytes"] == 64 * 1024 * 1024 for call in load_calls)
    assert corner_analysis["source"] == {
        "target": {
            "attempt_key": target.attempt_key,
            "run_id": target.run_id,
            "trace_sha256": target.trace_sha256,
        },
        "reference": {
            "attempt_key": reference.attempt_key,
            "run_id": reference.run_id,
            "trace_sha256": reference.trace_sha256,
        },
    }
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
    target_samples = tuple(TraceSample.from_record(row) for row in target.samples)
    reference_samples = tuple(TraceSample.from_record(row) for row in reference.samples)
    grid = common_distance_grid(target_samples, reference_samples, track_length_m=20)
    target_resampled = resample_trace(
        target_samples, grid, track_length_m=20
    )
    standalone = analyze_attempt_regions(
        target_samples,
        target_resampled,
        model,
        config=ResamplingConfig(),
    )
    assert standalone["regions"][0]["observations"] == region["target"]
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


def test_comparison_reserves_all_interval_work_before_reporting_any_interval(monkeypatch) -> None:
    target = _stored_attempt("target-attempt", (100, 200, 300))
    reference = _stored_attempt("reference-attempt", (100, 150, 200))
    attempts = {target.attempt_key: target, reference.attempt_key: reference}
    monkeypatch.setattr(
        service_module,
        "load_attempt_trace",
        lambda _database, key, **_kwargs: attempts.get(key),
    )
    model = TrackModel(
        model_id="bounded-work-test",
        revision=1,
        packet_format=2025,
        track_id=0,
        track_name="Melbourne",
        layout_id="default",
        track_length_m=20,
        distance_origin_m=0,
        provenance="synthetic bounded-work fixture",
        validation_status="draft",
        corners=(CornerDefinition("region-1", "Region 1", 0, 20),),
    )
    reservations: list[int] = []

    def reject_reservation(interval_count, *_args, **_kwargs):
        reservations.append(interval_count)
        raise ValueError("interval_evaluation_work_limit_exceeded")

    def must_not_evaluate(*_args, **_kwargs):
        raise AssertionError("an interval was evaluated before its total work was reserved")

    monkeypatch.setattr(service_module, "reserve_interval_evaluation_work", reject_reservation)
    monkeypatch.setattr(service_module, "analyze_comparison_window", must_not_evaluate)
    monkeypatch.setattr(service_module, "analyze_corner_regions", must_not_evaluate)

    with pytest.raises(ValueError, match="interval_evaluation_work_limit_exceeded"):
        service_module.compare_attempts(
            "test.sqlite3",
            target.attempt_key,
            reference.attempt_key,
            track_model=model,
            distance_window=DistanceWindow(5.0, 15.0),
        )

    assert reservations == [2]
