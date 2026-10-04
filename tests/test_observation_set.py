from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from f1_engineer.analysis import observation_set as observation_module
from f1_engineer.analysis.comparison_window import DistanceWindow
from f1_engineer.analysis.resampling import ResamplingConfig
from f1_engineer.storage.query import (
    AttemptTraceResourceEstimate,
    StoredAttemptTrace,
)


_CONTEXT = {
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


def _resource(attempt_number: int, attempt_key: str) -> AttemptTraceResourceEstimate:
    return AttemptTraceResourceEstimate(
        attempt_key=attempt_key,
        run_id="run-1",
        session_uid="session-1",
        car_index=0,
        attempt_number=attempt_number,
        disposition="completed",
        lap_time_ms=80_000,
        game_valid=True,
        start_observed=True,
        pit_encountered=False,
        superseded=False,
        lifecycle_assessed=True,
        exclusion_reasons=(),
        trace_ready=True,
        trace_row_count=3,
        trace_sha256="a" * 64,
        trace_schema_version=3,
        trace_size_bytes=512,
        context_segment_count=1,
        context_bytes=0,
        context_segments=((0, _CONTEXT),),
    )


def _attempt(
    resource: AttemptTraceResourceEstimate,
    *,
    speed_offset: float = 0.0,
    brake_value: float = 0.2,
) -> StoredAttemptTrace:
    samples = tuple(
        {
            "frame_identifier": frame,
            "session_time_s": session_time,
            "lap_distance_m": distance,
            "current_lap_time_ms": lap_time,
            "speed_mps": speed + speed_offset,
            "throttle": 0.5,
            "brake": brake_value,
            "steering": 0.1,
            "gear": 4,
            "drs_active": False,
        }
        for frame, session_time, distance, lap_time, speed in (
            (1, 0.0, 0.0, 0, 20.0),
            (2, 0.1, 10.0, 100, 30.0),
            (3, 0.2, 20.0, 200, 40.0),
        )
    )
    return StoredAttemptTrace(
        attempt_key=resource.attempt_key,
        run_id=resource.run_id,
        session_uid=resource.session_uid,
        car_index=resource.car_index,
        disposition=resource.disposition,
        lap_time_ms=resource.lap_time_ms,
        game_valid=resource.game_valid,
        reference_eligible=True,
        exclusion_reasons=resource.exclusion_reasons,
        trace_sha256=resource.trace_sha256 or "",
        trace_schema_version=resource.trace_schema_version or 0,
        quality={},
        context_segments=resource.context_segments,
        samples=samples,
        attempt_number=resource.attempt_number,
        start_observed=resource.start_observed,
        pit_encountered=resource.pit_encountered,
        superseded=resource.superseded,
        lifecycle_assessed=resource.lifecycle_assessed,
        source_sample_count=resource.trace_row_count,
    )


def _onset_resources(
    *,
    brake_onsets: tuple[int, ...],
    throttle_onsets: tuple[int, ...],
    invalid_second: bool = False,
    superseded_second: bool = False,
) -> tuple[tuple[AttemptTraceResourceEstimate, ...], dict[str, StoredAttemptTrace]]:
    context = {**_CONTEXT, "track_length_m": 30}
    resources = tuple(
        replace(
            _resource(index + 1, f"attempt-{index + 1}"),
            game_valid=False if index == 1 and invalid_second else True,
            superseded=True if index == 1 and superseded_second else False,
            trace_row_count=7,
            trace_size_bytes=2048,
            context_segments=((0, context),),
        )
        for index in range(len(brake_onsets))
    )
    attempts: dict[str, StoredAttemptTrace] = {}
    for index, resource in enumerate(resources):
        samples = tuple(
            {
                "frame_identifier": frame,
                "session_time_s": frame * 0.1,
                "lap_distance_m": distance,
                "current_lap_time_ms": frame * 100,
                "speed_mps": 20.0 + frame,
                "throttle": 0.6 if frame >= throttle_onsets[index] else 0.0,
                "brake": 0.2 if frame >= brake_onsets[index] else 0.0,
                "steering": 0.1,
                "gear": 4,
                "drs_active": False,
            }
            for frame, distance in enumerate(range(0, 31, 5))
        )
        attempts[resource.attempt_key] = replace(
            _attempt(resource), samples=samples
        )
    return resources, attempts


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attempt_key", "changed"),
        ("run_id", "changed"),
        ("session_uid", "changed"),
        ("car_index", 1),
        ("attempt_number", 9),
        ("disposition", "partial"),
        ("lap_time_ms", 81_000),
        ("game_valid", False),
        ("start_observed", False),
        ("pit_encountered", True),
        ("superseded", True),
        ("lifecycle_assessed", False),
        ("exclusion_reasons", ("changed",)),
        ("trace_sha256", "b" * 64),
        ("trace_schema_version", 2),
        ("source_sample_count", 4),
    ],
)
def test_preflight_snapshot_match_rejects_identity_and_eligibility_changes(
    field: str, value: object
) -> None:
    resource = _resource(1, "attempt-1")

    assert not observation_module._attempt_matches_preflight_snapshot(
        replace(_attempt(resource), **{field: value}), resource
    )


