from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


DIAGNOSTIC_REGION_DEBRIEF_SCHEMA_VERSION = 1
DIAGNOSTIC_REGION_DEBRIEF_VERSION = "diagnostic-region-debrief-v1"
MAX_DIAGNOSTIC_DEBRIEF_REGIONS = 64
MAX_DIAGNOSTIC_DEBRIEF_FACTS = 5
MAX_DIAGNOSTIC_DEBRIEF_OMISSIONS = 5
MAX_DIAGNOSTIC_DEBRIEF_TEXT_LENGTH = 240

_FACT_ORDER = (
    "connected_interval_time",
    "minimum_speed",
    "brake_10_percent_onset",
    "throttle_50_percent_onset",
    "exit_speed",
)


def build_diagnostic_region_debrief(
    supported_differences: Mapping[str, object],
    configured_windows: Mapping[str, object],
) -> dict[str, object]:
    """Build bounded explanatory text from an existing paired-region result."""
    facts: list[dict[str, object]] = []
    omissions: list[dict[str, str]] = []
    for kind in _FACT_ORDER:
        difference = _mapping(supported_differences.get(kind))
        if difference is None or difference.get("status") != "supported":
            reason = (
                difference.get("unavailable_reason")
                if difference is not None
                else None
            )
            omissions.append(_omission(kind, reason))
            continue

        fact = _fact(kind, difference, configured_windows)
        if fact is None:
            omissions.append(_omission(kind, "supported_measurement_malformed"))
        else:
            facts.append(fact)

    if len(facts) > MAX_DIAGNOSTIC_DEBRIEF_FACTS or len(omissions) > MAX_DIAGNOSTIC_DEBRIEF_OMISSIONS:
        raise ValueError("diagnostic_region_debrief_limit_exceeded")
    if any(len(str(item["text"])) > MAX_DIAGNOSTIC_DEBRIEF_TEXT_LENGTH for item in (*facts, *omissions)):
        raise ValueError("diagnostic_region_debrief_text_limit_exceeded")
    return {
        "schema_version": DIAGNOSTIC_REGION_DEBRIEF_SCHEMA_VERSION,
        "analysis_version": DIAGNOSTIC_REGION_DEBRIEF_VERSION,
        "diagnostic_only": True,
        "coaching_eligible": False,
        "ranking_eligible": False,
        "facts": facts,
        "omissions": omissions,
    }


