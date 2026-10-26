from __future__ import annotations

import math
from dataclasses import dataclass

from .corners import MAX_DISTANCE_WINDOW_SAMPLES, analyze_distance_window
from .resampling import TraceSample
from .region_context import RegionSampleContext, regional_sample_context


@dataclass(frozen=True, slots=True)
class RegionWindow:
    start_m: float
    end_m: float
    track_length_m: float

    def __post_init__(self):
        if (any(type(value) not in (int, float) or not math.isfinite(value)
                for value in (self.start_m, self.end_m, self.track_length_m))
                or not 0 <= self.start_m < self.end_m <= self.track_length_m <= 20_000
                or self.end_m - self.start_m > 1000):
            raise ValueError("Choose a numeric region within this track, at most 1,000 metres wide.")

    def view(self):
        return {"start_m": self.start_m, "end_m": self.end_m, "track_length_m": self.track_length_m}


class RegionTracker:
    def __init__(self):
        self.clear()

    def clear(self):
        self.records = {}

    def remove(self, task_id):
        self.records = {key: value for key, value in self.records.items() if key[0] != task_id}

    def interrupt(self):
        for record in self.records.values():
            record["invalid"] = True

    def prune(self, tasks):
        active = {(task.task_id, task.version) for task in tasks if task.region_window is not None}
        self.records = {key: value for key, value in self.records.items() if key[:2] in active}

    def observe(self, tasks, player, sample, *, track_length_m, context=None):
        active = {(task.task_id, task.version): task for task in tasks if task.region_window is not None}
        self.prune(tasks)
        lap = player["lap"]
        number = lap.get("current_lap_number")
        if type(number) is not int or not 1 <= number <= 255:
            self.interrupt()
            return
        self.records = {key: value for key, value in self.records.items() if key[2] >= number - 1}
        tyre = player.get("tyre")
        tyre = tyre if isinstance(tyre, dict) else {}
        compound = tyre.get("value", {}).get("actual_compound") if isinstance(tyre.get("value"), dict) else None
        healthy = (tyre.get("status") == "available" and type(compound) is int and 1 <= compound <= 255
                   and all(type(lap.get(key)) is int and lap[key] == value for key, value in
                           (("current_lap_invalid_id", 0), ("pit_status_id", 0), ("result_status_id", 2))))
        sample_context = regional_sample_context(context, player)
        for key, task in active.items():
            if not task.first_lap <= number <= task.until_lap:
                continue
            record = self.records.setdefault((*key, number),
                {"samples": [], "previous": None, "invalid": False, "compound": compound,
                 "previous_context": None, "context": RegionSampleContext()})
            if (not healthy or compound != record["compound"]
                    or track_length_m != task.region_window.track_length_m):
                record["invalid"] = True
            if record["invalid"] or sample is None:
                distance = lap.get("lap_distance_m")
                if sample is None and (
                    type(distance) in (int, float)
                    and task.region_window.start_m <= distance <= task.region_window.end_m
                    or record["samples"] and record["samples"][-1].distance_m < task.region_window.end_m
                ):
                    record["invalid"] = True
                record["previous"] = None
                continue
            previous = record["previous"]
            if previous is not None and sample.frame_identifier == previous.frame_identifier:
                if sample != previous:
                    record["invalid"] = True
                continue
            if previous is not None and (
                ((sample.frame_identifier - previous.frame_identifier) & 0xFFFFFFFF) >= 2**31
                or sample.distance_m < previous.distance_m or sample.time_s < previous.time_s
                or sample.session_time_s < previous.session_time_s
            ):
                record["invalid"] = True
                continue
            previous_context = record["previous_context"]
            record["previous"] = sample
            record["previous_context"] = sample_context
            samples = record["samples"]
            if sample.distance_m < task.region_window.start_m:
                if samples:
                    record["invalid"] = True
                continue
            if samples and samples[-1].distance_m >= task.region_window.end_m:
                continue
            if not samples and previous is not None and previous.distance_m < task.region_window.start_m:
                samples.append(previous)
                record["context"].observe(previous_context)
            if len(samples) >= MAX_DISTANCE_WINDOW_SAMPLES:
                record["invalid"] = True
                record["samples"] = []
                continue
            samples.append(sample)
            record["context"].observe(sample_context)

    def complete(self, task, event):
        record = self.records.pop((task.task_id, task.version, event["lap_number"]), None)
        if (record is None or record["invalid"] or type(event.get("previous_observed_invalid")) is not int
                or event["previous_observed_invalid"] != 0
                or type(event.get("lap_time_ms")) is not int or not 1 <= event["lap_time_ms"] <= 2**32 - 1):
            return "Regional observations unavailable: invalid, interrupted or missing lap evidence.", None
        window = task.region_window
        samples = tuple(record["samples"])
        if any(sample.time_s * 1000 > event["lap_time_ms"] for sample in samples):
            return "Regional observations unavailable: lap clock conflicts with completion.", None
        try:
            result = analyze_distance_window(samples, window.start_m, window.end_m,
                                             track_length_m=window.track_length_m)
        except ValueError:
            return "Regional observations unavailable: unsupported source chronology.", None
        minimum = result["observations"]["minimum_speed"]
        braking = result["observations"]["braking"]
        text = f"Observed region {window.start_m:g} to {window.end_m:g} metres. "
        if minimum["speed_kph"] is None:
            text += "Minimum speed unavailable. "
        else:
            partial = minimum["status"] != "observed_minimum_complete_window"
            text += (f"Sampled minimum {minimum['speed_kph']:.1f} kilometres per hour"
                     + (", partial coverage. " if partial else ". "))
        if (braking["status"] == "detected" and result["source_session_time_connected"]
                and braking["distance_bracket_m"] is not None):
            bracket = braking["distance_bracket_m"]
            text += f"Sustained brake onset between {bracket[0]:g} and {bracket[1]:g} metres. "
        else:
            text += "Brake onset unavailable; no supported uncensored sustained event. "
        elapsed = result["elapsed"]["elapsed_s"]
        text += f"Measured region time {elapsed:.3f} seconds. " if elapsed is not None else "Region time unavailable. "
        text += "Numeric observations only, not named-corner coaching."
        return text, {"lap_number": event["lap_number"], "actual_compound": record["compound"],
                      "first_frame_identifier": samples[0].frame_identifier if samples else None,
                      "last_frame_identifier": samples[-1].frame_identifier if samples else None,
                      "observed_context": record["context"].view(),
                      "measurement": result}