def test_observation_set_marks_a_changed_loaded_snapshot_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resources = (_resource(1, "attempt-1"), _resource(2, "attempt-2"))
    attempts = {item.attempt_key: _attempt(item) for item in resources}
    attempts["attempt-1"] = replace(
        attempts["attempt-1"],
        trace_sha256="b" * 64,
        lifecycle_assessed=False,
    )
    monkeypatch.setattr(
        observation_module,
        "load_attempt_trace_resource_estimates",
        lambda *_args, **_kwargs: resources,
    )
    monkeypatch.setattr(
        observation_module,
        "load_attempt_trace",
        lambda _database, key, **_kwargs: attempts[key],
    )
    monkeypatch.setattr(
        observation_module,
        "_load_capture_evidence",
        lambda *_args: {
            "complete": True,
            "footer_status": "complete",
            "recording_counters": {
                "queue_dropped": 0,
                "unpersisted_on_shutdown": 0,
                "socket_errors": 0,
            },
            "replay_counters": {
                "import_late_packets_ignored": 0,
                "import_frame_overflow_packets_dropped": 0,
            },
        },
    )

    result = observation_module.build_observation_set(
        tmp_path / "unused.sqlite3",
        ["attempt-1", "attempt-2"],
        DistanceWindow(0.0, 20.0),
        config=ResamplingConfig(grid_step_m=10.0),
    )

    assert result["attempts"][0]["analysis_status"] == "unavailable"
    assert result["attempts"][0]["analysis_reason"] == (
        "attempt_metadata_changed_during_read"
    )
    assert result["attempts"][0]["trace_sha256"] == "a" * 64


def test_observation_set_reports_supported_scalar_ranges_and_keeps_invalid_laps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = _resource(1, "attempt-1")
    second = replace(_resource(2, "attempt-2"), game_valid=False)
    resources = (first, second)
    attempts = {
        first.attempt_key: _attempt(first),
        second.attempt_key: _attempt(second, speed_offset=10.0, brake_value=0.6),
    }
    _patch_service_inputs(monkeypatch, resources, attempts)

    result = observation_module.build_observation_set(
        tmp_path / "unused.sqlite3",
        [item.attempt_key for item in resources],
        DistanceWindow(0.0, 20.0),
        config=ResamplingConfig(grid_step_m=10.0),
    )

    assert result["aggregates"]["minimum_speed"]["status"] == "supported"
    assert result["aggregates"]["minimum_speed"]["contributor_count"] == 2
    assert result["aggregates"]["minimum_speed"]["minimum"] == pytest.approx(72.0)
    assert result["aggregates"]["minimum_speed"]["maximum"] == pytest.approx(108.0)
    assert result["aggregates"]["peak_brake"]["minimum"] == pytest.approx(20.0)
    assert result["aggregates"]["peak_brake"]["maximum"] == pytest.approx(60.0)
    assert result["attempts"][1]["game_valid"] is False
    assert any(
        warning["code"] == "game_invalid"
        for warning in result["attempts"][1]["warnings"]
    )


