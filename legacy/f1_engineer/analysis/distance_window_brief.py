from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


DISTANCE_WINDOW_BRIEF_VERSION = "distance-window-brief-v1"
MAX_DISTANCE_WINDOW_BRIEF_FACTS = 5
MAX_DISTANCE_WINDOW_BRIEF_LIMITATIONS = 8
_SUPPORTED_POLICIES = frozenset({"time_trial", "practice_qualifying"})


def build_distance_window_brief(
    comparison: Mapping[str, object],
) -> dict[str, object]:
    """Summarize only fully supported measured facts from a selected window."""
    facts: list[dict[str, object]] = []
    limitations: list[dict[str, str]] = []
    window = _mapping(comparison.get("comparison_window"))
    bounds = _mapping(window.get("window_m"))
    start_m = _finite_number(bounds.get("start_m"))
    end_m = _finite_number(bounds.get("end_m"))
    policy = comparison.get("comparison_policy")
    target = _mapping(comparison.get("target"))
    reference = _mapping(comparison.get("reference"))
    valid_window = (
        start_m is not None and end_m is not None and start_m < end_m
    )
    valid_pair = (
        isinstance(policy, str)
        and policy in _SUPPORTED_POLICIES
        and target.get("disposition") == "completed"
        and reference.get("disposition") == "completed"
        and _nonempty_string(target.get("attempt_key"))
        and _nonempty_string(reference.get("attempt_key"))
    )

    def add_limitation(code: str, text: str) -> None:
        if not any(item["code"] == code for item in limitations):
            limitations.append({"code": code, "text": text})

    provenance = {
        "comparison_policy": policy,
        "window_m": {"start_m": start_m, "end_m": end_m}
        if valid_window
        else None,
        "target": _attempt_provenance(target),
        "reference": _attempt_provenance(reference),
    }
    if not valid_pair:
        add_limitation(
            "unsupported_comparison_pair",
            "A completed Time Trial or same-session Practice/Qualifying pair is required.",
        )
    if not valid_window:
        add_limitation(
            "selected_window_unavailable",
            "A finite increasing selected distance window is required.",
        )
    if valid_pair and valid_window and window:
        _append_interval_fact(window, provenance, facts, add_limitation)
        _append_observed_difference_fact(
            window,
            "minimum_speed",
            channel="speed_mps",
            value_key="speed_kph",
            unit="km/h",
            kind="observed_minimum_speed_difference",
            label="Observed minimum speed",
            multiplier=1.0,
            precision=1,
            start_m=start_m,
            end_m=end_m,
            provenance=provenance,
            facts=facts,
            add_limitation=add_limitation,
        )
        _append_observed_difference_fact(
            window,
            "peak_brake",
            channel="brake",
            value_key="value",
            unit="percentage_points",
            kind="peak_brake_input_difference",
            label="Peak recorded brake input",
            multiplier=100.0,
            precision=1,
            start_m=start_m,
            end_m=end_m,
            provenance=provenance,
            facts=facts,
            add_limitation=add_limitation,
        )
        _append_threshold_onset_fact(
            window,
            event_key="brake_10_percent",
            channel="brake",
            threshold=0.1,
            label="10% brake",
            kind="brake_10_percent_onset_difference",
            start_m=start_m,
            end_m=end_m,
            provenance=provenance,
            facts=facts,
            add_limitation=add_limitation,
        )
        _append_threshold_onset_fact(
            window,
            event_key="throttle_50_percent",
            channel="throttle",
            threshold=0.5,
            label="50% throttle",
            kind="throttle_50_percent_onset_difference",
            start_m=start_m,
            end_m=end_m,
            provenance=provenance,
            facts=facts,
            add_limitation=add_limitation,
        )

    warnings, omitted_warning_count = _context_warnings(comparison)
    text_parts = [str(fact["text"]) for fact in facts]
    if not text_parts:
        text_parts.append(
            "No supported measured-control facts are available for this distance window."
        )
    if limitations:
        text_parts.append(
            "Unsupported measurements: "
            + "; ".join(item["text"].rstrip(".") for item in limitations)
            + "."
        )
    if warnings:
        text_parts.append(
            "Context: "
            + "; ".join(item["text"].rstrip(".") for item in warnings)
            + "."
        )
    if omitted_warning_count:
        text_parts.append("Additional context warnings were omitted by the configured bound.")
    return {
        "schema_version": 1,
        "artifact_kind": "distance_window_brief",
        "analysis_version": DISTANCE_WINDOW_BRIEF_VERSION,
        "status": "available" if facts else "unavailable",
        "diagnostic_only": True,
        "coaching_eligible": False,
        "text": " ".join(text_parts),
        "window_m": provenance["window_m"],
        "provenance": provenance,
        "facts": facts[:MAX_DISTANCE_WINDOW_BRIEF_FACTS],
        "limitations": limitations[:MAX_DISTANCE_WINDOW_BRIEF_LIMITATIONS],
        "warnings": warnings,
        "limits": {
            "fact_limit": MAX_DISTANCE_WINDOW_BRIEF_FACTS,
            "limitation_limit": MAX_DISTANCE_WINDOW_BRIEF_LIMITATIONS,
            "omitted_limitation_count": max(
                0, len(limitations) - MAX_DISTANCE_WINDOW_BRIEF_LIMITATIONS
            ),
            "warning_limit": MAX_DISTANCE_WINDOW_BRIEF_LIMITATIONS,
            "omitted_warning_count": omitted_warning_count,
        },
    }


