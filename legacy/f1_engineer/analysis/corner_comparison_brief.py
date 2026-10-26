from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


CORNER_COMPARISON_BRIEF_VERSION = "corner-comparison-brief-v1"
MAX_CORNER_BRIEF_REGIONS = 3
MAX_CORNER_BRIEF_FACTS_PER_REGION = 5
MAX_CORNER_BRIEF_REASONS_PER_REGION = 4
MAX_CORNER_BRIEF_GATE_REASONS = 16
EXPECTED_RANKING_ANALYSIS_VERSION = "corner-loss-candidates-v1"
EXPECTED_RANKING_POLICY_VERSION = "tt-session-best-connected-regions-v1"
EXPECTED_REFERENCE_POLICY_VERSION = "tt-session-best-v2-lifecycle"

_REQUIRED_CONNECTED_FIELDS = (
    "target_resampled_time_connected",
    "reference_resampled_time_connected",
    "shared_delta_time_connected",
    "target_source_session_time_connected",
    "reference_source_session_time_connected",
    "interval_connected_supported_time",
)
_EXPECTED_THRESHOLDS = {"braking": 0.1, "throttle_50": 0.5}


def build_corner_comparison_brief(
    comparison: Mapping[str, object],
) -> dict[str, object]:
    """Summarize only D0032-ranked, provenance-matched corner measurements."""
    ranking = _mapping(comparison.get("corner_loss_candidates"))
    gates = validate_corner_ranking_source(comparison)
    if gates:
        upstream_omitted = (
            _nonnegative_integer(ranking.get("gate_reasons_omitted_count"))
            if ranking is not None
            else 0
        )
        return _abstained(gates, upstream_omitted=upstream_omitted or 0)
    assert ranking is not None

    candidates = ranking.get("ranked_candidates")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        return _abstained(["ranked_candidates_unavailable"])
    if not candidates:
        return {
            "schema_version": 1,
            "analysis_version": CORNER_COMPARISON_BRIEF_VERSION,
            "status": "no_ranked_candidates",
            "coaching_eligible": False,
            "text": "No ranked corner measurements are available to summarize.",
            "regions": [],
            "gate_reasons": [],
            "gate_reasons_omitted_count": 0,
            "omitted_region_count": 0,
            "limits": _limits(),
        }
    if len(candidates) > MAX_CORNER_BRIEF_REGIONS:
        return _abstained(["ranked_candidate_limit_exceeded"])

    analysis = _mapping(comparison.get("corner_analysis"))
    assert analysis is not None
    raw_regions = analysis.get("regions")
    if not isinstance(raw_regions, Sequence) or isinstance(raw_regions, (str, bytes)):
        return _abstained(["corner_region_evidence_unavailable"])
    if len(raw_regions) > 64:
        return _abstained(["corner_region_evidence_limit_exceeded"])

    provenance = _provenance(ranking)
    region_summaries: list[dict[str, object]] = []
    for index, raw_candidate in enumerate(candidates):
        candidate = _mapping(raw_candidate)
        if candidate is None:
            return _abstained(["ranked_candidate_invalid"])
        region, reason = _matching_region(candidate, raw_regions, index)
        if region is None:
            return _abstained([reason or "ranked_region_evidence_mismatch"])

        interval_fact = _interval_fact(candidate, provenance)
        facts = [interval_fact]
        omitted: list[dict[str, str]] = []
        for metric, result in (
            ("braking", _threshold_fact(region, "braking", provenance)),
            ("minimum_speed", _minimum_speed_fact(region, provenance)),
            ("throttle_50", _threshold_fact(region, "throttle_50", provenance)),
            ("exit_speed", _exit_speed_fact(region, provenance)),
        ):
            fact, reason_code = result
            if fact is not None:
                facts.append(fact)
            else:
                omitted.append({"metric": metric, "reason": reason_code})

        region_summaries.append(
            {
                "rank": index + 1,
                "region_id": candidate["region_id"],
                "region_label": candidate["region_label"],
                "analysis_window_m": list(candidate["analysis_window_m"]),
                "facts": facts[:MAX_CORNER_BRIEF_FACTS_PER_REGION],
                "omitted_measurements": omitted[:MAX_CORNER_BRIEF_REASONS_PER_REGION],
                "omitted_measurement_count": len(omitted),
                "connected_support": dict(candidate["connected_support"]),
                "provenance": provenance,
            }
        )

    text = " ".join(
        fact["text"]
        for region in region_summaries
        for fact in region["facts"]
        if isinstance(fact, Mapping) and isinstance(fact.get("text"), str)
    )
    if not text:
        text = "No supported corner measurements are available to summarize."
    return {
        "schema_version": 1,
        "analysis_version": CORNER_COMPARISON_BRIEF_VERSION,
        "status": "available",
        "coaching_eligible": False,
        "text": text,
        "regions": region_summaries,
        "gate_reasons": [],
        "gate_reasons_omitted_count": 0,
        "omitted_region_count": _nonnegative_integer(ranking.get("omitted_candidate_count")) or 0,
        "limits": _limits(),
    }


