from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


COMPARISON_BRIEF_VERSION = "comparison-brief-v1"
MAX_COMPARISON_BRIEF_FACTS = 6
MAX_COMPARISON_BRIEF_LIMITATIONS = 8
_SUPPORTED_POLICIES = frozenset({"time_trial", "practice_qualifying"})


def build_comparison_brief(comparison: Mapping[str, object]) -> dict[str, object]:
    """Format existing comparison evidence as bounded, deterministic facts."""
    facts: list[dict[str, object]] = []
    limitations: list[dict[str, str]] = []
    omitted_facts = 0

    def add_fact(fact: dict[str, object]) -> None:
        nonlocal omitted_facts
        if len(facts) < MAX_COMPARISON_BRIEF_FACTS:
            facts.append(fact)
        else:
            omitted_facts += 1

    def add_limitation(code: str, text: str) -> None:
        existing = next((item for item in limitations if item["code"] == code), None)
        if existing is None:
            limitations.append({"code": code, "text": text})
            return
        if text not in existing["text"]:
            existing["text"] += " " + text

    policy = comparison.get("comparison_policy")
    target = _mapping(comparison.get("target"))
    reference = _mapping(comparison.get("reference"))
    if not isinstance(policy, str) or policy not in _SUPPORTED_POLICIES:
        add_limitation(
            "unsupported_comparison_policy",
            "This comparison policy does not support a summary brief.",
        )
        return _report(facts, limitations, False, omitted_facts)
    if (
        target.get("disposition") != "completed"
        or reference.get("disposition") != "completed"
        or not _nonempty_string(target.get("attempt_key"))
        or not _nonempty_string(reference.get("attempt_key"))
    ):
        add_limitation(
            "attempt_identity_unavailable",
            "The brief requires two identified, completed attempts.",
        )
        return _report(facts, limitations, False, omitted_facts)

    target_key = str(target["attempt_key"])
    reference_key = str(reference["attempt_key"])
    provenance = {
        "target": _attempt_provenance(target),
        "reference": _attempt_provenance(reference),
    }
    if policy == "practice_qualifying":
        add_limitation(
            "practice_qualifying_conditions_uncontrolled",
            "Fuel load, tyre condition, traffic, and cooldown intent were not controlled.",
        )
    if comparison.get("diagnostic_only") is True:
        add_limitation(
            "diagnostic_comparison",
            "This is a diagnostic comparison; it does not establish why the lap-time difference occurred.",
        )

    target_lap_ms = _positive_number(target.get("lap_time_ms"))
    reference_lap_ms = _positive_number(reference.get("lap_time_ms"))
    difference_s = _finite_number(comparison.get("official_lap_time_difference_s"))
    expected_difference_s = (
        (target_lap_ms - reference_lap_ms) / 1000.0
        if target_lap_ms is not None and reference_lap_ms is not None
        else None
    )
    if (
        difference_s is not None
        and expected_difference_s is not None
        and math.isclose(difference_s, expected_difference_s, rel_tol=0, abs_tol=1e-9)
    ):
        add_fact(
            {
                "kind": "official_lap_time_difference",
                "value": difference_s,
                "unit": "s",
                "direction": "target_minus_reference",
                "source_fields": {
                    "target": "target.lap_time_ms",
                    "reference": "reference.lap_time_ms",
                    "derived": "official_lap_time_difference_s",
                },
                "provenance": provenance,
                "text": _difference_text(
                    difference_s,
                    positive="Target was {magnitude} s slower than the reference by official lap time.",
                    negative="Target was {magnitude} s faster than the reference by official lap time.",
                    tied="Target and reference had equal official lap times to 0.001 s.",
                ),
            }
        )
    else:
        add_limitation(
            "official_lap_time_unavailable",
            "The official lap-time difference is unavailable or inconsistent with the stored attempt times.",
        )

    _append_sector_facts(
        comparison,
        target_key,
        reference_key,
        provenance,
        add_fact,
        add_limitation,
    )
    _append_sector_residual_fact(
        comparison,
        provenance,
        add_fact,
    )
    _append_window_fact(comparison, provenance, add_fact, add_limitation)
    _append_attempt_limitations(target, "target", add_limitation)
    _append_attempt_limitations(reference, "reference", add_limitation)
    _append_capture_limitations(comparison, add_limitation)
    _append_coverage_limitations(comparison, add_limitation)

    return _report(
        facts,
        limitations,
        comparison.get("diagnostic_only") is True,
        omitted_facts,
    )


