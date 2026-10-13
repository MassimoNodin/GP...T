from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from .gaps import integer


@dataclass(frozen=True, slots=True)
class PaceReference:
    lap_number: int
    elapsed_ms: int
    compound: int

    def __post_init__(self):
        if (not integer(self.lap_number, 1, 255) or not integer(self.elapsed_ms, 1, 2**32 - 1)
                or not integer(self.compound, 1, 255)):
            raise ValueError("A pace reference requires a known lap number, positive reported time and tyre compound.")

    def view(self):
        return {"lap_number": self.lap_number, "elapsed_ms": self.elapsed_ms, "actual_compound": self.compound}


class PaceTracker:
    def __init__(self):
        self.clear()

    def clear(self):
        self.best = OrderedDict()
        self.interrupt()
        self.received_at = None

    def interrupt(self):
        self.number = None
        self.compound = None
        self.started = False
        self.eligible = False
        self.pending = None
        self.last_event_id = None
        self.last_completed = None

    def _health(self, player):
        lap = player["lap"]
        tyre = player.get("tyre")
        compound = None
        if isinstance(tyre, dict) and tyre.get("status") == "available" and isinstance(tyre.get("value"), dict):
            value = tyre["value"].get("actual_compound")
            if integer(value, 1, 255):
                compound = value
        healthy = (compound is not None and type(lap.get("current_lap_invalid_id")) is int
                   and lap["current_lap_invalid_id"] == 0
                   and type(lap.get("pit_status_id")) is int and lap["pit_status_id"] == 0
                   and type(lap.get("result_status_id")) is int and lap["result_status_id"] == 2)
        return healthy, compound

    def observe(self, player, *, now):
        number = player["lap"].get("current_lap_number")
        if (not integer(number, 1, 255)
                or self.received_at is not None and not 0 <= now - self.received_at < 3
                or self.number is not None and number - self.number not in (0, 1)):
            self.interrupt()
        self.received_at = now
        if not integer(number, 1, 255):
            return
        healthy, compound = self._health(player)
        if number != self.number:
            self.pending = (self.number, self.started and self.eligible, self.compound)
            self.number, self.compound = number, compound
            self.started, self.eligible = False, healthy
            self.last_event_id = self.last_completed = None
        else:
            self.eligible = self.eligible and healthy and compound == self.compound

    def complete(self, event):
        number, elapsed = event["lap_number"], event.get("lap_time_ms")
        self.last_event_id, self.last_completed = event["id"], None
        if (self.number == number + 1 and self.pending is not None and self.pending[0] == number
                and self.pending[1] and integer(elapsed, 1, 2**32 - 1)
                and type(event.get("previous_observed_invalid")) is int and event["previous_observed_invalid"] == 0):
            record = PaceReference(number, elapsed, self.pending[2])
            self.last_completed = record
            previous = self.best.get(record.compound)
            if previous is None or record.elapsed_ms < previous.elapsed_ms:
                self.best[record.compound] = record
                self.best.move_to_end(record.compound)
                while len(self.best) > 16:
                    self.best.popitem(last=False)
        if self.number == number + 1:
            self.started = True
        self.pending = None

    def reference(self, player):
        healthy, compound = self._health(player)
        if not healthy:
            raise ValueError("Known valid on-track player and tyre data are required to set target pace.")
        record = self.best.get(compound)
        if record is None:
            raise ValueError("No fully observed valid radio lap on your current tyre compound is available. Complete a valid lap with the radio connected first.")
        return record

    def answer(self, event, reference, tolerance_ms):
        record = self.last_completed if self.last_event_id == event["id"] else None
        if record is None:
            return "Target pace unavailable: lap validity, full observation or tyre continuity is not established."
        if record.compound != reference.compound:
            return "Target pace paused: tyre compound differs from the fixed reference. Request an update to choose a new reference."
        delta = record.elapsed_ms - reference.elapsed_ms
        if delta < 0:
            return f"Lap {record.lap_number}: {abs(delta) / 1000:.3f} seconds faster than the fixed reference; target met."
        if delta <= tolerance_ms:
            return f"Lap {record.lap_number}: {delta / 1000:.3f} seconds slower than the fixed reference; within the {tolerance_ms / 1000:.3f} second target."
        return (f"Lap {record.lap_number}: {delta / 1000:.3f} seconds slower than the fixed reference; "
                f"{(delta - tolerance_ms) / 1000:.3f} seconds outside the {tolerance_ms / 1000:.3f} second target.")
