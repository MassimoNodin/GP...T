from __future__ import annotations

from dataclasses import replace

import pytest

from f1_engineer.analysis import region_service
from f1_engineer.analysis.region_service import RegionReportUnavailable
from f1_engineer.storage.query import AttemptTraceReadLimitError, StoredAttemptTrace
from f1_engineer.tracks.model import CornerDefinition, TrackModel


def _context(**overrides: object) -> dict[str, object]:
    return {
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
        **overrides,
    }


def _attempt(
    *,
    disposition: str = "completed",
    game_valid: bool | None = False,
    context: dict[str, object] | None = None,
    trace_schema_version: int = 3,
    samples: tuple[dict[str, object], ...] | None = None,
) -> StoredAttemptTrace:
    if samples is None:
        samples = tuple(
            {
                "frame_identifier": frame,
                "lap_distance_m": distance,
                "current_lap_time_ms": time_ms,
                "session_time_s": session_time,
                "speed_mps": speed,
                "throttle": throttle,
                "brake": brake,
                "steering": steering,
                "gear": 3,
                "drs_active": False,
            }
            for frame, distance, time_ms, session_time, speed, throttle, brake, steering in (
                (1, 0.0, 0, 1.0, 20.0, 0.0, 0.0, 0.0),
                (2, 10.0, 500, 1.1, 15.0, 0.2, 0.2, 0.0),
                (3, 20.0, 1000, 1.2, 22.0, 0.8, 0.0, 0.2),
            )
        )
    active_context = context or _context()
    return StoredAttemptTrace(
        attempt_key="run:42:0:1",
        run_id="run",
        session_uid="42",
        car_index=0,
        disposition=disposition,
        lap_time_ms=80_000 if disposition == "completed" else None,
        game_valid=game_valid,
        reference_eligible=False,
        exclusion_reasons=("game_marked_invalid",) if game_valid is False else ("partial_attempt",),
        trace_sha256="a" * 64,
        trace_schema_version=trace_schema_version,
        quality={"sample_count": len(samples)},
        context_segments=((1, active_context),),
        samples=samples,
        attempt_number=1,
        start_observed=True,
        pit_encountered=False,
    )


def _model(*, track_length_m: float = 20.0) -> TrackModel:
    return TrackModel(
        model_id="melbourne-draft-test-v1",
        revision=1,
        packet_format=2025,
        track_id=0,
        track_name="Melbourne",
        layout_id="test-layout",
        track_length_m=track_length_m,
        distance_origin_m=0,
        provenance="synthetic test windows",
        validation_status="draft",
        corners=(
            CornerDefinition(
                identifier="window-1",
                label="Draft window 1",
                start_distance_m=0,
                end_distance_m=20,
                braking_search_window_m=(0, 20),
                turn_in_search_window_m=(0, 20),
                throttle_pickup_window_m=(0, 20),
                exit_distance_m=10,
            ),
        ),
    )


@pytest.mark.parametrize("schema_version", [1, 2, 3])
def test_single_attempt_report_accepts_invalid_and_partial_time_trial_traces(
    monkeypatch, schema_version: int
) -> None:
    attempt = _attempt(trace_schema_version=schema_version)
    calls: dict[str, object] = {}

    def load(_database, _attempt_key, **kwargs):
        calls.update(kwargs)
        return attempt

    monkeypatch.setattr(region_service, "load_attempt_trace", load)
    report = region_service.load_attempt_region_report("test.sqlite3", attempt.attempt_key, _model())

    assert report is not None
    assert report["diagnostic_only"] is True
    assert report["source"]["game_valid"] is False
    assert report["source"]["reference_eligible"] is False
    assert report["source"]["trace_schema_version"] == schema_version
    assert report["source"]["trace_sha256"] == "a" * 64
    assert report["regions"][0]["observations"]["minimum_speed"]["speed_kph"] == pytest.approx(54.0)
    assert calls["max_trace_rows"] == 100_000
    assert calls["max_trace_bytes"] == 64 * 1024 * 1024
    assert calls["max_context_segments"] == 1_024
    assert calls["max_context_bytes"] == 4 * 1024 * 1024

    partial_samples = attempt.samples[:2]
    partial = replace(
        attempt,
        disposition="partial",
        lap_time_ms=None,
        game_valid=None,
        reference_eligible=False,
        exclusion_reasons=("capture_ended_before_lap_completion",),
        samples=partial_samples,
    )
    monkeypatch.setattr(region_service, "load_attempt_trace", lambda *_args, **_kwargs: partial)
    partial_report = region_service.load_attempt_region_report(
        "test.sqlite3", attempt.attempt_key, _model()
    )

    assert partial_report is not None
    partial_observation = partial_report["regions"][0]["observations"]
    assert partial_report["source"]["disposition"] == "partial"
    assert partial_observation["minimum_speed"]["status"] == "observed_minimum_partial_window"
    assert partial_observation["minimum_speed"]["supported_grid_coverage"] < 1.0

    abandoned = replace(
        partial,
        disposition="abandoned",
        exclusion_reasons=("attempt_abandoned_before_completion",),
    )
    monkeypatch.setattr(region_service, "load_attempt_trace", lambda *_args, **_kwargs: abandoned)
    abandoned_report = region_service.load_attempt_region_report(
        "test.sqlite3", attempt.attempt_key, _model()
    )
    assert abandoned_report is not None
    assert abandoned_report["source"]["disposition"] == "abandoned"


@pytest.mark.parametrize(
    ("context_segments", "reason"),
    [
        (((1, _context(session_type="race", game_mode="race", rule_set="race")),), "unsupported_mode"),
        (
            (
                (1, _context()),
                (2, _context(track_name="Other circuit", track_id=4)),
            ),
            "context_changed",
        ),
    ],
)
def test_single_attempt_region_report_abstains_for_unsupported_context(
    monkeypatch, context_segments, reason: str
) -> None:
    attempt = replace(_attempt(), context_segments=context_segments)
    monkeypatch.setattr(region_service, "load_attempt_trace", lambda *_args, **_kwargs: attempt)

    with pytest.raises(RegionReportUnavailable) as caught:
        region_service.load_attempt_region_report("test.sqlite3", attempt.attempt_key, _model())

    assert caught.value.reason_code == reason


def test_single_attempt_region_report_abstains_for_incompatible_model(monkeypatch) -> None:
    attempt = _attempt()
    monkeypatch.setattr(region_service, "load_attempt_trace", lambda *_args, **_kwargs: attempt)
    incompatible = replace(_model(), track_id=2)

    with pytest.raises(RegionReportUnavailable) as caught:
        region_service.load_attempt_region_report("test.sqlite3", attempt.attempt_key, incompatible)

    assert caught.value.reason_code == "track_model_incompatible"


def test_single_attempt_region_report_abstains_when_source_or_grid_exceeds_bounds(
    monkeypatch,
) -> None:
    attempt = _attempt()

    def oversized(*_args, **_kwargs):
        raise AttemptTraceReadLimitError("rows")

    monkeypatch.setattr(region_service, "load_attempt_trace", oversized)
    with pytest.raises(RegionReportUnavailable) as caught:
        region_service.load_attempt_region_report("test.sqlite3", attempt.attempt_key, _model())
    assert caught.value.reason_code == "region_source_rows_limit_exceeded"

    with pytest.raises(RegionReportUnavailable) as caught_grid:
        region_service._bounded_track_grid(100_000.0, 1.0)
    assert caught_grid.value.reason_code == "region_resampling_grid_limit_exceeded"
