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
class LapConditions:
    tyre_age_range: tuple[int, int] | None
    visual_compound: int | None
    weather_ids: tuple[int, ...] | None
    track_temperature_range: tuple[int, int] | None = None
    air_temperature_range: tuple[int, int] | None = None


@dataclass(slots=True)
class DriverLapHistory:
    identity: str
    tracker: PaceTracker = field(default_factory=PaceTracker)
    laps: deque[PaceReference] = field(default_factory=lambda: deque(maxlen=64))
    conditions: dict[int, LapConditions] = field(default_factory=dict)
    open_conditions: LapConditions | None = None
    pending_conditions: LapConditions | None = None
    stop_count: int | None = None


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
                    or state.tracker.number is not None and number - state.tracker.number not in (0, 1)):
                state = DriverLapHistory(key)
                self.drivers[car] = state
            elif state.tracker.received_at is not None and not 0 <= now - state.tracker.received_at < 3:
                state.tracker.interrupt()
                state.open_conditions = None
                state.pending_conditions = None
            tyre = row.get("tyre", {})
            value = tyre.get("value") if isinstance(tyre, dict) and tyre.get("status") == "available" else None
            age = value.get("age_laps") if isinstance(value, dict) else None
            visual = value.get("visual_compound") if isinstance(value, dict) else None
            age = age if integer(age, 0, 255) else None
            visual = visual if integer(visual, 1, 255) else None
            context = snapshot.get("context")
            metadata = (context.get("value") if isinstance(context, dict)
                        and context.get("status") == "available" else None)
            weather = (metadata.get("weather_id") if isinstance(metadata, dict)
                       and all(type(metadata.get(field)) is int and metadata[field] == snapshot["scope"][field]
                               for field in ("session_uid", "packet_format")) else None)
            weather = weather if integer(weather, 0, 255) else None
            temperatures = {}
            for field in ("track_temperature_c", "air_temperature_c"):
                temperature = metadata.get(field) if weather is not None else None
                temperatures[field] = temperature if integer(temperature, -128, 127) else None
            stops = row["lap"].get("pit_stop_count")
            if (integer(stops, 0, 255) and state.stop_count is not None and stops != state.stop_count
                    or state.open_conditions is not None and state.open_conditions.tyre_age_range is not None
                    and age is not None and age < state.open_conditions.tyre_age_range[1]):
                state.tracker.eligible = False
            state.stop_count = stops if integer(stops, 0, 255) else None
            if state.tracker.number != number:
                state.pending_conditions = state.open_conditions
                state.open_conditions = LapConditions((age, age) if age is not None else None,
                                                       visual, (weather,) if weather is not None else None,
                                                       *((value, value) if value is not None else None
                                                         for value in temperatures.values()))
            elif state.open_conditions is not None:
                previous = state.open_conditions
                previous.tyre_age_range = ((previous.tyre_age_range[0], age)
                                          if previous.tyre_age_range is not None and age is not None else None)
                if previous.visual_compound != visual:
                    previous.visual_compound = None
                previous.weather_ids = (tuple(sorted(set((*previous.weather_ids, weather))))
                                        if previous.weather_ids is not None and weather is not None else None)
                for source, attribute in (("track_temperature_c", "track_temperature_range"),
                                          ("air_temperature_c", "air_temperature_range")):
                    current, bounds = temperatures[source], getattr(previous, attribute)
                    setattr(previous, attribute, (min(bounds[0], current), max(bounds[1], current))
                            if bounds is not None and current is not None else None)
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
            if event.get("kind") in ("pit_entered", "pit_exited"):
                state.tracker.eligible = False
                state.tracker.reset_sectors()
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
                if state.pending_conditions is not None:
                    state.conditions[record.lap_number] = state.pending_conditions
                retained = {record.lap_number for record in state.laps}
                state.conditions = {number: conditions for number, conditions in state.conditions.items() if number in retained}
