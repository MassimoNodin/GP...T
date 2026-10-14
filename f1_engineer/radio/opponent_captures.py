from __future__ import annotations

import copy
import math
import re
import threading
import time
import uuid

from .detail_demands import RadioDetailDemands
from .region_captures import RadioRegionCaptures


class RadioOpponentCaptures:
    def __init__(self, client, *, notify=None, clock=time.monotonic, clock_ns=time.monotonic_ns):
        self.clock_ns = clock_ns
        self.demands = RadioDetailDemands(client, notify=notify, clock=clock)
        self.captures = RadioRegionCaptures(client, notify=notify, clock=clock)
        self._lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._desired = []
        self._pair = None

    @staticmethod
    def _demand(request):
        return {"task_id": request["task_id"], "version": request["version"],
                "session_id": request["body"]["session_id"], "scope": request["body"]["scope"],
                "opponent": request["opponent"]}

    @classmethod
    def _valid(cls, request):
        if (not isinstance(request, dict)
                or set(request) != {"task_id", "version", "body", "opponent", "opponent_first_lap"}
                or not isinstance(request["body"], dict)
                or set(request["body"]) != {"generation", "session_id", "scope", "player", "first_lap", "laps", "window_m"}):
            return False
        body = request["body"]
        if not RadioDetailDemands._valid(cls._demand(request)):
            return False
        player = {**cls._demand(request), "opponent": body["player"]}
        window = body["window_m"]
        return (RadioDetailDemands._valid(player) and body["player"] != request["opponent"]
                and body["player"]["car_index"] != request["opponent"]["car_index"]
                and isinstance(body["generation"], str)
                and re.fullmatch(r"[a-f0-9]{32}", body["generation"]) is not None
                and type(body["laps"]) is int and 1 <= body["laps"] <= 20
                and all(type(number) is int and 1 <= number <= 256 - body["laps"]
                        for number in (body["first_lap"], request["opponent_first_lap"]))
                and isinstance(window, list) and len(window) == 2
                and all(type(number) in (int, float) and math.isfinite(number) and number >= 0 for number in window)
                and window[0] < window[1] <= 20_000 and window[1] - window[0] <= 1000)

    def replace(self, requests):
        copied = copy.deepcopy(requests)
        if (not isinstance(copied, list) or len(copied) > 1
                or any(not self._valid(item) for item in copied)):
            raise ValueError("opponent_capture_descriptors_invalid")
        with self._lock:
            if (self._desired and copied
                    and all(self._desired[0][key] == copied[0][key] for key in ("task_id", "version"))
                    and self._desired != copied):
                raise ValueError("opponent_capture_identity_conflict")
            self._desired = copied

    def _wanted(self, request):
        with self._lock:
            return request in self._desired

    @staticmethod
    def _row(snapshot, identity):
        rows = snapshot.get("drivers")
        if not isinstance(rows, list) or len(rows) > 24:
            return None
        matches = [row for row in rows if isinstance(row, dict)
                   and type(row.get("car_index")) is int and row["car_index"] == identity["car_index"]]
        if len(matches) != 1:
            return None
        row = matches[0]
        actual = row.get("identity")
        if (not isinstance(actual, dict) or set(actual) != set(identity)
                or any(type(actual[key]) is not type(value) or actual[key] != value for key, value in identity.items())
                or not isinstance(row.get("lap"), dict)):
            return None
        return row

    def _frame(self, request, snapshot):
        body = request["body"]
        if not isinstance(snapshot, dict):
            return None, "opponent_frame_unverified"
        received = snapshot.get("received_monotonic_ns")
        if (snapshot.get("status") != "available" or type(received) is not int
                or not 0 <= self.clock_ns() - received < 3_000_000_000):
            return None, "opponent_frame_unverified"
        scope = snapshot.get("scope")
        if (not isinstance(scope, dict) or set(scope) != set(body["scope"])
                or any(type(scope[key]) is not int for key in scope)
                or any(snapshot.get(key) != body[key] for key in ("generation", "session_id", "scope"))):
            return None, "opponent_scope_changed"
        if (type(snapshot.get("player_car_index")) is not int
                or snapshot["player_car_index"] != body["player"]["car_index"]):
            return None, "opponent_player_changed"
        context = snapshot.get("context")
        context_value = context.get("value") if isinstance(context, dict) and context.get("status") == "available" else None
        length = context_value.get("track_length_m") if isinstance(context_value, dict) else None
        if (type(length) not in (int, float) or not math.isfinite(length)
                or not body["window_m"][1] <= length <= 20_000
                or any(type(context_value.get(key)) is not int or context_value[key] != body["scope"][key]
                       for key in ("session_uid", "packet_format"))):
            return None, "opponent_geometry_unavailable"
        if self._pair["legs"] is not None and length != self._pair["track_length_m"]:
            return None, "opponent_geometry_changed"
        rows = [self._row(snapshot, identity) for identity in (body["player"], request["opponent"])]
        if any(row is None for row in rows):
            return None, "opponent_identity_changed"
        if self._pair["legs"] is None:
            for row, first in zip(rows, (body["first_lap"], request["opponent_first_lap"])):
                number = row["lap"].get("current_lap_number")
                if type(number) is not int or number != first - 1:
                    return None, "opponent_requested_lap_missed"
        return rows, None

    @staticmethod
    def _observed_owner(row, owner, snapshot):
        frame = snapshot.get("frame_ordinal")
        if type(frame) is not int or frame < owner["activation"][1]:
            return False
        observation = row.get("observation")
        if (not isinstance(observation, dict) or observation.get("identity") != row["identity"]
                or not isinstance(observation.get("identity"), dict)
                or any(type(observation["identity"].get(key)) is not type(value)
                       for key, value in row["identity"].items())):
            return False
        owners = observation.get("detail_task_ids")
        return (isinstance(owners, list) and 1 <= len(owners) <= 4
                and all(isinstance(identifier, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier)
                        for identifier in owners)
                and len(set(owners)) == len(owners) and owner["detail_task_id"] in owners)

    @staticmethod
    def _matching_result(result, track_length_m):
        response = result.get("response") or {}
        data = response.get("data") if response.get("status") == "available" else None
        if not isinstance(data, dict):
            return False
        body = result["body"]
        window = data.get("window")
        results = data.get("results")
        if (not isinstance(window, dict) or set(window) != {"start_m", "end_m", "track_length_m"}
                or any(type(number) not in (int, float) or not math.isfinite(number) for number in window.values())
                or window["start_m"] != body["window_m"][0] or window["end_m"] != body["window_m"][1]
                or window["track_length_m"] != track_length_m
                or not window["end_m"] <= window["track_length_m"] <= 20_000
                or not isinstance(results, list) or len(results) > body["laps"]
                or any(not isinstance(item, dict) or type(item.get("lap_number")) is not int
                       or not body["first_lap"] <= item["lap_number"] < body["first_lap"] + body["laps"]
                       for item in results)
                or len({item["lap_number"] for item in results}) != len(results)
                or data.get("state") == "complete" and len(results) != body["laps"]):
            return False
        return (data.get("lease_id") == result["lease_id"]
                and data.get("binding") == {key: body[key] for key in ("generation", "session_id", "scope", "player")}
                and type(data.get("first_lap")) is int and data["first_lap"] == body["first_lap"]
                and type(data.get("until_lap")) is int and data["until_lap"] == body["first_lap"] + body["laps"] - 1
                and data.get("opponent") == body.get("opponent")
                and data.get("detail_task_id") == body.get("detail_task_id"))

    def _cleanup(self):
        self.captures.replace([])
        self.captures.poll()
        self.demands.replace([])
        self.demands.poll()

    def poll(self, snapshot):
        with self._poll_lock:
            return self._poll(snapshot)

    def _poll(self, snapshot):
        with self._lock:
            desired = copy.deepcopy(self._desired)
        if self._pair is not None and self._pair["request"] not in desired:
            self._cleanup()
            self._pair = None
        if not desired:
            self._cleanup()
            return []
        request = desired[0]
        if self._pair is None:
            self._pair = {"request": request, "legs": None, "terminal": None,
                          "lease_ids": [uuid.uuid4().hex, uuid.uuid4().hex]}
        pair = self._pair
        rows, reason = self._frame(request, snapshot)
        if reason is not None and reason != "opponent_frame_unverified":
            pair["terminal"] = reason
        owner = None
        results = []
        state = "waiting"
        if pair["terminal"] is None:
            self.demands.replace([self._demand(request)])
            owners = self.demands.poll()
            owner = owners[0] if len(owners) == 1 else None
            if owner and owner.get("reason") in {
                    "detail_identity_unavailable", "detail_admission_rejected", "detail_rejected",
                    "detail_expired", "detail_released", "detail_activation_changed", "detail_command_rejected"}:
                pair["terminal"] = owner["reason"]
            elif rows is not None and owner and owner["state"] == "active":
                if pair["legs"] is None and self._observed_owner(rows[1], owner, snapshot):
                    pair["track_length_m"] = snapshot["context"]["value"]["track_length_m"]
                    opponent_body = {**copy.deepcopy(request["body"]), "opponent": request["opponent"],
                                     "first_lap": request["opponent_first_lap"],
                                     "detail_task_id": owner["detail_task_id"]}
                    pair["legs"] = [{"task_id": request["task_id"], "version": request["version"],
                                     "lease_id": lease_id, "body": copy.deepcopy(body)}
                                    for lease_id, body in zip(pair["lease_ids"], (request["body"], opponent_body))]
                state = "collecting" if pair["legs"] is not None else "waiting"
        if pair["legs"] is not None and pair["terminal"] is None:
            self.captures.replace(pair["legs"])
            results = self.captures.poll()
            for result in results:
                response = result.get("response") or {}
                data = response.get("data")
                if (response.get("status_code") in (401, 403, 404, 409, 422)
                        or response.get("reason") in ("region_capture_owner_budget_busy", "region_capture_release_pending", "region_capture_released")
                        or isinstance(data, dict) and data.get("state") in ("released", "expired", "unavailable")
                        or response.get("status") == "available" and not self._matching_result(result, pair["track_length_m"])):
                    pair["terminal"] = "opponent_paired_capture_unavailable"
            windows = [result.get("response", {}).get("data", {}).get("window")
                       for result in results if isinstance(result.get("response"), dict)
                       and isinstance(result["response"].get("data"), dict)]
            if len(windows) == 2 and windows[0] != windows[1]:
                pair["terminal"] = "opponent_paired_geometry_changed"
            if (state == "collecting" and len(results) == 2
                    and all(isinstance(result.get("response"), dict)
                            and result["response"].get("status") == "available"
                            and isinstance(result["response"].get("data"), dict)
                            and result["response"]["data"].get("state") == "complete" for result in results)):
                state = "complete"
        if pair["terminal"]:
            self._cleanup()
            state, reason, results = "unavailable", pair["terminal"], []
        if not self._wanted(request):
            self._cleanup()
            return []
        return [{**copy.deepcopy(request), "state": state, "reason": reason,
                 "owner": copy.deepcopy(owner), "legs": results}]

    def close(self):
        self.replace([])
        with self._poll_lock:
            captures = self.captures.close()
            demands = self.demands.close()
            self._pair = None
            return {"cleanup_verified": captures["cleanup_verified"] and demands["cleanup_verified"],
                    "captures": captures, "demands": demands}
