from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

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
        brake = 0.3 if (11 <= distance <= 16 if reference else 8 <= distance <= 14) else 0.0
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


def _brake_release_observation(
    bracket: tuple[float, float],
    *,
    left_censored: bool = False,
    right_censored: bool = False,
) -> dict[str, object]:
    event = {
        "channel": "brake",
        "threshold": 0.1,
        "start_distance_m": 5.0,
        "start_distance_bracket_m": None if left_censored else [4.0, 5.0],
        "end_distance_m": bracket[0],
        "end_distance_bracket_m": list(bracket),
        "start_session_time_s": 1.0,
        "end_session_time_s": 2.0,
        "left_censored": left_censored,
        "right_censored": right_censored,
    }
    return {
        "event_channel_coverage": {"brake": 1.0},
        "braking": {
            "status": "left_censored" if left_censored else "detected",
            "event_count": 1,
            "events_truncated": False,
            "rejected_short_event_count": 0,
            "unsupported_break_count": 0,
            "events": [event],
        },
    }
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
    release = region["supported_differences"]["brake_10_percent_release"]
    assert release["status"] == "supported"
    assert release["analysis_version"] == "brake-threshold-release-v1"
    assert release["target_minus_reference_end_bracket_m"] == pytest.approx([-3.0, -1.0])
    assert release["right_censored"] == {"target": False, "reference": False}
    throttle = region["supported_differences"]["throttle_50_percent_onset"]
    assert throttle["status"] == "supported"
    assert region["supported_differences"]["exit_speed"]["status"] == "supported"
    assert region["debrief"]["analysis_version"] == "diagnostic-region-debrief-v1"
    assert region["debrief"]["diagnostic_only"] is True
    assert region["debrief"]["coaching_eligible"] is False
    assert region["debrief"]["ranking_eligible"] is False
    assert [fact["kind"] for fact in region["debrief"]["facts"]] == [
        "connected_interval_time",
        "minimum_speed",
        "brake_10_percent_onset",
        "throttle_50_percent_onset",
        "exit_speed",
    ]
    assert any(
        item["code"] == "lifecycle_unassessed"
        for item in report["warnings"]["target"]
    )
    assert any(item["code"] == "capture_incomplete" for item in report["warnings"]["target"])
    assert report["resource_policy"]["source"]["grid_points"]["estimated"] == 42


def test_exact_ten_percent_sample_is_active_for_brake_event_and_release(monkeypatch) -> None:
    target = _attempt("target", reference=False)
    reference = _attempt("reference", reference=True)
    target_samples = tuple(
        {
            **sample,
            "brake": 0.1 if sample["lap_distance_m"] == 8.0 else sample["brake"],
        }
        for sample in target.samples
    )
    target = replace(target, samples=target_samples)
    _install_pair(monkeypatch, (target, reference))

    report = compare_attempt_regions(
        "test.sqlite3",
        target.attempt_key,
        reference.attempt_key,
        _model(),
    )

    assert report is not None
    region = report["regions"][0]
    assert region["target"]["braking"]["events"][0][
        "start_distance_bracket_m"
    ] == [7.0, 8.0]
    assert region["supported_differences"]["brake_10_percent_release"][
        "status"
    ] == "supported"


@pytest.mark.parametrize(
    ("target_bracket", "reference_bracket", "expected"),
    [
        ((10.0, 11.0), (5.0, 6.0), (4.0, 6.0)),
        ((5.0, 6.0), (10.0, 11.0), (-6.0, -4.0)),
        ((5.0, 5.0), (5.0, 5.0), (0.0, 0.0)),
        ((4.5, 5.5), (5.0, 6.0), (-1.5, 0.5)),
    ],
)
def test_brake_release_keeps_brackets_and_target_minus_reference_bounds(
    target_bracket: tuple[float, float],
    reference_bracket: tuple[float, float],
    expected: tuple[float, float],
) -> None:
    difference = paired_service._brake_threshold_release_difference(
        _brake_release_observation(target_bracket),
        _brake_release_observation(reference_bracket),
        search_window=(0.0, 20.0),
    )

    assert difference["status"] == "supported"
    assert difference["target_end_bracket_m"] == list(target_bracket)
    assert difference["reference_end_bracket_m"] == list(reference_bracket)
    assert difference["target_minus_reference_end_bracket_m"] == pytest.approx(expected)
    assert difference["unit"] == "m"
    assert difference["direction"] == "target_minus_reference_end_bracket_in_lap_distance"
    assert "duration" not in difference


def test_left_censored_brake_episode_can_have_observed_release() -> None:
    target = _brake_release_observation((10.0, 11.0), left_censored=True)
    reference = _brake_release_observation((8.0, 9.0))

    difference = paired_service._brake_threshold_release_difference(
        target,
        reference,
        search_window=(5.0, 20.0),
    )

    assert difference["status"] == "supported"
    assert difference["left_censored"] == {"target": True, "reference": False}
    assert difference["target_end_bracket_m"] == [10.0, 11.0]


