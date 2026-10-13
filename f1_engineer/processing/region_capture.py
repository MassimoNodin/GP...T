from __future__ import annotations

import copy
import math
import re
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass

from ..analysis.region_tracking import RegionTracker, RegionWindow
from ..analysis.resampling import TraceSample


MAX_REGION_CAPTURES = 3
MAX_RECENT_REGION_CAPTURES = 16
REGION_CAPTURE_TTL_S = 30


def _integer(value, minimum=0, maximum=2**64 - 1):
    return type(value) is int and minimum <= value <= maximum


def _hex(value, length):
    return isinstance(value, str) and re.fullmatch(f"[a-f0-9]{{{length}}}", value) is not None


def _binding(snapshot):
    scope = snapshot.get("scope")
    if (not _hex(snapshot.get("generation"), 32) or not _hex(snapshot.get("session_id"), 32)
            or not isinstance(scope, dict) or set(scope) != {"session_uid", "packet_format", "epoch"}
            or not _integer(scope.get("session_uid"), 1) or not _integer(scope.get("epoch"))
            or type(scope.get("packet_format")) is not int or scope["packet_format"] not in (2025, 2026)):
        return None
    rows = snapshot.get("drivers")
    if (not isinstance(rows, list) or len(rows) > 24
            or not _integer(snapshot.get("player_car_index"), 0, 23)):
        return None
    players = [row for row in rows if isinstance(row, dict)
               and row.get("car_index") == snapshot.get("player_car_index")]
    if len(players) != 1:
        return None
    player = players[0]
    identity = player.get("identity")
    if (not isinstance(identity, dict)
            or set(identity) != {"car_index", "tenure_ordinal", "participant_identity_fingerprint"}
            or not _integer(identity.get("car_index"), 0, 23)
            or not _integer(player.get("car_index"), 0, 23)
            or identity["car_index"] != player.get("car_index")
            or not _integer(identity.get("tenure_ordinal"), 1)
            or not _hex(identity.get("participant_identity_fingerprint"), 64)
            or not isinstance(player.get("lap"), dict)):
        return None
    return {"generation": snapshot["generation"], "session_id": snapshot["session_id"],
            "scope": scope, "player": identity}, player


@dataclass(frozen=True, slots=True)
class CaptureTask:
    task_id: str
    first_lap: int
    until_lap: int
    region_window: RegionWindow
    version: int = 1


