from __future__ import annotations

from dataclasses import replace

import pytest

from f1_engineer.analysis import region_service
from f1_engineer.analysis.region_service import RegionReportUnavailable
from f1_engineer.storage.query import (
    AttemptTraceReadLimitError,
    AttemptTraceResourceEstimate,
    StoredAttemptTrace,
)
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
        source_sample_count=len(samples),
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


def _patch_load(monkeypatch, attempt: StoredAttemptTrace) -> None:
    estimate = AttemptTraceResourceEstimate(
        attempt_key=attempt.attempt_key,
        run_id=attempt.run_id,
        session_uid=attempt.session_uid,
        car_index=attempt.car_index,
        attempt_number=attempt.attempt_number,
        disposition=attempt.disposition,
        lap_time_ms=attempt.lap_time_ms,
        game_valid=attempt.game_valid,
        start_observed=attempt.start_observed,
        pit_encountered=attempt.pit_encountered,
        superseded=attempt.superseded,
        lifecycle_assessed=attempt.lifecycle_assessed,
        exclusion_reasons=attempt.exclusion_reasons,
        trace_ready=True,
        trace_row_count=len(attempt.samples),
        trace_sha256=attempt.trace_sha256,
        trace_schema_version=attempt.trace_schema_version,
        trace_size_bytes=1024,
        context_segment_count=len(attempt.context_segments),
        context_bytes=128,
        context_segments=attempt.context_segments,
    )
    monkeypatch.setattr(
        region_service,
        "load_attempt_trace_resource_estimates",
        lambda *_args, **_kwargs: (estimate,),
    )
    monkeypatch.setattr(
        region_service,
        "load_attempt_trace",
        lambda *_args, **_kwargs: attempt,
    )
    monkeypatch.setattr(
        region_service,
        "get_processing_run_summary",
        lambda *_args: {
            "capture": {
                "complete": True,
                "footer_status": "complete",
                "recording_counters": {
                    "queue_dropped": 0,
                    "unpersisted_on_shutdown": 0,
                    "socket_errors": 0,
                },
            },
            "processing": {
                "replay_counters": {
                    "import_late_packets_ignored": 0,
                    "import_frame_overflow_packets_dropped": 0,
                }
            },
        },
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

    _patch_load(monkeypatch, attempt)
    monkeypatch.setattr(region_service, "load_attempt_trace", load)
    report = region_service.load_attempt_region_report("test.sqlite3", attempt.attempt_key, _model())

    assert report is not None
    assert report["schema_version"] == 3
    assert report["diagnostic_only"] is True
    assert report["context_mode"] == "time_trial"
    assert report["source"]["game_valid"] is False
    assert report["source"]["reference_eligible"] is False
    assert report["source"]["trace_schema_version"] == schema_version
    assert report["source"]["trace_sha256"] == "a" * 64
    assert report["model"]["layout_identity_status"] == "caller_declared"
    assert report["source"]["capture"]["complete"] is True
    assert any(warning["code"] == "game_invalid" for warning in report["warnings"])
    assert report["regions"][0]["observations"]["minimum_speed"]["speed_kph"] == pytest.approx(54.0)
    assert calls["columns"] == region_service.REGION_POSITION_TRACE_COLUMNS
    position_evidence = report["regions"][0]["position_evidence"]
    if schema_version == 1:
        assert position_evidence["status"] == "motion_unavailable_for_trace_schema"
        assert position_evidence["source_position_sample_count"] is None
    else:
        assert position_evidence["status"] == "positions_unavailable_in_window"
        assert position_evidence["source_sample_count_in_window"] == 2
        assert position_evidence["unsupported_source_sample_count_in_window"] == 2
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
        source_sample_count=len(partial_samples),
    )
    _patch_load(monkeypatch, partial)
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
    _patch_load(monkeypatch, abandoned)
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
    _patch_load(monkeypatch, attempt)

    with pytest.raises(RegionReportUnavailable) as caught:
        region_service.load_attempt_region_report("test.sqlite3", attempt.attempt_key, _model())

    assert caught.value.reason_code == reason


def test_single_attempt_region_report_abstains_for_incompatible_model(monkeypatch) -> None:
    attempt = _attempt()
    _patch_load(monkeypatch, attempt)
    incompatible = replace(_model(), track_id=2)

    with pytest.raises(RegionReportUnavailable) as caught:
        region_service.load_attempt_region_report("test.sqlite3", attempt.attempt_key, incompatible)

    assert caught.value.reason_code == "track_model_incompatible"