def _append_interval_fact(
    window: Mapping[str, Any],
    provenance: dict[str, object],
    facts: list[dict[str, object]],
    add_limitation: Any,
) -> None:
    delta = _mapping(window.get("delta"))
    change_s = _finite_number(delta.get("delta_change_s"))
    if (
        delta.get("status") != "supported"
        or delta.get("interval_connected_supported_time") is not True
        or change_s is None
    ):
        add_limitation(
            "interval_time_unavailable",
            "Connected interval-time evidence is unavailable.",
        )
        return
    window_bounds = _mapping(provenance.get("window_m"))
    start_m = _finite_number(window_bounds.get("start_m"))
    end_m = _finite_number(window_bounds.get("end_m"))
    if start_m is None or end_m is None:
        add_limitation(
            "selected_window_unavailable",
            "A finite increasing selected distance window is required.",
        )
        return
    if abs(change_s) < 0.0005:
        summary = "Target-minus-reference interval-time difference changed by less than 0.001 s"
    else:
        direction = "increased" if change_s > 0 else "decreased"
        summary = (
            "Target-minus-reference interval-time difference "
            f"{direction} by {abs(change_s):.3f} s"
        )
    facts.append(
        {
            "kind": "connected_interval_time_difference",
            "value": change_s,
            "unit": "s",
            "direction": "target_minus_reference_end_delta_minus_start_delta",
            "window_m": {"start_m": start_m, "end_m": end_m},
            "source_fields": {
                "delta_change": "comparison_window.delta.delta_change_s",
                "connected_support": "comparison_window.delta.interval_connected_supported_time",
                "window": "comparison_window.window_m",
            },
            "provenance": provenance,
            "text": f"{summary} across {start_m:g}–{end_m:g} m.",
        }
    )


