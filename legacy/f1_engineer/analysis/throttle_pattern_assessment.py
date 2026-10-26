from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .corner_comparison_brief import validate_corner_ranking_source
from .driving_pattern_assessment import (
    MAX_DRIVING_PATTERN_ASSESSMENTS,
    MAX_GATE_REASONS,
    MAX_REASONS_PER_ASSESSMENT,
    _bounds,
    _close,
    _expected_provenance,
    _facts,
    _finite_number,
    _mapping,
    _matching_region,
    _omission_reasons,
    _valid_fact,
    _exit_speed_evidence,
)


THROTTLE_PATTERN_ASSESSMENT_SCHEMA_VERSION = 1
THROTTLE_PATTERN_ASSESSMENT_VERSION = "throttle-pattern-assessment-v1"
THROTTLE_PATTERN_RULE_ID = "farther-along-throttle-onset-lower-exit-speed"
THROTTLE_PATTERN_RULE_VERSION = 1
_EXPECTED_CORNER_BRIEF_VERSION = "corner-comparison-brief-v1"
_EXPECTED_RANKING_VERSION = "corner-loss-candidates-v1"


def build_throttle_pattern_assessment(
    comparison: Mapping[str, object],
) -> dict[str, object]:
    """Assess one bounded throttle/exit pattern from authorized comparison facts."""
    ranking = _mapping(comparison.get("corner_loss_candidates"))
    if ranking is None:
        return _abstained(["corner_ranking_unavailable"], None)

    gate_reasons = validate_corner_ranking_source(comparison)
    if ranking.get("analysis_version") != _EXPECTED_RANKING_VERSION:
        gate_reasons.append("corner_ranking_version_unsupported")
    elif (
        ranking.get("status") == "no_positive_supported_differences"
        and gate_reasons == ["no_ranked_corner_candidates"]
        and ranking.get("ranked_candidates") == []
    ):
        return _no_ranked_candidates(ranking)
    if gate_reasons:
        return _abstained(gate_reasons, ranking)

    candidates = ranking.get("ranked_candidates")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        return _abstained(["ranked_candidates_unavailable"], ranking)
    if len(candidates) > MAX_DRIVING_PATTERN_ASSESSMENTS:
        return _abstained(["ranked_candidate_limit_exceeded"], ranking)
    if not candidates:
        return _abstained(["ranked_candidates_empty"], ranking)

    brief = _mapping(comparison.get("corner_comparison_brief"))
    if (
        brief is None
        or brief.get("analysis_version") != _EXPECTED_CORNER_BRIEF_VERSION
        or brief.get("status") != "available"
        or brief.get("coaching_eligible") is not False
    ):
        return _abstained(["structured_corner_measurements_unavailable"], ranking)

    brief_regions = brief.get("regions")
    if not isinstance(brief_regions, Sequence) or isinstance(brief_regions, (str, bytes)):
        return _abstained(["structured_corner_regions_malformed"], ranking)
    if len(brief_regions) > MAX_DRIVING_PATTERN_ASSESSMENTS:
        return _abstained(["structured_corner_region_limit_exceeded"], ranking)

    expected_provenance = _expected_provenance(ranking)
    assessments: list[dict[str, object]] = []
    for index, raw_candidate in enumerate(candidates):
        candidate = _mapping(raw_candidate)
        if candidate is None:
            return _abstained(["ranked_candidate_invalid"], ranking)
        rank = index + 1
        if candidate.get("rank") != rank:
            return _abstained(["ranked_candidate_order_invalid"], ranking)
        region_matches = [
            item
            for item in brief_regions
            if isinstance(item, Mapping)
            and item.get("region_id") == candidate.get("region_id")
        ]
        if len(region_matches) != 1:
            return _abstained(["ranked_region_identity_ambiguous"], ranking)
        region = _mapping(region_matches[0])
        assert region is not None
        if not _matching_region(candidate, region, rank, expected_provenance):
            return _abstained(["ranked_region_measurement_mismatch"], ranking)
        assessments.append(_assess_region(candidate, region, expected_provenance))

    return {
        "schema_version": THROTTLE_PATTERN_ASSESSMENT_SCHEMA_VERSION,
        "analysis_version": THROTTLE_PATTERN_ASSESSMENT_VERSION,
        "status": "available",
        "rule": {
            "id": THROTTLE_PATTERN_RULE_ID,
            "version": THROTTLE_PATTERN_RULE_VERSION,
            "description": (
                "Farther-along 50% throttle onset with lower configured exit speed"
            ),
        },
        "validation_status": "experimental",
        "diagnostic_only": True,
        "coaching_eligible": False,
        "coaching_admission": {
            "status": "not_admitted",
            "eligible": False,
            "reasons": [
                "rule_validation_experimental",
                "real_capture_validation_required",
                "action_mapping_not_validated",
            ],
        },
        "action": None,
        "source": dict(_mapping(ranking.get("source")) or {}),
        "gate_reasons": [],
        "gate_reasons_omitted_count": 0,
        "regions": assessments,
        "limits": {
            "maximum_region_assessments": MAX_DRIVING_PATTERN_ASSESSMENTS,
            "maximum_reasons_per_region": MAX_REASONS_PER_ASSESSMENT,
            "maximum_gate_reasons": MAX_GATE_REASONS,
        },
    }


