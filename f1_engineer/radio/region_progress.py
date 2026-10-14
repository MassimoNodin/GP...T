from __future__ import annotations

import math
from collections import OrderedDict, deque
from copy import deepcopy
from dataclasses import dataclass

from .regions import RegionWindow
from ..analysis.region_context import validate_region_context, compare_reported_context


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def _number(value, minimum, maximum):
    return type(value) in (int, float) and minimum <= value <= maximum and math.isfinite(value)


@dataclass(frozen=True, slots=True)
class RegionReference:
    lap_number: int
    actual_compound: int
    window: RegionWindow
    elapsed_s: float
    minimum_speed_kph: float
    brake_bracket_m: tuple[float, float] | None
    observed_context: dict | None = None

    def __post_init__(self):
        if self.observed_context is not None and (
                not isinstance(self.observed_context, dict)
                or validate_region_context(self.observed_context, self.observed_context.get("sample_count")) is None):
            raise ValueError("A regional reference requires bounded reported context without coaching eligibility.")
        if (not _integer(self.lap_number, 1, 255) or not _integer(self.actual_compound, 1, 255)
                or not isinstance(self.window, RegionWindow) or not _number(self.elapsed_s, 0, 86_400)
                or self.elapsed_s <= 0
                or not _number(self.minimum_speed_kph, 0, 65535)):
            raise ValueError("A regional reference needs a complete measured window and known lap/compound.")
        if self.brake_bracket_m is not None:
            bracket = self.brake_bracket_m
            if (type(bracket) is not tuple or len(bracket) != 2
                    or not all(_number(value, self.window.start_m, self.window.end_m) for value in bracket)
                    or bracket[0] > bracket[1]):
                raise ValueError("A regional brake reference requires a bounded measured onset bracket.")

    def view(self):
        return {"lap_number": self.lap_number, "actual_compound": self.actual_compound,
                "window": self.window.view(), "elapsed_s": self.elapsed_s,
                "minimum_speed_kph": self.minimum_speed_kph,
                "brake_bracket_m": list(self.brake_bracket_m) if self.brake_bracket_m is not None else None,
                "observed_context": deepcopy(self.observed_context),
                "diagnostic_only": True, "coaching_eligible": False, "ranking_eligible": False}


def measured_reference(window, event, evidence):
    if (not isinstance(window, RegionWindow) or not isinstance(event, dict) or not isinstance(evidence, dict)
            or type(event.get("previous_observed_invalid")) is not int
            or event.get("kind") != "lap_completed" or event.get("qualification") != "observed_lap_transition"
            or event["previous_observed_invalid"] != 0 or not _integer(event.get("lap_number"), 1, 255)
            or not _integer(event.get("lap_time_ms"), 1, 2**32 - 1)
            or type(evidence.get("lap_number")) is not int or evidence["lap_number"] != event["lap_number"]
            or not _integer(evidence.get("actual_compound"), 1, 255)
            or any(not _integer(evidence.get(key), 0, 2**32 - 1)
                   for key in ("first_frame_identifier", "last_frame_identifier"))):
        return None
    measurement = evidence.get("measurement")
    if (not isinstance(measurement, dict) or measurement.get("analysis_version") != "numeric-distance-window-v1"
            or measurement.get("analysis_window_m") != [window.start_m, window.end_m]
            or measurement.get("track_length_m") != window.track_length_m
            or measurement.get("source_session_time_connected") is not True
            or measurement.get("diagnostic_only") is not True
            or any(measurement.get(key) is not False for key in
                   ("layout_verified", "coaching_eligible", "ranking_eligible"))
            or not _integer(measurement.get("source_sample_count"), 2, 4096)):
        return None
    coverage = measurement.get("supported_grid_coverage")
    elapsed = measurement.get("elapsed")
    observations = measurement.get("observations")
    if (not isinstance(coverage, dict) or any(not _number(coverage.get(key), 1, 1) for key in ("time_s", "speed_mps"))
            or not isinstance(elapsed, dict) or elapsed.get("status") != "supported"
            or not _number(elapsed.get("elapsed_s"), 0, event["lap_time_ms"] / 1000)
            or elapsed["elapsed_s"] <= 0
            or not isinstance(observations, dict)):
        return None
    minimum = observations.get("minimum_speed")
    if (not isinstance(minimum, dict) or minimum.get("status") != "observed_minimum_complete_window"
            or not _number(minimum.get("speed_kph"), 0, 65535)
            or not _number(minimum.get("distance_m"), window.start_m, window.end_m)
            or not _number(minimum.get("supported_grid_coverage"), 1, 1)
            or not _integer(minimum.get("observed_sample_count"), 1, measurement["source_sample_count"])):
        return None
    braking = observations.get("braking")
    bracket = braking.get("distance_bracket_m") if isinstance(braking, dict) else None
    if (not isinstance(braking, dict) or braking.get("status") != "detected"
            or not _number(coverage.get("brake"), 1, 1)):
        bracket = None
    elif (type(bracket) is not list or len(bracket) != 2
            or not all(_number(value, window.start_m, window.end_m) for value in bracket)
            or bracket[0] > bracket[1]):
        bracket = None
    return RegionReference(event["lap_number"], evidence["actual_compound"], window,
                           elapsed["elapsed_s"], minimum["speed_kph"],
                           tuple(bracket) if bracket is not None else None,
                           validate_region_context(evidence.get("observed_context"), measurement["source_sample_count"]))


