from __future__ import annotations

from copy import deepcopy

from .region_progress import RegionReference
from ..analysis.region_context import compare_reported_context


FOCUS_POLICY = "observed-numeric-repeatability-v1"
_LIMITS = {"tyre_age_laps": 3, "track_temperature_c": 5, "air_temperature_c": 5}
_BOUNDS = {"weather_id": (0, 5), "tyre_age_laps": (0, 255),
           "track_temperature_c": (-128, 127), "air_temperature_c": (-128, 127)}


def _ranges(context):
    if not isinstance(context, dict):
        return {}
    if isinstance(context.get("reported_ranges"), dict):
        return context["reported_ranges"]
    return {key: [value, value] for key, value in context.items() if type(value) is int}


def compatible_focus_context(reference, current):
    left, right = _ranges(reference), _ranges(current)
    for key in ("weather_id", *_LIMITS):
        if any(type(values.get(key)) is not list or len(values[key]) != 2
               or any(type(value) is not int or not _BOUNDS[key][0] <= value <= _BOUNDS[key][1]
                      for value in values[key])
               or values[key][0] > values[key][1] for values in (left, right)):
            return False, f"reported {key.replace('_', ' ')} missing"
    if left["weather_id"] != right["weather_id"] or left["weather_id"][0] != left["weather_id"][1]:
        return False, "reported weather changed or varied within the region"
    for key, limit in _LIMITS.items():
        if max(abs(left[key][0] - right[key][1]), abs(left[key][1] - right[key][0])) > limit:
            return False, f"reported {key.replace('_', ' ')} exceeds the focus policy tolerance"
    return True, None


def choose_focus_reference(candidates, window, compound, current_context):
    eligible = [reference for reference in candidates
                if isinstance(reference, RegionReference) and reference.window == window and reference.actual_compound == compound
                and compatible_focus_context(reference.observed_context, current_context)[0]]
    if not eligible:
        raise ValueError("No complete retained same-compound region meets the focus context policy. "
                         "Track this region with known weather, temperatures and tyre age first; no coaching task started.")
    return deepcopy(min(eligible, key=lambda reference: (reference.elapsed_s, reference.lap_number)))


def choose_focus_loss(candidates, player, current_context):
    lap = player.get("lap", {})
    tyre = player.get("tyre")
    value = tyre.get("value") if isinstance(tyre, dict) and tyre.get("status") == "available" else None
    compound = value.get("actual_compound") if isinstance(value, dict) else None
    number = lap.get("current_lap_number")
    if (type(number) is not int or not 2 <= number <= 254 or type(compound) is not int
            or not 1 <= compound <= 255
            or any(type(lap.get(key)) is not int or lap[key] != expected for key, expected in
                   (("current_lap_invalid_id", 0), ("pit_status_id", 0), ("result_status_id", 2)))):
        return None
    candidates = tuple(reference for reference in candidates if isinstance(reference, RegionReference))
    choices = []
    for current in candidates:
        if current.lap_number != number - 1 or current.actual_compound != compound:
            continue
        previous = [reference for reference in candidates if reference.lap_number < current.lap_number]
        try:
            reference = choose_focus_reference(previous, current.window, compound, current_context)
        except ValueError:
            continue
        if not compatible_focus_context(reference.observed_context, current.observed_context)[0]:
            continue
        delta = current.elapsed_s - reference.elapsed_s
        if delta >= 0.001:
            choices.append((delta, current, reference))
    if not choices:
        return None
    delta, current, reference = min(choices, key=lambda choice: (-choice[0], choice[1].window.start_m,
                                                               choice[1].window.end_m, choice[2].lap_number))
    return current.window, {"selection": "largest_retained_supported_region_loss",
                            "candidate_windows": len(choices), "loss_s": delta,
                            "target_lap": current.lap_number, "reference_lap": reference.lap_number,
                            "whole_circuit_ranking": False, "causation_verified": False}


def focus_cue(reference):
    if reference.brake_bracket_m is not None:
        start, end = reference.brake_bracket_m
        return (f"One focus: make your braking transition repeatable, comparing it with the reference's "
                f"{start:g} to {end:g} metre onset range. Do not move braking later just to match it.")
    return (f"One focus: make the minimum-speed phase repeatable. The reference's sampled minimum was "
            f"{reference.minimum_speed_kph:.1f} kilometres per hour, a comparison rather than a speed target.")


def focus_progress(reference, comparison):
    if comparison is None:
        return "Focus progress unavailable: incomplete or invalid regional evidence. The reference stays fixed.", None
    current = comparison["current"]
    compatible, reason = compatible_focus_context(reference.observed_context, current.get("observed_context"))
    if not compatible:
        return f"Focus progress unavailable: {reason}. The reference stays fixed; no new coaching suggestion.", None
    delta = comparison["elapsed_delta_s"]
    direction = "longer" if delta >= 0 else "shorter"
    text = (f"Region {reference.window.start_m:g} to {reference.window.end_m:g} metres, fixed best retained lap "
            f"{reference.lap_number}: measured time {abs(delta):.3f} seconds {direction}; "
            f"sampled minimum {current['minimum_speed_kph']:.1f} versus {reference.minimum_speed_kph:.1f} "
            f"kilometres per hour. {focus_cue(reference)} ")
    context = comparison["reported_context_comparison"]
    context_text, _ = compare_reported_context(current.get("observed_context"), reference.observed_context)
    text += context_text
    if context["different_reported_fields"]:
        text += "Reported context differs within the stated focus tolerances. "
    text += "Same compound; sampled context only. Fuel, traffic and physical layout unverified; no proven cause or guaranteed gain."
    return text, {"policy": FOCUS_POLICY, "reference": reference.view(),
                  "comparison": deepcopy(comparison), "suggestion_only": True,
                  "continuous_equivalence_verified": False, "layout_verified": False,
                  "causation_verified": False, "fuel_verified": False, "traffic_verified": False,
                  "maximum_reported_differences": deepcopy(_LIMITS), "weather_must_match": True}
