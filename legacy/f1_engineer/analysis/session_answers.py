from __future__ import annotations

import math


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


BRAKING_CAVEAT = (
    "These are numeric distance regions, not named corners; "
    "later braking is not automatically better."
)


def braking_observation_message(report: dict, *, max_observations: int = 3,
                                include_interpretations: bool = True) -> str | None:
    observations = braking_observations(report, limit=max_observations)
    if not observations:
        return None
    text = " ".join(observations)
    return text + " " + BRAKING_CAVEAT if include_interpretations else text


def braking_observations(report: dict, *, limit: int = 3) -> list[str]:
    """Observation sentences for supported braking zones, at most ``limit`` (max 3)."""
    observations = []
    if limit < 1:
        return observations
    for zone in report.get("braking_zones", []):
        bounds = zone.get("region_m")
        interval = zone.get("difference_interval_m")
        difference = zone.get("target_minus_reference_m")
        if (zone.get("supported") is not True or not _number(difference)
                or not isinstance(bounds, list) or len(bounds) != 2
                or not all(_number(value) for value in bounds)
                or not isinstance(interval, list) or len(interval) != 2
                or not all(_number(value) for value in interval)):
            continue
        direction = "later" if difference > 0 else "earlier" if difference < 0 else "at the same distance"
        observations.append(
            f"In the observed {bounds[0]:g}-{bounds[1]:g} m region, the target's sustained "
            f"10% brake onset is {abs(difference):g} m {direction}; "
            f"the target-minus-reference bracket is {interval[0]:g} to {interval[1]:g} m."
        )
        if len(observations) >= min(3, max(limit, 0)):
            break
    return observations