class RegionCaptureStore:
    def __init__(self, *, clock=time.monotonic, clock_ns=time.perf_counter_ns):
        self._clock = clock
        self._clock_ns = clock_ns
        self._lock = threading.RLock()
        self._leases = OrderedDict()
        self._tracker = RegionTracker()
        self._latest = None

    @contextmanager
    def owner_transaction(self):
        with self._lock:
            try:
                yield
            except Exception:
                self.invalidate("region_publication_failed")
                self._latest = None
                raise

    def invalidate(self, reason):
        with self._lock:
            self._latest = None
            self._tracker.clear()
            for lease in self._leases.values():
                if lease["state"] in ("active", "complete"):
                    lease.update(state="unavailable", reason=reason, results=[])

    def _expire(self):
        now = self._clock()
        for lease in self._leases.values():
            if lease["state"] in ("active", "complete") and now >= lease["expires_at"]:
                lease.update(state="expired", reason="region_capture_expired", results=[])
                self._tracker.remove(lease["task"].task_id)

    def _view(self, lease):
        task = lease["task"]
        result = {"lease_id": task.task_id, "state": lease["state"], "reason": lease["reason"],
                  "binding": copy.deepcopy(lease["binding"]), "first_lap": task.first_lap,
                  "until_lap": task.until_lap, "window": task.region_window.view(),
                  "expires_in_s": max(0, lease["expires_at"] - self._clock()),
                  "results": copy.deepcopy(lease["results"])}
        for item in result["results"]:
            received = item["received_monotonic_ns"]
            age = (self._clock_ns() - received) / 1e9 if _integer(received) else None
            item["completion_age_s"] = age if age is not None and age >= 0 else None
        return result

    def arm(self, lease_id, body, snapshot):
        if (not _hex(lease_id, 32) or not isinstance(body, dict)
                or set(body) != {"generation", "session_id", "scope", "player", "first_lap", "laps", "window_m"}
                or not _integer(body.get("first_lap"), 1, 255) or not _integer(body.get("laps"), 1, 20)
                or body["first_lap"] + body["laps"] - 1 > 255
                or type(body.get("window_m")) is not list or len(body["window_m"]) != 2):
            return 422, {"reason": "region_capture_request_invalid"}
        if (not isinstance(body.get("scope"), dict) or not isinstance(body.get("player"), dict)
                or any(type(body["scope"].get(key)) is not int for key in ("session_uid", "packet_format", "epoch"))
                or any(type(body["player"].get(key)) is not int for key in ("car_index", "tenure_ordinal"))):
            return 422, {"reason": "region_capture_request_invalid"}
        with self._lock:
            self._expire()
            parsed = _binding(snapshot)
            if (snapshot.get("status") != "available" or parsed is None or self._latest is None
                    or not _integer(snapshot.get("received_monotonic_ns"))
                    or not 0 <= self._clock_ns() - snapshot["received_monotonic_ns"] < 3_000_000_000
                    or parsed[0] != self._latest["binding"]):
                return 409, {"reason": "region_capture_scope_unavailable"}
            binding = {key: body[key] for key in ("generation", "session_id", "scope", "player")}
            if binding != parsed[0]:
                return 409, {"reason": "region_capture_binding_mismatch"}
            try:
                window = RegionWindow(*body["window_m"], self._latest["track_length_m"])
            except ValueError:
                return 422, {"reason": "region_capture_window_invalid"}
            known = self._leases.get(lease_id)
            if known is not None:
                if known["request"] != body:
                    return 409, {"reason": "region_capture_identity_conflict"}
                if known["state"] not in ("active", "complete"):
                    return 409, {"reason": "region_capture_not_active"}
                known["expires_at"] = self._clock() + REGION_CAPTURE_TTL_S
                return 202, self._view(known)
            if body["first_lap"] != self._latest["lap_number"] + 1:
                return 409, {"reason": "region_capture_requires_next_lap"}
            if sum(lease["state"] == "active" for lease in self._leases.values()) >= MAX_REGION_CAPTURES:
                return 409, {"reason": "region_capture_budget_busy"}
            while len(self._leases) >= MAX_RECENT_REGION_CAPTURES:
                terminal = next((key for key, lease in self._leases.items()
                                 if lease["state"] not in ("active", "complete")), None)
                if terminal is None:
                    return 409, {"reason": "region_capture_history_busy"}
                del self._leases[terminal]
            task = CaptureTask(lease_id, body["first_lap"], body["first_lap"] + body["laps"] - 1, window)
            lease = {"task": task, "request": copy.deepcopy(body), "binding": copy.deepcopy(binding),
                     "state": "active", "reason": None, "results": [],
                     "cursor": self._latest["cursor"], "ordinal": self._latest["ordinal"],
                     "expires_at": self._clock() + REGION_CAPTURE_TTL_S}
            self._leases[lease_id] = lease
            return 202, self._view(lease)

    def get(self, lease_id, snapshot):
        with self._lock:
            self._expire()
            parsed = _binding(snapshot)
            if (snapshot.get("status") != "available" or parsed is None
                    or not _integer(snapshot.get("received_monotonic_ns"))
                    or not 0 <= self._clock_ns() - snapshot["received_monotonic_ns"] < 3_000_000_000):
                self.invalidate("region_runtime_unavailable")
            elif self._latest is not None and parsed[0] != self._latest["binding"]:
                self.invalidate("region_capture_scope_changed")
            lease = self._leases.get(lease_id)
            return None if lease is None else self._view(lease)

    def release(self, lease_id):
        with self._lock:
            lease = self._leases.get(lease_id)
            if lease is None:
                return None
            lease.update(state="released", reason="region_capture_released", results=[])
            self._tracker.remove(lease_id)
            return self._view(lease)

    def observe(self, snapshot):
        with self._lock:
            self._expire()
            parsed = _binding(snapshot)
            if parsed is None:
                self.invalidate("region_player_unavailable")
                return
            binding, player = parsed
            received = snapshot.get("received_monotonic_ns")
            ordinal, cursor = snapshot.get("frame_ordinal"), snapshot.get("event_cursor")
            context = snapshot.get("context", {})
            value = context.get("value") if isinstance(context, dict) else None
            length = value.get("track_length_m") if isinstance(value, dict) else None
            number = player["lap"].get("current_lap_number")
            if (snapshot.get("status") != "available" or not _integer(received)
                    or not 0 <= self._clock_ns() - received < 3_000_000_000
                    or not _integer(ordinal) or not _integer(cursor) or not _integer(number, 1, 255)
                    or not isinstance(context, dict) or context.get("status") != "available" or not isinstance(value, dict)
                    or any(type(value.get(key)) is not int or value[key] != binding["scope"][key]
                           for key in ("session_uid", "packet_format"))
                    or type(length) not in (int, float) or not math.isfinite(length) or not 0 < length <= 20_000):
                self.invalidate("region_frame_unavailable")
                return
            if self._latest is not None and (binding != self._latest["binding"]
                                             or length != self._latest["track_length_m"]):
                self.invalidate("region_capture_scope_changed")
            self._latest = {"binding": copy.deepcopy(binding), "track_length_m": length,
                            "lap_number": number, "ordinal": ordinal, "cursor": cursor}
            active = [lease for lease in self._leases.values() if lease["state"] == "active"]
            if not active:
                return
            tasks = [lease["task"] for lease in active]
            sample = _sample(snapshot, player, self._clock_ns())
            self._tracker.observe(tasks, player, sample, track_length_m=length)
            for lease in active:
                if binding != lease["binding"] or ordinal < lease["ordinal"] or cursor < lease["cursor"]:
                    lease.update(state="unavailable", reason="region_capture_scope_changed", results=[])
                    continue
                if snapshot.get("event_floor", cursor) > lease["cursor"]:
                    lease.update(state="unavailable", reason="region_event_history_gap", results=[])
                    continue
                for event in snapshot.get("events", ()):
                    if (event.get("driver") != binding["player"] or event.get("scope") != binding["scope"]
                            or not lease["cursor"] < event.get("id", 0) <= cursor):
                        continue
                    if event.get("kind") == "driver_discontinuity":
                        lease.update(state="unavailable", reason="region_driver_discontinuity", results=[])
                        break
                    task = lease["task"]
                    if (event.get("kind") != "lap_completed"
                            or event.get("qualification") != "observed_lap_transition"
                            or event.get("lap_number") != number - 1
                            or not task.first_lap <= event["lap_number"] <= task.until_lap):
                        continue
                    text, evidence = self._tracker.complete(task, event)
                    if (event.get("lap_time_ms") != player["lap"].get("last_lap_time_ms")
                            or not _integer(event.get("received_monotonic_ns"))
                            or not 0 <= self._clock_ns() - event["received_monotonic_ns"] < 3_000_000_000):
                        text, evidence = "Regional observations unavailable: completion mismatch or stale event.", None
                    lease["results"].append({"event_id": event["id"], "lap_number": event["lap_number"],
                        "received_monotonic_ns": event.get("received_monotonic_ns", received),
                        "text": text, "evidence": evidence})
                lease["cursor"], lease["ordinal"] = cursor, ordinal
                if lease["state"] == "active" and number > lease["task"].until_lap:
                    lease["state"] = "complete"
            self._tracker.prune([lease["task"] for lease in self._leases.values() if lease["state"] == "active"])


