from __future__ import annotations

from copy import deepcopy

from f1_engineer.analysis.lap_debrief import build_lap_debrief


def _comparison(*, region_count: int = 3) -> dict[str, object]:
    target = {
        "attempt_key": "run:42:0:3",
        "run_id": "run",
        "trace_sha256": "3" * 64,
        "disposition": "completed",
        "lap_time_ms": 90_200,
    }
    reference = {
        "attempt_key": "run:42:0:1",
        "run_id": "run",
        "trace_sha256": "1" * 64,
        "disposition": "completed",
        "lap_time_ms": 90_000,
    }
    target_identity = {
        key: target[key] for key in ("attempt_key", "run_id", "trace_sha256")
    }
    reference_identity = {
        key: reference[key] for key in ("attempt_key", "run_id", "trace_sha256")
    }
    model = {
        "model_id": "reviewed-model",
        "revision": 1,
        "validation_status": "validated",
        "registered": True,
        "approved_for_candidate_ranking": True,
        "model_content_sha256": "a" * 64,
        "approval_provenance": {"review_record": "synthetic-approval"},
    }
    selection = {
        "reference_kind": "session_best",
        "status": "selected",
        "policy_version": "tt-session-best-v2-lifecycle",
        "scope": {"run_id": "run", "session_uid": "42", "car_index": 0},
        "selected_reference": {
            "attempt_key": reference["attempt_key"],
            "trace_sha256": reference["trace_sha256"],
        },
        "reasons": [],
    }
    provenance = {
        "target": target_identity,
        "reference": reference_identity,
        "model": model,
        "reference_selection": selection,
    }
    source_fields = {
        "target": "target.lap_time_ms",
        "reference": "reference.lap_time_ms",
        "derived": "official_lap_time_difference_s",
    }
    connected_support = {
        "target_resampled_time_connected": True,
        "reference_resampled_time_connected": True,
        "shared_delta_time_connected": True,
        "target_source_session_time_connected": True,
        "reference_source_session_time_connected": True,
        "interval_connected_supported_time": True,
        "target_time_coverage": 1.0,
        "reference_time_coverage": 1.0,
        "shared_time_coverage": 1.0,
        "entry_delta_s": 0.05,
        "exit_delta_s": 0.20,
    }

    candidates = []
    regions = []
    for rank in range(1, region_count + 1):
        region_id = f"turn-{rank}"
        label = f"Turn {rank}"
        start_m = float(rank * 100)
        end_m = start_m + 70.0
        difference = (region_count - rank + 1) / 100.0
        region_support = {
            **connected_support,
            "entry_delta_s": 0.0,
            "exit_delta_s": difference,
        }
        candidates.append(
            {
                "rank": rank,
                "region_id": region_id,
                "region_label": label,
                "analysis_window_m": [start_m, end_m],
                "recorded_time_difference_s": difference,
                "measurement_direction": "target_minus_reference_interval_time_difference",
                "connected_support": deepcopy(region_support),
            }
        )
        interval_fact = {
            "kind": "recorded_interval_time_difference",
            "value": difference,
            "unit": "s",
            "direction": "target_minus_reference",
            "analysis_window_m": [start_m, end_m],
            "connected_support": deepcopy(region_support),
            "boundary_delta_evidence": {
                "entry_target_minus_reference_s": 0.0,
                "exit_target_minus_reference_s": difference,
            },
            "provenance": deepcopy(provenance),
            "text": "Generated interval measurement from D0033.",
        }
        regions.append(
            {
                "rank": rank,
                "region_id": region_id,
                "region_label": label,
                "analysis_window_m": [start_m, end_m],
                "facts": [interval_fact],
                "connected_support": deepcopy(region_support),
                "provenance": deepcopy(provenance),
            }
        )

    return {
        "comparison_policy": "time_trial",
        "official_lap_time_difference_s": 0.2,
        "target": target,
        "reference": reference,
        "comparison_brief": {
            "analysis_version": "comparison-brief-v1",
            "status": "available",
            "facts": [
                {
                    "kind": "official_lap_time_difference",
                    "value": 0.2,
                    "unit": "s",
                    "direction": "target_minus_reference",
                    "source_fields": source_fields,
                    "provenance": {
                        "target": target_identity,
                        "reference": reference_identity,
                    },
                }
            ],
            "limitations": [],
            "limits": {"omitted_limitation_count": 0},
        },
        "corner_loss_candidates": {
            "analysis_version": "corner-loss-candidates-v1",
            "policy_version": "tt-session-best-connected-regions-v1",
            "status": "ranked",
            "coaching_eligible": False,
            "source": {
                "target": target_identity,
                "reference": reference_identity,
                "model": model,
                "reference_selection": selection,
                "capture_evidence": "passed_session_best_policy",
            },
            "ranked_candidates": candidates,
            "omitted_candidate_count": 0,
        },
        "corner_comparison_brief": {
            "analysis_version": "corner-comparison-brief-v1",
            "status": "available",
            "coaching_eligible": False,
            "regions": regions,
            "gate_reasons": [],
            "omitted_region_count": 0,
        },
        "corner_analysis": {
            "diagnostic_only": False,
            "model": {
                "model_id": "reviewed-model",
                "revision": 1,
                "validation_status": "validated",
            },
            "source": {
                "target": target_identity,
                "reference": reference_identity,
            },
        },
    }


