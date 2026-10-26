from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .corner_comparison_brief import validate_corner_ranking_source


LAP_DEBRIEF_VERSION = "lap-debrief-v1"
MAX_LAP_DEBRIEF_REGIONS = 3
MAX_LAP_DEBRIEF_LIMITATIONS = 8
MAX_LAP_DEBRIEF_TEXT_CHARACTERS = 2_400
_EXPECTED_COMPARISON_BRIEF_VERSION = "comparison-brief-v1"
_EXPECTED_CORNER_BRIEF_VERSION = "corner-comparison-brief-v1"
_EXPECTED_RANKING_VERSION = "corner-loss-candidates-v1"
_EXPECTED_RANKING_POLICY_VERSION = "tt-session-best-connected-regions-v1"


def build_lap_debrief(comparison: Mapping[str, object]) -> dict[str, object]:
    """Compose a bounded debrief from one already-computed comparison result."""
    limitations: list[dict[str, str]] = []
    seen_codes: set[str] = set()

    def add_limitation(code: str, text: str) -> None:
        if code in seen_codes:
            return
        seen_codes.add(code)
        limitations.append({"code": code, "text": _bounded_text(text, 280)})

    comparison_brief = _mapping(comparison.get("comparison_brief"))
    official_lap_time = _official_lap_time_fact(comparison, comparison_brief)
    if official_lap_time is None:
        add_limitation(
            "official_lap_time_unavailable",
            "A consistent official lap-time difference could not be verified from both completed attempts.",
        )

    region_result = _authorized_region_summaries(comparison)
    regions = region_result["regions"]
    if region_result["limitation"] is not None:
        code, text = region_result["limitation"]
        add_limitation(code, text)

    if comparison_brief is None:
        add_limitation(
            "comparison_brief_unavailable",
            "The source comparison brief is unavailable; no lap-time fact was added.",
        )
    else:
        raw_limitations = comparison_brief.get("limitations")
        if _sequence(raw_limitations):
            for item in raw_limitations[:32]:
                limitation = _mapping(item)
                if limitation is None:
                    continue
                code = limitation.get("code")
                text = limitation.get("text")
                if isinstance(code, str) and _nonempty_string(text):
                    add_limitation(_safe_code(code), text)

    limitations.sort(
        key=lambda item: (_LIMITATION_PRIORITY.get(item["code"], 50), item["code"])
    )
    omitted_limitations = max(0, len(limitations) - MAX_LAP_DEBRIEF_LIMITATIONS)
    visible_limitations = limitations[:MAX_LAP_DEBRIEF_LIMITATIONS]
    if comparison_brief is not None:
        brief_limits = _mapping(comparison_brief.get("limits"))
        if brief_limits is not None:
            omitted_limitations += _nonnegative_integer(
                brief_limits.get("omitted_limitation_count")
            ) or 0

    core_facts = int(official_lap_time is not None) + len(regions)
    if core_facts == 0:
        status = "abstained"
    elif (
        visible_limitations
        or official_lap_time is None
        or not regions
        or region_result["omitted_region_count"] > 0
    ):
        status = "partial"
    else:
        status = "available"

    text_parts: list[str] = []
    if official_lap_time is not None:
        text_parts.append(str(official_lap_time["text"]))
    if regions:
        text_parts.append(
            "Largest authorized recorded-region differences: "
            + " ".join(str(region["text"]) for region in regions)
        )
    elif region_result["limitation"] is None:
        text_parts.append("No ranked region measurements qualified for this comparison.")
    text_parts.append(
        "These are recorded measurements; they do not establish causes or recommend driving changes."
    )
    if visible_limitations:
        text_parts.append(
            "Limitations: "
            + "; ".join(item["text"].rstrip(".") for item in visible_limitations)
            + "."
        )
    if omitted_limitations:
        text_parts.append("Additional limitation details were omitted by the configured bounds.")
    debrief_text = _bounded_text(" ".join(text_parts), MAX_LAP_DEBRIEF_TEXT_CHARACTERS)

    return {
        "schema_version": 1,
        "analysis_version": LAP_DEBRIEF_VERSION,
        "status": status,
        "comparison_policy": comparison.get("comparison_policy"),
        "diagnostic_only": True,
        "coaching_eligible": False,
        "text": debrief_text,
        "official_lap_time": official_lap_time,
        "ranked_regions": regions,
        "omitted_region_count": region_result["omitted_region_count"],
        "limitations": visible_limitations,
        "omitted_limitation_count": omitted_limitations,
        "limits": {
            "region_limit": MAX_LAP_DEBRIEF_REGIONS,
            "limitation_limit": MAX_LAP_DEBRIEF_LIMITATIONS,
            "text_characters": MAX_LAP_DEBRIEF_TEXT_CHARACTERS,
        },
    }


