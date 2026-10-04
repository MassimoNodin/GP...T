from __future__ import annotations

from dataclasses import replace

import pytest

from f1_engineer.analysis import paired_region_service as paired_service
from f1_engineer.analysis.paired_region_service import (
    PairedRegionReportUnavailable,
    compare_attempt_regions,
)
from f1_engineer.analysis.resampling import ResamplingConfig
from f1_engineer.storage.query import AttemptTraceResourceEstimate, StoredAttemptTrace
from f1_engineer.tracks.model import CornerDefinition, TrackModel


def _context(
    *,
    session_type: str = "time_trial",
    game_mode: str = "time_trial",
    rule_set: str = "time_trial",
    **overrides: object,
) -> dict[str, object]:
    return {
        "packet_format": 2025,
        "track_id": 0,
        "track_name": "Melbourne",
        "track_length_m": 20.0,
        "weather_id": 0,
        "weather_name": "clear",
        "session_type": session_type,
        "game_mode": game_mode,
        "rule_set": rule_set,
        "formula_id": 0,
        "equal_car_performance_id": 0,
        "steering_assist_id": 0,
        "braking_assist_id": 0,
        "gearbox_assist_id": 1,
        **overrides,
    }


def _samples(*, reference: bool) -> tuple[dict[str, object], ...]:
    rows = []
    for distance in range(21):
        lap_step_ms = 52 if reference else 50
        brake = 0.3 if (11 <= distance <= 17 if reference else 8 <= distance <= 14) else 0.0
        throttle = 0.6 if (distance >= 15 if reference else distance >= 13) else 0.0
        minimum_speed = 13.0 if reference else 15.0
        speed = minimum_speed if distance == 10 else 25.0 + distance * 0.1
        rows.append(
            {
                "frame_identifier": distance + 1,
                "lap_distance_m": float(distance),
                "current_lap_time_ms": distance * lap_step_ms,
                "session_time_s": 10.0 + distance * 0.05,
                "speed_mps": speed,
                "throttle": throttle,
                "brake": brake,
                "steering": 0.2 if distance >= 8 else 0.0,
                "gear": 4,
                "drs_active": False,
            }
        )
    return tuple(rows)


def _attempt(attempt_key: str, *, reference: bool, context: dict[str, object] | None = None) -> StoredAttemptTrace:
    rows = _samples(reference=reference)
    return StoredAttemptTrace(
        attempt_key=attempt_key,
        run_id="run-1",
        session_uid="42",
        car_index=0,
        disposition="completed",
        lap_time_ms=90_000 if reference else 89_000,
        game_valid=False,
        reference_eligible=False,
        exclusion_reasons=("game_marked_invalid",),
        trace_sha256=("b" if reference else "a") * 64,
        trace_schema_version=3,
        quality={"sample_count": len(rows)},
        context_segments=((1, context or _context()),),
        samples=rows,
        attempt_number=2 if reference else 1,
        start_observed=True,
        pit_encountered=False,
        superseded=None,
        lifecycle_assessed=False,
        source_sample_count=len(rows),
    )


def _estimate(attempt: StoredAttemptTrace, *, bytes_count: int = 4096) -> AttemptTraceResourceEstimate:
    return AttemptTraceResourceEstimate(
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
        trace_row_count=attempt.source_sample_count,
        trace_sha256=attempt.trace_sha256,
        trace_schema_version=attempt.trace_schema_version,
        trace_size_bytes=bytes_count,
        context_segment_count=len(attempt.context_segments),
        context_bytes=256,
        context_segments=attempt.context_segments,
    )


def _model(*, track_length_m: float = 20.0) -> TrackModel:
    return TrackModel(
        model_id="local-melbourne-draft-v1",
        revision=3,
        packet_format=2025,
        track_id=0,
        track_name="Melbourne",
        layout_id="caller-layout",
        track_length_m=track_length_m,
        distance_origin_m=0.0,
        provenance="synthetic diagnostic regions",
        validation_status="draft",
        corners=(
            CornerDefinition(
                identifier="window-1",
                label="Draft window one",
                start_distance_m=5.0,
                end_distance_m=20.0,
                braking_search_window_m=(5.0, 18.0),
                turn_in_search_window_m=(5.0, 18.0),
                throttle_pickup_window_m=(5.0, 20.0),
                exit_distance_m=17.0,
            ),
        ),
    )


