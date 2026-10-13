from dataclasses import replace

import pytest

from f1_engineer.analysis.corners import analyze_distance_window
from f1_engineer.analysis.resampling import TraceSample


def trace():
    return tuple(
        TraceSample(frame_identifier=index, distance_m=index * 5.0, time_s=index * 0.05,
                    session_time_s=10 + index * 0.05, speed_mps=50 - index,
                    brake=0.5 if 4 <= index <= 8 else 0.0, throttle=0.0,
                    steering=0.0, gear=3, drs_active=False)
        for index in range(13)
    )


def measure(samples=None, start=10.0, end=50.0):
    return analyze_distance_window(trace() if samples is None else samples, start, end,
                                   track_length_m=100.0)


def test_connected_numeric_window_reports_measured_duration_speed_and_brake():
    result = measure()
    assert result["elapsed"]["status"] == "supported"
    assert result["elapsed"]["elapsed_s"] == pytest.approx(0.4)
    assert result["source_session_time_connected"]
    assert result["supported_grid_coverage"] == {"time_s": 1.0, "speed_mps": 1.0, "brake": 1.0}
    minimum = result["observations"]["minimum_speed"]
    assert minimum["status"] == "observed_minimum_complete_window"
    assert minimum["speed_kph"] == pytest.approx(41 * 3.6)
    assert minimum["source_anchor"]["frame_identifier"] == 9
    braking = result["observations"]["braking"]
    assert braking["status"] == "detected"
    assert braking["distance_m"] == 20
    assert braking["distance_bracket_m"] == [15, 20]
    assert result["diagnostic_only"]
    assert not any(result[key] for key in ("layout_verified", "coaching_eligible", "ranking_eligible"))
    assert result["observations"]["driver_apex"]["status"] == "unavailable"
    assert result["observations"]["exit_speeds"] == []


def test_fractional_boundaries_interpolate_only_supported_time():
    result = measure(start=10.5, end=49.5)
    assert result["elapsed"]["elapsed_s"] == pytest.approx(0.39)


@pytest.mark.parametrize("samples", [(), trace()[4:], trace()[:8]])
def test_missing_boundaries_never_invent_elapsed(samples):
    result = measure(samples)
    assert result["elapsed"]["elapsed_s"] is None


@pytest.mark.parametrize("field,value", [
    ("session_time_s", None), ("session_time_s", 9.0), ("session_time_s", 11.0),
    ("distance_m", None), ("distance_m", 1.0), ("time_s", None), ("time_s", 0.0),
])
def test_interior_discontinuities_never_bridge_elapsed(field, value):
    samples = list(trace())
    samples[6] = replace(samples[6], **{field: value})
    assert measure(samples)["elapsed"]["elapsed_s"] is None


def test_sparse_source_is_not_connected_even_when_boundaries_present():
    result = measure(trace()[::3])
    assert not result["source_session_time_connected"]
    assert result["elapsed"]["elapsed_s"] is None
    assert result["observations"]["minimum_speed"]["status"] != "observed_minimum_complete_window"


def test_missing_speed_preserves_supported_time_without_minimum():
    result = measure(tuple(replace(sample, speed_mps=None) for sample in trace()))
    assert result["elapsed"]["elapsed_s"] == pytest.approx(0.4)
    assert result["observations"]["minimum_speed"]["speed_kph"] is None


def test_missing_brake_preserves_time_and_speed_without_onset():
    result = measure(tuple(replace(sample, brake=None) for sample in trace()))
    assert result["elapsed"]["status"] == "supported"
    assert result["observations"]["braking"]["distance_m"] is None


def test_brake_active_at_entrance_is_not_an_observed_braking_point():
    result = measure(tuple(replace(sample, brake=0.5) for sample in trace()))
    braking = result["observations"]["braking"]
    assert braking["status"] == "left_censored"
    assert braking["distance_m"] is None


def test_brief_brake_spike_is_not_sustained_onset():
    result = measure(tuple(replace(sample, brake=0.5 if index == 4 else 0.0)
                           for index, sample in enumerate(trace())))
    assert result["observations"]["braking"]["distance_m"] is None


@pytest.mark.parametrize("start,end,length", [
    (True, 50, 100), (0, False, 100), (0, 50, True), (-1, 50, 100),
    (50, 50, 100), (51, 50, 100), (0, 101, 100), (0, 50, 20_001),
    (float("nan"), 50, 100), (0, float("inf"), 100), (0, 50, "100"),
])
def test_invalid_window_bounds_rejected(start, end, length):
    with pytest.raises(ValueError, match="distance_window_bounds_invalid"):
        analyze_distance_window(trace(), start, end, track_length_m=length)


def test_window_work_is_bounded_before_resampling():
    with pytest.raises(ValueError, match="width_limit"):
        analyze_distance_window((), 0, 1001, track_length_m=2000)
    with pytest.raises(ValueError, match="sample_limit"):
        measure((trace()[0],) * 4097)


@pytest.mark.parametrize("field,value", [
    ("frame_identifier", True), ("frame_identifier", -1), ("frame_identifier", 2**32),
    ("distance_m", True), ("distance_m", -1), ("distance_m", float("nan")),
    ("speed_mps", -1), ("speed_mps", float("inf")), ("speed_mps", "fast"),
    ("brake", 1.1), ("brake", True), ("throttle", -0.1),
    ("time_s", -1), ("session_time_s", 86_401), ("steering", 2),
    ("gear", True), ("gear", 9), ("drs_active", 1),
])
def test_invalid_sample_channels_rejected(field, value):
    samples = list(trace())
    samples[6] = replace(samples[6], **{field: value})
    with pytest.raises(ValueError, match="distance_window_sample_invalid"):
        measure(samples)


def test_conflicting_duplicate_frames_rejected():
    samples = list(trace())
    samples.insert(6, replace(samples[6], brake=1.0))
    with pytest.raises(ValueError, match="chronological"):
        measure(samples)


def test_identical_duplicate_does_not_change_elapsed():
    samples = list(trace())
    samples.insert(6, samples[6])
    assert measure(samples)["elapsed"]["elapsed_s"] == pytest.approx(0.4)


def test_source_samples_are_not_modified():
    samples = trace()
    original = tuple(samples)
    measure(samples)
    assert samples == original