def test_debrief_preserves_verified_lap_fact_and_top_three_provenance() -> None:
    result = build_lap_debrief(_comparison())

    assert result["status"] == "available"
    assert result["diagnostic_only"] is True
    assert result["coaching_eligible"] is False
    assert result["official_lap_time"]["value_s"] == 0.2
    assert result["official_lap_time"]["provenance"]["target"]["attempt_key"] == (
        "run:42:0:3"
    )
    assert [region["rank"] for region in result["ranked_regions"]] == [1, 2, 3]
    assert [region["region_id"] for region in result["ranked_regions"]] == [
        "turn-1",
        "turn-2",
        "turn-3",
    ]
    assert result["ranked_regions"][2]["provenance"]["model"]["model_content_sha256"] == (
        "a" * 64
    )
    assert "0.200 s slower" in result["text"]
    assert "do not establish causes" in result["text"]


def test_mismatched_d0033_entry_rejects_the_whole_ranked_region_list() -> None:
    comparison = _comparison()
    comparison["corner_comparison_brief"]["regions"][1]["facts"][0]["value"] = 9.0

    result = build_lap_debrief(comparison)

    assert result["ranked_regions"] == []
    assert result["omitted_region_count"] == 0
    assert result["limitations"][0]["code"] == "ranked_regions_rejected"
    assert "did not match" in result["limitations"][0]["text"]


def test_unsorted_ranks_are_rejected_even_when_child_facts_match() -> None:
    comparison = _comparison()
    candidate = comparison["corner_loss_candidates"]["ranked_candidates"][0]
    summary = comparison["corner_comparison_brief"]["regions"][0]
    fact = summary["facts"][0]
    for evidence in (candidate["connected_support"], summary["connected_support"], fact["connected_support"]):
        evidence["exit_delta_s"] = 0.005
    fact["boundary_delta_evidence"]["exit_target_minus_reference_s"] = 0.005
    candidate["recorded_time_difference_s"] = 0.005
    fact["value"] = 0.005

    result = build_lap_debrief(comparison)

    assert result["ranked_regions"] == []
    assert "rank order" in result["limitations"][0]["text"]


def test_duplicate_region_identity_is_rejected() -> None:
    comparison = _comparison()
    comparison["corner_loss_candidates"]["ranked_candidates"][1]["region_id"] = "turn-1"
    comparison["corner_comparison_brief"]["regions"][1]["region_id"] = "turn-1"

    result = build_lap_debrief(comparison)

    assert result["ranked_regions"] == []
    assert "unique region identity" in result["limitations"][0]["text"]