def _append_sector_facts(
    comparison: Mapping[str, object],
    target_key: str,
    reference_key: str,
    provenance: dict[str, object],
    add_fact: Any,
    add_limitation: Any,
) -> None:
    difference = _mapping(comparison.get("sector_timing_difference_ms"))
    timing = _mapping(comparison.get("reported_timing_evidence"))
    target_timing = _mapping(timing.get("target"))
    reference_timing = _mapping(timing.get("reference"))
    if (
        difference.get("status") != "matched"
        or target_timing.get("status") != "matched"
        or reference_timing.get("status") != "matched"
    ):
        for side, evidence in (("target", target_timing), ("reference", reference_timing)):
            if evidence.get("status") != "matched":
                status = evidence.get("status")
                status_label = status if isinstance(status, str) else "unavailable"
                add_limitation(
                    "reported_sector_timing_unavailable",
                    f"Reported sector timing is {status_label} for the {side} attempt.",
                )
        if (
            target_timing.get("status") == "matched"
            and reference_timing.get("status") == "matched"
        ):
            add_limitation(
                "reported_sector_difference_unavailable",
                "Reported sector differences could not be reconciled for this pair.",
            )
        return

    sectors = _mapping(difference.get("sectors"))
    for number in (1, 2, 3):
        name = f"sector{number}"
        item = _mapping(sectors.get(name))
        target_valid = item.get("target_valid")
        reference_valid = item.get("reference_valid")
        if item.get("status") != "matched_values":
            continue
        value = _finite_number(item.get("target_minus_reference_ms"))
        if value is None:
            continue
        if target_valid is not True or reference_valid is not True:
            add_limitation(
                "sector_validity_not_supported",
                f"Sector {number} difference is not summarized because at least one reported sector is invalid or has unknown validity.",
            )
            continue
        add_fact(
            {
                "kind": "reported_sector_difference",
                "sector": number,
                "value": value,
                "unit": "ms",
                "direction": "target_minus_reference",
                "validity": {"target": target_valid, "reference": reference_valid},
                "source_fields": {
                    "target": f"reported_timing_evidence.target.sector{number}_time_ms",
                    "reference": f"reported_timing_evidence.reference.sector{number}_time_ms",
                    "difference": f"sector_timing_difference_ms.sectors.{name}.target_minus_reference_ms",
                    "target_validity": f"sector_timing_difference_ms.sectors.{name}.target_valid",
                    "reference_validity": f"sector_timing_difference_ms.sectors.{name}.reference_valid",
                },
                "provenance": provenance,
                "text": _difference_text(
                    value / 1000.0,
                    positive=f"Reported Sector {number} was {{magnitude}} s slower on the target.",
                    negative=f"Reported Sector {number} was {{magnitude}} s faster on the target.",
                    tied=f"Reported Sector {number} times were equal to 0.001 s.",
                ),
                "target_attempt_key": target_key,
                "reference_attempt_key": reference_key,
            }
        )


def _append_sector_residual_fact(
    comparison: Mapping[str, object],
    provenance: dict[str, object],
    add_fact: Any,
) -> None:
    difference = _mapping(comparison.get("sector_timing_difference_ms"))
    timing = _mapping(comparison.get("reported_timing_evidence"))
    target = _mapping(timing.get("target"))
    reference = _mapping(timing.get("reference"))
    if (
        difference.get("status") != "matched"
        or target.get("status") != "matched"
        or reference.get("status") != "matched"
        or not _all_sectors_valid(target)
        or not _all_sectors_valid(reference)
    ):
        return
    target_residual = _finite_number(difference.get("target_sector_sum_residual_ms"))
    reference_residual = _finite_number(difference.get("reference_sector_sum_residual_ms"))
    if target_residual is None or reference_residual is None:
        return
    add_fact(
        {
            "kind": "reported_sector_sum_residual",
            "target_value": target_residual,
            "reference_value": reference_residual,
            "unit": "ms",
            "source_fields": {
                "target": "reported_timing_evidence.target.sector_sum_residual_ms",
                "reference": "reported_timing_evidence.reference.sector_sum_residual_ms",
            },
            "provenance": provenance,
            "text": (
                f"Target reported sectors {_residual_text(target_residual)}; "
                f"reference reported sectors {_residual_text(reference_residual)}."
            ),
        }
    )