@pytest.mark.parametrize(
    ("change", "search_window", "expected_reason"),
    [
        ("missing_coverage", (0.0, 20.0), "target_brake_coverage_incomplete"),
        ("partial_coverage", (0.0, 20.0), "target_brake_coverage_incomplete"),
        ("missing_counter", (0.0, 20.0), "target_threshold_event_counters_unavailable"),
        ("boolean_counter", (0.0, 20.0), "target_threshold_event_counters_unavailable"),
        ("truncated", (0.0, 20.0), "target_threshold_event_examples_truncated"),
        ("ambiguous", (0.0, 20.0), "target_threshold_event_ambiguous"),
        ("short_episode", (0.0, 20.0), "target_threshold_event_ambiguous"),
        ("unsupported_break", (0.0, 20.0), "target_threshold_event_ambiguous"),
        ("right_censored", (0.0, 20.0), "target_brake_release_right_censored"),
        ("malformed_bracket", (0.0, 20.0), "target_brake_release_bracket_unavailable"),
        ("exclusive_end", (0.0, 20.0), "target_brake_release_bracket_unavailable"),
        ("outside_event", (0.0, 20.0), "target_brake_threshold_event_malformed"),
        ("time_rewind", (0.0, 20.0), "target_brake_threshold_event_malformed"),
    ],
)
def test_brake_release_abstains_when_evidence_is_ambiguous_or_unsupported(
    change: str,
    search_window: tuple[float, float],
    expected_reason: str,
) -> None:
    target = _brake_release_observation((10.0, 11.0))
    detection = target["braking"]
    assert isinstance(detection, dict)
    event = detection["events"][0]
    assert isinstance(event, dict)
    if change == "missing_coverage":
        target["event_channel_coverage"] = {}
    elif change == "partial_coverage":
        target["event_channel_coverage"] = {"brake": 0.99}
    elif change == "missing_counter":
        detection.pop("unsupported_break_count")
    elif change == "boolean_counter":
        detection["event_count"] = True
    elif change == "truncated":
        detection["events_truncated"] = True
    elif change == "ambiguous":
        detection["event_count"] = 2
    elif change == "short_episode":
        detection["rejected_short_event_count"] = 1
    elif change == "unsupported_break":
        detection["unsupported_break_count"] = 1
    elif change == "right_censored":
        event["right_censored"] = True
    elif change == "malformed_bracket":
        event["end_distance_bracket_m"] = [11.0, 10.0]
    elif change == "exclusive_end":
        event["end_distance_m"] = 19.0
        event["end_distance_bracket_m"] = [19.0, 20.0]
    elif change == "outside_event":
        event["end_distance_m"] = 20.0
    elif change == "time_rewind":
        event["end_session_time_s"] = 0.5

    difference = paired_service._brake_threshold_release_difference(
        target,
        _brake_release_observation((8.0, 9.0)),
        search_window=search_window,
    )

    assert difference["status"] == "unavailable"
    assert difference["unavailable_reason"] == expected_reason


def test_brake_release_unavailable_is_versioned_and_origin_adjusted_window_is_used() -> None:
    unavailable = paired_service._brake_threshold_release_difference(
        _brake_release_observation((10.0, 11.0)),
        _brake_release_observation((8.0, 9.0)),
        search_window=None,
    )
    assert unavailable["status"] == "unavailable"
    assert unavailable["analysis_version"] == "brake-threshold-release-v1"
    assert unavailable["unavailable_reason"] == "search_window_unconfigured"

    definition = _model().corners[0]
    target = _brake_release_observation((110.0, 111.0))
    reference = _brake_release_observation((112.0, 113.0))
    target["braking"]["events"][0]["start_distance_m"] = 110.0
    reference["braking"]["events"][0]["start_distance_m"] = 112.0
    differences = paired_service._supported_differences(
        definition,
        target,
        reference,
        None,
        100.0,
        105.0,
        120.0,
    )

    assert differences["brake_10_percent_release"]["status"] == "supported"
    assert differences["brake_10_percent_release"][
        "target_minus_reference_end_bracket_m"
    ] == pytest.approx([-3.0, -1.0])


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


def test_pair_resampling_v2_sums_both_estimates_and_accepts_exact_cap(monkeypatch) -> None:
    target = _attempt("target", reference=False)
    reference = _attempt("reference", reference=True)
    _install_pair(monkeypatch, (target, reference))
    config = ResamplingConfig()
    side_work = paired_service.estimate_resampling_work(21, 21, config)
    expected = 2 * side_work

    def fail_if_loaded(*_args, **_kwargs):
        pytest.fail("the combined work preflight must run before trace reads")

    monkeypatch.setattr(paired_service, "load_attempt_trace", fail_if_loaded)
    monkeypatch.setattr(paired_service, "PAIRED_REGION_RESAMPLING_WORK_LIMIT", expected - 1)
    with pytest.raises(
        PairedRegionReportUnavailable,
        match="region_pair_resampling_work_limit_exceeded",
    ):
        compare_attempt_regions("test.sqlite3", "target", "reference", _model())

    monkeypatch.setattr(paired_service, "PAIRED_REGION_RESAMPLING_WORK_LIMIT", expected)
    monkeypatch.setattr(
        paired_service,
        "load_attempt_trace",
        lambda _database, key, **_kwargs: target if key == "target" else reference,
    )
    report = compare_attempt_regions("test.sqlite3", "target", "reference", _model())

    assert report is not None
    assert report["resource_policy"]["resampling"]["estimated_work"] == expected
    assert report["resource_policy"]["resampling"]["version"] == (
        "indexed-hard-block-resampling-preflight-v2"
    )


def test_debrief_region_limit_is_checked_before_region_conversion() -> None:
    model = SimpleNamespace(corners=[None] * 65)

    with pytest.raises(
        PairedRegionReportUnavailable,
        match="diagnostic_region_debrief_region_limit_exceeded",
    ):
        paired_service._paired_regions(model, {"regions": [None] * 65})


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
