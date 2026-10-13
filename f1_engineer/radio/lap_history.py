from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field

from .pace import PaceReference, PaceTracker, integer


def identity_key(row):
    identity = row.get("identity") if isinstance(row, dict) else None
    if (not isinstance(identity, dict)
            or set(identity) != {"car_index", "tenure_ordinal", "participant_identity_fingerprint"}
            or not integer(row.get("car_index"), 0, 23)
            or identity.get("car_index") != row["car_index"]
            or not integer(identity.get("car_index"), 0, 23)
            or not integer(identity.get("tenure_ordinal"), 1, 2**64 - 1)
            or not isinstance(identity.get("participant_identity_fingerprint"), str)
            or re.fullmatch(r"[a-f0-9]{64}", identity["participant_identity_fingerprint"]) is None):
        return None
    return json.dumps(identity, sort_keys=True, separators=(",", ":"))


@dataclass(slots=True)
class DriverLapHistory:
    identity: str
    tracker: PaceTracker = field(default_factory=PaceTracker)
    laps: deque[PaceReference] = field(default_factory=lambda: deque(maxlen=64))


class FieldLapHistory:
    def __init__(self):
        self.clear()

    def clear(self):
        self.drivers: dict[int, DriverLapHistory] = {}

    def get(self, row) -> DriverLapHistory | None:
        key = identity_key(row)
        state = self.drivers.get(row.get("car_index")) if key is not None else None
        return state if state is not None and state.identity == key else None

    def feed(self, snapshot, *, now, after_cursor, age_s=None):
        age_s = snapshot["age_s"] if age_s is None else age_s
        rows = {}
        counts = {}
        for row in snapshot["drivers"]:
            if isinstance(row, dict) and integer(row.get("car_index"), 0, 23):
                car = row["car_index"]
                counts[car] = counts.get(car, 0) + 1
            key = identity_key(row)
            if key is None or not isinstance(row.get("lap"), dict):
                continue
            car = row["car_index"]
            rows[car] = row
        rows = {car: row for car, row in rows.items() if counts[car] == 1}
        self.drivers = {car: state for car, state in self.drivers.items() if car in rows}
        for car, row in rows.items():
            key = identity_key(row)
            state = self.drivers.get(car)
            number = row["lap"].get("current_lap_number")
            if (state is None or state.identity != key
                    or not integer(number, 1, 255)
                    or state.tracker.number is not None and number - state.tracker.number not in (0, 1)
                    or state.tracker.received_at is not None and not 0 <= now - state.tracker.received_at < 3):
                state = DriverLapHistory(key)
                self.drivers[car] = state
            state.tracker.observe(row, now=now)
        events = {}
        conflicts = set()
        for event in snapshot["events"]:
            if (not isinstance(event, dict) or not integer(event.get("id"), 1, 2**64 - 1)
                    or not after_cursor < event["id"] <= snapshot["event_cursor"]
                    or event.get("scope") != snapshot["scope"]
                    or any(type(event["scope"].get(field)) is not int for field in snapshot["scope"])):
                continue
            if event["id"] in events and events[event["id"]] != event:
                conflicts.add(event["id"])
            events[event["id"]] = event
        blocked = set()
        for event_id in sorted(events):
            if event_id in conflicts:
                continue
            event = events[event_id]
            identity = event.get("driver")
            if not isinstance(identity, dict):
                continue
            car = identity.get("car_index")
            if (not integer(car, 0, 23) or car not in rows or identity != rows[car]["identity"]
                    or identity_key({"car_index": car, "identity": identity}) != self.drivers[car].identity
                    or car in blocked):
                continue
            row, state = rows[car], self.drivers[car]
            if event.get("kind") == "driver_discontinuity":
                self.drivers[car] = DriverLapHistory(state.identity)
                self.drivers[car].tracker.observe(row, now=now)
                blocked.add(car)
                continue
            if event.get("kind") not in ("sector_completed", "lap_completed"):
                continue
            receipt = event.get("received_monotonic_ns")
            age = (age_s + (snapshot["received_monotonic_ns"] - receipt) / 1e9
                   if integer(receipt, 0, 2**64 - 1) else None)
            if age is None or not age_s <= age < 3:
                self.drivers[car] = DriverLapHistory(state.identity)
                self.drivers[car].tracker.observe(row, now=now)
                blocked.add(car)
                continue
            if event["kind"] == "sector_completed":
                state.tracker.sector(event, row)
                continue
            number, elapsed = event.get("lap_number"), event.get("lap_time_ms")
            if (event.get("qualification") != "observed_lap_transition"
                    or not integer(number, 1, 255) or not integer(elapsed, 1, 2**32 - 1)
                    or row["lap"].get("current_lap_number") != number + 1
                    or row["lap"].get("last_lap_time_ms") != elapsed):
                state.tracker.interrupt()
                state.tracker.observe(row, now=now)
                continue
            state.tracker.complete(event)
            record = state.tracker.last_completed
            if record is not None and not any(previous.lap_number == record.lap_number for previous in state.laps):
                state.laps.append(record)
