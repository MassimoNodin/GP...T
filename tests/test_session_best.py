from __future__ import annotations

import json
from dataclasses import replace
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

import f1_engineer.cli as cli_module
from f1_engineer.analysis import session_best as session_best_module
from f1_engineer.analysis.reference_selection import ReferenceRequest, select_reference
from f1_engineer.analysis.session_best import (
    SessionBestStatus,
    assess_session_best,
)
from f1_engineer.storage.importer import import_capture, list_laps
from f1_engineer.storage.query import (
    AttemptInventoryReadLimitError,
    AttemptTraceReadLimitError,
    StoredAttemptInventoryEntry,
    StoredReferenceInventory,
    load_reference_inventory,
)
from tests.test_phase_e_positive_integration import (
    _LAP_DURATIONS_MS,
    _write_synthetic_capture,
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


def _entry(
    attempt_number: int,
    *,
    lap_time_ms: int | None = None,
    disposition: str = "completed",
    game_valid: bool | None = True,
    reference_eligible: bool = True,
    exclusion_reasons: tuple[str, ...] = (),
    context: dict[str, object] | None = None,
    superseded: bool | None = False,
    lifecycle_assessed: bool = True,
    trace_ready: bool = True,
    trace_row_count: int | None = 10,
    trace_size_bytes: int | None = 100,
) -> StoredAttemptInventoryEntry:
    return StoredAttemptInventoryEntry(
        attempt_key=f"run:42:0:{attempt_number}",
        run_id="run",
        session_uid="42",
        car_index=0,
        attempt_number=attempt_number,
        disposition=disposition,
        lap_time_ms=(
            80_000 - attempt_number * 100 if lap_time_ms is None else lap_time_ms
        ),
        game_valid=game_valid,
        reference_eligible=reference_eligible,
        start_observed=True,
        pit_encountered=False,
        sample_count=10,
        exclusion_reasons=exclusion_reasons,
        trace_ready=trace_ready,
        trace_row_count=trace_row_count,
        trace_sha256=f"{attempt_number:064x}",
        trace_schema_version=1,
        quality={"sample_count": 10},
        context_segments=((100, _CONTEXT if context is None else context),),
        superseded=superseded,
        lifecycle_assessed=lifecycle_assessed,
        trace_size_bytes=trace_size_bytes,
        context_segment_count=1,
        context_bytes=len(
            json.dumps(
                {"from_frame_identifier": 100, "context": _CONTEXT if context is None else context},
                separators=(",", ":"),
            ).encode("utf-8")
        ),
    )


def _inventory(
    anchor: StoredAttemptInventoryEntry,
    attempts: tuple[StoredAttemptInventoryEntry, ...],
    *,
    capture_complete: bool = True,
    capture_completion: dict[str, object] | None = None,
    processing_quality: dict[str, object] | None = None,
) -> StoredReferenceInventory:
    return StoredReferenceInventory(
        target_attempt_key=anchor.attempt_key,
        run_id="run",
        session_uid="42",
        car_index=0,
        target_attempt_number=anchor.attempt_number,
        capture_complete=capture_complete,
        capture_completion=(
            _CLEAN_CAPTURE if capture_completion is None else capture_completion
        ),
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


def _wire_inventory(monkeypatch, inventory: StoredReferenceInventory) -> None:
    monkeypatch.setattr(
        session_best_module,
        "load_reference_inventory",
        lambda _database, _key, **kwargs: inventory,
    )
    entries = {entry.attempt_key: entry for entry in inventory.attempts}
    monkeypatch.setattr(
        session_best_module,
        "load_attempt_trace",
        lambda _database, key, **kwargs: _loaded_trace(entries[key]),
    )


def _loaded_trace(entry: StoredAttemptInventoryEntry) -> SimpleNamespace:
    return SimpleNamespace(
        attempt_key=entry.attempt_key,
        run_id=entry.run_id,
        session_uid=entry.session_uid,
        car_index=entry.car_index,
        attempt_number=entry.attempt_number,
        disposition=entry.disposition,
        lap_time_ms=entry.lap_time_ms,
        game_valid=entry.game_valid,
        reference_eligible=entry.reference_eligible,
        start_observed=entry.start_observed,
        pit_encountered=entry.pit_encountered,
        superseded=entry.superseded,
        lifecycle_assessed=entry.lifecycle_assessed,
        exclusion_reasons=entry.exclusion_reasons,
        trace_sha256=entry.trace_sha256,
        trace_schema_version=entry.trace_schema_version,
        quality=entry.quality,
        context_segments=entry.context_segments,
        source_sample_count=entry.trace_row_count,
        samples=tuple({} for _ in range(entry.trace_row_count or 0)),
    )


def test_completed_capture_best_includes_fastest_later_lap_without_changing_prior_reference(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(_LAP_DURATIONS_MS, 4, 14_000)
    capture = tmp_path / "session-best.f1ecap"
    database = tmp_path / "session-best.sqlite3"
    _write_synthetic_capture(capture, complete_final_lap=True)
    imported = import_capture(capture, database)
    attempts = list_laps(database, run_id=imported.run_id)
    anchor = next(item for item in attempts if item["attempt_number"] == 2)
    final_completed = next(
        item for item in attempts if item["attempt_number"] == 4
    )

    overview = assess_session_best(database, anchor["attempt_key"]).to_dict()
    prior_reference = select_reference(database, ReferenceRequest(anchor["attempt_key"]))

    assert overview["status"] == "assessed"
    assert overview["scope"]["context_anchor_attempt_key"] == anchor["attempt_key"]
    assert overview["eligible_time_trial_best"]["attempt"]["attempt_key"] == final_completed[
        "attempt_key"
    ]
    assert overview["eligible_time_trial_best"]["attempt"]["lap_time_ms"] == 14_000
    assert prior_reference.selected_reference["attempt_key"] != final_completed["attempt_key"]
    assert final_completed["attempt_number"] > anchor["attempt_number"]
    with pytest.raises(AttemptInventoryReadLimitError, match="scope_attempts"):
        load_reference_inventory(
            database,
            anchor["attempt_key"],
            include_after_target=True,
            max_scope_attempts=2,
        )
    with pytest.raises(AttemptInventoryReadLimitError, match="context_segments"):
        load_reference_inventory(
            database,
            anchor["attempt_key"],
            include_after_target=True,
            max_scope_attempts=256,
            max_context_segments=1,
        )


def test_assessment_reports_invalid_superseded_context_mismatch_and_corrupt_candidates(
    monkeypatch,
) -> None:
    anchor = _entry(2, lap_time_ms=80_000)
    invalid = _entry(
        3,
        lap_time_ms=70_000,
        game_valid=False,
        reference_eligible=False,
        exclusion_reasons=("game_marked_invalid",),
    )
    superseded = _entry(4, lap_time_ms=71_000, superseded=True)
    corrupt = _entry(5, lap_time_ms=72_000)
    changed_context = {**_CONTEXT, "weather_name": "light_rain"}
    mismatched = _entry(6, lap_time_ms=73_000, context=changed_context)
    later_best = _entry(7, lap_time_ms=75_000)
    inventory = _inventory(
        anchor,
        (anchor, invalid, superseded, corrupt, mismatched, later_best),
    )
    _wire_inventory(monkeypatch, inventory)

    original_loader = session_best_module.load_attempt_trace

    def load_trace(_database, attempt_key, **kwargs):
        if attempt_key == corrupt.attempt_key:
            raise ValueError("checksum mismatch")
        return original_loader(_database, attempt_key, **kwargs)

    monkeypatch.setattr(session_best_module, "load_attempt_trace", load_trace)

    result = assess_session_best("unused.sqlite3", anchor.attempt_key).to_dict()
    candidates = {item["attempt_key"]: item for item in result["candidates"]}

    assert result["eligible_time_trial_best"]["attempt"]["attempt_key"] == later_best.attempt_key
    assert "game_invalid" in candidates[invalid.attempt_key]["exclusion_reasons"]
    assert "superseded_by_flashback" in candidates[superseded.attempt_key]["exclusion_reasons"]
    assert candidates[corrupt.attempt_key]["exclusion_reasons"] == [
        "trace_missing_or_corrupt"
    ]
    assert "incompatible_time_trial_context" in candidates[mismatched.attempt_key][
        "exclusion_reasons"
    ]
    assert candidates[invalid.attempt_key]["recorded_time_rank"] == 1


def test_equal_recorded_times_choose_earliest_attempt_deterministically(monkeypatch) -> None:
    earlier = _entry(1, lap_time_ms=79_000)
    anchor = _entry(2, lap_time_ms=80_000)
    later = _entry(3, lap_time_ms=79_000)
    _wire_inventory(monkeypatch, _inventory(anchor, (earlier, anchor, later)))

    result = assess_session_best("unused.sqlite3", anchor.attempt_key).to_dict()

    assert result["eligible_time_trial_best"]["attempt"]["attempt_key"] == earlier.attempt_key


@pytest.mark.parametrize(
    ("context", "expected_status"),
    [
        ({**_CONTEXT, "session_type": "race", "game_mode": "race", "rule_set": "race"}, "unsupported_mode"),
        ({**_CONTEXT, "session_type": "unknown", "game_mode": "unknown", "rule_set": "unknown"}, "unknown_mode"),
    ],
)
def test_non_time_trial_modes_keep_recorded_order_but_do_not_select_a_best(
    monkeypatch, context, expected_status
) -> None:
    anchor = _entry(1, context=context)
    later = _entry(2, lap_time_ms=79_000, context=context)
    _wire_inventory(
        monkeypatch,
        _inventory(
            anchor,
            (anchor, later),
            capture_complete=False,
            capture_completion={"status": "incomplete"},
            processing_quality={
                "import_late_packets_ignored": 2,
                "import_frame_overflow_packets_dropped": None,
            },
        ),
    )

    result = assess_session_best("unused.sqlite3", anchor.attempt_key).to_dict()

    assert result["recorded_time_ordering"]["status"] == "available"
    assert result["recorded_time_ordering"]["diagnostic_only"] is True
    assert result["eligible_time_trial_best"]["status"] == expected_status
    assert result["eligible_time_trial_best"]["attempt"] is None
    assert "capture_not_finalized" in result["reasons"]
    assert "capture_completion_unavailable" in result["reasons"]
    assert "import_late_packet_drops" in result["reasons"]
    assert "import_frame_loss_metrics_invalid" in result["reasons"]
    assert all(item["time_trial_eligibility"] == "not_assessed" for item in result["candidates"])


def test_unexpected_trace_limit_abstains_and_shrinks_each_read_budget(monkeypatch) -> None:
    anchor = _entry(1, trace_size_bytes=140)
    later = _entry(2, trace_size_bytes=60)
    anchor = replace(anchor, context_bytes=9_000)
    later = replace(later, context_bytes=1_000)
    _wire_inventory(monkeypatch, _inventory(anchor, (anchor, later)))
    calls: list[dict[str, int]] = []

    def load_trace(_database, attempt_key, **kwargs):
        calls.append({
            "max_trace_bytes": kwargs["max_trace_bytes"],
            "max_trace_rows": kwargs["max_trace_rows"],
            "max_context_bytes": kwargs["max_context_bytes"],
            "max_context_segments": kwargs["max_context_segments"],
        })
        if attempt_key == later.attempt_key:
            raise AttemptTraceReadLimitError("bytes")
        return _loaded_trace(anchor)

    monkeypatch.setattr(session_best_module, "load_attempt_trace", load_trace)
    result = assess_session_best(
        "unused.sqlite3",
        anchor.attempt_key,
        max_candidate_trace_bytes=200,
        max_candidate_trace_rows=20,
        max_candidate_context_bytes=10_000,
        max_candidate_context_segments=2,
    ).to_dict()

    assert [call["max_trace_bytes"] for call in calls] == [140, 60]
    assert [call["max_trace_rows"] for call in calls] == [10, 10]
    assert calls[1]["max_context_bytes"] < calls[0]["max_context_bytes"]
    assert result["eligible_time_trial_best"]["attempt"] is None
    assert result["eligible_time_trial_best"]["status"] == "assessment_limit_exceeded"
    assert "trace_bytes_limit_exceeded" in result["reasons"]


def test_inventory_snapshot_mismatch_abstains_instead_of_using_stale_eligibility(
    monkeypatch,
) -> None:
    anchor = _entry(1)
    fastest = _entry(2, lap_time_ms=70_000)
    _wire_inventory(monkeypatch, _inventory(anchor, (anchor, fastest)))

    def changed_trace(_database, attempt_key, **kwargs):
        loaded = _loaded_trace(anchor if attempt_key == anchor.attempt_key else fastest)
        if attempt_key == fastest.attempt_key:
            loaded.lap_time_ms = 75_000
        return loaded

    monkeypatch.setattr(session_best_module, "load_attempt_trace", changed_trace)
    result = assess_session_best("unused.sqlite3", anchor.attempt_key).to_dict()

    assert result["eligible_time_trial_best"]["attempt"] is None
    assert result["eligible_time_trial_best"]["status"] == "assessment_limit_exceeded"
    assert "candidate_inventory_changed_during_assessment" in result["reasons"]


def test_assessment_abstains_on_inventory_or_aggregate_trace_limits(monkeypatch) -> None:
    anchor = _entry(1)
    monkeypatch.setattr(
        session_best_module,
        "load_reference_inventory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AttemptInventoryReadLimitError("scope_attempts")
        ),
    )
    inventory_limited = assess_session_best("unused.sqlite3", anchor.attempt_key)
    assert inventory_limited.status is SessionBestStatus.ABSTAINED
    assert inventory_limited.to_dict()["eligible_time_trial_best"]["status"] == (
        "assessment_limit_exceeded"
    )

    other = _entry(2)
    _wire_inventory(monkeypatch, _inventory(anchor, (anchor, other)))
    trace_limited = assess_session_best(
        "unused.sqlite3",
        anchor.attempt_key,
        max_candidate_trace_bytes=150,
    )
    document = trace_limited.to_dict()
    assert trace_limited.status is SessionBestStatus.ABSTAINED
    assert document["eligible_time_trial_best"]["attempt"] is None
    assert document["eligible_time_trial_best"]["status"] == "assessment_limit_exceeded"
    assert all(
        item["time_trial_eligibility"] == "not_assessed"
        for item in document["candidates"]
    )


def test_candidate_rows_are_capped_with_explicit_omission_count(monkeypatch) -> None:
    attempts = tuple(_entry(index, lap_time_ms=90_000 - index) for index in range(1, 41))
    anchor = attempts[19]
    _wire_inventory(monkeypatch, _inventory(anchor, attempts))

    result = assess_session_best(
        "unused.sqlite3", anchor.attempt_key, max_candidate_rows=32
    ).to_dict()

    assert len(result["candidates"]) == 32
    assert result["recorded_time_ordering"]["candidates_omitted_count"] == 8
    assert result["candidates"][0]["attempt_number"] == 40


def test_session_best_cli_has_explicit_anchor_and_serializes_abstention(
    monkeypatch, capsys
) -> None:
    assessment = SimpleNamespace(
        status=SessionBestStatus.ABSTAINED,
        to_dict=lambda: {"status": "abstained", "anchor_attempt_key": "anchor"},
    )
    monkeypatch.setattr(cli_module, "assess_session_best", lambda *_: assessment)
    args = cli_module.build_parser().parse_args(
        ["session-best", "anchor", "--database", "archive.sqlite3"]
    )

    exit_code = args.handler(args)

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {
        "anchor_attempt_key": "anchor",
        "status": "abstained",
    }