def _sample(snapshot, player, now_ns):
    observation = player.get("observation")
    if (not isinstance(observation, dict) or observation.get("status") != "available"
            or observation.get("identity") != player["identity"]):
        return None
    source, value = observation.get("source"), observation.get("value")
    if not isinstance(source, dict) or not isinstance(value, dict):
        return None
    expected = {**snapshot["scope"], "frame_identifier": snapshot.get("frame_identifier"),
                "frame_ordinal": snapshot["frame_ordinal"], "session_time_s": snapshot.get("session_time_s"),
                "lap_received_monotonic_ns": snapshot["received_monotonic_ns"]}
    if (any(source.get(key) != item for key, item in expected.items())
            or any(not _integer(source.get(key)) for key in
                   ("session_uid", "packet_format", "epoch", "frame_ordinal", "lap_received_monotonic_ns"))
            or not _integer(source.get("frame_identifier"), 0, 2**32 - 1)
            or not _integer(source.get("telemetry_received_monotonic_ns"))
            or not _integer(source.get("telemetry_captured_at_ns"))
            or not 0 <= now_ns - source["telemetry_received_monotonic_ns"] < 3_000_000_000
            or any(value.get(key) is None or type(value[key]) is not type(player["lap"].get(field))
                   or value[key] != player["lap"].get(field) for key, field in
                   (("lap_number", "current_lap_number"), ("lap_distance_m", "lap_distance_m"),
                    ("current_lap_time_ms", "current_lap_time_ms")))
            or not _integer(value.get("speed_kph"), 0, 65535)
            or not _integer(value.get("current_lap_time_ms"), 0, 86_400_000)):
        return None
    for item, low, high in ((value.get("lap_distance_m"), 0, 20_000),
                            (source.get("session_time_s"), 0, 86_400), (value.get("brake"), 0, 1)):
        if type(item) not in (int, float) or not math.isfinite(item) or not low <= item <= high:
            return None
    return TraceSample(source["frame_identifier"], value["lap_distance_m"], value["current_lap_time_ms"] / 1000,
                       value["speed_kph"] / 3.6, None, value["brake"], None, None, None,
                       session_time_s=source["session_time_s"])