def validate_corner_ranking_source(comparison: Mapping[str, object]) -> list[str]:
    """Return D0033's pure authority/provenance gates for the existing D0032 result."""
    return _validate_ranking_source(
        comparison, _mapping(comparison.get("corner_loss_candidates"))
    )


def _validate_ranking_source(
    comparison: Mapping[str, object], ranking: Mapping[str, Any] | None
) -> list[str]:
    if comparison.get("comparison_policy") != "time_trial":
        return ["unsupported_comparison_policy"]
    if ranking is None:
        return ["corner_ranking_unavailable"]
    if ranking.get("status") != "ranked":
        reasons = _strings(ranking.get("gate_reasons"))[:MAX_CORNER_BRIEF_GATE_REASONS]
        if reasons:
            return reasons
        return [
            "corner_ranking_abstained"
            if ranking.get("status") == "abstained"
            else "no_ranked_corner_candidates"
        ]
    if (
        ranking.get("analysis_version") != EXPECTED_RANKING_ANALYSIS_VERSION
        or ranking.get("policy_version") != EXPECTED_RANKING_POLICY_VERSION
    ):
        return ["corner_ranking_version_unsupported"]
    if ranking.get("coaching_eligible") is not False:
        return ["corner_ranking_coaching_gate_invalid"]

    source = _mapping(ranking.get("source"))
    target = _mapping(comparison.get("target"))
    reference = _mapping(comparison.get("reference"))
    analysis = _mapping(comparison.get("corner_analysis"))
    analysis_source = _mapping(analysis.get("source")) if analysis else None
    if source is None or target is None or reference is None or analysis_source is None:
        return ["corner_ranking_provenance_unavailable"]
    for side, attempt in (("target", target), ("reference", reference)):
        identity = _mapping(source.get(side))
        trace_identity = _mapping(analysis_source.get(side))
        if identity is None or trace_identity is None:
            return [f"{side}_corner_provenance_unavailable"]
        for field in ("attempt_key", "run_id", "trace_sha256"):
            if identity.get(field) != attempt.get(field) or trace_identity.get(field) != attempt.get(field):
                return [f"{side}_corner_provenance_mismatch"]

    model = _mapping(source.get("model"))
    analysis_model = _mapping(analysis.get("model"))
    if (
        model is None
        or analysis_model is None
        or model.get("validation_status") != "validated"
        or model.get("registered") is not True
        or model.get("approved_for_candidate_ranking") is not True
        or not _sha256(model.get("model_content_sha256"))
        or not _mapping(model.get("approval_provenance"))
        or model.get("model_id") != analysis_model.get("model_id")
        or model.get("revision") != analysis_model.get("revision")
        or analysis_model.get("validation_status") != "validated"
    ):
        return ["corner_model_approval_provenance_invalid"]

    selection = _mapping(source.get("reference_selection"))
    selected = _mapping(selection.get("selected_reference")) if selection else None
    if (
        source.get("capture_evidence") != "passed_session_best_policy"
        or selection is None
        or selection.get("reference_kind") != "session_best"
        or selection.get("status") != "selected"
        or selection.get("policy_version") != EXPECTED_REFERENCE_POLICY_VERSION
        or _strings(selection.get("reasons"))
        or selected is None
        or selected.get("attempt_key") != reference.get("attempt_key")
        or selected.get("trace_sha256") != reference.get("trace_sha256")
    ):
        return ["session_best_provenance_invalid"]
    if analysis.get("diagnostic_only") is not False:
        return ["corner_analysis_diagnostic_only"]
    return []


