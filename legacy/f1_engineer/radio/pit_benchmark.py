from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass

from .lap_history import identity_key
from .pace import integer


def tyre_value(row):
    tyre = row.get("tyre")
    value = tyre.get("value") if isinstance(tyre, dict) and tyre.get("status") == "available" else None
    return value if isinstance(value, dict) and integer(value.get("actual_compound"), 1, 255) else None


@dataclass(slots=True)
class PitVisit:
    event_id: int
    stop_count: int
    old_tyre: dict
    position: int | None
    field: list
    positions_fresh: bool
    exited: bool = False
    exit_lap: int | None = None


class PitBenchmarkTracker:
    def __init__(self):
        self.clear()

    def clear(self):
        self.pending: PitVisit | None = None
        self.last_reference = None
        self.last_compound = None

    def feed(self, snapshot, player, history, name_for, *, after_cursor, age_s):
        value = tyre_value(player)
        compound = value["actual_compound"] if value else None
        invalidated = (self.last_compound is not None and compound != self.last_compound
                       or self.last_reference is not None
                       and not any(identity_key(row) == self.last_reference and history.get(row) is not None
                                   and history.get(row).laps for row in snapshot["drivers"]))
        if invalidated:
            self.last_reference = self.last_compound = None
        events, conflicts = {}, set()
        for event in snapshot["events"]:
            if (not isinstance(event, dict) or not integer(event.get("id"), 1, 2**64 - 1)
                    or not after_cursor < event["id"] <= snapshot["event_cursor"]
                    or event.get("scope") != snapshot["scope"]
                    or any(type(event["scope"].get(field)) is not int for field in snapshot["scope"])):
                continue
            if event["id"] in events and events[event["id"]] != event:
                conflicts.add(event["id"])
            events[event["id"]] = event
        for event_id in sorted(events):
            if event_id in conflicts:
                continue
            event = events[event_id]
            driver = event.get("driver")
            if (not isinstance(driver, dict)
                    or identity_key({"car_index": player["car_index"], "identity": driver}) != identity_key(player)):
                continue
            kind = event.get("kind")
            if kind == "driver_discontinuity":
                self.clear()
                invalidated = True
                continue
            if kind not in ("pit_entered", "pit_exited"):
                continue
            receipt = event.get("received_monotonic_ns")
            age = (age_s + (snapshot["received_monotonic_ns"] - receipt) / 1e9
                   if integer(receipt, 0, 2**64 - 1) else None)
            if age is None or not age_s <= age < 3:
                self.pending = None
                continue
            if kind == "pit_entered":
                invalidated = invalidated or self.last_compound is not None
                self.last_reference = self.last_compound = None
                stops, old = event.get("pre_pit_stop_count"), event.get("pre_pit_tyre")
                if (not integer(stops, 0, 254) or not isinstance(old, dict)
                        or not integer(old.get("actual_compound"), 1, 255)):
                    self.pending = None
                    continue
                previous = event.get("pre_pit_received_monotonic_ns")
                fresh = integer(previous, 0, 2**64 - 1) and 0 <= receipt - previous < 3_000_000_000
                field = event.get("pre_pit_field")
                self.pending = PitVisit(event_id, stops, deepcopy(old), event.get("pre_pit_position"),
                                        deepcopy(field) if isinstance(field, list) and len(field) <= 24 else [], fresh)
            elif self.pending is not None:
                self.pending.exited = True
                self.pending.exit_lap = player["lap"].get("current_lap_number")
        visit = self.pending
        if visit is None or not visit.exited:
            return None, invalidated
        stops = player["lap"].get("pit_stop_count")
        if (not integer(stops, 0, 255) or stops != visit.stop_count + 1
                or not integer(visit.exit_lap, 1, 255)
                or player["lap"].get("current_lap_number") != visit.exit_lap):
            self.pending = None
            return None, invalidated
        if type(player["lap"].get("pit_status_id")) is not int or player["lap"]["pit_status_id"] != 0:
            self.pending = None
            return None, invalidated
        if value is None:
            return None, invalidated
        old_age, new_age = visit.old_tyre.get("age_laps"), value.get("age_laps")
        changed = (compound != visit.old_tyre["actual_compound"]
                   or integer(old_age, 0, 255) and integer(new_age, 0, 255) and new_age < old_age)
        if not changed:
            return None, invalidated
        self.pending = None
        report = self._report(visit, snapshot, player, history, name_for, value)
        self.last_reference = report["evidence"].get("reference_identity")
        self.last_compound = compound
        return report, invalidated

    def _report(self, visit, snapshot, player, history, name_for, tyre):
        compound = tyre["actual_compound"]
        evidence = {"pit_entry_event_id": visit.event_id, "stop_count": visit.stop_count + 1,
                    "actual_compound": compound, "pre_pit_position": visit.position,
                    "reference_identity": None, "sample_count": 0}
        prefix = f"New tyres, compound {compound}. "
        context = snapshot.get("context")
        metadata = context.get("value") if isinstance(context, dict) and context.get("status") == "available" else None
        scoped = (isinstance(metadata, dict)
                  and all(type(metadata.get(field)) is int and metadata[field] == snapshot["scope"][field]
                          for field in ("session_uid", "packet_format")))
        visual = tyre.get("visual_compound")
        visual = visual if integer(visual, 1, 255) else None
        label = {16: "soft", 17: "medium", 18: "hard", 7: "intermediate", 8: "wet"}.get(visual)
        if scoped and type(metadata.get("formula_id")) is int and metadata["formula_id"] == 0 and label:
            prefix = f"You're on {label} tyres, actual compound {compound}. "
        def unavailable(reason, text):
            evidence["reason"] = reason
            return {"key": f"pit-benchmark:{visit.event_id}", "text": prefix + text, "evidence": evidence}
        if not visit.positions_fresh or not integer(visit.position, 1, 24):
            return unavailable("pre_pit_position_unavailable", "Benchmark unavailable: reliable pre-pit positions are missing.")
        entries = []
        for entry in visit.field:
            if not isinstance(entry, dict) or not isinstance(entry.get("identity"), dict):
                continue
            identity = entry["identity"]
            key = identity_key({"car_index": identity.get("car_index"), "identity": identity})
            if key is not None:
                entries.append((key, entry))
        keys = Counter(key for key, entry in entries)
        positions = Counter(entry.get("position") for key, entry in entries if integer(entry.get("position"), 1, 24))
        player_key = identity_key(player)
        player_entries = [entry for key, entry in entries if key == player_key]
        if (len(player_entries) != 1 or player_entries[0].get("position") != visit.position
                or positions[visit.position] != 1):
            return unavailable("pre_pit_position_disputed", "Benchmark unavailable: pre-pit positions are disputed.")
        unknown = sum(not integer(entry.get("position"), 1, 24) for key, entry in entries)
        candidates = []
        for key, entry in entries:
            position = entry.get("position")
            if key == player_key or keys[key] != 1 or not integer(position, 1, 24) or positions[position] != 1:
                continue
            rows = [row for row in snapshot["drivers"] if identity_key(row) == key]
            if len(rows) != 1:
                continue
            state = history.get(rows[0])
            if state is None:
                continue
            eligible = [record for record in state.laps if record.compound == compound
                        and (conditions := state.conditions.get(record.lap_number)) is not None
                        and (conditions.weather_ids is None or len(conditions.weather_ids) == 1)
                        and (not integer(visual, 1, 255) or conditions.visual_compound in (None, visual))]
            if not eligible:
                continue
            samples = [eligible[-1]]
            for record in reversed(eligible[:-1]):
                following = samples[-1]
                left, right = state.conditions[record.lap_number], state.conditions[following.lap_number]
                if (record.lap_number + 1 != following.lap_number or left.weather_ids != right.weather_ids
                        or left.tyre_age_range is not None and right.tyre_age_range is not None
                        and right.tyre_age_range[0] < left.tyre_age_range[1]):
                    break
                samples.append(record)
            candidates.append((abs(position - visit.position), position, entry.get("leader_gap_ms"), key,
                               rows[0], state, list(reversed(samples))))
        if not candidates:
            return unavailable("no_eligible_same_compound_reference", "No eligible opponent laps on this compound were retained in this session.")
        closest = min(candidate[0] for candidate in candidates)
        candidates = [candidate for candidate in candidates if candidate[0] == closest]
        player_gap = player_entries[0].get("leader_gap_ms")
        if len(candidates) > 1:
            if (not integer(player_gap, 0 if visit.position == 1 else 1, 2**32 - 1)
                    or any(not integer(candidate[2], 0 if candidate[1] == 1 else 1, 2**32 - 1) for candidate in candidates)):
                return unavailable("pre_pit_gap_tie_break_unavailable", "Equally close references exist, but their pre-pit race-gap tie-break is unavailable.")
            candidates.sort(key=lambda candidate: (abs(candidate[2] - player_gap), candidate[1] > visit.position, candidate[1]))
        distance, position, gap, key, row, state, samples = candidates[0]
        count = len(samples)
        average = sum(record.elapsed_ms for record in samples) / count
        minutes, seconds = divmod(average / 1000, 60)
        clock = (f"{int(minutes)} minute{'s' if minutes != 1 else ''} " if minutes else "") + f"{seconds:.3f} seconds"
        name = name_for(row) or f"car slot {row['car_index']}"
        relation = "ahead" if position < visit.position else "behind"
        description = f"{name}, {distance} place{'s' if distance != 1 else ''} {relation} before your stop, "
        description += (f"averaged {clock} across {count} observed clean laps" if count > 1
                        else f"recorded {clock} on one observed clean lap, a single-lap reference not a trend")
        numbers = [record.lap_number for record in samples]
        description += f" on this compound this session, laps {numbers[0]} to {numbers[-1]}. "
        ages = [state.conditions[record.lap_number].tyre_age_range for record in samples]
        age_range = (min(age[0] for age in ages), max(age[1] for age in ages)) if all(age is not None for age in ages) else None
        description += (f"Observed tyre ages {age_range[0]} to {age_range[1]} laps. " if age_range else "Tyre-age range unavailable. ")
        weather = state.conditions[samples[-1].lap_number].weather_ids
        current_weather = metadata.get("weather_id") if scoped else None
        if weather is not None and integer(current_weather, 0, 255):
            description += ("Observed weather differs from now. " if weather != (current_weather,)
                            else "Observed weather code matches now. ")
        else:
            description += "Weather equivalence unknown. "
        temperatures = {}
        for field, attribute, label in (("track_temperature_c", "track_temperature_range", "Track"),
                                        ("air_temperature_c", "air_temperature_range", "Air")):
            ranges = [getattr(state.conditions[record.lap_number], attribute) for record in samples]
            bounds = (min(value[0] for value in ranges), max(value[1] for value in ranges)) if all(value is not None for value in ranges) else None
            current = metadata.get(field) if scoped else None
            temperatures[field] = {"reference_range": bounds, "current": current if integer(current, -128, 127) else None}
            if bounds is not None and integer(current, -128, 127):
                description += f"{label} temperature was {bounds[0]} to {bounds[1]} degrees, now {current}. "
            else:
                description += f"{label} temperature comparison unavailable. "
        if any(state.conditions[record.lap_number].visual_compound is None for record in samples):
            description += "Reference visual tyre label unavailable. "
        if unknown:
            description += "Some pre-pit positions were unknown; selection uses known positions only. "
        description += "Fuel, traffic and unobserved interruptions are not established; this is not a guaranteed target."
        evidence.update(reference_identity=key, reference_pre_pit_position=position,
                        lap_numbers=numbers, sample_count=count, average_ms=average,
                        tyre_age_range=age_range, weather_ids=weather, unknown_pre_pit_positions=unknown)
        evidence["temperatures"] = temperatures
        return {"key": f"pit-benchmark:{visit.event_id}", "text": prefix + description, "evidence": evidence}
