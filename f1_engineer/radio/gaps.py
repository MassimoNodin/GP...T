from __future__ import annotations

import json


GAP_CHANGES = {"leader_gap_change": ("leader", "delta_to_race_leader_ms", "gap to leader"),
               "front_gap_change": ("ahead", "delta_to_car_in_front_ms", "gap to car ahead")}


def integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def relative_gap_ms(player, target):
    laps = [row.get("lap", {}) for row in (player, target)]
    if any(type(lap.get("pit_status_id")) is not int or lap["pit_status_id"] != 0
           or type(lap.get("result_status_id")) is not int or lap["result_status_id"] != 2
           or not integer(lap.get("current_lap_number"), 1, 255)
           or not integer(lap.get("delta_to_race_leader_ms"), 0, 2**32 - 1) for lap in laps):
        return None
    if laps[0]["current_lap_number"] != laps[1]["current_lap_number"]:
        return None
    return laps[1]["delta_to_race_leader_ms"] - laps[0]["delta_to_race_leader_ms"]


class LapGapTracker:
    def __init__(self):
        self.clear()

    def clear(self):
        self.samples = {}
        self.boundaries = {}
        self.results = {}
        self.number = None
        self.received_at = None

    def observe(self, player, resolve, *, now):
        number = player["lap"].get("current_lap_number")
        if not integer(number, 1, 255):
            self.clear()
            return
        if (self.received_at is not None and not 0 <= now - self.received_at < 3
                or self.number is not None and number - self.number not in (0, 1)):
            self.clear()
        if number != self.number:
            self.results.clear()
        self.number, self.received_at = number, now
        for metric, (role, field, label) in GAP_CHANGES.items():
            row = resolve(role)
            sample = None
            position = player["lap"].get("car_position")
            gap = player["lap"].get(field)
            if (row is not None and row["identity"] != player["identity"]
                    and integer(number, 1, 255) and integer(position, 2, 24)
                    and integer(gap, 1, 2**32 - 1)
                    and type(player["lap"].get("pit_status_id")) is int and player["lap"]["pit_status_id"] == 0
                    and type(row["lap"].get("pit_status_id")) is int and row["lap"]["pit_status_id"] == 0
                    and type(player["lap"].get("result_status_id")) is int and player["lap"]["result_status_id"] == 2
                    and type(row["lap"].get("result_status_id")) is int and row["lap"]["result_status_id"] == 2
                    and integer(row["lap"].get("current_lap_number"), number, min(number + 1, 255))):
                token = json.dumps([position, row["identity"], row["lap"].get("car_position")], sort_keys=True)
                sample = (token, gap, row["lap"]["current_lap_number"])
            previous = self.samples.get(metric)
            if sample is None or previous is None or sample[0] != previous[0] or sample[2] - previous[2] not in (0, 1):
                self.boundaries.pop(metric, None)
                self.results.pop(metric, None)
            self.samples[metric] = sample

    def complete(self, number):
        if self.number != number + 1:
            return
        for metric, sample in self.samples.items():
            if sample is None or sample[2] != self.number:
                self.boundaries.pop(metric, None)
                self.results.pop(metric, None)
                continue
            token, gap, rival_lap = sample
            previous = self.boundaries.get(metric)
            if previous is not None and previous[0] == number - 1 and previous[1] == token:
                self.results[metric] = (number, gap - previous[2])
            else:
                self.results.pop(metric, None)
            self.boundaries[metric] = (number, token, gap)

    def answer(self, metric, *, is_player):
        label = GAP_CHANGES[metric][2] + " change"
        if not is_player:
            return label + " unavailable; supported only for you"
        result = self.results.get(metric)
        if result is None:
            return label + " unavailable; two consecutive comparable lap boundaries required"
        number, delta = result
        if delta == 0:
            return f"lap {number} {label}: unchanged"
        direction = "lost" if delta > 0 else "gained"
        return f"lap {number} {label}: you {direction} {abs(delta) / 1000:.3f} seconds between lap boundaries"