def _official_lap_time_fact(
    comparison: Mapping[str, object], brief: Mapping[str, Any] | None
) -> dict[str, object] | None:
    if (
        brief is None
        or brief.get("analysis_version") != _EXPECTED_COMPARISON_BRIEF_VERSION
        or brief.get("status") != "available"
    ):
        return None
    target = _mapping(comparison.get("target"))
    reference = _mapping(comparison.get("reference"))
    if target is None or reference is None:
        return None
    target_ms = _positive_integer(target.get("lap_time_ms"))
    reference_ms = _positive_integer(reference.get("lap_time_ms"))
    reported = _finite_number(comparison.get("official_lap_time_difference_s"))
    if target_ms is None or reference_ms is None or reported is None:
        return None
    expected = (target_ms - reference_ms) / 1_000.0
    if reported != expected:
        return None

    facts = brief.get("facts")
    if not _sequence(facts):
        return None
    matches = [
        _mapping(fact)
        for fact in facts
        if _mapping(fact) is not None
        and _mapping(fact).get("kind") == "official_lap_time_difference"
    ]
    if len(matches) != 1:
        return None
    fact = matches[0]
    assert fact is not None
    source_fields = _mapping(fact.get("source_fields"))
    provenance = _mapping(fact.get("provenance"))
    if (
        _finite_number(fact.get("value")) != expected
        or fact.get("unit") != "s"
        or fact.get("direction") != "target_minus_reference"
        or source_fields is None
        or source_fields.get("target") != "target.lap_time_ms"
        or source_fields.get("reference") != "reference.lap_time_ms"
        or source_fields.get("derived") != "official_lap_time_difference_s"
        or not _provenance_matches_attempts(provenance, target, reference)
    ):
        return None

    if expected > 0:
        sentence = f"The target lap was {expected:.3f} s slower by official lap time."
    elif expected < 0:
        sentence = f"The target lap was {abs(expected):.3f} s faster by official lap time."
    else:
        sentence = "The target and reference had equal official lap times to 0.001 s."
    return {
        "value_s": expected,
        "direction": "target_minus_reference",
        "text": sentence,
        "source_fields": {
            "target": "target.lap_time_ms",
            "reference": "reference.lap_time_ms",
            "derived": "official_lap_time_difference_s",
        },
        "provenance": {
            "target": _attempt_identity(target),
            "reference": _attempt_identity(reference),
        },
    }