@pytest.mark.parametrize(
    ("onsets", "expected_minimum", "expected_maximum"),
    [
        ((1, 1), 0.0, 5.0),
        ((1, 2), 0.0, 10.0),
        ((1, 3), 5.0, 15.0),
    ],
)
def test_onset_spread_preserves_identical_overlapping_and_disjoint_brackets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    onsets: tuple[int, int],
    expected_minimum: float,
    expected_maximum: float,
) -> None:
    resources, attempts = _onset_resources(
        brake_onsets=onsets,
        throttle_onsets=onsets,
        invalid_second=True,
    )
    _patch_service_inputs(monkeypatch, resources, attempts)
    monkeypatch.setattr(
        observation_module,
        "_load_capture_evidence",
        lambda *_args: {
            "complete": False,
            "footer_status": "incomplete",
            "recording_counters": {
                "queue_dropped": 0,
                "unpersisted_on_shutdown": 0,
                "socket_errors": 0,
            },
            "replay_counters": {
                "import_late_packets_ignored": 0,
                "import_frame_overflow_packets_dropped": 0,
            },
        },
    )

    result = observation_module.build_observation_set(
        tmp_path / "unused.sqlite3",
        [resource.attempt_key for resource in resources],
        DistanceWindow(0.0, 30.0),
        config=ResamplingConfig(grid_step_m=10.0),
    )

    spread = result["onset_repeatability"]
    assert spread["analysis_version"] == "selected-window-onset-spread-v1"
    assert spread["consistency_claim"] is False
    for key in ("brake_10_percent", "throttle_50_percent"):
        metric = spread["metrics"][key]
        assert metric["status"] == "supported"
        assert metric["contributor_count"] == 2
        assert metric["minimum_possible_spread_m"] == pytest.approx(expected_minimum)
        assert metric["maximum_possible_spread_m"] == pytest.approx(expected_maximum)
        assert metric["right_censored_contributor_count"] == 2
        assert [item["right_censored"] for item in metric["contributors"]] == [True, True]
        assert [item["attempt_number"] for item in metric["contributors"]] == [1, 2]
    assert any(
        warning["code"] == "game_invalid"
        for warning in result["attempts"][1]["warnings"]
    )
    assert result["attempts"][1]["onset_repeatability"]["brake_10_percent"][
        "status"
    ] == "contributes"
    assert result["coaching_eligible"] is False
    assert any(warning["code"] == "capture_incomplete" for warning in result["warnings"])
    assert any(
        warning["code"] == "capture_incomplete"
        for warning in result["attempts"][0]["warnings"]
    )


