from __future__ import annotations

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