def _authorized_region_summaries(
    comparison: Mapping[str, object],
) -> dict[str, Any]:
    empty = {"regions": [], "omitted_region_count": 0, "limitation": None}
    if comparison.get("comparison_policy") != "time_trial":
        return {
            **empty,
            "limitation": (
                "ranked_regions_unavailable",
                "Ranked region measurements are available only under the existing Time Trial ranking policy.",
            ),
        }

    ranking = _mapping(comparison.get("corner_loss_candidates"))
    corner_brief = _mapping(comparison.get("corner_comparison_brief"))
    if ranking is None:
        return {
            **empty,
            "limitation": (
                "ranked_regions_unavailable",
                "The comparison has no D0032 ranked-region evidence.",
            ),
        }
    ranking_status = ranking.get("status")
    if ranking_status != "ranked":
        reasons = _reason_codes(ranking.get("gate_reasons"))
        if not reasons and corner_brief is not None:
            reasons = _reason_codes(corner_brief.get("gate_reasons"))
        if ranking_status == "no_positive_supported_differences":
            reason_text = "No positive supported recorded-region differences qualified for ranking."
        elif reasons:
            reason_text = (
                "Ranked region measurements abstained at the existing evidence gates: "
                + ", ".join(_humanize_reason(reason) for reason in reasons[:3])
                + "."
            )
        else:
            reason_text = "Ranked region measurements abstained at the existing evidence gates."
        return {
            **empty,
            "limitation": ("ranked_regions_abstained", reason_text),
        }

    if (
        ranking.get("analysis_version") != _EXPECTED_RANKING_VERSION
        or ranking.get("policy_version") != _EXPECTED_RANKING_POLICY_VERSION
        or ranking.get("coaching_eligible") is not False
        or not _ranking_source_matches_attempts(
            _mapping(ranking.get("source")),
            _mapping(comparison.get("target")),
            _mapping(comparison.get("reference")),
        )
    ):
        return {
            **empty,
            "limitation": (
                "ranked_regions_rejected",
                "D0032 ranked-region evidence failed its version, authority, or attempt-provenance check.",
            ),
        }
    if validate_corner_ranking_source(comparison):
        return {
            **empty,
            "limitation": (
                "ranked_regions_rejected",
                "The existing D0033 authority and provenance checks did not authorize this region ranking.",
            ),
        }

    if (
        corner_brief is None
        or corner_brief.get("analysis_version") != _EXPECTED_CORNER_BRIEF_VERSION
        or corner_brief.get("status") != "available"
        or corner_brief.get("coaching_eligible") is not False
        or _reason_codes(corner_brief.get("gate_reasons"))
    ):
        return {
            **empty,
            "limitation": (
                "ranked_regions_rejected",
                "D0033 did not provide a matching available summary for the ranked regions.",
            ),
        }

    candidates = ranking.get("ranked_candidates")
    summaries = corner_brief.get("regions")
    if (
        not _sequence(candidates)
        or not _sequence(summaries)
        or len(candidates) > MAX_LAP_DEBRIEF_REGIONS
        or len(summaries) != len(candidates)
        or not candidates
    ):
        return {
            **empty,
            "limitation": (
                "ranked_regions_rejected",
                "D0032 and D0033 ranked-region counts are missing or exceed the debrief limit.",
            ),
        }

    target = _mapping(comparison.get("target"))
    reference = _mapping(comparison.get("reference"))
    ranking_source = _mapping(ranking.get("source"))
    if target is None or reference is None or ranking_source is None:
        return {
            **empty,
            "limitation": (
                "ranked_regions_rejected",
                "Ranked-region attempt provenance is unavailable.",
            ),
        }

    regions: list[dict[str, object]] = []
    seen_region_ids: set[str] = set()
    previous_order_key: tuple[float, float, str] | None = None
    for index, (raw_candidate, raw_summary) in enumerate(zip(candidates, summaries)):
        candidate = _mapping(raw_candidate)
        summary = _mapping(raw_summary)
        if candidate is None or summary is None:
            return {
                **empty,
                "limitation": (
                    "ranked_regions_rejected",
                    "A ranked-region entry is malformed; no partial ranked list was substituted.",
                ),
            }
        region = _validated_region(
            candidate,
            summary,
            rank=index + 1,
            target=target,
            reference=reference,
            ranking_source=ranking_source,
        )
        if region is None:
            return {
                **empty,
                "limitation": (
                    "ranked_regions_rejected",
                    "A D0033 region summary did not match its D0032 rank, measurements, or provenance.",
                ),
            }
        order_key = (
            -float(region["recorded_time_difference_s"]),
            float(region["analysis_window_m"][0]),
            str(region["region_id"]),
        )
        if (
            region["region_id"] in seen_region_ids
            or (previous_order_key is not None and order_key < previous_order_key)
        ):
            return {
                **empty,
                "limitation": (
                    "ranked_regions_rejected",
                    "D0032 rank order or unique region identity did not match its descending measurement ranking.",
                ),
            }
        seen_region_ids.add(str(region["region_id"]))
        previous_order_key = order_key
        regions.append(region)

    omitted = _nonnegative_integer(corner_brief.get("omitted_region_count"))
    return {
        "regions": regions,
        "omitted_region_count": omitted or 0,
        "limitation": None,
    }


