from __future__ import annotations

from copy import deepcopy

from f1_engineer.analysis.corner_loss_candidates import build_corner_loss_candidates


_TARGET = {
    "attempt_key": "run:42:0:3",
    "run_id": "run",
    "session_uid": "42",
    "car_index": 0,
    "disposition": "completed",
    "lap_time_ms": 79_000,
    "game_valid": True,
    "reference_eligible": True,
    "start_observed": True,
    "pit_encountered": False,
    "superseded": False,
    "lifecycle_assessed": True,
    "trace_sha256": "3" * 64,
}
_REFERENCE = {
    "attempt_key": "run:42:0:1",
    "run_id": "run",
    "session_uid": "42",
    "car_index": 0,
    "trace_sha256": "1" * 64,
}
_MODEL = {
    "model_id": "melbourne-reviewed-v1",
    "revision": 1,
    "validation_status": "validated",
}
_APPROVAL = {
    "model_id": "melbourne-reviewed-v1",
    "revision": 1,
    "registered": True,
    "approved_for_candidate_ranking": True,
    "model_content_sha256": "a" * 64,
    "approval_provenance": {"review_record": "synthetic-test-approval"},
}


def _region(
    identifier: str,
    start: float,
    change: float | None,
    *,
    status: str = "supported_region_delta_change",
    connected: bool = True,
    complex_id: str | None = None,
) -> dict[str, object]:
    return {
        "identifier": identifier,
        "label": f"Region {identifier}",
        "analysis_window_m": [start, start + 10],
        "complex_id": complex_id,
        "delta_change": {
            "status": status,
            "entry_delta_s": 0.1 if change is not None else None,
            "exit_delta_s": 0.1 + change if change is not None else None,
            "delta_change_s": change,
            "target_time_coverage": 1.0 if connected else 0.7,
            "reference_time_coverage": 1.0 if connected else 0.8,
            "shared_time_coverage": 1.0 if connected else 0.6,
            "target_resampled_time_connected": connected,
            "reference_resampled_time_connected": connected,
            "shared_delta_time_connected": connected,
            "target_source_session_time_connected": connected,
            "reference_source_session_time_connected": connected,
            "interval_connected_supported_time": connected,
            "unavailable_reasons": [] if connected else ["shared_delta_time_disconnected"],
        },
    }


def _selection() -> dict[str, object]:
    return {
        "reference_kind": "session_best",
        "status": "selected",
        "policy_version": "tt-session-best-v2-lifecycle",
        "target": {"attempt_key": _TARGET["attempt_key"]},
        "scope": {
            "type": "same_run_session_player_prior_attempts",
            "run_id": "run",
            "session_uid": "42",
            "car_index": 0,
            "before_attempt_number": 3,
        },
        "selected_reference": {
            "attempt_key": _REFERENCE["attempt_key"],
            "attempt_number": 1,
            "lap_time_ms": 80_000,
            "trace_sha256": _REFERENCE["trace_sha256"],
            "trace_schema_version": 3,
        },
        "candidates": [
            {
                "attempt_key": _REFERENCE["attempt_key"],
                "eligible": True,
                "selected": True,
                "trace_sha256": _REFERENCE["trace_sha256"],
            }
        ],
        "reasons": [],
    }


def _corner_analysis(regions: list[dict[str, object]]) -> dict[str, object]:
    return {
        "diagnostic_only": False,
        "model": _MODEL,
        "source": {
            "target": {
                "attempt_key": _TARGET["attempt_key"],
                "run_id": _TARGET["run_id"],
                "trace_sha256": _TARGET["trace_sha256"],
            },
            "reference": {
                "attempt_key": _REFERENCE["attempt_key"],
                "run_id": _REFERENCE["run_id"],
                "trace_sha256": _REFERENCE["trace_sha256"],
            },
        },
        "regions": regions,
    }


def _build(
    *,
    target: dict[str, object] | None = None,
    reference: dict[str, object] | None = None,
    analysis: dict[str, object] | None = None,
    selection: dict[str, object] | None = None,
    model: dict[str, object] | None = None,
    approval: dict[str, object] | None = None,
    policy: str = "time_trial",
    context_supported: bool = True,
) -> dict[str, object]:
    return build_corner_loss_candidates(
        comparison_policy=policy,
        target=_TARGET if target is None else target,
        reference=_REFERENCE if reference is None else reference,
        corner_analysis=(
            _corner_analysis([_region("a", 10, 0.2)]) if analysis is None else analysis
        ),
        reference_selection=_selection() if selection is None else selection,
        selected_model=_MODEL if model is None else model,
        model_approval=_APPROVAL if approval is None else approval,
        target_context_supported=context_supported,
    )