def _append_observed_difference_fact(
    window: Mapping[str, Any],
    field: str,
    *,
    channel: str,
    value_key: str,
    unit: str,
    kind: str,
    label: str,
    multiplier: float,
    precision: int,
    start_m: float,
    end_m: float,
    provenance: dict[str, object],
    facts: list[dict[str, object]],
    add_limitation: Any,
) -> None:
    target = _mapping(window.get("target"))
    reference = _mapping(window.get("reference"))
    for side, attempt in (("target", target), ("reference", reference)):
        coverage = _finite_number(_mapping(attempt.get("coverage")).get(channel))
        if coverage is None or not math.isclose(coverage, 1.0, rel_tol=0, abs_tol=1e-9):
            add_limitation(
                f"{field}_incomplete_coverage",
                f"{label} is omitted because {side} {channel} coverage is incomplete.",
            )
            return
    target_value, target_anchor = _observed_value(target, field, value_key, start_m, end_m)
    reference_value, reference_anchor = _observed_value(
        reference, field, value_key, start_m, end_m
    )
    if target_value is None or reference_value is None:
        add_limitation(
            f"{field}_unavailable",
            f"{label} is omitted because a supported observation is unavailable "
            "on one or both attempts.",
        )
        return
    if (
        not _valid_source_anchor(target_anchor, start_m, end_m)
        or not _valid_source_anchor(reference_anchor, start_m, end_m)
    ):
        add_limitation(
            f"{field}_source_anchor_unavailable",
            f"{label} is omitted because a valid source frame, time and distance "
            "anchor is unavailable.",
        )
        return
    difference = (target_value - reference_value) * multiplier
    unit_text = "percentage points" if unit == "percentage_points" else unit
    level_unit = "%" if unit == "percentage_points" else unit
    target_display_value = target_value * multiplier
    reference_display_value = reference_value * multiplier
    if round(difference, precision) == 0:
        text = (
            f"Target and reference {label.lower()} matched to "
            f"{10 ** -precision:g} {unit_text} "
            f"({target_display_value:.{precision}f}{level_unit} vs "
            f"{reference_display_value:.{precision}f}{level_unit})."
        )
    else:
        direction = "higher" if difference > 0 else "lower"
        text = (
            f"Target {label.lower()} was {abs(difference):.{precision}f} {unit_text} "
            f"{direction} than reference ({target_display_value:.{precision}f}{level_unit} "
            f"vs {reference_display_value:.{precision}f}{level_unit})."
        )
    facts.append(
        {
            "kind": kind,
            "target_value": target_value,
            "reference_value": reference_value,
            "target_value_display": target_display_value,
            "reference_value_display": reference_display_value,
            "value_display_unit": level_unit,
            "value": difference,
            "unit": unit,
            "direction": "target_minus_reference",
            "target_anchor": target_anchor,
            "reference_anchor": reference_anchor,
            "source_fields": {
                "target_value": f"comparison_window.target.{field}.{value_key}",
                "reference_value": f"comparison_window.reference.{field}.{value_key}",
                "target_coverage": f"comparison_window.target.coverage.{channel}",
                "reference_coverage": f"comparison_window.reference.coverage.{channel}",
            },
            "provenance": provenance,
            "text": text,
        }
    )


def _append_threshold_onset_fact(
    window: Mapping[str, Any],
    *,
    event_key: str,
    channel: str,
    threshold: float,
    label: str,
    kind: str,
    start_m: float,
    end_m: float,
    provenance: dict[str, object],
    facts: list[dict[str, object]],
    add_limitation: Any,
) -> None:
    attempts: dict[str, Mapping[str, Any]] = {
        "target": _mapping(window.get("target")),
        "reference": _mapping(window.get("reference")),
    }
    events: dict[str, Mapping[str, Any]] = {}
    for side, attempt in attempts.items():
        coverage = _finite_number(_mapping(attempt.get("coverage")).get(channel))
        if coverage is None or not math.isclose(coverage, 1.0, rel_tol=0, abs_tol=1e-9):
            add_limitation(
                f"{event_key}_incomplete_coverage",
                f"{label} onset is omitted because {side} {channel} coverage is incomplete.",
            )
            return
        detection = _mapping(_mapping(attempt.get("threshold_events")).get(event_key))
        event_list = detection.get("events")
        if (
            detection.get("status") != "detected"
            or detection.get("event_count") != 1
            or detection.get("events_truncated") is not False
            or detection.get("rejected_short_event_count") != 0
            or detection.get("unsupported_break_count") != 0
            or detection.get("left_censored_event_count") != 0
            or not isinstance(event_list, list)
            or len(event_list) != 1
        ):
            add_limitation(
                f"{event_key}_not_unique_supported_event",
                f"{label} onset is omitted because each attempt must have exactly "
                "one uncensored supported sustained event without gaps, short "
                "rejected episodes or truncation.",
            )
            return
        event = _mapping(event_list[0])
        bracket = _bracket(event.get("start_distance_bracket_m"), start_m, end_m)
        onset_distance = _finite_number(event.get("start_distance_m"))
        if (
            event.get("channel") != channel
            or not _close_number(event.get("threshold"), threshold)
            or event.get("left_censored") is not False
            or not isinstance(event.get("right_censored"), bool)
            or _finite_number(event.get("start_session_time_s")) is None
            or bracket is None
            or onset_distance is None
            or (bracket is not None and not bracket[0] <= onset_distance <= bracket[1])
        ):
            add_limitation(
                f"{event_key}_onset_bracket_unavailable",
                f"{label} onset is omitted because its observed source bracket "
                "is unavailable or malformed.",
            )
            return
        events[side] = {**event, "start_distance_bracket_m": bracket}
    target_bracket = events["target"]["start_distance_bracket_m"]
    reference_bracket = events["reference"]["start_distance_bracket_m"]
    assert isinstance(target_bracket, tuple) and isinstance(reference_bracket, tuple)
    difference_bracket = (
        target_bracket[0] - reference_bracket[1],
        target_bracket[1] - reference_bracket[0],
    )
    censor_notes = []
    for side in ("target", "reference"):
        if events[side].get("right_censored") is True:
            censor_notes.append(f"{side} event continued to the end of observed support")
    note = f" {'; '.join(censor_notes)}." if censor_notes else ""
    facts.append(
        {
            "kind": kind,
            "channel": channel,
            "threshold": threshold,
            "target_start_bracket_m": list(target_bracket),
            "reference_start_bracket_m": list(reference_bracket),
            "target_minus_reference_start_bracket_m": list(difference_bracket),
            "right_censored": {
                "target": events["target"]["right_censored"],
                "reference": events["reference"]["right_censored"],
            },
            "source_fields": {
                "target_event": f"comparison_window.target.threshold_events.{event_key}.events[0]",
                "reference_event": (
                    "comparison_window.reference.threshold_events."
                    f"{event_key}.events[0]"
                ),
                "coverage": f"comparison_window.<side>.coverage.{channel}",
            },
            "provenance": provenance,
            "text": (
                f"{label} threshold onset was bracketed at "
                f"{_distance_range(target_bracket)} m (target) and "
                f"{_distance_range(reference_bracket)} m (reference); "
                f"the target-minus-reference onset difference is bounded to "
                f"{_signed_bound(difference_bracket[0], lower=True)} to "
                f"{_signed_bound(difference_bracket[1], lower=False)} m."
                f"{note}"
            ),
        }
    )


