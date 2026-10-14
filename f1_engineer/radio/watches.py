from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

from .gaps import integer


@dataclass
class WatchState:
    sample: tuple | None = None
    token: tuple | None = None
    history: list = field(default_factory=list)
    notice: str | None = None
    reason: str = "collecting comparable boundaries"


class RaceWatchTracker:
    def __init__(self):
        self.clear()

    def clear(self):
        self.states = {}
        self.number = None
        self.received_at = None

    def reset_intervals(self):
        for state in self.states.values():
            state.sample = state.token = state.notice = None
            state.history.clear()
            state.reason = "observation continuity lost"

    def remove(self, task_id):
        self.states = {key: state for key, state in self.states.items() if key[0] != task_id}

    def extend_version(self, task_id, old_version, new_version):
        state = self.states.pop((task_id, old_version), None)
        if state is not None:
            state.notice = None
            self.states[(task_id, new_version)] = state

    def views(self):
        return [{"task_id": key[0], "version": key[1], "comparable_boundaries": len(state.history),
                 "notice_state": state.notice, "reason": state.reason,
                 "gap_basis": ("same-frame leader-gap difference" if len(state.token) > 3 else "direct adjacent gap")
                              if state.sample is not None else None,
                 "role": state.sample[0] if state.sample is not None else None}
                for key, state in self.states.items()]

    def observe(self, tasks, player, resolve, *, now, track_length_m=None):
        number = player["lap"].get("current_lap_number")
        invalidated = False
        if (not integer(number, 1, 255)
                or self.received_at is not None and not 0 <= now - self.received_at < 3
                or self.number is not None and number - self.number not in (0, 1)):
            self.reset_intervals()
            invalidated = True
        self.number = number if integer(number, 1, 255) else None
        self.received_at = now
        active = {(task.task_id, task.version): task for task in tasks if "race_watch" in task.plan.metrics}
        self.states = {key: state for key, state in self.states.items() if key in active}
        for key, task in active.items():
            state = self.states.setdefault(key, WatchState())
            row = resolve(task.plan.targets[0], json.loads(task.fixed_bindings[0]))
            sample, token, reason = self._sample(player, row, resolve,
                                                 allow_non_adjacent=task.plan.targets[0].startswith("name:"),
                                                 track_length_m=track_length_m)
            previous = state.sample
            if (sample is None or token != state.token
                    or previous is not None and sample[2] - previous[2] not in (0, 1)):
                invalidated = invalidated or bool(state.history)
                state.history.clear()
                if sample is not None:
                    state.notice = None
            state.sample, state.token, state.reason = sample, token, reason
        return invalidated

    def _sample(self, player, row, resolve, *, allow_non_adjacent=False, track_length_m=None):
        if row is None:
            return None, None, "fixed driver unavailable or replaced"
        position, rival_position = player["lap"].get("car_position"), row["lap"].get("car_position")
        if (not integer(position, 1, 24) or not integer(rival_position, 1, 24)
                or position == rival_position):
            return None, None, "race positions unavailable or ambiguous"
        non_adjacent = abs(position - rival_position) != 1
        if non_adjacent and not allow_non_adjacent:
            return None, None, "fixed driver no longer adjacent"
        for participant, race_position in ((player, position), (row, rival_position)):
            unique = resolve(f"position:{race_position}")
            if unique is None or unique["identity"] != participant["identity"]:
                return None, None, "race positions ambiguous"
            lap = participant["lap"]
            if not (type(lap.get("pit_status_id")) is int and lap["pit_status_id"] == 0
                    and type(lap.get("result_status_id")) is int and lap["result_status_id"] == 2):
                return None, None, "pit or racing status ineligible or unknown"
        number, rival_number = player["lap"].get("current_lap_number"), row["lap"].get("current_lap_number")
        role = "ahead" if rival_position < position else "behind"
        offset = 1 if role == "ahead" else -1
        if (not integer(number, 1, 255) or not integer(rival_number, 1, 255)
                or rival_number - number not in (0, offset)):
            return None, None, "lap alignment ineligible or unknown"
        distances = [participant["lap"].get("total_distance_m") for participant in (player, row)]
        physical_available = (type(track_length_m) in (int, float) and math.isfinite(track_length_m)
                              and 0 < track_length_m <= 20_000
                              and all(type(distance) in (int, float) and math.isfinite(distance) for distance in distances))
        if physical_available and abs(distances[1] - distances[0]) >= track_length_m:
            return None, None, "watched driver is lapped or lap-distance alignment is unavailable"
        if non_adjacent:
            if not physical_available:
                return None, None, "non-adjacent lap-distance alignment unavailable"
            progress = distances[1] - distances[0]
            if progress == 0 or (progress > 0) != (role == "ahead"):
                return None, None, "non-adjacent race position and distance disagree"
            leader = resolve("position:1")
            if (leader is None or type(leader["lap"].get("pit_status_id")) is not int
                    or leader["lap"]["pit_status_id"] != 0
                    or type(leader["lap"].get("result_status_id")) is not int
                    or leader["lap"]["result_status_id"] != 2
                    or not integer(leader["lap"].get("current_lap_number"), 1, 255)):
                return None, None, "shared race-leader reference unavailable or ineligible"
            gaps = [0 if participant["lap"]["car_position"] == 1 else
                    participant["lap"].get("delta_to_race_leader_ms") for participant in (player, row)]
            if any(not integer(gap, 0 if participant["lap"]["car_position"] == 1 else 1, 2**32 - 1)
                   for participant, gap in zip((player, row), gaps)):
                return None, None, "shared leader-gap timing unavailable"
            difference = gaps[1] - gaps[0]
            if difference == 0 or (difference < 0) != (role == "ahead"):
                return None, None, "shared leader gaps disagree with race order"
            token = (role, position, rival_position, "leader_relative", json.dumps(leader["identity"], sort_keys=True))
            return (role, abs(difference), rival_number), token, "eligible same-frame leader-gap difference"
        source = player if role == "ahead" else row
        gap = source["lap"].get("delta_to_car_in_front_ms")
        if not integer(gap, 1, 2**32 - 1):
            return None, None, "gap unavailable"
        return (role, gap, rival_number), (role, position, rival_position), "eligible adjacent reference"

    def complete(self, task, number):
        state = self.states.get((task.task_id, task.version))
        if state is None:
            return None
        sample = state.sample
        if (sample is None or self.number != number + 1
                or sample[0] == "ahead" and sample[2] != self.number):
            state.history.clear()
            reason = state.reason if sample is None else "lap alignment at completion unavailable"
            notice = "paused: " + reason
            text = "Watch paused: " + reason + "."
        else:
            role, gap, rival_number = sample
            if state.history and state.history[-1][0] != number - 1:
                state.history.clear()
                state.notice = None
            state.history.append((number, gap))
            state.history = state.history[-3:]
            if len(state.history) < 3:
                notice, text = "warming", "Watch collecting three comparable lap boundaries."
            else:
                changes = [state.history[index][1] - state.history[index - 1][1] for index in (1, 2)]
                total = sum(changes)
                if all(change <= -100 for change in changes) and total <= -300:
                    notice = "closing:" + role
                    action = "You are catching the watched driver ahead" if role == "ahead" else "The watched driver behind is closing"
                    text = f"{action}: gap reduced {abs(total) / 1000:.3f} seconds over two observed laps, now {gap / 1000:.3f} seconds."
                elif all(change >= 100 for change in changes) and total >= 300:
                    notice = "opening:" + role
                    action = "The watched driver ahead is pulling away" if role == "ahead" else "You are pulling away from the watched driver behind"
                    text = f"{action}: gap increased {total / 1000:.3f} seconds over two observed laps, now {gap / 1000:.3f} seconds."
                else:
                    notice, text = "uncertain", "Recent gaps show no consistent closing or opening trend over two observed laps."
            if len(state.token) > 3 and notice != "warming":
                text += " Derived from same-frame gaps to the same race leader, not a direct adjacent-car gap."
        if notice == state.notice:
            return None
        state.notice = notice
        return text
