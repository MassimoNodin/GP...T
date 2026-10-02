from __future__ import annotations

from argparse import Namespace

from f1_engineer import cli as cli_module
from f1_engineer.analysis import reference_selection as selection_module
from f1_engineer.analysis.reference_selection import (
    CandidateAssessment,
    ReferenceKind,
    ReferenceRequest,
    ReferenceSelection,
    ReferenceSelectionStatus,
)
from f1_engineer.storage.query import (
    StoredAttemptInventoryEntry,
    StoredAttemptTrace,
    StoredReferenceInventory,
)


_CONTEXT = {
    "packet_format": 2025,
    "track_id": 0,
    "track_name": "Melbourne",
    "track_length_m": 5276,
    "session_type": "time_trial",
    "game_mode": "time_trial",
    "rule_set": "time_trial",
    "formula_id": 0,
    "equal_car_performance_id": 0,
    "steering_assist_id": 0,
    "braking_assist_id": 0,
    "gearbox_assist_id": 1,
    "weather_id": 0,
    "weather_name": "clear",
    "track_temperature_c": 32,
    "air_temperature_c": 22,
}
_CLEAN_CAPTURE = {
    "status": "complete",
    "received": 100,
    "recorded": 100,
    "queue_dropped": 0,
    "frame_overflow_packets_dropped": 0,
    "late_packets_ignored": 0,
    "socket_errors": 0,
    "unpersisted_on_shutdown": 0,
}


def _trace(
    attempt_number: int,
    *,
    game_valid: bool | None = True,
    reference_eligible: bool | None = True,
    context: dict[str, object] | None = None,
    schema_version: int = 1,
) -> StoredAttemptTrace:
    context = _CONTEXT if context is None else context
    return StoredAttemptTrace(
        attempt_key=f"run:42:0:{attempt_number}",
        run_id="run",
        session_uid="42",
        car_index=0,
        disposition="completed",
        lap_time_ms=80_000 - attempt_number * 100,
        game_valid=game_valid,
        reference_eligible=bool(reference_eligible),
        exclusion_reasons=("game_marked_invalid",) if game_valid is False else (),
        trace_sha256=str(attempt_number) * 64,
        trace_schema_version=schema_version,
        quality={"sample_count": 10},
        context_segments=((100, context),),
        samples=(),
        attempt_number=attempt_number,
        start_observed=True,
        pit_encountered=False,
    )


def _entry(
    attempt_number: int,
    *,
    lap_time_ms: int | None = None,
    game_valid: bool | None = True,
    reference_eligible: bool = True,
    exclusion_reasons: tuple[str, ...] = (),
    context: dict[str, object] | None = None,
) -> StoredAttemptInventoryEntry:
    return StoredAttemptInventoryEntry(
        attempt_key=f"run:42:0:{attempt_number}",
        run_id="run",
        session_uid="42",
        car_index=0,
        attempt_number=attempt_number,
        disposition="completed",
        lap_time_ms=lap_time_ms or 80_000 - attempt_number * 100,
        game_valid=game_valid,
        reference_eligible=reference_eligible,
        start_observed=True,
        pit_encountered=False,
        sample_count=10,
        exclusion_reasons=exclusion_reasons,
        trace_ready=True,
        trace_row_count=10,
        trace_sha256=str(attempt_number) * 64,
        trace_schema_version=1,
        quality={"sample_count": 10},
        context_segments=((100, _CONTEXT if context is None else context),),
    )


def _inventory(
    target_attempt_number: int,
    attempts: tuple[StoredAttemptInventoryEntry, ...],
    *,
    completion: dict[str, object] | None = None,
    processing_quality: dict[str, object] | None = None,
) -> StoredReferenceInventory:
    return StoredReferenceInventory(
        target_attempt_key=f"run:42:0:{target_attempt_number}",
        run_id="run",
        session_uid="42",
        car_index=0,
        target_attempt_number=target_attempt_number,
        capture_complete=True,
        capture_completion=_CLEAN_CAPTURE if completion is None else completion,
        processing_quality=(
            {
                "import_late_packets_ignored": 0,
                "import_frame_overflow_packets_dropped": 0,
            }
            if processing_quality is None
            else processing_quality
        ),
        attempts=attempts,
    )