def _validated_region(
    candidate: Mapping[str, Any],
    summary: Mapping[str, Any],
    *,
    rank: int,
    target: Mapping[str, Any],
    reference: Mapping[str, Any],
    ranking_source: Mapping[str, Any],
) -> dict[str, object] | None:
    region_id = candidate.get("region_id")
    label = candidate.get("region_label")
    window = _bounds(candidate.get("analysis_window_m"))
    difference = _finite_number(candidate.get("recorded_time_difference_s"))
    support = _mapping(candidate.get("connected_support"))
    summary_support = _mapping(summary.get("connected_support"))
    fact_list = summary.get("facts")
    if (
        candidate.get("rank") != rank
        or summary.get("rank") != rank
        or not _nonempty_string(region_id)
        or not _nonempty_string(label)
        or window is None
        or difference is None
        or difference <= 0
        or candidate.get("measurement_direction")
        != "target_minus_reference_interval_time_difference"
        or summary.get("region_id") != region_id
        or summary.get("region_label") != label
        or _bounds(summary.get("analysis_window_m")) != window
        or support is None
        or not _support_is_complete(support)
        or summary_support is None
        or not _support_matches(support, summary_support)
        or not _sequence(fact_list)
        or not fact_list
    ):
        return None
    fact = _mapping(fact_list[0])
    if fact is None:
        return None
    fact_support = _mapping(fact.get("connected_support"))
    region_provenance = _mapping(summary.get("provenance"))
    fact_provenance = _mapping(fact.get("provenance"))
    if (
        fact.get("kind") != "recorded_interval_time_difference"
        or fact.get("unit") != "s"
        or fact.get("direction") != "target_minus_reference"
        or _finite_number(fact.get("value")) != difference
        or _bounds(fact.get("analysis_window_m")) != window
        or fact_support is None
        or not _support_matches(support, fact_support)
        or not _interval_measurement_matches(difference, support, fact)
        or not _region_provenance_matches(
            region_provenance, fact_provenance, ranking_source, target, reference
        )
    ):
        return None

    start_m, end_m = window
    bounded_label = _bounded_text(label, 120)
    return {
        "rank": rank,
        "region_id": _bounded_text(region_id, 120),
        "region_label": bounded_label,
        "analysis_window_m": [start_m, end_m],
        "recorded_time_difference_s": difference,
        "text": (
            f"{bounded_label} recorded {difference:.3f} s more interval time "
            f"across [{start_m:g}, {end_m:g}) m."
        ),
        "source_fields": {
            "difference": "corner_loss_candidates.ranked_candidates[].recorded_time_difference_s",
            "window": "corner_loss_candidates.ranked_candidates[].analysis_window_m",
        },
        "connected_support": dict(support),
        "provenance": _bounded_region_provenance(region_provenance),
    }