def _append_window_fact(
    comparison: Mapping[str, object],
    provenance: dict[str, object],
    add_fact: Any,
    add_limitation: Any,
) -> None:
    window = _mapping(comparison.get("comparison_window"))
    if not window:
        return
    delta = _mapping(window.get("delta"))
    bounds = _mapping(window.get("window_m"))
    start_m = _finite_number(bounds.get("start_m"))
    end_m = _finite_number(bounds.get("end_m"))
    change_s = _finite_number(delta.get("delta_change_s"))
    if (
        delta.get("status") != "supported"
        or delta.get("interval_connected_supported_time") is not True
        or start_m is None
        or end_m is None
        or end_m <= start_m
        or change_s is None
    ):
        add_limitation(
            "selected_window_unsupported",
            "The selected distance window lacks connected supported delta-time evidence and is not summarized.",
        )
        return
    fact_text = _window_text(change_s, start_m, end_m)
    add_fact(
        {
            "kind": "supported_distance_window_delta_change",
            "value": change_s,
            "unit": "s",
            "direction": "target_minus_reference_end_delta_minus_start_delta",
            "window_m": {"start_m": start_m, "end_m": end_m},
            "source_fields": {
                "delta_change": "comparison_window.delta.delta_change_s",
                "start_m": "comparison_window.window_m.start_m",
                "end_m": "comparison_window.window_m.end_m",
            },
            "provenance": provenance,
            "text": fact_text,
        }
    )


def _append_attempt_limitations(
    attempt: Mapping[str, object], side: str, add_limitation: Any
) -> None:
    details: list[str] = []
    validity = attempt.get("game_valid")
    if validity is False:
        details.append("the game marked it invalid")
    elif validity is None:
        details.append("game validity is unknown")

    if attempt.get("superseded") is True:
        details.append("it was superseded by lifecycle evidence")
    exclusions = attempt.get("lifecycle_exclusions")
    reasons = (
        [str(value) for value in exclusions[:3] if isinstance(value, str)]
        if isinstance(exclusions, list)
        else []
    )
    if attempt.get("lifecycle_assessed") is not True:
        details.append(
            "lifecycle evidence is unassessed"
            + (f" ({', '.join(reasons)})" if reasons else "")
        )
    elif reasons:
        details.append("lifecycle exclusions apply: " + ", ".join(reasons))
    if details:
        add_limitation(
            "attempt_integrity",
            f"For the {side} attempt, " + "; ".join(details) + ".",
        )


def _append_capture_limitations(
    comparison: Mapping[str, object], add_limitation: Any
) -> None:
    evidence = _mapping(comparison.get("processing_run_evidence"))
    grouped: dict[tuple[str, str], tuple[Mapping[str, Any], list[str]]] = {}
    for side in ("target", "reference"):
        summary = _mapping(evidence.get(side))
        run_id = summary.get("run_id")
        group_key = ("run", run_id) if isinstance(run_id, str) else ("side", side)
        group = grouped.get(group_key)
        if group is None:
            grouped[group_key] = (summary, [side])
        else:
            group[1].append(side)

    for summary, sides in grouped.values():
        side_label = "shared" if len(sides) > 1 else sides[0]
        capture = _mapping(summary.get("capture"))
        if not summary or "complete" not in capture or capture.get("complete") is None:
            add_limitation(
                "capture_completeness_unknown",
                f"Capture completion evidence is unknown for the {side_label} run.",
            )
        elif capture.get("complete") is False:
            add_limitation(
                "capture_incomplete",
                f"The {side_label} capture has an incomplete footer.",
            )

        recording = _mapping(capture.get("recording_counters"))
        recording_keys = ("queue_dropped", "unpersisted_on_shutdown", "socket_errors")
        if any(recording.get(key) is None for key in recording_keys):
            add_limitation(
                "recording_loss_counts_unknown",
                f"Acquisition loss counts are unknown for the {side_label} capture.",
            )
        lost = sum(
            count
            for key in recording_keys
            if (count := _nonnegative_integer(recording.get(key))) is not None
        )
        if lost:
            add_limitation(
                "recording_losses",
                f"The {side_label} capture reports {lost} acquisition loss or socket-error events.",
            )
        processing = _mapping(summary.get("processing"))
        replay = _mapping(processing.get("replay_counters"))
        replay_keys = ("import_late_packets_ignored", "import_frame_overflow_packets_dropped")
        if any(replay.get(key) is None for key in replay_keys):
            add_limitation(
                "replay_loss_counts_unknown",
                f"Frame-admission loss counts are unknown for the {side_label} import.",
            )
        replay_losses = sum(
            count
            for key in replay_keys
            if (count := _nonnegative_integer(replay.get(key))) is not None
        )
        if replay_losses:
            add_limitation(
                "replay_losses",
                f"The {side_label} import reports {replay_losses} frame-admission exclusions or drops.",
            )