def test_ranks_supported_differences_stably_and_caps_candidates() -> None:
    result = _build(
        analysis=_corner_analysis(
            [
                _region("later-tie", 30, 0.9),
                _region("first", 10, 1.2),
                _region("earlier-tie", 20, 0.9),
                _region("fourth", 40, 0.4),
            ]
        )
    )

    assert result["status"] == "ranked"
    assert result["coaching_eligible"] is False
    assert result["measurement_label"] == "recorded time differences"
    candidates = result["ranked_candidates"]
    assert [item["region_id"] for item in candidates] == [
        "first",
        "earlier-tie",
        "later-tie",
    ]
    assert result["omitted_candidate_count"] == 1
    assert candidates[0]["connected_support"]["interval_connected_supported_time"] is True
    assert result["source"]["capture_evidence"] == "passed_session_best_policy"


def test_nonpositive_and_sub_millisecond_differences_are_excluded() -> None:
    result = _build(
        analysis=_corner_analysis(
            [
                _region("zero", 10, 0.0),
                _region("negative", 30, -0.2),
                _region("sub-millisecond", 50, 0.0004),
            ]
        )
    )

    assert result["status"] == "no_positive_supported_differences"
    assert result["ranked_candidates"] == []
    counts = result["region_assessment"]
    assert counts["connected_interval_count"] == 3
    assert counts["non_positive_region_count"] == 2
    assert counts["below_display_resolution_count"] == 1
    assert result["region_assessment"]["excluded_regions"]


def test_reference_target_and_model_gates_abstain() -> None:
    wrong_reference = deepcopy(_REFERENCE)
    wrong_reference["trace_sha256"] = "f" * 64
    invalid_target = deepcopy(_TARGET)
    invalid_target["game_valid"] = False
    unapproved = deepcopy(_APPROVAL)
    unapproved["approved_for_candidate_ranking"] = False

    result = _build(
        reference=wrong_reference,
        target=invalid_target,
        approval=unapproved,
    )

    assert result["status"] == "abstained"
    assert result["ranked_candidates"] == []
    assert "target_not_game_valid" in result["gate_reasons"]
    assert "session_best_reference_checksum_mismatch" in result["gate_reasons"]
    assert "track_model_not_approved_for_candidate_ranking" in result["gate_reasons"]


def test_full_session_best_candidate_assessment_is_required() -> None:
    incomplete_selection = _selection()
    incomplete_selection["candidates"] = []

    result = _build(selection=incomplete_selection)

    assert result["status"] == "abstained"
    assert "session_best_candidate_assessment_mismatch" in result["gate_reasons"]


def test_capture_incomplete_and_non_time_trial_modes_abstain() -> None:
    incomplete_selection = _selection()
    incomplete_selection["status"] = "no_eligible_reference"
    incomplete_selection["selected_reference"] = None
    incomplete_selection["reasons"] = ["capture_not_finalized"]

    incomplete = _build(selection=incomplete_selection)
    race = _build(policy="practice_qualifying")

    assert incomplete["status"] == "abstained"
    assert "capture_not_finalized" in incomplete["gate_reasons"]
    assert incomplete["source"]["capture_evidence"] == "not_established"
    assert race["status"] == "abstained"
    assert "unsupported_comparison_policy" in race["gate_reasons"]


def test_unsupported_regions_are_exclusions_not_zero_differences() -> None:
    result = _build(
        analysis=_corner_analysis(
            [
                _region(
                    "gap",
                    10,
                    None,
                    status="unsupported_interior",
                    connected=False,
                )
            ]
        )
    )

    assert result["status"] == "abstained"
    assert result["ranked_candidates"] == []
    assert result["region_assessment"]["unsupported_region_count"] == 1
    excluded = result["region_assessment"]["excluded_regions"][0]
    assert excluded["recorded_time_difference_s"] is None


def test_complex_or_overlapping_regions_abstain_as_a_whole_model() -> None:
    complex_result = _build(
        analysis=_corner_analysis([_region("complex-member", 10, 0.5, complex_id="c1")])
    )
    overlap_result = _build(
        analysis=_corner_analysis(
            [_region("one", 10, 0.5), _region("two", 15, 0.4)]
        )
    )

    assert complex_result["status"] == "abstained"
    assert "complex_regions_not_supported_for_ranking" in complex_result["gate_reasons"]
    assert overlap_result["status"] == "abstained"
    assert "overlapping_regions_not_supported_for_ranking" in overlap_result["gate_reasons"]


def test_region_assessment_limit_abstains_and_reports_omitted_regions() -> None:
    analysis = _corner_analysis(
        [_region(f"r{index:02}", index * 20, 0.1) for index in range(65)]
    )

    result = _build(analysis=analysis)

    assert result["status"] == "abstained"
    assert "region_assessment_limit_exceeded" in result["gate_reasons"]
    counts = result["region_assessment"]
    assert counts["assessed_region_count"] == 64
    assert counts["omitted_region_count"] == 1
    assert counts["all_model_regions_assessed"] is False
    assert result["ranked_candidates"] == []