def test_session_best_selects_fastest_prior_candidate_and_records_exclusions(monkeypatch) -> None:
    target = _trace(4, game_valid=False, reference_eligible=False)
    attempts = (
        _entry(1, game_valid=False, reference_eligible=False, exclusion_reasons=("game_marked_invalid",)),
        _entry(2, lap_time_ms=79_500),
        _entry(3, lap_time_ms=79_400),
        _entry(4, game_valid=False, reference_eligible=False, exclusion_reasons=("game_marked_invalid",)),
        _entry(5, lap_time_ms=78_000),
    )
    traces = {
        target.attempt_key: target,
        "run:42:0:2": _trace(2, schema_version=2),
        "run:42:0:3": _trace(3, schema_version=1),
    }
    monkeypatch.setattr(selection_module, "load_attempt_trace", lambda _db, key, **_: traces.get(key))
    monkeypatch.setattr(
        selection_module,
        "load_reference_inventory",
        lambda _db, _key: _inventory(4, attempts),
    )

    result = selection_module.select_reference(
        "telemetry.sqlite3", ReferenceRequest(target.attempt_key)
    )

    assert result.status is ReferenceSelectionStatus.SELECTED
    assert result.reference_kind is ReferenceKind.SESSION_BEST
    assert result.selected_reference["attempt_key"] == "run:42:0:3"
    assert result.selected_reference["lap_time_ms"] == 79_400
    assert result.selected_reference["trace_schema_version"] == 1
    assert result.diagnostic_only is True
    assert [item.selected for item in result.candidates] == [False, False, True, False, False]
    assert result.candidates[0].exclusion_reasons[0] == "game_invalid"
    assert result.candidates[3].exclusion_reasons == ("target_attempt",)
    assert result.candidates[4].exclusion_reasons == ("recorded_after_target",)


def test_session_best_ties_use_earlier_attempt_number(monkeypatch) -> None:
    target = _trace(3)
    attempts = (_entry(1, lap_time_ms=79_000), _entry(2, lap_time_ms=79_000), _entry(3))
    traces = {target.attempt_key: target, "run:42:0:1": _trace(1), "run:42:0:2": _trace(2)}
    monkeypatch.setattr(selection_module, "load_attempt_trace", lambda _db, key, **_: traces.get(key))
    monkeypatch.setattr(
        selection_module,
        "load_reference_inventory",
        lambda _db, _key: _inventory(3, attempts),
    )

    result = selection_module.select_reference(
        "telemetry.sqlite3", ReferenceRequest(target.attempt_key)
    )

    assert result.selected_reference["attempt_key"] == "run:42:0:1"
    assert result.candidates[0].selected is True


def test_session_best_rejects_conditions_context_and_corrupt_traces(monkeypatch) -> None:
    target = _trace(4)
    changed_weather = {**_CONTEXT, "weather_id": 2, "weather_name": "light_rain"}
    attempts = (
        _entry(1, context=changed_weather),
        _entry(2),
        _entry(3),
        _entry(4),
    )

    def load_trace(_db, key, **_):
        if key == target.attempt_key:
            return target
        if key.endswith(":2"):
            raise ValueError("trace file is missing or its checksum does not match SQLite")
        return _trace(int(key.rsplit(":", 1)[1]))

    monkeypatch.setattr(selection_module, "load_attempt_trace", load_trace)
    monkeypatch.setattr(
        selection_module,
        "load_reference_inventory",
        lambda _db, _key: _inventory(4, attempts),
    )

    result = selection_module.select_reference(
        "telemetry.sqlite3", ReferenceRequest(target.attempt_key)
    )

    assert result.status is ReferenceSelectionStatus.SELECTED
    assert result.selected_reference["attempt_key"] == "run:42:0:3"
    assert result.candidates[0].exclusion_reasons == ("incompatible_time_trial_context",)
    assert result.candidates[1].exclusion_reasons == ("trace_missing_or_corrupt",)


def test_session_best_abstains_when_capture_reports_recording_loss(monkeypatch) -> None:
    target = _trace(2)
    attempts = (_entry(1), _entry(2))
    lossy_capture = {**_CLEAN_CAPTURE, "queue_dropped": 2}
    monkeypatch.setattr(
        selection_module,
        "load_attempt_trace",
        lambda _db, key, **_: target if key == target.attempt_key else _trace(1),
    )
    monkeypatch.setattr(
        selection_module,
        "load_reference_inventory",
        lambda _db, _key: _inventory(2, attempts, completion=lossy_capture),
    )

    result = selection_module.select_reference(
        "telemetry.sqlite3", ReferenceRequest(target.attempt_key)
    )

    assert result.status is ReferenceSelectionStatus.NO_ELIGIBLE_REFERENCE
    assert result.selected_reference is None
    assert result.reasons == ("capture_queue_drops",)
    assert result.candidates[0].exclusion_reasons == ("capture_queue_drops",)


def test_session_best_abstains_when_import_assembler_reports_loss(monkeypatch) -> None:
    target = _trace(2)
    attempts = (_entry(1), _entry(2))
    monkeypatch.setattr(
        selection_module,
        "load_attempt_trace",
        lambda _db, key, **_: target if key == target.attempt_key else _trace(1),
    )
    monkeypatch.setattr(
        selection_module,
        "load_reference_inventory",
        lambda _db, _key: _inventory(
            2,
            attempts,
            processing_quality={
                "import_late_packets_ignored": 0,
                "import_frame_overflow_packets_dropped": 1,
            },
        ),
    )

    result = selection_module.select_reference(
        "telemetry.sqlite3", ReferenceRequest(target.attempt_key)
    )

    assert result.status is ReferenceSelectionStatus.NO_ELIGIBLE_REFERENCE
    assert result.selected_reference is None
    assert result.reasons == ("import_frame_overflow_drops",)