def test_boundary_delta_arithmetic_and_d0033_authority_are_rechecked() -> None:
    comparison = _comparison(region_count=1)
    candidate = comparison["corner_loss_candidates"]["ranked_candidates"][0]
    summary = comparison["corner_comparison_brief"]["regions"][0]
    fact = summary["facts"][0]
    for evidence in (candidate["connected_support"], summary["connected_support"], fact["connected_support"]):
        evidence["exit_delta_s"] = 0.2
    fact["boundary_delta_evidence"]["exit_target_minus_reference_s"] = 0.2

    arithmetic_result = build_lap_debrief(comparison)
    assert arithmetic_result["ranked_regions"] == []
    assert arithmetic_result["limitations"][0]["code"] == "ranked_regions_rejected"

    comparison = _comparison(region_count=1)
    comparison["corner_loss_candidates"]["source"]["model"][
        "approved_for_candidate_ranking"
    ] = False
    authority_result = build_lap_debrief(comparison)
    assert authority_result["ranked_regions"] == []
    assert authority_result["limitations"][0]["code"] == "ranked_regions_rejected"


def test_ranking_abstention_remains_visible_and_draft_regions_are_not_substituted() -> None:
    comparison = _comparison()
    comparison["corner_loss_candidates"].update(
        status="abstained",
        gate_reasons=["track_model_not_validated"],
        ranked_candidates=[],
    )
    comparison["corner_comparison_brief"].update(
        status="abstained",
        gate_reasons=["track_model_not_validated"],
        regions=[],
    )

    result = build_lap_debrief(comparison)

    assert result["ranked_regions"] == []
    assert result["status"] == "partial"
    assert "track model not validated" in result["limitations"][0]["text"]


def test_practice_qualifying_never_gains_ranked_regions_from_child_payloads() -> None:
    comparison = _comparison(region_count=1)
    comparison["comparison_policy"] = "practice_qualifying"

    result = build_lap_debrief(comparison)

    assert result["ranked_regions"] == []
    assert result["limitations"][0]["code"] == "ranked_regions_unavailable"
    assert "Time Trial ranking policy" in result["limitations"][0]["text"]


def test_debrief_prioritizes_integrity_and_capture_limitations_with_bounds() -> None:
    comparison = _comparison(region_count=0)
    comparison["comparison_brief"]["limitations"] = [
        {"code": "practice_qualifying_conditions_uncontrolled", "text": "conditions"},
        {"code": "delta_coverage_partial", "text": "coverage"},
        {"code": "capture_incomplete", "text": "capture"},
        {"code": "attempt_integrity", "text": "integrity"},
        *[
            {"code": f"extra_{index}", "text": "x" * 600}
            for index in range(12)
        ],
    ]
    comparison["corner_loss_candidates"].update(
        status="no_positive_supported_differences", ranked_candidates=[]
    )
    comparison["corner_comparison_brief"].update(
        status="no_ranked_candidates", regions=[]
    )

    result = build_lap_debrief(comparison)

    assert result["limitations"][0]["code"] == "attempt_integrity"
    assert result["limitations"][1]["code"] == "capture_incomplete"
    assert len(result["limitations"]) <= 8
    assert all(len(item["text"]) <= 280 for item in result["limitations"])
    assert len(result["text"]) <= result["limits"]["text_characters"]
    assert result["omitted_limitation_count"] > 0


def test_missing_legacy_child_fields_produce_a_bounded_abstention() -> None:
    result = build_lap_debrief({"comparison_policy": "time_trial"})

    assert result["status"] == "abstained"
    assert result["ranked_regions"] == []
    assert result["diagnostic_only"] is True
    assert result["coaching_eligible"] is False
    assert len(result["limitations"]) <= result["limits"]["limitation_limit"]
    assert len(result["text"]) <= result["limits"]["text_characters"]