def _install_pair(monkeypatch, attempts: tuple[StoredAttemptTrace, StoredAttemptTrace]) -> None:
    estimates = tuple(_estimate(attempt) for attempt in attempts)
    by_key = {attempt.attempt_key: attempt for attempt in attempts}
    monkeypatch.setattr(
        paired_service,
        "load_attempt_trace_resource_estimates",
        lambda *_args, **_kwargs: estimates,
    )
    monkeypatch.setattr(
        paired_service,
        "load_attempt_trace",
        lambda _database, key, **_kwargs: by_key[key],
    )
    monkeypatch.setattr(
        paired_service,
        "get_processing_run_summary",
        lambda *_args: {
            "capture": {
                "complete": False,
                "footer_status": "incomplete",
                "recording_counters": {"queue_dropped": 1, "unknown_counter": None},
            },
            "processing": {
                "replay_counters": {"import_late_packets_ignored": 2},
            },
        },
    )


def test_paired_report_is_diagnostic_and_uses_bracketed_control_differences(monkeypatch) -> None:
    target = _attempt("target", reference=False)
    reference = _attempt("reference", reference=True)
    _install_pair(monkeypatch, (target, reference))

    report = compare_attempt_regions(
        "test.sqlite3",
        target.attempt_key,
        reference.attempt_key,
        _model(),
        model_metadata={
            "origin": "local_draft",
            "content_sha256": "c" * 64,
            "source_filename": "melbourne.json",
        },
    )

    assert report is not None
    assert report["artifact_kind"] == "paired_distance_region_observations"
    assert report["diagnostic_only"] is True
    assert report["coaching_eligible"] is False
    assert report["ranking_eligible"] is False
    assert report["model"]["origin"] == "local_draft"
    assert report["model"]["content_sha256"] == "c" * 64
    assert report["track"]["layout_identity_status"] == "caller_declared"
    assert report["attempts"]["target"]["capture"]["complete"] is False
    assert report["attempts"]["reference"]["replay_counters"]["import_late_packets_ignored"] == 2
    region = report["regions"][0]
    assert region["diagnostic_only"] is True
    assert region["coaching_eligible"] is False
    assert region["ranking_eligible"] is False
    assert "differences" not in region
    assert region["target"]["minimum_speed"]["source_anchor"]["frame_identifier"] == 11
    assert region["supported_differences"]["connected_interval_time"]["status"] == "supported"
    assert region["supported_differences"]["minimum_speed"]["value"] == pytest.approx(7.2)
    brake = region["supported_differences"]["brake_10_percent_onset"]
    assert brake["status"] == "supported"
    assert brake["target_minus_reference_start_bracket_m"][0] <= brake[
        "target_minus_reference_start_bracket_m"
    ][1]
    throttle = region["supported_differences"]["throttle_50_percent_onset"]
    assert throttle["status"] == "supported"
    assert region["supported_differences"]["exit_speed"]["status"] == "supported"
    assert any(item["code"] == "capture_incomplete" for item in report["warnings"]["target"])
    assert report["resource_policy"]["source"]["grid_points"]["estimated"] == 42


def test_validated_model_and_eligible_sources_keep_delta_diagnostic(monkeypatch) -> None:
    target = replace(_attempt("target", reference=False), reference_eligible=True)
    reference = replace(_attempt("reference", reference=True), reference_eligible=True)
    _install_pair(monkeypatch, (target, reference))

    report = compare_attempt_regions(
        "test.sqlite3",
        "target",
        "reference",
        replace(_model(), validation_status="validated"),
    )

    assert report is not None
    region = report["regions"][0]
    assert region["diagnostic_only"] is True
    assert region["delta_change"]["status"] == "diagnostic_region_delta_change"
    assert region["coaching_eligible"] is False
    assert region["ranking_eligible"] is False


def test_pair_trace_reads_use_each_preflight_reservation(monkeypatch) -> None:
    target = _attempt("target", reference=False)
    reference = _attempt("reference", reference=True)
    _install_pair(monkeypatch, (target, reference))
    reads: list[tuple[str, dict[str, object]]] = []

    def load(_database, key, **kwargs):
        reads.append((key, kwargs))
        return target if key == "target" else reference

    monkeypatch.setattr(paired_service, "load_attempt_trace", load)

    compare_attempt_regions("test.sqlite3", "target", "reference", _model())

    assert [key for key, _ in reads] == ["target", "reference"]
    for key, kwargs in reads:
        expected = target if key == "target" else reference
        estimate = _estimate(expected)
        assert kwargs["max_trace_bytes"] == estimate.trace_size_bytes
        assert kwargs["max_trace_rows"] == estimate.trace_row_count
        assert kwargs["max_context_segments"] == estimate.context_segment_count
        assert kwargs["max_context_bytes"] == estimate.context_bytes


