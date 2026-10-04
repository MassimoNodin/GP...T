from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .corner_comparison_brief import validate_corner_ranking_source


DRIVING_PATTERN_ASSESSMENT_SCHEMA_VERSION = 1
DRIVING_PATTERN_ASSESSMENT_VERSION = "driving-pattern-assessment-v1"
DRIVING_PATTERN_RULE_ID = "earlier-braking-lower-minimum-speed-no-exit-advantage"
DRIVING_PATTERN_RULE_VERSION = 1
MAX_DRIVING_PATTERN_ASSESSMENTS = 3
MAX_REASONS_PER_ASSESSMENT = 8
MAX_GATE_REASONS = 16
_EXPECTED_CORNER_BRIEF_VERSION = "corner-comparison-brief-v1"
_EXPECTED_RANKING_VERSION = "corner-loss-candidates-v1"
_REQUIRED_CONNECTED_FIELDS = (
    "target_resampled_time_connected",
    "reference_resampled_time_connected",
    "shared_delta_time_connected",
    "target_source_session_time_connected",
    "reference_source_session_time_connected",
    "interval_connected_supported_time",
)
_REQUIRED_COVERAGE_FIELDS = (
    "target_time_coverage",
    "reference_time_coverage",
    "shared_time_coverage",
)


def build_driving_pattern_assessment(
    comparison: Mapping[str, object],
) -> dict[str, object]:
    """Assess one bounded braking pattern using only authorized comparison output."""
    ranking = _mapping(comparison.get("corner_loss_candidates"))
    gate_reasons = validate_corner_ranking_source(comparison)
    if ranking is None:
        gate_reasons = ["corner_ranking_unavailable"]
    elif ranking.get("analysis_version") != _EXPECTED_RANKING_VERSION:
        gate_reasons.append("corner_ranking_version_unsupported")
    elif (
        ranking.get("status") == "no_positive_supported_differences"
        and gate_reasons == ["no_ranked_corner_candidates"]
        and ranking.get("ranked_candidates") == []
    ):
        return _empty(ranking)

    if gate_reasons:
        return _abstained(gate_reasons, ranking)
    assert ranking is not None

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
    if not isinstance(brief_regions, Sequence) or isinstance(
        brief_regions, (str, bytes)
    ):
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
        "schema_version": DRIVING_PATTERN_ASSESSMENT_SCHEMA_VERSION,
        "analysis_version": DRIVING_PATTERN_ASSESSMENT_VERSION,
        "status": "available",
        "rule": {
            "id": DRIVING_PATTERN_RULE_ID,
            "version": DRIVING_PATTERN_RULE_VERSION,
            "description": (
                "Earlier brake onset, lower minimum speed, and no observed exit-speed advantage"
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

    interval, reason = _fact(facts, region, "recorded_interval_time_difference")
    candidate_difference = _finite_number(candidate.get("recorded_time_difference_s"))
    connected_support = _mapping(candidate.get("connected_support"))
    interval_boundaries = _mapping(interval.get("boundary_delta_evidence")) if interval else None
    entry_delta = _finite_number(connected_support.get("entry_delta_s")) if connected_support else None
    exit_delta = _finite_number(connected_support.get("exit_delta_s")) if connected_support else None
    if (
        not _valid_fact(
            interval,
            kind="recorded_interval_time_difference",
            unit="s",
            provenance=expected_provenance,
        )
        or interval is None
        or _finite_number(interval.get("value")) != candidate_difference
        or _bounds(interval.get("analysis_window_m"))
        != _bounds(candidate.get("analysis_window_m"))
        or interval.get("connected_support") != candidate.get("connected_support")
        or interval_boundaries is None
        or entry_delta is None
        or exit_delta is None
        or not _close(interval_boundaries.get("entry_target_minus_reference_s"), entry_delta)
        or not _close(interval_boundaries.get("exit_target_minus_reference_s"), exit_delta)
        or candidate_difference is None
        or not _close(candidate_difference, exit_delta - entry_delta)
        or candidate_difference <= 0
    ):
        unavailable = True
        reasons.append(reason or "recorded_interval_time_evidence_invalid")

    brake, reason = _fact(facts, region, "braking_threshold_onset_difference")
    brake_evidence = _brake_evidence(brake, expected_provenance)
    if brake_evidence is None:
        unavailable = True
        reasons.append(reason or "brake_onset_evidence_invalid")

    minimum, reason = _fact(facts, region, "observed_minimum_speed_difference")
    minimum_evidence = _minimum_speed_evidence(minimum, candidate, expected_provenance)
    if minimum_evidence is None:
        unavailable = True
        reasons.append(reason or "minimum_speed_evidence_invalid")

    exit_speed, reason = _fact(facts, region, "configured_exit_anchor_speed_difference")
    exit_evidence = _exit_speed_evidence(exit_speed, expected_provenance)
    if exit_evidence is None:
        unavailable = True
        reasons.append(reason or "exit_speed_evidence_invalid")

    contradictions: list[str] = []
    if brake_evidence is not None:
        lower, upper = brake_evidence["target_minus_reference_bounds_m"]
        if upper >= 0:
            if lower < 0:
                unavailable = True
                reasons.append("brake_onset_order_unresolved")
            else:
                contradictions.append("target_brake_onset_not_earlier")
    if minimum_evidence is not None and minimum_evidence["target_minus_reference_kph"] >= 0:
        contradictions.append("target_minimum_speed_not_lower")
    if exit_evidence is not None and exit_evidence["target_minus_reference_kph"] > 0:
        contradictions.append("target_exit_speed_advantage_observed")

    reasons.extend(contradictions)
    if unavailable:
        status = "unavailable"
    elif contradictions:
        status = "contradicted"
    else:
        status = "matched"

    evidence: dict[str, object] = {}
    if candidate_difference is not None:
        evidence["recorded_interval_time_difference_s"] = candidate_difference
    if brake_evidence is not None:
        evidence["brake_onset"] = brake_evidence
    if minimum_evidence is not None:
        evidence["minimum_speed"] = minimum_evidence
    if exit_evidence is not None:
        evidence["exit_speed"] = exit_evidence

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


def _brake_evidence(
    fact: Mapping[str, Any] | None,
    expected_provenance: Mapping[str, object],
) -> dict[str, object] | None:
    if not _valid_fact(
        fact,
        kind="braking_threshold_onset_difference",
        unit="m",
        provenance=expected_provenance,
    ):
        return None
    assert fact is not None
    target = _mapping(fact.get("target"))
    reference = _mapping(fact.get("reference"))
    target_bracket = _bounds(target.get("bracket_m")) if target else None
    reference_bracket = _bounds(reference.get("bracket_m")) if reference else None
    difference_bounds = _bounds(fact.get("difference_bounds_m"))
    target_distance = _finite_number(target.get("distance_m")) if target else None
    reference_distance = _finite_number(reference.get("distance_m")) if reference else None
    if (
        target_bracket is None
        or reference_bracket is None
        or difference_bounds is None
        or target_distance is None
        or reference_distance is None
        or not _close(target.get("threshold"), 0.1)
        or not _close(reference.get("threshold"), 0.1)
        or not target_bracket[0] <= target_distance <= target_bracket[1]
        or not reference_bracket[0] <= reference_distance <= reference_bracket[1]
        or not _close(difference_bounds[0], target_bracket[0] - reference_bracket[1])
        or not _close(difference_bounds[1], target_bracket[1] - reference_bracket[0])
    ):
        return None
    return {
        "threshold_percent": 10,
        "target_bracket_m": list(target_bracket),
        "reference_bracket_m": list(reference_bracket),
        "target_minus_reference_bounds_m": list(difference_bounds),
    }


def _minimum_speed_evidence(
    fact: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
    expected_provenance: Mapping[str, object],
) -> dict[str, object] | None:
    if not _valid_fact(
        fact,
        kind="observed_minimum_speed_difference",
        unit="km/h",
        provenance=expected_provenance,
    ):
        return None
    assert fact is not None
    target = _mapping(fact.get("target"))
    reference = _mapping(fact.get("reference"))
    window = _bounds(candidate.get("analysis_window_m"))
    target_speed = _finite_number(target.get("speed_kph")) if target else None
    reference_speed = _finite_number(reference.get("speed_kph")) if reference else None
    target_distance = _finite_number(target.get("distance_m")) if target else None
    reference_distance = _finite_number(reference.get("distance_m")) if reference else None
    value = _finite_number(fact.get("value"))
    if (
        target_speed is None
        or reference_speed is None
        or target_speed < 0
        or reference_speed < 0
        or target_distance is None
        or reference_distance is None
        or window is None
        or not window[0] <= target_distance < window[1]
        or not window[0] <= reference_distance < window[1]
        or not _close(target.get("coverage"), 1.0)
        or not _close(reference.get("coverage"), 1.0)
        or value is None
        or not _close(value, target_speed - reference_speed)
    ):
        return None
    return {
        "target_speed_kph": target_speed,
        "reference_speed_kph": reference_speed,
        "target_minus_reference_kph": value,
        "coverage": 1.0,
    }


def _exit_speed_evidence(
    fact: Mapping[str, Any] | None,
    expected_provenance: Mapping[str, object],
) -> dict[str, object] | None:
    if not _valid_fact(
        fact,
        kind="configured_exit_anchor_speed_difference",
        unit="km/h",
        provenance=expected_provenance,
    ):
        return None
    assert fact is not None
    target = _mapping(fact.get("target"))
    reference = _mapping(fact.get("reference"))
    target_speed = _finite_number(target.get("speed_kph")) if target else None
    reference_speed = _finite_number(reference.get("speed_kph")) if reference else None
    target_distance = _finite_number(target.get("distance_m")) if target else None
    reference_distance = _finite_number(reference.get("distance_m")) if reference else None
    value = _finite_number(fact.get("value"))
    distance = _finite_number(fact.get("distance_m"))
    if (
        target_speed is None
        or reference_speed is None
        or target_speed < 0
        or reference_speed < 0
        or target_distance is None
        or reference_distance is None
        or not _close(target_distance, reference_distance)
        or not _close(target.get("offset_m"), 0.0)
        or not _close(reference.get("offset_m"), 0.0)
        or distance is None
        or not _close(distance, target_distance)
        or value is None
        or not _close(value, target_speed - reference_speed)
    ):
        return None
    return {
        "distance_m": distance,
        "target_speed_kph": target_speed,
        "reference_speed_kph": reference_speed,
        "target_minus_reference_kph": value,
    }


def _matching_region(
    candidate: Mapping[str, Any],
    region: Mapping[str, Any],
    rank: int,
    expected_provenance: Mapping[str, object],
) -> bool:
    connected_support = _mapping(candidate.get("connected_support"))
    if (
        region.get("rank") != rank
        or region.get("region_id") != candidate.get("region_id")
        or region.get("region_label") != candidate.get("region_label")
        or _bounds(region.get("analysis_window_m"))
        != _bounds(candidate.get("analysis_window_m"))
        or _mapping(region.get("provenance")) != expected_provenance
        or region.get("connected_support") != candidate.get("connected_support")
        or connected_support is None
        or any(connected_support.get(field) is not True for field in _REQUIRED_CONNECTED_FIELDS)
    ):
        return False
    for field in _REQUIRED_COVERAGE_FIELDS:
        coverage = _finite_number(connected_support.get(field))
        if coverage is None or not 0.0 <= coverage <= 1.0:
            return False
    return True


def _valid_fact(
    fact: Mapping[str, Any] | None,
    *,
    kind: str,
    unit: str,
    provenance: Mapping[str, object],
) -> bool:
    return bool(
        fact is not None
        and fact.get("kind") == kind
        and fact.get("unit") == unit
        and fact.get("direction") == "target_minus_reference"
        and _mapping(fact.get("provenance")) == provenance
    )


def _facts(region: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw = region.get("facts")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    duplicate_kinds: set[str] = set()
    for item in raw:
        fact = _mapping(item)
        kind = fact.get("kind") if fact else None
        if isinstance(kind, str):
            if kind in result:
                duplicate_kinds.add(kind)
            result[kind] = fact  # type: ignore[assignment]
    for kind in duplicate_kinds:
        result.pop(kind, None)
    return result


def _fact(
    facts: Mapping[str, Mapping[str, Any]],
    region: Mapping[str, Any],
    kind: str,
) -> tuple[Mapping[str, Any] | None, str | None]:
    fact = facts.get(kind)
    if fact is not None:
        return fact, None
    omission_metric = {
        "recorded_interval_time_difference": "connected_interval_time",
        "braking_threshold_onset_difference": "braking",
        "observed_minimum_speed_difference": "minimum_speed",
        "configured_exit_anchor_speed_difference": "exit_speed",
    }[kind]
    reasons = _omission_reasons(region, omission_metric)
    return (
        None,
        f"{omission_metric}_measurement_omitted:{reasons[0]}"
        if reasons
        else f"{omission_metric}_measurement_omitted",
    )


def _omission_reasons(region: Mapping[str, Any], metric: str | None = None) -> list[str]:
    raw = region.get("omitted_measurements")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    result: list[str] = []
    for item in raw:
        omission = _mapping(item)
        if omission is None or (metric is not None and omission.get("metric") != metric):
            continue
        reason = omission.get("reason")
        if isinstance(reason, str) and reason:
            result.append(reason[:80])
    return result


def _expected_provenance(ranking: Mapping[str, Any]) -> dict[str, object]:
    source = _mapping(ranking.get("source")) or {}
    return {
        "target": dict(_mapping(source.get("target")) or {}),
        "reference": dict(_mapping(source.get("reference")) or {}),
        "model": dict(_mapping(source.get("model")) or {}),
        "reference_selection": dict(
            _mapping(source.get("reference_selection")) or {}
        ),
    }


def _abstained(
    reasons: Sequence[str], ranking: Mapping[str, Any] | None
) -> dict[str, object]:
    unique = list(dict.fromkeys(reason for reason in reasons if isinstance(reason, str)))
    visible = unique[:MAX_GATE_REASONS]
    return {
        "schema_version": DRIVING_PATTERN_ASSESSMENT_SCHEMA_VERSION,
        "analysis_version": DRIVING_PATTERN_ASSESSMENT_VERSION,
        "status": "abstained",
        "rule": {
            "id": DRIVING_PATTERN_RULE_ID,
            "version": DRIVING_PATTERN_RULE_VERSION,
            "description": (
                "Earlier brake onset, lower minimum speed, and no observed exit-speed advantage"
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


def _empty(ranking: Mapping[str, Any]) -> dict[str, object]:
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


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _bounds(value: object) -> tuple[float, float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        return None
    lower = _finite_number(value[0])
    upper = _finite_number(value[1])
    return (lower, upper) if lower is not None and upper is not None and lower <= upper else None


def _close(value: object, expected: float) -> bool:
    number = _finite_number(value)
    return number is not None and math.isclose(
        number, expected, rel_tol=0.0, abs_tol=1e-6
    )