def _matching_region(
    candidate: Mapping[str, Any],
    regions: Sequence[object],
    index: int,
) -> tuple[Mapping[str, Any] | None, str | None]:
    identifier = candidate.get("region_id")
    label = candidate.get("region_label")
    window = _bounds(candidate.get("analysis_window_m"))
    connected = _mapping(candidate.get("connected_support"))
    if (
        not _nonempty(identifier)
        or not _nonempty(label)
        or window is None
        or connected is None
        or any(connected.get(field) is not True for field in _REQUIRED_CONNECTED_FIELDS)
        or not _positive_integer(candidate.get("rank"))
        or candidate.get("rank") != index + 1
    ):
        return None, "ranked_candidate_evidence_invalid"

    matches = [
        region
        for region in regions
        if isinstance(region, Mapping) and region.get("identifier") == identifier
    ]
    if len(matches) != 1:
        return None, "ranked_region_identity_ambiguous"
    region = matches[0]
    delta = _mapping(region.get("delta_change"))
    if (
        region.get("label") != label
        or _bounds(region.get("analysis_window_m")) != window
        or region.get("diagnostic_only") is not False
        or delta is None
        or delta.get("status") != "supported_region_delta_change"
        or any(delta.get(field) is not True for field in _REQUIRED_CONNECTED_FIELDS)
    ):
        return None, "ranked_region_support_mismatch"
    difference = _finite_number(candidate.get("recorded_time_difference_s"))
    source_difference = _finite_number(delta.get("delta_change_s"))
    if difference is None or difference <= 0 or source_difference != difference:
        return None, "ranked_region_difference_mismatch"
    for field in (
        "target_time_coverage",
        "reference_time_coverage",
        "shared_time_coverage",
        "entry_delta_s",
        "exit_delta_s",
    ):
        candidate_value = _finite_number(connected.get(field))
        region_value = _finite_number(delta.get(field))
        if candidate_value is None or region_value != candidate_value:
            return None, "ranked_region_support_mismatch"
    if any(
        not 0.0 <= float(connected[field]) <= 1.0
        for field in (
            "target_time_coverage",
            "reference_time_coverage",
            "shared_time_coverage",
        )
    ):
        return None, "ranked_region_support_mismatch"
    return region, None


def _interval_fact(
    candidate: Mapping[str, Any], provenance: Mapping[str, object]
) -> dict[str, object]:
    value = float(candidate["recorded_time_difference_s"])
    start, end = candidate["analysis_window_m"]
    return {
        "kind": "recorded_interval_time_difference",
        "label": "Recorded interval time difference",
        "value": value,
        "unit": "s",
        "direction": "target_minus_reference",
        "analysis_window_m": [start, end],
        "connected_support": dict(candidate["connected_support"]),
        "boundary_delta_evidence": {
            "entry_target_minus_reference_s": candidate["connected_support"]["entry_delta_s"],
            "exit_target_minus_reference_s": candidate["connected_support"]["exit_delta_s"],
        },
        "source_fields": {
            "entry_delta": "corner_analysis.regions[].delta_change.entry_delta_s",
            "exit_delta": "corner_analysis.regions[].delta_change.exit_delta_s",
            "derived_difference": "corner_analysis.regions[].delta_change.delta_change_s",
        },
        "provenance": provenance,
        "text": (
            f"{candidate['region_label']}: the target recorded {value:.3f} s more interval time "
            f"than the selected reference across [{start:g}, {end:g}) m, with connected support."
        ),
    }