@pytest.mark.parametrize(
    ("context", "reason"),
    [
        (_context(session_type="race", game_mode="race", rule_set="race"), "unsupported_mode"),
        (_context(session_type="unknown", game_mode="unknown", rule_set="unknown"), "unknown_mode"),
    ],
)
def test_race_and_unknown_pair_modes_abstain_before_trace_decode(
    monkeypatch, context: dict[str, object], reason: str
) -> None:
    target = _attempt("target", reference=False, context=context)
    reference = _attempt("reference", reference=True, context=context)
    _install_pair(monkeypatch, (target, reference))

    def fail_if_loaded(*_args, **_kwargs):
        pytest.fail("unsupported mode must abstain before trace decode")

    monkeypatch.setattr(paired_service, "load_attempt_trace", fail_if_loaded)
    with pytest.raises(PairedRegionReportUnavailable, match=reason):
        compare_attempt_regions(
            "test.sqlite3", "target", "reference", _model()
        )


def test_pair_source_budget_is_reserved_before_trace_decode(monkeypatch) -> None:
    target = _attempt("target", reference=False)
    reference = _attempt("reference", reference=True)
    estimates = (_estimate(target, bytes_count=40 * 1024 * 1024), _estimate(reference, bytes_count=25 * 1024 * 1024))
    monkeypatch.setattr(
        paired_service,
        "load_attempt_trace_resource_estimates",
        lambda *_args, **_kwargs: estimates,
    )
    monkeypatch.setattr(
        paired_service,
        "load_attempt_trace",
        lambda *_args, **_kwargs: pytest.fail("aggregate source cap must reject before trace read"),
    )

    with pytest.raises(PairedRegionReportUnavailable, match="source_bytes_limit_exceeded"):
        compare_attempt_regions("test.sqlite3", "target", "reference", _model())


def test_incompatible_pair_scope_abstains_before_trace_decode(monkeypatch) -> None:
    target = _attempt("target", reference=False)
    reference = replace(_attempt("reference", reference=True), car_index=1)
    _install_pair(monkeypatch, (target, reference))
    monkeypatch.setattr(
        paired_service,
        "load_attempt_trace",
        lambda *_args, **_kwargs: pytest.fail("pair scope must be checked before trace read"),
    )

    with pytest.raises(PairedRegionReportUnavailable, match="must_share_player"):
        compare_attempt_regions("test.sqlite3", "target", "reference", _model())


def test_target_context_is_rechecked_before_reference_trace_is_loaded(monkeypatch) -> None:
    target = _attempt("target", reference=False)
    reference = _attempt("reference", reference=True)
    _install_pair(monkeypatch, (target, reference))
    changed_target = replace(
        target,
        context_segments=((1, _context(weather_id=1)),),
    )
    loaded: list[str] = []

    def load(_database, key, **_kwargs):
        loaded.append(key)
        return changed_target if key == "target" else reference

    monkeypatch.setattr(paired_service, "load_attempt_trace", load)

    with pytest.raises(PairedRegionReportUnavailable, match="context_changed_during_read"):
        compare_attempt_regions("test.sqlite3", "target", "reference", _model())

    assert loaded == ["target"]


def test_pair_grid_reservation_counts_both_resampled_traces_before_trace_decode(monkeypatch) -> None:
    context = _context(track_length_m=50_000.0)
    target = _attempt("target", reference=False, context=context)
    reference = _attempt("reference", reference=True, context=context)
    _install_pair(monkeypatch, (target, reference))
    monkeypatch.setattr(
        paired_service,
        "load_attempt_trace",
        lambda *_args, **_kwargs: pytest.fail("oversized pair grid must reject before trace read"),
    )

    with pytest.raises(PairedRegionReportUnavailable, match="grid_limit_exceeded"):
        compare_attempt_regions(
            "test.sqlite3",
            "target",
            "reference",
            _model(track_length_m=50_000.0),
            config=ResamplingConfig(grid_step_m=1.0),
        )


def test_paired_region_analysis_work_formula_is_request_wide() -> None:
    assert paired_service._paired_region_analysis_work(
        2, 10, 12, 20, 20, 20, 3
    ) == 2 * (40 * 22 + 16 * (20 + 20 + 20 + 3) * 5)


def test_practice_qualifying_pair_is_supported_with_uncontrolled_conditions_warning(monkeypatch) -> None:
    practice = _context(
        session_type="practice_1",
        game_mode="driver_career_25",
        rule_set="practice_qualifying",
    )
    target = _attempt("target", reference=False, context=practice)
    reference = _attempt("reference", reference=True, context=practice)
    _install_pair(monkeypatch, (target, reference))

    report = compare_attempt_regions(
        "test.sqlite3",
        "target",
        "reference",
        _model(),
        policy="practice_qualifying",
    )

    assert report is not None
    assert report["comparison_policy"] == "practice_qualifying"
    assert "traffic_uncontrolled" in report["policy_limitations"]
    assert any(
        warning["code"] == "practice_qualifying_conditions_uncontrolled"
        for warning in report["warnings"]["target"]
    )