def _interval_measurement_matches(
    difference: float,
    support: Mapping[str, Any],
    fact: Mapping[str, Any],
) -> bool:
    entry_delta = _finite_number(support.get("entry_delta_s"))
    exit_delta = _finite_number(support.get("exit_delta_s"))
    boundary_evidence = _mapping(fact.get("boundary_delta_evidence"))
    fact_entry = _finite_number(
        boundary_evidence.get("entry_target_minus_reference_s")
        if boundary_evidence is not None
        else None
    )
    fact_exit = _finite_number(
        boundary_evidence.get("exit_target_minus_reference_s")
        if boundary_evidence is not None
        else None
    )
    return (
        entry_delta is not None
        and exit_delta is not None
        and fact_entry is not None
        and fact_exit is not None
        and math.isclose(exit_delta - entry_delta, difference, rel_tol=0.0, abs_tol=1e-9)
        and math.isclose(fact_entry, entry_delta, rel_tol=0.0, abs_tol=1e-9)
        and math.isclose(fact_exit, exit_delta, rel_tol=0.0, abs_tol=1e-9)
    )


def _ranking_source_matches_attempts(
    source: Mapping[str, Any] | None,
    target: Mapping[str, Any] | None,
    reference: Mapping[str, Any] | None,
) -> bool:
    if source is None or target is None or reference is None:
        return False
    return _identity_matches(source.get("target"), target) and _identity_matches(
        source.get("reference"), reference
    )


def _region_provenance_matches(
    region: Mapping[str, Any] | None,
    fact: Mapping[str, Any] | None,
    ranking_source: Mapping[str, Any],
    target: Mapping[str, Any],
    reference: Mapping[str, Any],
) -> bool:
    if region is None or fact is None:
        return False
    region_target = _mapping(region.get("target"))
    region_reference = _mapping(region.get("reference"))
    fact_target = _mapping(fact.get("target"))
    fact_reference = _mapping(fact.get("reference"))
    source_target = _mapping(ranking_source.get("target"))
    source_reference = _mapping(ranking_source.get("reference"))
    region_model = _mapping(region.get("model"))
    fact_model = _mapping(fact.get("model"))
    source_model = _mapping(ranking_source.get("model"))
    if any(
        item is None
        for item in (
            region_target,
            region_reference,
            fact_target,
            fact_reference,
            source_target,
            source_reference,
            region_model,
            fact_model,
            source_model,
        )
    ):
        return False
    assert region_target and region_reference and fact_target and fact_reference
    assert source_target and source_reference and region_model and fact_model and source_model
    identity_fields = ("attempt_key", "run_id", "trace_sha256")
    model_fields = ("model_id", "revision", "model_content_sha256")
    selection = _mapping(ranking_source.get("reference_selection"))
    if (
        not _nonempty_string(source_model.get("model_id"))
        or not _positive_integer(source_model.get("revision"))
        or not _nonempty_string(source_model.get("model_content_sha256"))
        or selection is None
        or selection.get("reference_kind") != "session_best"
        or selection.get("status") != "selected"
        or selection.get("policy_version") != "tt-session-best-v2-lifecycle"
    ):
        return False
    return (
        _identity_matches(region_target, target)
        and _identity_matches(region_reference, reference)
        and all(region_target.get(field) == source_target.get(field) == fact_target.get(field) for field in identity_fields)
        and all(region_reference.get(field) == source_reference.get(field) == fact_reference.get(field) for field in identity_fields)
        and all(region_model.get(field) == source_model.get(field) == fact_model.get(field) for field in model_fields)
        and region.get("reference_selection") == ranking_source.get("reference_selection")
        and fact.get("reference_selection") == ranking_source.get("reference_selection")
    )


def _provenance_matches_attempts(
    provenance: Mapping[str, Any] | None,
    target: Mapping[str, Any],
    reference: Mapping[str, Any],
) -> bool:
    if provenance is None:
        return False
    return _identity_matches(provenance.get("target"), target) and _identity_matches(
        provenance.get("reference"), reference
    )


def _identity_matches(value: object, attempt: Mapping[str, Any]) -> bool:
    identity = _mapping(value)
    if identity is None:
        return False
    expected = _attempt_identity(attempt)
    if any(not _nonempty_string(expected[field]) for field in expected):
        return False
    return all(identity.get(field) == expected[field] for field in expected)