def test_onset_spread_requires_two_distinct_eligible_attempts_and_excludes_left_censoring(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resources, attempts = _onset_resources(
        brake_onsets=(1, 2),
        throttle_onsets=(1, 2),
        superseded_second=True,
    )
    _patch_service_inputs(monkeypatch, resources, attempts)
    result = observation_module.build_observation_set(
        tmp_path / "unused.sqlite3",
        [resource.attempt_key for resource in resources],
        DistanceWindow(0.0, 30.0),
        config=ResamplingConfig(grid_step_m=10.0),
    )
    brake = result["onset_repeatability"]["metrics"]["brake_10_percent"]
    assert brake["status"] == "insufficient_contributors"
    assert brake["contributor_count"] == 1
    assert brake["minimum_possible_spread_m"] is None
    assert brake["maximum_possible_spread_m"] is None
    assert brake["excluded_attempts"][0]["attempt_key"] == "attempt-2"
    assert "superseded_by_lifecycle_evidence" in brake["excluded_attempts"][0]["reasons"]

    left_censored_resources, left_censored_attempts = _onset_resources(
        brake_onsets=(0, 1),
        throttle_onsets=(0, 1),
    )
    _patch_service_inputs(monkeypatch, left_censored_resources, left_censored_attempts)
    left_censored = observation_module.build_observation_set(
        tmp_path / "unused.sqlite3",
        [resource.attempt_key for resource in left_censored_resources],
        DistanceWindow(0.0, 30.0),
        config=ResamplingConfig(grid_step_m=10.0),
    )
    brake = left_censored["onset_repeatability"]["metrics"]["brake_10_percent"]
    assert brake["contributor_count"] == 1
    assert any(
        item["reason"] == "left_censored_onset"
        for item in brake["exclusion_counts"]
    )
    assert left_censored["attempts"][0]["onset_repeatability"]["brake_10_percent"][
        "status"
    ] == "unavailable"


def test_onset_spread_maximum_uses_distinct_attempt_pairs() -> None:
    candidates = [
        {
            "attempt_key": "wide",
            "attempt_number": 1,
            "trace_sha256": "a" * 64,
            "bracket_m": [0.0, 20.0],
            "right_censored": False,
        },
        {
            "attempt_key": "narrow",
            "attempt_number": 2,
            "trace_sha256": "b" * 64,
            "bracket_m": [9.0, 10.0],
            "right_censored": False,
        },
    ]

    result = observation_module._aggregate_onset_spread(
        "brake_10_percent", 0.1, candidates, Counter(), []
    )

    assert result["minimum_possible_spread_m"] == 0.0
    assert result["maximum_possible_spread_m"] == 11.0


def test_onset_spread_bounds_excluded_rows_and_omitted_reason_categories() -> None:
    attempts = [
        {
            "attempt_key": f"attempt-{index}",
            "attempt_number": index,
            "onset_repeatability": {
                "brake_10_percent": {
                    "status": "excluded",
                    "reasons": [f"reason-{index}", f"reason-{index + 1}"],
                }
            },
        }
        for index in range(1, 7)
    ]
    exclusions = Counter({f"reason-{index}": index for index in range(1, 11)})

    result = observation_module._aggregate_onset_spread(
        "brake_10_percent", 0.1, [], exclusions, attempts
    )

    assert result["excluded_attempt_count"] == 6
    assert len(result["excluded_attempts"]) == 6
    assert len(result["exclusion_counts"]) == 8
    assert result["exclusion_reason_omitted_count"] == 2


def test_onset_spread_rejects_multiple_malformed_and_censored_events() -> None:
    supported_event = {
        "channel": "brake",
        "threshold": 0.1,
        "start_distance_m": 5.0,
        "start_distance_bracket_m": [0.0, 5.0],
        "start_session_time_s": 0.1,
        "left_censored": False,
        "right_censored": True,
    }
    summary = {
        "status": "detected",
        "threshold": 0.1,
        "events": [supported_event],
        "event_count": 1,
        "events_truncated": False,
        "left_censored_event_count": 0,
        "right_censored_event_count": 1,
        "rejected_short_event_count": 0,
        "unsupported_break_count": 0,
    }
    observations = {
        "coverage": {"brake": 1.0},
        "threshold_events": {"brake_10_percent": summary},
    }
    bracket, reason = observation_module._supported_onset_bracket(
        observations,
        event_key="brake_10_percent",
        channel="brake",
        threshold=0.1,
        start_m=0.0,
        end_m=20.0,
        track_length_m=20.0,
    )
    assert reason is None
    assert bracket is not None and bracket["right_censored"] is True

    multiple = {**summary, "event_count": 2, "events": [supported_event, supported_event]}
    invalid_bracket = {
        **summary,
        "events": [{**supported_event, "start_distance_bracket_m": [5.0, 4.0]}],
    }
    left_censored = {
        **summary,
        "status": "left_censored",
        "left_censored_event_count": 1,
        "events": [{**supported_event, "left_censored": True}],
    }
    invalid_count = {**summary, "event_count": True}
    censor_count_mismatch = {**summary, "right_censored_event_count": 0}
    for candidate, expected in (
        (multiple, "threshold_event_not_unique"),
        (invalid_bracket, "threshold_onset_bracket_invalid"),
        (left_censored, "left_censored_onset"),
        (invalid_count, "threshold_event_counts_invalid"),
        (censor_count_mismatch, "threshold_event_censoring_counts_mismatch"),
    ):
        result, reason = observation_module._supported_onset_bracket(
            {"coverage": {"brake": 1.0}, "threshold_events": {"brake_10_percent": candidate}},
            event_key="brake_10_percent",
            channel="brake",
            threshold=0.1,
            start_m=0.0,
            end_m=20.0,
            track_length_m=20.0,
        )
        assert result is None
        assert reason == expected


@pytest.mark.parametrize(
    ("changes", "expected_reason"),
    [
        ({"disposition": "partial"}, "attempt_not_completed"),
        ({"lifecycle_assessed": False}, "lifecycle_evidence_unassessed"),
        ({"superseded": True}, "superseded_by_lifecycle_evidence"),
    ],
)
def test_partial_and_unassessed_attempts_remain_visible_but_do_not_contribute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    changes: dict[str, object],
    expected_reason: str,
) -> None:
    first = _resource(1, "attempt-1")
    second = replace(_resource(2, "attempt-2"), **changes)
    resources = (first, second)
    attempts = {item.attempt_key: _attempt(item) for item in resources}
    _patch_service_inputs(monkeypatch, resources, attempts)

    result = observation_module.build_observation_set(
        tmp_path / "unused.sqlite3",
        [item.attempt_key for item in resources],
        DistanceWindow(0.0, 20.0),
        config=ResamplingConfig(grid_step_m=10.0),
    )

    speed = result["aggregates"]["minimum_speed"]
    assert speed["status"] == "insufficient_contributors"
    assert speed["contributor_count"] == 1
    assert result["attempts"][1]["analysis_status"] == "available"
    assert expected_reason in result["attempts"][1]["aggregate_exclusion_reasons"]