def _observed_value(
    attempt: Mapping[str, Any],
    field: str,
    value_key: str,
    start_m: float,
    end_m: float,
) -> tuple[float | None, Mapping[str, Any]]:
    observation = _mapping(attempt.get(field))
    value = _finite_number(observation.get(value_key))
    anchor = _mapping(observation.get("anchor"))
    if observation.get("status") != "observed" or value is None or value < 0:
        return None, anchor
    if field == "peak_brake" and value > 1.0:
        return None, anchor
    if field == "minimum_speed" and value_key == "speed_kph":
        return value, anchor
    if field == "peak_brake" and value_key == "value":
        return value, anchor
    return None, anchor


def _valid_source_anchor(
    anchor: Mapping[str, Any], start_m: float, end_m: float
) -> bool:
    frame = anchor.get("frame_identifier")
    session_time = _finite_number(anchor.get("session_time_s"))
    distance = _finite_number(anchor.get("lap_distance_m"))
    return (
        isinstance(frame, int)
        and not isinstance(frame, bool)
        and 0 <= frame <= 0xFFFFFFFF
        and session_time is not None
        and session_time >= 0
        and distance is not None
        and start_m <= distance < end_m
    )


def _bracket(value: object, start_m: float, end_m: float) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    low = _finite_number(value[0])
    high = _finite_number(value[1])
    if (
        low is None
        or high is None
        or low > high
        or low < start_m - 1e-7
        or high >= end_m
    ):
        return None
    return (low, high)