def test_session_best_abstains_when_import_assembler_metrics_are_missing(monkeypatch) -> None:
    target = _trace(2)
    attempts = (_entry(1), _entry(2))
    monkeypatch.setattr(
        selection_module,
        "load_attempt_trace",
        lambda _db, key, **_: target if key == target.attempt_key else _trace(1),
    )
    monkeypatch.setattr(
        selection_module,
        "load_reference_inventory",
        lambda _db, _key: StoredReferenceInventory(
            target_attempt_key=target.attempt_key,
            run_id="run",
            session_uid="42",
            car_index=0,
            target_attempt_number=2,
            capture_complete=True,
            capture_completion=_CLEAN_CAPTURE,
            processing_quality={},
            attempts=attempts,
        ),
    )

    result = selection_module.select_reference(
        "telemetry.sqlite3", ReferenceRequest(target.attempt_key)
    )

    assert result.status is ReferenceSelectionStatus.NO_ELIGIBLE_REFERENCE
    assert result.selected_reference is None
    assert result.reasons == ("import_frame_loss_metrics_unavailable",)


def test_session_best_reports_race_policy_as_unsupported(monkeypatch) -> None:
    race_context = {**_CONTEXT, "session_type": "race", "game_mode": "my_team_career_25", "rule_set": "race"}
    target = _trace(1, context=race_context)
    monkeypatch.setattr(selection_module, "load_attempt_trace", lambda *_args, **_kwargs: target)
    monkeypatch.setattr(
        selection_module,
        "load_reference_inventory",
        lambda _db, _key: _inventory(1, (_entry(1),)),
    )

    result = selection_module.select_reference(
        "telemetry.sqlite3", ReferenceRequest(target.attempt_key)
    )

    assert result.status is ReferenceSelectionStatus.UNSUPPORTED_POLICY
    assert result.reasons == ("target_unsupported_mode",)
    assert result.selected_reference is None


def test_reference_cli_runs_comparison_after_selection(monkeypatch, capsys) -> None:
    target_key = "run:42:0:2"
    selection = ReferenceSelection(
        target_attempt_key=target_key,
        reference_kind=ReferenceKind.SESSION_BEST,
        status=ReferenceSelectionStatus.SELECTED,
        policy_version="tt-session-best-v1",
        scope={"run_id": "run"},
        target_reference_eligible=False,
        diagnostic_only=True,
        selected_reference={"attempt_key": "run:42:0:1"},
        candidates=(
            CandidateAssessment(
                attempt_key="run:42:0:1",
                attempt_number=1,
                lap_time_ms=79_000,
                eligible=True,
                selected=True,
                trace_sha256="a" * 64,
                exclusion_reasons=(),
            ),
        ),
        reasons=(),
    )
    comparison_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(cli_module, "select_reference", lambda *_: selection)
    monkeypatch.setattr(
        cli_module,
        "compare_attempts",
        lambda database, target, reference: comparison_calls.append(
            (database, target, reference)
        )
        or {"comparison_ready": True},
    )

    exit_code = cli_module._reference(
        Namespace(database="state.sqlite3", target_attempt_key=target_key)
    )
    import json

    document = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert comparison_calls == [("state.sqlite3", target_key, "run:42:0:1")]
    assert document["comparison"] == {
        "status": "available",
        "result": {"comparison_ready": True},
    }


def test_reference_cli_preserves_selection_when_comparison_is_unavailable(monkeypatch, capsys) -> None:
    target_key = "run:42:0:2"
    selection = ReferenceSelection(
        target_attempt_key=target_key,
        reference_kind=ReferenceKind.SESSION_BEST,
        status=ReferenceSelectionStatus.SELECTED,
        policy_version="tt-session-best-v1",
        scope={"run_id": "run"},
        target_reference_eligible=True,
        diagnostic_only=False,
        selected_reference={"attempt_key": "run:42:0:1"},
        candidates=(),
        reasons=(),
    )
    monkeypatch.setattr(cli_module, "select_reference", lambda *_: selection)

    def fail_comparison(*_args, **_kwargs):
        raise ValueError("attempts have no overlapping supported distance range")

    monkeypatch.setattr(cli_module, "compare_attempts", fail_comparison)

    exit_code = cli_module._reference(
        Namespace(database="state.sqlite3", target_attempt_key=target_key)
    )
    import json

    document = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert document["selected_reference"]["attempt_key"] == "run:42:0:1"
    assert document["comparison"] == {
        "status": "unavailable",
        "reason": "attempts have no overlapping supported distance range",
    }