def _assess_region(
    candidate: Mapping[str, Any],
    region: Mapping[str, Any],
    expected_provenance: Mapping[str, object],
) -> dict[str, object]:
    facts = _facts(region)
    reasons: list[str] = []
    unavailable = False

    interval, interval_reason = _interval_evidence(
        facts.get("recorded_interval_time_difference"), candidate, expected_provenance
    )
    if interval is None:
        unavailable = True
        reasons.append(interval_reason or "recorded_interval_time_evidence_invalid")

    throttle, throttle_reason = _throttle_onset_evidence(
        facts.get("throttle_50_threshold_onset_difference"), expected_provenance
    )
    if throttle is None:
        unavailable = True
        reasons.append(throttle_reason or "throttle_onset_evidence_invalid")

    exit_fact = facts.get("configured_exit_anchor_speed_difference")
    exit_speed = _exit_speed_evidence(exit_fact, expected_provenance)
    if exit_speed is None:
        unavailable = True
        omission = _omission_reasons(region, "exit_speed")
        reasons.append(
            f"exit_speed_measurement_omitted:{omission[0]}"
            if omission
            else "exit_speed_evidence_invalid"
        )

    contradictions: list[str] = []
    if interval is not None and interval["recorded_interval_time_difference_s"] <= 0:
        contradictions.append("target_interval_time_not_positive")
    if throttle is not None:
        lower, upper = throttle["target_minus_reference_bounds_m"]
        if lower <= 0:
            if upper < 0:
                contradictions.append("target_throttle_onset_not_farther_along")
            else:
                unavailable = True
                reasons.append("throttle_onset_order_unresolved")
    if (
        exit_speed is not None
        and exit_speed["target_speed_kph"] - exit_speed["reference_speed_kph"] >= 0
    ):
        contradictions.append("target_exit_speed_not_lower")

    reasons.extend(contradictions)
    if unavailable:
        status = "unavailable"
    elif contradictions:
        status = "contradicted"
    else:
        status = "matched"

    evidence: dict[str, object] = {}
    if interval is not None:
        evidence["recorded_interval_time_difference_s"] = interval[
            "recorded_interval_time_difference_s"
        ]
    if throttle is not None:
        evidence["throttle_onset"] = throttle
    if exit_speed is not None:
        evidence["exit_speed"] = {
            **exit_speed,
            "target_minus_reference_kph": (
                exit_speed["target_speed_kph"] - exit_speed["reference_speed_kph"]
            ),
        }

    return {
        "rank": candidate["rank"],
        "region_id": candidate["region_id"],
        "region_label": candidate["region_label"],
        "analysis_window_m": list(candidate["analysis_window_m"]),
        "status": status,
        "reasons": list(dict.fromkeys(reasons))[:MAX_REASONS_PER_ASSESSMENT],
        "evidence": evidence,
        "provenance": dict(_mapping(region.get("provenance")) or {}),
        "action": None,
        "coaching_eligible": False,
    }


def _interval_evidence(
    fact: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
    expected_provenance: Mapping[str, object],
) -> tuple[dict[str, float] | None, str | None]:
    if not _valid_fact(
        fact,
        kind="recorded_interval_time_difference",
        unit="s",
        provenance=expected_provenance,
    ):
        return None, "recorded_interval_time_measurement_omitted"
    assert fact is not None
    candidate_difference = _finite_number(candidate.get("recorded_time_difference_s"))
    connected_support = _mapping(candidate.get("connected_support"))
    boundaries = _mapping(fact.get("boundary_delta_evidence"))
    entry = _finite_number(connected_support.get("entry_delta_s")) if connected_support else None
    exit_ = _finite_number(connected_support.get("exit_delta_s")) if connected_support else None
    value = _finite_number(fact.get("value"))
    if (
        candidate_difference is None
        or value is None
        or not _close(value, candidate_difference)
        or _bounds(fact.get("analysis_window_m"))
        != _bounds(candidate.get("analysis_window_m"))
        or fact.get("connected_support") != candidate.get("connected_support")
        or boundaries is None
        or entry is None
        or exit_ is None
        or not _close(boundaries.get("entry_target_minus_reference_s"), entry)
        or not _close(boundaries.get("exit_target_minus_reference_s"), exit_)
        or not _close(candidate_difference, exit_ - entry)
    ):
        return None, "recorded_interval_time_evidence_invalid"
    return {"recorded_interval_time_difference_s": exit_ - entry}, None