def _threshold_fact(
    region: Mapping[str, Any], metric: str, provenance: Mapping[str, object]
) -> tuple[dict[str, object] | None, str]:
    if metric == "braking":
        channel = "brake"
        path_name = "braking"
        event_name = "brake_onset"
        title = "Brake-threshold onset"
        threshold = _EXPECTED_THRESHOLDS[metric]
    else:
        channel = "throttle"
        path_name = "throttle_pickup"
        event_name = "throttle_0.5_onset"
        title = "50% throttle-threshold onset"
        threshold = _EXPECTED_THRESHOLDS[metric]

    sides: dict[str, dict[str, object]] = {}
    for side in ("target", "reference"):
        attempt = _mapping(region.get(side)) or {}
        if metric == "braking":
            event = _mapping(attempt.get(path_name))
        else:
            throttle_pickup = _mapping(attempt.get("throttle_pickup")) or {}
            event = _mapping(throttle_pickup.get("0.5"))
        coverage = _mapping(attempt.get("event_channel_coverage")) or {}
        if event is None or _finite_number(coverage.get(channel)) != 1.0:
            return None, "channel_coverage_incomplete"
        events = event.get("events")
        if (
            event.get("status") != "detected"
            or event.get("event_name") != event_name
            or _nonnegative_integer(event.get("event_count")) != 1
            or event.get("events_truncated") is not False
            or not isinstance(events, Sequence)
            or isinstance(events, (str, bytes))
            or len(events) != 1
            or _nonnegative_integer(event.get("rejected_short_event_count")) != 0
            or _nonnegative_integer(event.get("unsupported_break_count")) != 0
        ):
            return None, "threshold_event_not_unique_or_supported"
        observed = _mapping(events[0])
        if observed is None:
            return None, "threshold_onset_bracket_unavailable"
        distance = _finite_number(event.get("distance_m"))
        bracket = _bounds(observed.get("start_distance_bracket_m"))
        if (
            observed.get("left_censored") is not False
            or observed.get("channel") != channel
            or _finite_number(observed.get("threshold")) != threshold
            or distance is None
            or bracket is None
            or not bracket[0] <= distance <= bracket[1]
        ):
            return None, "threshold_onset_bracket_unavailable"
        sides[side] = {
            "distance_m": distance,
            "bracket_m": [bracket[0], bracket[1]],
            "threshold": threshold,
        }

    target = sides["target"]
    reference = sides["reference"]
    value = float(target["distance_m"]) - float(reference["distance_m"])
    target_bracket = target["bracket_m"]
    reference_bracket = reference["bracket_m"]
    lower = float(target_bracket[0]) - float(reference_bracket[1])
    upper = float(target_bracket[1]) - float(reference_bracket[0])
    if lower > 0:
        direction_text = "farther along"
        bound_text = f"brackets span +{lower:.1f} to +{upper:.1f} m"
    elif upper < 0:
        direction_text = "earlier along"
        bound_text = f"brackets span {lower:.1f} to {upper:.1f} m"
    else:
        direction_text = "with overlapping onset brackets"
        bound_text = f"brackets span {lower:.1f} to +{upper:.1f} m"
    return (
        {
            "kind": f"{metric}_threshold_onset_difference",
            "label": title,
            "value": value,
            "unit": "m",
            "direction": "target_minus_reference",
            "threshold": threshold,
            "threshold_percent": threshold * 100,
            "target": target,
            "reference": reference,
            "difference_bounds_m": [lower, upper],
            "source_fields": {
                "target": f"corner_analysis.regions[].target.{path_name}",
                "reference": f"corner_analysis.regions[].reference.{path_name}",
                "target_channel_coverage": f"corner_analysis.regions[].target.event_channel_coverage.{channel}",
                "reference_channel_coverage": f"corner_analysis.regions[].reference.event_channel_coverage.{channel}",
            },
            "provenance": provenance,
            "text": (
                f"{title} was estimated {abs(value):.1f} m {direction_text} on the target "
                f"at the {threshold * 100:g}% input threshold; {bound_text}."
            ),
        },
        "",
    )


def _minimum_speed_fact(
    region: Mapping[str, Any], provenance: Mapping[str, object]
) -> tuple[dict[str, object] | None, str]:
    window = _bounds(region.get("analysis_window_m"))
    if window is None:
        return None, "minimum_speed_window_unavailable"
    sides: dict[str, dict[str, object]] = {}
    for side in ("target", "reference"):
        attempt = _mapping(region.get(side)) or {}
        minimum = _mapping(attempt.get("minimum_speed")) or {}
        coverage = _finite_number(minimum.get("supported_grid_coverage"))
        speed = _finite_number(minimum.get("speed_kph"))
        distance = _finite_number(minimum.get("distance_m"))
        if (
            minimum.get("status") != "observed_minimum_complete_window"
            or coverage != 1.0
            or speed is None
            or speed < 0
            or distance is None
            or not window[0] <= distance < window[1]
        ):
            return None, "minimum_speed_window_or_observation_incomplete"
        sides[side] = {"speed_kph": speed, "distance_m": distance, "coverage": coverage}
    value = float(sides["target"]["speed_kph"]) - float(sides["reference"]["speed_kph"])
    descriptor = "higher" if value > 0 else "lower" if value < 0 else "equal"
    return (
        {
            "kind": "observed_minimum_speed_difference",
            "label": "Observed minimum speed",
            "value": value,
            "unit": "km/h",
            "direction": "target_minus_reference",
            "target": sides["target"],
            "reference": sides["reference"],
            "source_fields": {
                "target": "corner_analysis.regions[].target.minimum_speed",
                "reference": "corner_analysis.regions[].reference.minimum_speed",
            },
            "provenance": provenance,
            "text": (
                f"Observed minimum speed was {abs(value):.1f} km/h {descriptor} on the target "
                f"within the fully supported region window."
            ),
        },
        "",
    )