def _context_warnings(
    comparison: Mapping[str, object],
) -> tuple[list[dict[str, str]], int]:
    candidates: list[dict[str, str]] = []
    policy = comparison.get("comparison_policy")
    if policy == "practice_qualifying":
        candidates.append(
            {
                "code": "practice_qualifying_conditions_uncontrolled",
                "text": "Fuel load, tyre condition, traffic, and cooldown intent "
                "were not controlled.",
            }
        )
    if comparison.get("diagnostic_only") is True:
        candidates.append(
            {
                "code": "diagnostic_comparison",
                "text": "This pair is diagnostic and does not establish why the "
                "measurements differ.",
            }
        )
    for side in ("target", "reference"):
        attempt = _mapping(comparison.get(side))
        details: list[str] = []
        if attempt.get("game_valid") is False:
            details.append("the game marked the lap invalid")
        elif attempt.get("game_valid") is None:
            details.append("game validity is unknown")
        if attempt.get("superseded") is True:
            details.append("the lap was superseded by lifecycle evidence")
        exclusions = attempt.get("lifecycle_exclusions")
        reasons = (
            [value for value in exclusions[:3] if isinstance(value, str)]
            if isinstance(exclusions, list)
            else []
        )
        if attempt.get("lifecycle_assessed") is not True:
            details.append("lifecycle evidence is unassessed")
        if reasons:
            details.append("lifecycle exclusions: " + ", ".join(reasons))
        if details:
            candidates.append(
                {
                    "code": f"{side}_attempt_integrity",
                    "text": f"For the {side} lap, " + "; ".join(details) + ".",
                }
            )

    run_evidence = _mapping(comparison.get("processing_run_evidence"))
    seen_runs: set[str] = set()
    for side in ("target", "reference"):
        summary = _mapping(run_evidence.get(side))
        run_id = summary.get("run_id")
        if isinstance(run_id, str) and run_id in seen_runs:
            continue
        if isinstance(run_id, str):
            seen_runs.add(run_id)
        capture = _mapping(summary.get("capture"))
        processing = _mapping(summary.get("processing"))
        details = []
        if "complete" not in capture or capture.get("complete") is None:
            details.append("capture completion is unknown")
        elif capture.get("complete") is False:
            details.append("the capture footer is incomplete")
        recording = _mapping(capture.get("recording_counters"))
        recording_keys = ("queue_dropped", "unpersisted_on_shutdown", "socket_errors")
        recording_values = [_nonnegative_integer(recording.get(key)) for key in recording_keys]
        recording_total = sum(value for value in recording_values if value is not None)
        if any(value is None for value in recording_values):
            if recording_total:
                details.append(
                    f"recording reports at least {recording_total} known loss or "
                    "socket-error events; other counters are unknown"
                )
            else:
                details.append("some recording loss counters are unknown")
        elif recording_total:
            details.append(
                f"recording reports {recording_total} loss or socket-error events"
            )
        replay = _mapping(processing.get("replay_counters"))
        replay_keys = ("import_late_packets_ignored", "import_frame_overflow_packets_dropped")
        replay_values = [_nonnegative_integer(replay.get(key)) for key in replay_keys]
        replay_total = sum(value for value in replay_values if value is not None)
        if any(value is None for value in replay_values):
            if replay_total:
                details.append(
                    f"import reports at least {replay_total} known frame-admission "
                    "exclusions or drops; other counters are unknown"
                )
            else:
                details.append("some frame-admission loss counts are unknown")
        elif replay_total:
            details.append(
                f"import reports {replay_total} frame-admission exclusions or drops"
            )
        if details:
            matching_side_count = sum(
                _mapping(run_evidence.get(candidate)).get("run_id") == run_id
                for candidate in ("target", "reference")
            )
            side_label = (
                "shared"
                if isinstance(run_id, str) and matching_side_count > 1
                else side
            )
            candidates.append(
                {
                    "code": "capture_and_import_evidence",
                    "text": f"For the {side_label} run, " + "; ".join(details) + ".",
                }
            )
    return candidates[:MAX_DISTANCE_WINDOW_BRIEF_LIMITATIONS], max(
        0, len(candidates) - MAX_DISTANCE_WINDOW_BRIEF_LIMITATIONS
    )


def _nonnegative_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _attempt_provenance(attempt: Mapping[str, Any]) -> dict[str, object]:
    return {
        "attempt_key": attempt.get("attempt_key"),
        "run_id": attempt.get("run_id"),
        "trace_sha256": attempt.get("trace_sha256"),
    }


def _distance_range(value: tuple[float, float]) -> str:
    low = _outward_bound(value[0], lower=True)
    high = _outward_bound(value[1], lower=False)
    return f"{low:.1f}–{high:.1f}"


def _signed_bound(value: float, *, lower: bool) -> str:
    return f"{_outward_bound(value, lower=lower):+.1f}".replace("-", "−")


def _outward_bound(value: float, *, lower: bool) -> float:
    scale = 10.0
    scaled = value * scale
    rounded = math.floor(scaled + 1e-12) if lower else math.ceil(scaled - 1e-12)
    return rounded / scale


def _close_number(value: object, expected: float) -> bool:
    number = _finite_number(value)
    return number is not None and math.isclose(number, expected, rel_tol=0, abs_tol=1e-9)


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)