def _append_coverage_limitations(
    comparison: Mapping[str, object], add_limitation: Any
) -> None:
    quality = _mapping(comparison.get("quality"))
    coverage = _finite_number(quality.get("delta_time_coverage"))
    if coverage is None:
        add_limitation(
            "delta_coverage_unknown",
            "Shared distance-delta coverage is unknown.",
        )
    elif coverage < 1.0:
        percent = f"{coverage * 100:.1f}".rstrip("0").rstrip(".")
        add_limitation(
            "delta_coverage_partial",
            f"Shared distance-delta coverage is {percent}% of the comparison grid.",
        )


def _all_sectors_valid(evidence: Mapping[str, object]) -> bool:
    return all(
        evidence.get(f"sector{number}_valid") is True
        and _positive_number(evidence.get(f"sector{number}_time_ms")) is not None
        for number in (1, 2, 3)
    )


def _attempt_provenance(attempt: Mapping[str, object]) -> dict[str, object]:
    return {
        "attempt_key": attempt.get("attempt_key"),
        "run_id": attempt.get("run_id"),
        "trace_sha256": attempt.get("trace_sha256"),
    }


def _report(
    facts: list[dict[str, object]],
    limitations: list[dict[str, str]],
    diagnostic_only: bool,
    omitted_facts: int,
) -> dict[str, object]:
    prioritized_limitations = sorted(
        limitations,
        key=lambda item: _LIMITATION_PRIORITY.get(item["code"], 100),
    )
    omitted_limitations = max(
        0, len(prioritized_limitations) - MAX_COMPARISON_BRIEF_LIMITATIONS
    )
    prioritized_limitations = prioritized_limitations[:MAX_COMPARISON_BRIEF_LIMITATIONS]
    sentences = [str(fact["text"]) for fact in facts]
    text = " ".join(sentences) if sentences else "No supported comparison facts are available for this pair."
    if prioritized_limitations:
        text += " Limitations: " + "; ".join(
            item["text"].rstrip(".") for item in prioritized_limitations
        ) + "."
    if omitted_facts or omitted_limitations:
        text += " Additional brief details were omitted by the configured bounds."
    return {
        "schema_version": 1,
        "analysis_version": COMPARISON_BRIEF_VERSION,
        "status": "available" if facts else "unavailable",
        "diagnostic_only": diagnostic_only,
        "text": text,
        "facts": facts,
        "limitations": prioritized_limitations,
        "limits": {
            "fact_limit": MAX_COMPARISON_BRIEF_FACTS,
            "omitted_fact_count": omitted_facts,
            "limitation_limit": MAX_COMPARISON_BRIEF_LIMITATIONS,
            "omitted_limitation_count": omitted_limitations,
        },
    }


_LIMITATION_PRIORITY = {
    "unsupported_comparison_policy": 0,
    "attempt_identity_unavailable": 0,
    "practice_qualifying_conditions_uncontrolled": 1,
    "diagnostic_comparison": 2,
    "attempt_integrity": 3,
    "capture_incomplete": 4,
    "capture_completeness_unknown": 4,
    "recording_losses": 5,
    "recording_loss_counts_unknown": 5,
    "replay_losses": 6,
    "replay_loss_counts_unknown": 6,
    "delta_coverage_partial": 7,
    "delta_coverage_unknown": 7,
    "reported_sector_timing_unavailable": 8,
    "reported_sector_difference_unavailable": 8,
    "sector_validity_not_supported": 8,
    "selected_window_unsupported": 9,
    "official_lap_time_unavailable": 10,
}


def _difference_text(
    value: float,
    *,
    positive: str,
    negative: str,
    tied: str,
) -> str:
    if round(value, 3) == 0:
        return tied
    template = positive if value > 0 else negative
    return template.format(magnitude=f"{abs(value):.3f}")


def _window_text(change_s: float, start_m: float, end_m: float) -> str:
    start = _format_distance(start_m)
    end = _format_distance(end_m)
    if round(change_s, 3) == 0:
        return (
            f"Target-minus-reference delta changed by less than 0.001 s across "
            f"the supported {start}–{end} m window."
        )
    action = "increased" if change_s > 0 else "decreased"
    return (
        f"Target-minus-reference delta {action} by {abs(change_s):.3f} s across "
        f"the supported {start}–{end} m window."
    )


def _residual_text(value_ms: float) -> str:
    if value_ms == 0:
        return "sum exactly to the lap time"
    return (
        f"sum to {abs(value_ms):g} ms "
        f"{'more than' if value_ms > 0 else 'less than'} the lap time"
    )


def _format_distance(value: float) -> str:
    return f"{value:g}"


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _positive_number(value: object) -> float | None:
    number = _finite_number(value)
    return number if number is not None and number > 0 else None


def _nonnegative_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
