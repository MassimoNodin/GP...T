from __future__ import annotations

from copy import deepcopy
import math
import re

from .opponent_captures import RadioOpponentCaptures
from .detail_demands import RadioDetailDemands
from .region_progress import measured_reference
from .regions import RegionWindow


def compare_opponent_region(request, captured, *, lap_number, label, now):
    unavailable = ("Opponent region comparison unavailable: no fresh matching complete diagnostic evidence.", None)
    try:
        return _compare(request, captured, lap_number=lap_number, label=label, now=now) or unavailable
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        return unavailable


def _compare(request, captured, *, lap_number, label, now):
    if (not RadioOpponentCaptures._valid(request) or not isinstance(captured, dict)
            or any(captured.get(key) != value for key, value in request.items())
            or not RadioOpponentCaptures._valid({key: captured.get(key) for key in request})
            or captured.get("state") not in ("collecting", "complete")
            or type(lap_number) is not int
            or not request["body"]["first_lap"] <= lap_number < request["body"]["first_lap"] + request["body"]["laps"]
            or not isinstance(label, str) or not label.strip() or len(label.encode("utf-8")) > 80
            or any(ord(character) < 32 for character in label)
            or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                   for value in (now, captured.get("received_at"), captured.get("request_age_s"), captured.get("frame_age_s")))
            or now < captured["received_at"]
            or now - captured["received_at"] + max(captured["request_age_s"], captured["frame_age_s"]) >= 3):
        return None
    owner = captured.get("owner")
    if (not isinstance(owner, dict) or owner.get("state") != "active"
            or any(owner.get(key) != value for key, value in RadioOpponentCaptures._demand(request).items())
            or not RadioDetailDemands._valid({key: owner.get(key) for key in RadioOpponentCaptures._demand(request)})
            or not isinstance(owner.get("detail_task_id"), str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", owner["detail_task_id"]) is None
            or not isinstance(owner.get("binding_id"), str)
            or re.fullmatch(r"[a-f0-9]{64}", owner["binding_id"]) is None
            or not isinstance(owner.get("activation"), (list, tuple)) or len(owner["activation"]) != 2
            or any(type(value) is not int for value in owner["activation"])
            or owner["activation"][0] < 1 or owner["activation"][1] < 0):
        return None
    legs = captured.get("legs")
    if not isinstance(legs, list) or len(legs) != 2:
        return None
    opponent_body = {**request["body"], "opponent": request["opponent"],
                     "first_lap": request["opponent_first_lap"], "detail_task_id": owner["detail_task_id"]}
    expected_bodies = (request["body"], opponent_body)
    if (any(not isinstance(leg, dict) or leg.get("body") != body
            or type(leg.get("task_id")) is not int or leg["task_id"] != request["task_id"]
            or type(leg.get("version")) is not int or leg["version"] != request["version"]
            or not isinstance(leg.get("lease_id"), str) or re.fullmatch(r"[a-f0-9]{32}", leg["lease_id"]) is None
            for leg, body in zip(legs, expected_bodies))
            or legs[0]["lease_id"] == legs[1]["lease_id"]):
        return None
    for leg in legs:
        body = {key: value for key, value in leg["body"].items() if key not in ("opponent", "detail_task_id")}
        if (not RadioOpponentCaptures._valid({**request, "body": body,
                                             "opponent": leg["body"].get("opponent", request["opponent"])})
                or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                       for value in (leg.get("received_at"), leg.get("request_age_s")))
                or now < leg["received_at"] or now - leg["received_at"] + leg["request_age_s"] >= 3):
            return None
    windows = [leg["response"]["data"]["window"] for leg in legs]
    if windows[0] != windows[1]:
        return None
    window = RegionWindow(windows[0]["start_m"], windows[0]["end_m"], windows[0]["track_length_m"])
    if any(not RadioOpponentCaptures._matching_result(leg, window.track_length_m) for leg in legs):
        return None
    opponent_lap = request["opponent_first_lap"] + lap_number - request["body"]["first_lap"]
    records = []
    references = []
    for leg, number in zip(legs, (lap_number, opponent_lap)):
        data = leg["response"]["data"]
        if data.get("state") not in ("active", "complete"):
            return None
        matches = [result for result in data["results"] if result["lap_number"] == number]
        if len(matches) != 1:
            return None
        record = matches[0]
        reference = measured_reference(window, record["completion"], record.get("evidence"))
        if reference is None:
            return None
        records.append(record)
        references.append(reference)
    player, opponent = references
    same_compound = player.actual_compound == opponent.actual_compound
    elapsed_delta = player.elapsed_s - opponent.elapsed_s if same_compound else None
    speed_delta = player.minimum_speed_kph - opponent.minimum_speed_kph if same_compound else None
    shift = None
    if same_compound and player.brake_bracket_m is not None and opponent.brake_bracket_m is not None:
        shift = (player.brake_bracket_m[0] - opponent.brake_bracket_m[1],
                 player.brake_bracket_m[1] - opponent.brake_bracket_m[0])
    text = (f"Your lap {player.lap_number} versus {label.strip()}'s lap {opponent.lap_number}, "
            f"region {window.start_m:g} to {window.end_m:g} metres: "
            f"sampled minimum {player.minimum_speed_kph:.1f} versus {opponent.minimum_speed_kph:.1f} kilometres per hour. "
            f"Region time {player.elapsed_s:.3f} versus {opponent.elapsed_s:.3f} seconds. ")
    for name, reference in (("Your", player), (label.strip() + "'s", opponent)):
        bracket = reference.brake_bracket_m
        text += (f"{name} brake onset {bracket[0]:g} to {bracket[1]:g} metres. " if bracket is not None
                 else f"{name} brake onset unavailable. ")
    if same_compound:
        if abs(speed_delta) >= 0.05:
            text += f"Your sampled minimum is {abs(speed_delta):.1f} kilometres per hour {'higher' if speed_delta > 0 else 'lower'}. "
        else:
            text += "Sampled minima are within 0.1 kilometres per hour. "
        if shift is not None:
            direction = "later" if shift[0] > 0 else "earlier" if shift[1] < 0 else "direction unresolved"
            text += f"Your onset offset {shift[0]:g} to {shift[1]:g} metres, {direction}. "
        text += f"Same compound {player.actual_compound}; "
    else:
        text += f"Different compounds {player.actual_compound} and {opponent.actual_compound}; no like-for-like deltas. "
    text += "tyre age, fuel, conditions and traffic not verified. Independent full laps; numeric diagnostic, not coaching."
    if len(text.encode("utf-8")) > 900:
        return None
    return text, {"player": player.view(), "opponent": opponent.view(), "opponent_identity": deepcopy(request["opponent"]),
                  "player_completion": deepcopy(records[0]["completion"]),
                  "opponent_completion": deepcopy(records[1]["completion"]),
                  "completion_ages_s": [record["completion_age_s"] for record in records],
                  "elapsed_delta_s": elapsed_delta, "minimum_speed_delta_kph": speed_delta,
                  "brake_shift_bracket_m": list(shift) if shift is not None else None,
                  "same_compound": same_compound, "missing_context": ["tyre_age", "fuel", "conditions", "traffic"],
                  "diagnostic_only": True, "coaching_eligible": False, "ranking_eligible": False}