@pytest.mark.parametrize(
    ("change", "expected_reason"),
    [
        ({"run_id": "run-2"}, "observation_set_attempts_must_share_processing_run"),
        ({"session_uid": "session-2"}, "observation_set_attempts_must_share_session"),
        ({"car_index": 1}, "observation_set_attempts_must_share_player"),
        (
            {"context_segments": ((0, {**_CONTEXT, "track_id": 1}),)},
            "observation_set_attempts_have_incompatible_context",
        ),
    ],
)
def test_observation_set_rejects_mixed_scope_or_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    change: dict[str, object],
    expected_reason: str,
) -> None:
    resources = (_resource(1, "attempt-1"), replace(_resource(2, "attempt-2"), **change))
    monkeypatch.setattr(
        observation_module,
        "load_attempt_trace_resource_estimates",
        lambda *_args, **_kwargs: resources,
    )

    with pytest.raises(ValueError, match=expected_reason):
        observation_module.build_observation_set(
            tmp_path / "unused.sqlite3",
            [item.attempt_key for item in resources],
            DistanceWindow(0.0, 20.0),
        )


def test_observation_set_rejects_race_mode_and_missing_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resources = (_resource(1, "attempt-1"), _resource(2, "attempt-2"))
    race_context = {**_CONTEXT, "session_type": "race", "game_mode": "career", "rule_set": "race"}
    race_resources = (
        replace(resources[0], context_segments=((0, race_context),)),
        replace(resources[1], context_segments=((0, race_context),)),
    )
    monkeypatch.setattr(
        observation_module,
        "load_attempt_trace_resource_estimates",
        lambda *_args, **_kwargs: race_resources,
    )
    with pytest.raises(ValueError, match="observation_set_unsupported_mode"):
        observation_module.build_observation_set(
            tmp_path / "unused.sqlite3",
            [item.attempt_key for item in race_resources],
            DistanceWindow(0.0, 20.0),
        )

    monkeypatch.setattr(
        observation_module,
        "load_attempt_trace_resource_estimates",
        lambda *_args, **_kwargs: resources[:1],
    )
    with pytest.raises(ValueError, match="observation_set_attempt_not_found_or_unavailable"):
        observation_module.build_observation_set(
            tmp_path / "unused.sqlite3",
            [item.attempt_key for item in resources],
            DistanceWindow(0.0, 20.0),
        )