class RegionProgress:
    def __init__(self):
        self.clear()

    def clear(self):
        self.references = OrderedDict()
        self.focus_candidates = deque(maxlen=64)

    def _key(self, window, compound):
        return window, compound

    def observe(self, window, event, evidence, *, focus_allowed=True):
        reference = measured_reference(window, event, evidence)
        if reference is None:
            return
        key = self._key(window, reference.actual_compound)
        previous = self.references.get(key)
        if previous is not None and reference.lap_number <= previous.lap_number:
            return
        self.references[key] = reference
        if focus_allowed:
            self.focus_candidates.append(reference)
        self.references.move_to_end(key)
        while len(self.references) > 8:
            self.references.popitem(last=False)

    def reference(self, window, player):
        lap = player.get("lap", {})
        tyre = player.get("tyre")
        value = tyre.get("value") if isinstance(tyre, dict) and tyre.get("status") == "available" else None
        compound = value.get("actual_compound") if isinstance(value, dict) else None
        if (type(lap.get("current_lap_invalid_id")) is not int or lap["current_lap_invalid_id"] != 0
                or type(lap.get("pit_status_id")) is not int or lap["pit_status_id"] != 0
                or type(lap.get("result_status_id")) is not int or lap["result_status_id"] != 2
                or not _integer(compound, 1, 255)):
            raise ValueError("Regional comparisons need a known on-track player and tyre compound.")
        reference = self.references.get(self._key(window, compound))
        if reference is None:
            raise ValueError("Track this numeric region first; no complete previous same-compound observation is retained.")
        return reference

    def compare(self, reference, event, evidence):
        current = measured_reference(reference.window, event, evidence)
        if current is None or current.lap_number <= reference.lap_number:
            return "Fixed-reference comparison unavailable: incomplete or invalid regional evidence.", None
        if current.actual_compound != reference.actual_compound:
            return "Fixed-reference comparison unavailable: tyre compound differs from the reference.", None
        elapsed_delta = current.elapsed_s - reference.elapsed_s
        speed_delta = current.minimum_speed_kph - reference.minimum_speed_kph
        shift = None
        if current.brake_bracket_m is not None and reference.brake_bracket_m is not None:
            shift = (current.brake_bracket_m[0] - reference.brake_bracket_m[1],
                     current.brake_bracket_m[1] - reference.brake_bracket_m[0])
        elapsed_text = (f"{abs(elapsed_delta):.3f} seconds {'longer' if elapsed_delta > 0 else 'shorter'}"
                        if abs(elapsed_delta) >= 0.0005 else "within 0.001 seconds")
        speed_text = (f"{abs(speed_delta):.1f} kilometres per hour {'higher' if speed_delta > 0 else 'lower'}"
                      if abs(speed_delta) >= 0.05 else "within 0.1 kilometres per hour")
        text = (f"Region {reference.window.start_m:g} to {reference.window.end_m:g} metres against "
                f"fixed observed lap {reference.lap_number} on compound {reference.actual_compound}: "
                f"region time {elapsed_text}; sampled minimum {speed_text}. ")
        if shift is None:
            text += "Brake-onset shift unavailable. "
        else:
            direction = "later" if shift[0] > 0 else "earlier" if shift[1] < 0 else "direction unresolved"
            text += f"Brake-onset shift {shift[0]:g} to {shift[1]:g} metres, {direction}. "
        context_text, context_evidence = compare_reported_context(current.observed_context, reference.observed_context)
        text += context_text
        if context_text and context_evidence["missing_reported_fields"]:
            text += "Some reported context unavailable. "
        text += ("Same compound; context is sampled, not verified equivalence; fuel and traffic unverified. " if context_text else
                 "Same compound only; tyre age, fuel, conditions and traffic not established. ")
        text += "Diagnostic, not coaching."
        return text, {"reference": reference.view(), "current": current.view(),
                      "elapsed_delta_s": elapsed_delta, "minimum_speed_delta_kph": speed_delta,
                      "brake_shift_bracket_m": list(shift) if shift is not None else None,
                      "reported_context_comparison": context_evidence,
                      "diagnostic_only": True, "coaching_eligible": False, "ranking_eligible": False}