def _attempt_identity(attempt: Mapping[str, Any]) -> dict[str, object]:
    return {
        "attempt_key": attempt.get("attempt_key"),
        "run_id": attempt.get("run_id"),
        "trace_sha256": attempt.get("trace_sha256"),
    }


def _bounded_region_provenance(value: Mapping[str, Any]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in ("target", "reference"):
        identity = _mapping(value.get(key))
        if identity is not None:
            result[key] = {
                name: _bounded_text(identity.get(name), 128)
                for name in ("attempt_key", "run_id", "trace_sha256")
                if isinstance(identity.get(name), str)
            }
    model = _mapping(value.get("model"))
    if model is not None:
        result["model"] = {
            name: model.get(name)
            for name in ("model_id", "revision", "model_content_sha256")
            if isinstance(model.get(name), (str, int))
            and not isinstance(model.get(name), bool)
        }
    selection = _mapping(value.get("reference_selection"))
    if selection is not None:
        result["reference_selection"] = {
            key: selection.get(key)
            for key in ("reference_kind", "status", "policy_version")
            if isinstance(selection.get(key), str)
        }
    return result


def _support_is_complete(support: Mapping[str, Any]) -> bool:
    required = (
        "target_resampled_time_connected",
        "reference_resampled_time_connected",
        "shared_delta_time_connected",
        "target_source_session_time_connected",
        "reference_source_session_time_connected",
        "interval_connected_supported_time",
    )
    if any(support.get(key) is not True for key in required):
        return False
    return all(
        (value := _finite_number(support.get(key))) is not None and 0 <= value <= 1
        for key in ("target_time_coverage", "reference_time_coverage", "shared_time_coverage")
    )


def _support_matches(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    fields = (
        "target_resampled_time_connected",
        "reference_resampled_time_connected",
        "shared_delta_time_connected",
        "target_source_session_time_connected",
        "reference_source_session_time_connected",
        "interval_connected_supported_time",
        "target_time_coverage",
        "reference_time_coverage",
        "shared_time_coverage",
    )
    return all(left.get(field) == right.get(field) for field in fields)


def _ranking_reason_codes(value: object) -> list[str]:
    if not _sequence(value):
        return []
    return [_safe_code(item) for item in value[:8] if isinstance(item, str)]


def _reason_codes(value: object) -> list[str]:
    return _ranking_reason_codes(value)


def _humanize_reason(value: str) -> str:
    return _bounded_text(value.replace("_", " ").replace("-", " "), 100)


def _safe_code(value: str) -> str:
    safe = "".join(character if character.isalnum() or character == "_" else "_" for character in value)
    return safe[:80] or "unspecified_limitation"


def _bounded_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    collapsed = " ".join(value.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 1)].rstrip() + "…"


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _sequence(value: object) -> Sequence[Any] | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return None


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _positive_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _nonnegative_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _bounds(value: object) -> tuple[float, float] | None:
    if not _sequence(value) or len(value) != 2:
        return None
    start = _finite_number(value[0])
    end = _finite_number(value[1])
    if start is None or end is None or end <= start:
        return None
    return start, end


_LIMITATION_PRIORITY = {
    "attempt_integrity": 0,
    "capture_incomplete": 1,
    "capture_completeness_unknown": 2,
    "recording_losses": 3,
    "recording_loss_counts_unknown": 3,
    "replay_losses": 4,
    "replay_loss_counts_unknown": 4,
    "delta_coverage_partial": 5,
    "delta_coverage_unknown": 5,
    "ranked_regions_rejected": 6,
    "ranked_regions_abstained": 7,
    "ranked_regions_unavailable": 7,
    "official_lap_time_unavailable": 8,
    "practice_qualifying_conditions_uncontrolled": 9,
    "diagnostic_comparison": 10,
    "comparison_brief_unavailable": 11,
}