@pytest.mark.parametrize(
    ("changes", "expected_reason"),
    [
        ({"lap_time_ms": 0}, "practice_qualifying_positive_lap_time_unavailable"),
        ({"start_observed": False}, "practice_qualifying_start_unobserved"),
        ({"pit_encountered": True}, "practice_qualifying_pit_encountered"),
    ],
)
def test_practice_qualifying_attempts_need_positive_time_start_and_no_pit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    changes: dict[str, object],
    expected_reason: str,
) -> None:
    context = {
        **_CONTEXT,
        "session_type": "practice_1",
        "game_mode": "driver_career_25",
        "rule_set": "practice_qualifying",
    }
    first = replace(_resource(1, "attempt-1"), context_segments=((0, context),))
    second = replace(
        _resource(2, "attempt-2"),
        context_segments=((0, context),),
        **changes,
    )
    resources = (first, second)
    _patch_service_inputs(
        monkeypatch,
        resources,
        {item.attempt_key: _attempt(item) for item in resources},
    )

    result = observation_module.build_observation_set(
        tmp_path / "unused.sqlite3",
        [item.attempt_key for item in resources],
        DistanceWindow(0.0, 20.0),
        policy="practice_qualifying",
        config=ResamplingConfig(grid_step_m=10.0),
    )

    assert result["aggregates"]["minimum_speed"]["contributor_count"] == 1
    assert expected_reason in result["attempts"][1]["aggregate_exclusion_reasons"]
    assert result["attempts"][1]["analysis_status"] == "available"
    assert result["attempts"][1]["warnings"][0]["code"] == (
        "practice_qualifying_conditions_uncontrolled"
    )


@pytest.mark.parametrize(
    ("keys", "expected_reason"),
    [
        (["attempt-1"], "observation_set_attempt_count_out_of_range"),
        (["attempt-1"] * 2, "observation_set_attempt_keys_must_be_unique"),
        ([f"attempt-{index}" for index in range(9)], "observation_set_attempt_count_out_of_range"),
    ],
)
def test_observation_set_rejects_invalid_selection_bounds(
    tmp_path: Path,
    keys: list[str],
    expected_reason: str,
) -> None:
    with pytest.raises(ValueError, match=expected_reason):
        observation_module.build_observation_set(
            tmp_path / "unused.sqlite3",
            keys,
            DistanceWindow(0.0, 20.0),
        )


def test_observation_set_rejects_cumulative_trace_budget_before_loading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resources = (
        replace(_resource(1, "attempt-1"), trace_size_bytes=40 * 1024 * 1024),
        replace(_resource(2, "attempt-2"), trace_size_bytes=30 * 1024 * 1024),
    )
    monkeypatch.setattr(
        observation_module,
        "load_attempt_trace_resource_estimates",
        lambda *_args, **_kwargs: resources,
    )
    monkeypatch.setattr(
        observation_module,
        "load_attempt_trace",
        lambda *_args, **_kwargs: pytest.fail("trace read exceeded request-wide budget"),
    )

    with pytest.raises(ValueError, match="observation_set_source_bytes_limit_exceeded"):
        observation_module.build_observation_set(
            tmp_path / "unused.sqlite3",
            [item.attempt_key for item in resources],
            DistanceWindow(0.0, 20.0),
        )


def _patch_service_inputs(
    monkeypatch: pytest.MonkeyPatch,
    resources: tuple[AttemptTraceResourceEstimate, ...],
    attempts: dict[str, StoredAttemptTrace],
) -> None:
    monkeypatch.setattr(
        observation_module,
        "load_attempt_trace_resource_estimates",
        lambda *_args, **_kwargs: resources,
    )
    monkeypatch.setattr(
        observation_module,
        "load_attempt_trace",
        lambda _database, key, **_kwargs: attempts[key],
    )
    monkeypatch.setattr(
        observation_module,
        "_load_capture_evidence",
        lambda *_args: {
            "complete": True,
            "footer_status": "complete",
            "recording_counters": {
                "queue_dropped": 0,
                "unpersisted_on_shutdown": 0,
                "socket_errors": 0,
            },
            "replay_counters": {
                "import_late_packets_ignored": 0,
                "import_frame_overflow_packets_dropped": 0,
            },
        },
    )