def _throttle_onset_evidence(
    fact: Mapping[str, Any] | None,
    expected_provenance: Mapping[str, object],
) -> tuple[dict[str, object] | None, str | None]:
    if not _valid_fact(
        fact,
        kind="throttle_50_threshold_onset_difference",
        unit="m",
        provenance=expected_provenance,
    ):
        return None, "throttle_onset_measurement_omitted"
    assert fact is not None
    target = _mapping(fact.get("target"))
    reference = _mapping(fact.get("reference"))
    target_bracket = _bounds(target.get("bracket_m")) if target else None
    reference_bracket = _bounds(reference.get("bracket_m")) if reference else None
    difference_bounds = _bounds(fact.get("difference_bounds_m"))
    target_distance = _finite_number(target.get("distance_m")) if target else None
    reference_distance = _finite_number(reference.get("distance_m")) if reference else None
    value = _finite_number(fact.get("value"))
    if (
        target_bracket is None
        or reference_bracket is None
        or difference_bounds is None
        or target_distance is None
        or reference_distance is None
        or value is None
        or not _close(fact.get("threshold"), 0.5)
        or not _close(fact.get("threshold_percent"), 50.0)
        or not _close(target.get("threshold"), 0.5)
        or not _close(reference.get("threshold"), 0.5)
        or not target_bracket[0] <= target_distance <= target_bracket[1]
        or not reference_bracket[0] <= reference_distance <= reference_bracket[1]
        or not _close(value, target_distance - reference_distance)
        or not _close(
            difference_bounds[0], target_bracket[0] - reference_bracket[1]
        )
        or not _close(
            difference_bounds[1], target_bracket[1] - reference_bracket[0]
        )
    ):
        return None, "throttle_onset_evidence_invalid"
    computed_difference_bounds = (
        target_bracket[0] - reference_bracket[1],
        target_bracket[1] - reference_bracket[0],
    )
    return {
        "threshold_percent": 50,
        "target_bracket_m": list(target_bracket),
        "reference_bracket_m": list(reference_bracket),
        "target_minus_reference_bounds_m": list(computed_difference_bounds),
    }, None


def _abstained(
    reasons: Sequence[str], ranking: Mapping[str, Any] | None
) -> dict[str, object]:
    unique = list(dict.fromkeys(reason for reason in reasons if isinstance(reason, str)))
    visible = unique[:MAX_GATE_REASONS]
    return {
        "schema_version": THROTTLE_PATTERN_ASSESSMENT_SCHEMA_VERSION,
        "analysis_version": THROTTLE_PATTERN_ASSESSMENT_VERSION,
        "status": "abstained",
        "rule": {
            "id": THROTTLE_PATTERN_RULE_ID,
            "version": THROTTLE_PATTERN_RULE_VERSION,
            "description": (
                "Farther-along 50% throttle onset with lower configured exit speed"
            ),
        },
        "validation_status": "experimental",
        "diagnostic_only": True,
        "coaching_eligible": False,
        "coaching_admission": {
            "status": "not_admitted",
            "eligible": False,
            "reasons": ["upstream_evidence_gate_failed"],
        },
        "action": None,
        "source": dict(_mapping(ranking.get("source")) or {}) if ranking else None,
        "gate_reasons": visible,
        "gate_reasons_omitted_count": max(0, len(unique) - len(visible)),
        "regions": [],
        "limits": {
            "maximum_region_assessments": MAX_DRIVING_PATTERN_ASSESSMENTS,
            "maximum_reasons_per_region": MAX_REASONS_PER_ASSESSMENT,
            "maximum_gate_reasons": MAX_GATE_REASONS,
        },
    }


def _no_ranked_candidates(ranking: Mapping[str, Any]) -> dict[str, object]:
    result = _abstained(["no_ranked_candidates"], ranking)
    result["status"] = "no_ranked_candidates"
    result["gate_reasons"] = []
    result["gate_reasons_omitted_count"] = 0
    result["coaching_admission"] = {
        "status": "not_admitted",
        "eligible": False,
        "reasons": ["no_ranked_candidates"],
    }
    return result