def _fact(
    kind: str,
    difference: Mapping[str, Any],
    configured_windows: Mapping[str, object],
) -> dict[str, object] | None:
    if kind == "connected_interval_time":
        value = _finite_number(difference.get("value"))
        if value is None or difference.get("unit") != "s":
            return None
        rounded = f"{abs(value):.3f}"
        if round(value, 3) == 0:
            text = f"Connected-interval time change rounds to {rounded} s (target versus reference)."
        else:
            direction = "longer" if value > 0 else "shorter"
            text = f"Across connected support, target took {rounded} s {direction} than reference."
        fields = ["supported_differences.connected_interval_time.value"]
    elif kind == "minimum_speed":
        target = _finite_number(difference.get("target_value"))
        reference = _finite_number(difference.get("reference_value"))
        value = _finite_number(difference.get("value"))
        if (
            target is None
            or reference is None
            or value is None
            or difference.get("unit") != "km/h"
            or not math.isclose(value, target - reference, rel_tol=0.0, abs_tol=1e-6)
        ):
            return None
        if round(value, 2) == 0:
            text = (
                f"Observed minimum speed difference rounds to 0.00 km/h "
                f"({target:.2f} target; {reference:.2f} reference)."
            )
            fields = [
                "supported_differences.minimum_speed.target_value",
                "supported_differences.minimum_speed.reference_value",
                "supported_differences.minimum_speed.value",
            ]
            return {"kind": kind, "text": text, "source_fields": fields}
        if value == 0:
            text = (
                f"Observed minimum speed was the same on both attempts "
                f"({target:.2f} km/h target; {reference:.2f} km/h reference)."
            )
        else:
            direction = "higher than" if value > 0 else "lower than"
            text = (
                f"Observed minimum speed on target was {abs(value):.2f} km/h {direction} reference "
                f"({target:.2f} vs {reference:.2f} km/h)."
            )
        fields = [
            "supported_differences.minimum_speed.target_value",
            "supported_differences.minimum_speed.reference_value",
            "supported_differences.minimum_speed.value",
        ]
    elif kind in {"brake_10_percent_onset", "throttle_50_percent_onset"}:
        target_bounds = _numeric_bracket(difference.get("target_start_bracket_m"))
        reference_bounds = _numeric_bracket(difference.get("reference_start_bracket_m"))
        delta_bounds = _numeric_bracket(
            difference.get("target_minus_reference_start_bracket_m")
        )
        censoring = _mapping(difference.get("right_censored"))
        if (
            target_bounds is None
            or reference_bounds is None
            or delta_bounds is None
            or censoring is None
            or not isinstance(censoring.get("target"), bool)
            or not isinstance(censoring.get("reference"), bool)
            or not math.isclose(
                delta_bounds[0], target_bounds[0] - reference_bounds[1], rel_tol=0.0, abs_tol=1e-6
            )
            or not math.isclose(
                delta_bounds[1], target_bounds[1] - reference_bounds[0], rel_tol=0.0, abs_tol=1e-6
            )
        ):
            return None
        target = _format_distance_bracket(target_bounds)
        reference = _format_distance_bracket(reference_bounds)
        delta = _format_distance_bracket(delta_bounds)
        event = "Brake" if kind == "brake_10_percent_onset" else "Throttle"
        text = (
            f"{event} onset brackets: target {target}; reference {reference}; "
            f"target minus reference {delta}."
        )
        censored_sides = [side for side in ("target", "reference") if censoring[side]]
        if censored_sides:
            sides = " and ".join(censored_sides)
            text = text[:-1] + f"; {sides} event continued to observed support end."
        fields = [
            f"supported_differences.{kind}.target_start_bracket_m",
            f"supported_differences.{kind}.reference_start_bracket_m",
            f"supported_differences.{kind}.target_minus_reference_start_bracket_m",
            f"supported_differences.{kind}.right_censored",
        ]
    elif kind == "exit_speed":
        target = _finite_number(difference.get("target_value"))
        reference = _finite_number(difference.get("reference_value"))
        value = _finite_number(difference.get("value"))
        distance = _finite_number(difference.get("distance_m"))
        if (
            target is None
            or reference is None
            or value is None
            or distance is None
            or difference.get("unit") != "km/h"
            or not math.isclose(value, target - reference, rel_tol=0.0, abs_tol=1e-6)
            or _finite_number(configured_windows.get("exit_distance_m")) != distance
        ):
            return None
        if round(value, 2) == 0:
            text = (
                f"At the configured exit ({distance:.1f} m), target-reference speed "
                f"difference rounds to 0.00 km/h ({target:.2f} target; "
                f"{reference:.2f} reference)."
            )
            fields = [
                "supported_differences.exit_speed.distance_m",
                "supported_differences.exit_speed.target_value",
                "supported_differences.exit_speed.reference_value",
                "supported_differences.exit_speed.value",
            ]
            return {"kind": kind, "text": text, "source_fields": fields}
        if value == 0:
            text = (
                f"At the configured exit ({distance:.1f} m), target speed was the same "
                f"as reference ({target:.2f} km/h)."
            )
        else:
            direction = "higher than" if value > 0 else "lower than"
            text = (
                f"At the configured exit ({distance:.1f} m), target speed was "
                f"{abs(value):.2f} km/h {direction} reference "
                f"({target:.2f} vs {reference:.2f} km/h)."
            )
        fields = [
            "supported_differences.exit_speed.distance_m",
            "supported_differences.exit_speed.target_value",
            "supported_differences.exit_speed.reference_value",
            "supported_differences.exit_speed.value",
        ]
    else:
        return None
    if len(text) > MAX_DIAGNOSTIC_DEBRIEF_TEXT_LENGTH:
        return None
    return {"kind": kind, "text": text, "source_fields": fields}


def _omission(kind: str, reason: object) -> dict[str, str]:
    reason_code = (
        reason.strip()[:96]
        if isinstance(reason, str) and reason.strip()
        else "measurement_unavailable"
    )
    explanation = " ".join(reason_code.replace("_", " ").split())
    text = f"{kind.replace('_', ' ')} omitted: {explanation}."
    if len(text) > MAX_DIAGNOSTIC_DEBRIEF_TEXT_LENGTH:
        text = text[: MAX_DIAGNOSTIC_DEBRIEF_TEXT_LENGTH - 1].rstrip() + "…"
    return {"kind": kind, "reason_code": reason_code, "text": text}


def _numeric_bracket(value: object) -> tuple[float, float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        return None
    lower = _finite_number(value[0])
    upper = _finite_number(value[1])
    if lower is None or upper is None or lower > upper:
        return None
    return lower, upper


def _format_distance_bracket(bounds: tuple[float, float]) -> str:
    lower, upper = bounds
    lower = math.floor(lower * 10 + 1e-9) / 10
    upper = math.ceil(upper * 10 - 1e-9) / 10
    return f"{lower:.1f}–{upper:.1f} m"


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None
