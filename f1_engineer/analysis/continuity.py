from __future__ import annotations

import math


MAX_SESSION_TIME_GAP_S = 0.100


def session_time_discontinuity(
    previous_s: float,
    current_s: float,
    *,
    max_gap_s: float = MAX_SESSION_TIME_GAP_S,
) -> str | None:
    """Classify session-time gaps and regressions with source float32 tolerance."""
    tolerance = max(float32_ulp(previous_s), float32_ulp(current_s))
    if current_s < previous_s - tolerance:
        return "session_time_regression"
    if current_s - previous_s > max_gap_s + tolerance:
        return "session_time_gap"
    return None


def float32_ulp(value: float) -> float:
    magnitude = abs(value)
    if magnitude == 0 or magnitude < 2**-126:
        return 2**-149
    return 2 ** (math.floor(math.log2(magnitude)) - 23)