def _exit_speed_fact(
    region: Mapping[str, Any], provenance: Mapping[str, object]
) -> tuple[dict[str, object] | None, str]:
    sides: dict[str, dict[str, object]] = {}
    for side in ("target", "reference"):
        attempt = _mapping(region.get(side)) or {}
        observations = attempt.get("exit_speeds")
        if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
            return None, "configured_exit_anchor_unavailable"
        anchors = [
            _mapping(item)
            for item in observations
            if isinstance(item, Mapping) and _finite_number(item.get("offset_m")) == 0.0
        ]
        if len(anchors) != 1:
            return None, "configured_exit_anchor_unavailable"
        anchor = anchors[0]
        assert anchor is not None
        distance = _finite_number(anchor.get("distance_m"))
        speed = _finite_number(anchor.get("speed_kph"))
        if anchor.get("status") != "supported" or distance is None or speed is None or speed < 0:
            return None, "configured_exit_speed_unsupported"
        sides[side] = {"distance_m": distance, "speed_kph": speed, "offset_m": 0.0}
    if not math.isclose(
        float(sides["target"]["distance_m"]),
        float(sides["reference"]["distance_m"]),
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        return None, "configured_exit_anchors_do_not_match"
    value = float(sides["target"]["speed_kph"]) - float(sides["reference"]["speed_kph"])
    descriptor = "higher" if value > 0 else "lower" if value < 0 else "equal"
    distance = float(sides["target"]["distance_m"])
    return (
        {
            "kind": "configured_exit_anchor_speed_difference",
            "label": "Speed at configured exit anchor",
            "value": value,
            "unit": "km/h",
            "direction": "target_minus_reference",
            "distance_m": distance,
            "target": sides["target"],
            "reference": sides["reference"],
            "source_fields": {
                "target": "corner_analysis.regions[].target.exit_speeds[offset_m=0]",
                "reference": "corner_analysis.regions[].reference.exit_speeds[offset_m=0]",
            },
            "provenance": provenance,
            "text": (
                f"At the configured exit anchor ({distance:g} m), target speed was "
                f"{abs(value):.1f} km/h {descriptor} than reference."
            ),
        },
        "",
    )


def _provenance(ranking: Mapping[str, Any]) -> dict[str, object]:
    source = _mapping(ranking.get("source")) or {}
    return {
        "target": dict(_mapping(source.get("target")) or {}),
        "reference": dict(_mapping(source.get("reference")) or {}),
        "model": dict(_mapping(source.get("model")) or {}),
        "reference_selection": dict(_mapping(source.get("reference_selection")) or {}),
    }


def _abstained(
    reasons: Sequence[str], *, upstream_omitted: int = 0
) -> dict[str, object]:
    unique = list(dict.fromkeys(reason for reason in reasons if isinstance(reason, str)))
    visible = unique[:MAX_CORNER_BRIEF_GATE_REASONS]
    text = (
        "Corner comparison brief abstained because ranked measurements did not pass "
        "the evidence gates."
    )
    return {
        "schema_version": 1,
        "analysis_version": CORNER_COMPARISON_BRIEF_VERSION,
        "status": "abstained",
        "coaching_eligible": False,
        "text": text,
        "regions": [],
        "gate_reasons": visible,
        "gate_reasons_omitted_count": max(0, len(unique) - len(visible)) + upstream_omitted,
        "omitted_region_count": 0,
        "limits": _limits(),
    }


def _limits() -> dict[str, int]:
    return {
        "maximum_regions": MAX_CORNER_BRIEF_REGIONS,
        "maximum_facts_per_region": MAX_CORNER_BRIEF_FACTS_PER_REGION,
        "maximum_reasons_per_region": MAX_CORNER_BRIEF_REASONS_PER_REGION,
        "maximum_gate_reasons": MAX_CORNER_BRIEF_GATE_REASONS,
    }


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _strings(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, str)]


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
    if lower is None or upper is None or lower > upper:
        return None
    return lower, upper


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonnegative_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True