def test_single_attempt_region_report_supports_practice_qualifying_context(monkeypatch) -> None:
    context = _context(
        session_type="practice_1",
        game_mode="driver_career_25",
        rule_set="practice_qualifying",
    )
    attempt = _attempt(context=context)
    _patch_load(monkeypatch, attempt)

    report = region_service.load_attempt_region_report(
        "test.sqlite3",
        attempt.attempt_key,
        _model(),
        model_metadata={"origin": "local_draft", "content_sha256": "b" * 64},
    )

    assert report is not None
    assert report["context_mode"] == "practice_qualifying"
    assert report["model"]["origin"] == "local_draft"
    assert report["model"]["content_sha256"] == "b" * 64
    assert any(
        warning["code"] == "practice_qualifying_conditions_uncontrolled"
        for warning in report["warnings"]
    )


def test_region_analysis_work_cap_is_checked_after_resampling_before_evaluation(
    monkeypatch,
) -> None:
    attempt = _attempt()
    _patch_load(monkeypatch, attempt)
    expected = region_service._region_analysis_work(1, 3, 21, 0)
    monkeypatch.setattr(region_service, "REGION_ANALYSIS_WORK_LIMIT", expected - 1)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("region evaluator must not run over the work limit")

    monkeypatch.setattr(region_service, "analyze_attempt_regions", fail_if_called)
    with pytest.raises(RegionReportUnavailable) as caught:
        region_service.load_attempt_region_report(
            "test.sqlite3", attempt.attempt_key, _model()
        )
    assert caught.value.reason_code == "region_analysis_work_limit_exceeded"


def test_resampling_work_cap_precedes_resampling_and_accepts_exact_limit(monkeypatch) -> None:
    attempt = _attempt()
    _patch_load(monkeypatch, attempt)
    expected = region_service._region_resampling_work(
        1,
        len(attempt.samples),
        21,
        region_service.ResamplingConfig(),
        hard_block_count=0,
    )
    monkeypatch.setattr(region_service, "REGION_RESAMPLING_WORK_LIMIT", expected - 1)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("resampling must not run over the work limit")

    monkeypatch.setattr(region_service, "resample_trace", fail_if_called)
    with pytest.raises(RegionReportUnavailable) as caught:
        region_service.load_attempt_region_report(
            "test.sqlite3", attempt.attempt_key, _model()
        )
    assert caught.value.reason_code == "region_resampling_work_limit_exceeded"

    monkeypatch.undo()
    _patch_load(monkeypatch, attempt)
    monkeypatch.setattr(region_service, "REGION_RESAMPLING_WORK_LIMIT", expected)
    report = region_service.load_attempt_region_report(
        "test.sqlite3", attempt.attempt_key, _model()
    )
    assert report is not None
    assert report["resource_policy"]["resampling"]["estimated_work"] == expected


def test_region_warnings_preserve_unknown_counters_with_positive_losses(monkeypatch) -> None:
    attempt = _attempt()
    _patch_load(monkeypatch, attempt)
    monkeypatch.setattr(
        region_service,
        "get_processing_run_summary",
        lambda *_args: {
            "capture": {
                "complete": False,
                "footer_status": "incomplete",
                "recording_counters": {
                    "queue_dropped": 1,
                    "unpersisted_on_shutdown": None,
                    "socket_errors": 0,
                },
            },
            "processing": {
                "replay_counters": {
                    "import_late_packets_ignored": 2,
                    "import_frame_overflow_packets_dropped": None,
                }
            },
        },
    )

    report = region_service.load_attempt_region_report(
        "test.sqlite3", attempt.attempt_key, _model()
    )

    warnings = {warning["code"]: warning["text"] for warning in report["warnings"]}
    assert warnings["capture_incomplete"]
    assert "unknown" in warnings["recording_loss_reported"]
    assert "unknown" in warnings["replay_frame_exclusions_reported"]


def test_single_attempt_region_report_abstains_when_source_or_grid_exceeds_bounds(
    monkeypatch,
) -> None:
    attempt = _attempt()

    def oversized(*_args, **_kwargs):
        raise AttemptTraceReadLimitError("rows")

    _patch_load(monkeypatch, attempt)
    monkeypatch.setattr(region_service, "load_attempt_trace", oversized)
    with pytest.raises(RegionReportUnavailable) as caught:
        region_service.load_attempt_region_report("test.sqlite3", attempt.attempt_key, _model())
    assert caught.value.reason_code == "region_source_rows_limit_exceeded"

    with pytest.raises(RegionReportUnavailable) as caught_grid:
        region_service._bounded_track_grid(100_000.0, 1.0)
    assert caught_grid.value.reason_code == "region_resampling_grid_limit_exceeded"
