from __future__ import annotations

import json
import math
import re
import time
import uuid
from copy import deepcopy

from ..driver_names import display_driver_name, driver_name_aliases
from .gaps import GAP_CHANGES, LapGapTracker
from .questions import QuestionPlan, UNAVAILABLE_FACTS, plan_question, timing_fact
from .scheduler import RadioMessage, RadioScheduler
from .tasks import LapReportRegistry, parse_task_request
from .watches import RaceWatchTracker
from .pace import PaceTracker
from .lap_history import FieldLapHistory
from .pit_benchmark import PitBenchmarkTracker
from .regions import RegionTracker, RegionWindow
from .region_progress import RegionProgress
from ..analysis.resampling import TraceSample


def _integer(value, minimum=0, maximum=2**64 - 1):
    return type(value) is int and minimum <= value <= maximum


def _number(value):
    return type(value) in (float, int) and 0 <= value <= 86_400 and math.isfinite(value)


def _identity(value):
    return (isinstance(value, dict)
            and set(value) == {"car_index", "tenure_ordinal", "participant_identity_fingerprint"}
            and _integer(value.get("car_index"), 0, 23)
            and _integer(value.get("tenure_ordinal"), 1)
            and isinstance(value.get("participant_identity_fingerprint"), str)
            and re.fullmatch(r"[a-f0-9]{64}", value["participant_identity_fingerprint"]) is not None)


def lap_message(number: int, milliseconds: int) -> str:
    minutes, remainder = divmod(milliseconds, 60_000)
    clock = f"{minutes} minute{'s' if minutes != 1 else ''} " if minutes else ""
    return f"Lap {number}: {clock}{remainder / 1000:.3f} seconds."


class RadioController:
    def __init__(self, scheduler: RadioScheduler, *, notify=None, backend_regions=False) -> None:
        self.scheduler = scheduler
        self.notify = notify or (lambda event: None)
        self._cursor: int | None = None
        self._player = None
        self._received_at = 0.0
        self._age_s = 0.0
        self._answer_id = 0
        self._status = None
        self._drivers = []
        self._observation_frame = None
        self._fact_age_s = None
        self._history = []
        self.tasks = LapReportRegistry(notify=self.notify)
        self.gaps = LapGapTracker()
        self.watches = RaceWatchTracker()
        self.pace = PaceTracker()
        self.lap_history = FieldLapHistory()
        self.pit_benchmarks = PitBenchmarkTracker()
        self.regions = RegionTracker()
        self.region_progress = RegionProgress()
        self._track_length_m = None
        self._backend_regions = backend_regions
        self._capture_namespace = uuid.uuid4()
        self._capture_results = []

    def _unavailable(self, reason: str) -> None:
        self.scheduler.set_scope(None)
        self._cursor = None
        self._player = None
        self._drivers = []
        self._history.clear()
        safe_reason = reason if re.fullmatch(r"[a-z0-9_]{1,96}", reason) else "unavailable"
        self._reset_gaps()
        self.tasks.clear(safe_reason)
        self.watches.clear()
        self.pace.clear()
        self.lap_history.clear()
        self.pit_benchmarks.clear()
        self.regions.clear()
        self.region_progress.clear()
        self._report("unavailable", safe_reason)

    def _report(self, status: str, reason=None) -> None:
        if (status, reason) != self._status:
            self._status = (status, reason)
            self.notify({"event": "radio_backend", "status": status, "reason": reason})

    def feed(self, snapshot: dict, *, now: float, transport_age_s: float = 0.0, region_captures=None) -> None:
        self._capture_results = region_captures if isinstance(region_captures, list) else []
        if snapshot.get("status") != "available":
            self._unavailable(str(snapshot.get("reason", "unavailable")))
            return
        scope = snapshot.get("scope")
        if (not _integer(snapshot.get("schema_version"), 1, 1) or not isinstance(scope, dict)
                or set(scope) != {"session_uid", "packet_format", "epoch"}
                or not _integer(scope.get("session_uid"), 1) or scope.get("packet_format") not in (2025, 2026)
                or not _integer(scope.get("epoch")) or not _number(snapshot.get("age_s"))
                or not _integer(snapshot.get("player_car_index"), 0, 23)
                or not _number(transport_age_s) or not _integer(snapshot.get("received_monotonic_ns"))
                or not _integer(snapshot.get("event_cursor")) or not _integer(snapshot.get("event_floor"))
                or snapshot["event_floor"] > snapshot["event_cursor"]
                or not isinstance(snapshot.get("drivers"), list) or len(snapshot["drivers"]) > 24
                or not isinstance(snapshot.get("events"), list) or len(snapshot["events"]) > 128):
            self._unavailable("backend_snapshot_invalid")
            return
        for field in ("generation", "session_id"):
            value = snapshot.get(field)
            if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{32}", value) is None:
                self._unavailable("backend_snapshot_invalid")
                return
        age = snapshot["age_s"] + transport_age_s
        if age >= 3:
            self._unavailable("lap_observations_stale")
            return
        players = [row for row in snapshot["drivers"] if isinstance(row, dict)
                   and _integer(row.get("car_index"), 0, 23)
                   and row.get("car_index") == snapshot.get("player_car_index")]
        if (len(players) != 1 or not _identity(players[0].get("identity"))
                or players[0]["identity"]["car_index"] != players[0]["car_index"]
                or not isinstance(players[0].get("lap"), dict)):
            self._unavailable("player_binding_unavailable")
            return
        player = players[0]
        binding = json.dumps([snapshot["generation"], snapshot["session_id"], scope, player["identity"]],
                             sort_keys=True, separators=(",", ":"))
        changed = self.scheduler.scope != binding
        previous_lap = self._player["lap"].get("current_lap_number") if self._player is not None else None
        self.scheduler.set_scope(binding)
        if changed:
            self._history.clear()
            self.tasks.clear("scope_changed")
            self.watches.clear()
            self.pace.clear()
            self.lap_history.clear()
            self.pit_benchmarks.clear()
            self.regions.clear()
            self.region_progress.clear()
            self._reset_gaps()
        elif not _integer(player["lap"].get("current_lap_number"), 1, 255):
            self._clear_tasks("lap_number_unavailable")
        elif _integer(previous_lap, 1, 255) and _integer(player["lap"].get("current_lap_number"), 1, 255):
            difference = player["lap"]["current_lap_number"] - previous_lap
            if difference < 0 or difference > 1:
                self._clear_tasks("lap_number_discontinuity")
        self._player = deepcopy(player)
        self._drivers = deepcopy(snapshot["drivers"])
        self._received_at = now
        self._age_s = age
        self._observation_frame = {**scope, "frame_identifier": snapshot.get("frame_identifier"),
                                   "frame_ordinal": snapshot.get("frame_ordinal"),
                                   "session_time_s": snapshot.get("session_time_s"),
                                   "lap_received_monotonic_ns": snapshot.get("received_monotonic_ns")}
        context = snapshot.get("context")
        length = (context["value"].get("track_length_m") if isinstance(context, dict)
                  and context.get("status") == "available" and isinstance(context.get("value"), dict)
                  and all(type(context["value"].get(key)) is int and context["value"][key] == scope[key]
                          for key in ("session_uid", "packet_format")) else None)
        if self._track_length_m is not None and length != self._track_length_m:
            self.regions.interrupt()
            self.region_progress.clear()
            self.scheduler.cancel_owner("lap_reports")
        self._track_length_m = length if type(length) in (int, float) and math.isfinite(length) and 0 < length <= 20_000 else None
        self.regions.observe(() if self._backend_regions else self.tasks.active(), self._player, self._region_sample(now=now),
                             track_length_m=self._track_length_m)
        if self.pace.observe(self._player, now=now):
            self.scheduler.cancel_owner("pace_sectors")
            if any("consistency" in task.plan.metrics for task in self.tasks.active()):
                self.scheduler.cancel_owner("lap_reports")
        if self.watches.observe(self.tasks.active(), self._player, self._resolve_target, now=now):
            self.scheduler.cancel_owner("lap_reports")
        prior_gap_results = dict(self.gaps.results)
        self.gaps.observe(self._player, self._resolve_target, now=now)
        if prior_gap_results and prior_gap_results != self.gaps.results:
            self.scheduler.cancel_owner("gap_changes")
            self.scheduler.cancel_owner("lap_reports")
        cursor = snapshot["event_cursor"]
        self._report("available")
        if (changed or self._cursor is None or cursor < self._cursor
                or snapshot["event_floor"] > self._cursor):
            if not changed and self._cursor is not None:
                self.scheduler.cancel()
                self.tasks.clear("event_history_gap")
                self.watches.clear()
                self.pace.clear()
                self.lap_history.clear()
                self.pit_benchmarks.clear()
                self.regions.clear()
                self.region_progress.clear()
                self._reset_gaps()
                self.notify({"event": "radio_resynchronised", "reason": "event_history_gap"})
            self._cursor = cursor
            self.lap_history.feed(snapshot, now=now, after_cursor=cursor, age_s=age)
            return
        self.lap_history.feed(snapshot, now=now, after_cursor=self._cursor, age_s=age)
        benchmark, invalidated = self.pit_benchmarks.feed(
            snapshot, self._player, self.lap_history, self._participant_name,
            after_cursor=self._cursor, age_s=age)
        if invalidated:
            self.scheduler.cancel_owner("pit_benchmark")
        if benchmark is not None:
            self.notify({"event": "radio_pit_benchmark", **benchmark, "observation_age_s": age})
            self.scheduler.submit(RadioMessage(benchmark["key"], binding, benchmark["text"], "proactive",
                                              now + 60, observation_age_s=age,
                                              dispatch_before=now + 3 - age, owner="pit_benchmark"), now=now)
        seen_events = set()
        if any(isinstance(event, dict) and event.get("kind") == "driver_discontinuity"
                    and _integer(event.get("id"), 1) and self._cursor < event["id"] <= cursor
                    and event.get("scope") == scope for event in snapshot["events"]):
            self._reset_gaps()
        for event in snapshot["events"]:
            if (not isinstance(event, dict) or not _integer(event.get("id"), 1)
                    or not self._cursor < event["id"] <= cursor
                    or event.get("scope") != scope or event.get("driver") != player["identity"]):
                continue
            if event["id"] in seen_events:
                continue
            seen_events.add(event["id"])
            if event.get("kind") == "driver_discontinuity":
                self._clear_tasks("driver_discontinuity")
                continue
            if event.get("kind") == "sector_completed":
                if (not _integer(event.get("lap_number"), 1, 255)
                        or not _integer(event.get("received_monotonic_ns"))):
                    self.pace.reset_sectors()
                    self.scheduler.cancel_owner("pace_sectors")
                    continue
                elapsed_ns = snapshot["received_monotonic_ns"] - event["received_monotonic_ns"]
                event_age = age + elapsed_ns / 1e9
                if elapsed_ns < 0 or event_age >= 3:
                    self.pace.reset_sectors()
                    self.scheduler.cancel_owner("pace_sectors")
                    continue
                captured = self.pace.sector(event, self._player)
                if captured is None:
                    continue
                if not captured:
                    self.scheduler.cancel_owner("pace_sectors")
                self._report_pace_sector(event, now=now, age=event_age)
                continue
            if (event.get("kind") != "lap_completed"
                    or event.get("qualification") != "observed_lap_transition"
                    or not _integer(event.get("lap_number"), 1, 255)
                    or not _integer(event.get("received_monotonic_ns"))):
                continue
            elapsed_ns = snapshot["received_monotonic_ns"] - event["received_monotonic_ns"]
            if elapsed_ns < 0:
                continue
            event_age = age + elapsed_ns / 1e9
            self.scheduler.cancel_owner("pace_sectors")
            elapsed = event.get("lap_time_ms")
            if (event_age < 3 and player["lap"].get("current_lap_number") == event["lap_number"] + 1
                    and (elapsed is None or _integer(elapsed, 1, 2**32 - 1)
                         and elapsed == player["lap"].get("last_lap_time_ms"))):
                self.gaps.complete(event["lap_number"])
                self.pace.complete(event)
                self.notify({"event": "radio_pace_lap_observed", "lap_number": event["lap_number"],
                             "valid_reference": self.pace.last_completed.view() if self.pace.last_completed else None})
                self.notify({"event": "radio_gap_change_observed", "lap_number": event["lap_number"],
                             "changes_ms": {metric: result[1] for metric, result in self.gaps.results.items()},
                             "observation_age_s": event_age})
            else:
                self._reset_gaps()
            reported = self._report_tasks(event, now=now, age=event_age)
            if reported or not _integer(event.get("lap_time_ms"), 1, 2**32 - 1):
                continue
            text = lap_message(event["lap_number"], event["lap_time_ms"])
            if event.get("previous_observed_invalid") == 1:
                text += " Observed invalid."
            self.scheduler.submit(RadioMessage(f"lap:{event['id']}", binding, text, "proactive",
                                              now + 10 - event_age, observation_age_s=event_age), now=now)
        self._cursor = cursor
        number = player["lap"].get("current_lap_number")
        if _integer(number, 1, 255):
            if any(task["state"] == "active" and task["until_lap"] is not None
                   and task["until_lap"] <= number - 1 for task in self.tasks.views()):
                self.scheduler.cancel_owner("lap_reports")
            self.tasks.complete_lap(number - 1)
        self.regions.prune(self.tasks.active())

    def region_capture_requests(self):
        if not self._backend_regions or self._player is None or self.scheduler.scope is None:
            return []
        generation, session_id, scope, player = json.loads(self.scheduler.scope)
        requests = []
        for task in self.tasks.active():
            if task.region_window is None:
                continue
            lease_id = uuid.uuid5(self._capture_namespace,
                                 f"{self.scheduler.scope}:{task.task_id}:{task.version}").hex
            requests.append({"task_id": task.task_id, "version": task.version, "lease_id": lease_id,
                             "body": {"generation": generation, "session_id": session_id,
                                      "scope": scope, "player": player, "first_lap": task.first_lap,
                                      "laps": task.until_lap - task.first_lap + 1,
                                      "window_m": [task.region_window.start_m, task.region_window.end_m]}})
        return requests

    def _captured_region(self, task, event, *, now):
        unavailable = ("Regional observations unavailable: no fresh matching backend capture.", None)
        expected = next((request for request in self.region_capture_requests()
                         if request["task_id"] == task.task_id and request["version"] == task.version), None)
        matches = [item for item in self._capture_results if isinstance(item, dict)
                   and item.get("task_id") == task.task_id and item.get("version") == task.version]
        if expected is None or len(matches) != 1:
            return unavailable
        item = matches[0]
        response = item.get("response")
        if (type(item.get("task_id")) is not int or type(item.get("version")) is not int
                or any(item.get(key) != expected[key] for key in ("lease_id", "body"))
                or not isinstance(response, dict) or response.get("status") != "available"
                or not _number(item.get("request_age_s"))
                or type(item.get("received_at")) not in (int, float)
                or not math.isfinite(item["received_at"]) or item["received_at"] < 0
                or now < item["received_at"]):
            return unavailable
        data = response.get("data")
        binding = {key: expected["body"][key] for key in ("generation", "session_id", "scope", "player")}
        if (not isinstance(data, dict) or data.get("lease_id") != expected["lease_id"]
                or data.get("state") not in ("active", "complete") or data.get("binding") != binding
                or data.get("window") != task.region_window.view()
                or type(data.get("first_lap")) is not int or type(data.get("until_lap")) is not int
                or data.get("first_lap") != task.first_lap
                or data.get("until_lap") != task.until_lap or not isinstance(data.get("results"), list)
                or len(data["results"]) > 20):
            return unavailable
        results = [result for result in data["results"] if isinstance(result, dict)
                   and type(result.get("event_id")) is int and result["event_id"] == event["id"]
                   and type(result.get("lap_number")) is int and result["lap_number"] == event["lap_number"]]
        if len(results) != 1:
            return unavailable
        result = results[0]
        if (not _number(result.get("completion_age_s"))
                or type(result.get("received_monotonic_ns")) is not int
                or result["received_monotonic_ns"] != event.get("received_monotonic_ns")
                or not isinstance(result.get("text"), str) or not result["text"]):
            return unavailable
        try:
            text_length = len(result["text"].encode("utf-8"))
        except UnicodeError:
            return unavailable
        if text_length > 900:
            return unavailable
        age = result["completion_age_s"] + item["request_age_s"] + now - item["received_at"]
        if age >= 3:
            return unavailable
        evidence = result.get("evidence")
        if evidence is not None:
            measurement = evidence.get("measurement") if isinstance(evidence, dict) else None
            if (not isinstance(measurement, dict) or evidence.get("lap_number") != event["lap_number"]
                    or measurement.get("diagnostic_only") is not True
                    or any(measurement.get(key) is not False for key in
                           ("layout_verified", "coaching_eligible", "ranking_eligible"))):
                return unavailable
        self._fact_age_s = max(self._fact_age_s or 0, age)
        self.notify({"event": "radio_region_capture_consumed", "task_id": task.task_id,
                     "version": task.version, "lease_id": expected["lease_id"],
                     "event_id": event["id"], "completion_age_s": age,
                     "request_latency_s": item["request_age_s"]})
        return result["text"], deepcopy(evidence)

    def check_freshness(self, *, now: float) -> None:
        if self._player is not None and self._age_s + now - self._received_at >= 3:
            self._unavailable("lap_observations_stale")

    def answer_last_lap(self, *, now: float) -> None:
        self.check_freshness(now=now)
        text = "No fresh player telemetry is available."
        if self._player is not None:
            lap = self._player["lap"]
            number, elapsed = lap.get("current_lap_number"), lap.get("last_lap_time_ms")
            text = (lap_message(number - 1, elapsed)
                    if _integer(number, 2, 255) and _integer(elapsed, 1, 2**32 - 1)
                    else "No completed player lap time is available.")
        self._answer_id += 1
        self.scheduler.submit(RadioMessage(f"answer:{self._answer_id}", self.scheduler.scope,
                                          text, "answer", now + 15,
                                          observation_age_s=(self._age_s + now - self._received_at
                                                             if self._player is not None else None)), now=now)

    def status(self) -> dict:
        return {"backend": self._status[0] if self._status else "unavailable",
                "reason": self._status[1] if self._status else "not_connected",
                "conversation_turns": len(self._history),
                "tasks": self.tasks.views(),
                "watches": self.watches.views(),
                "pace_bests": [record.view() for record in self.pace.best.values()],
                "quiet": self.scheduler.muted, "pending_messages": len(self.scheduler.pending),
                "active_message": self.scheduler.active.key if self.scheduler.active else None}

    def forget_conversation(self) -> None:
        self._history.clear()
        self.notify({"event": "radio_conversation_cleared"})

    def _participant_name(self, row: dict) -> str | None:
        participant = row.get("participant")
        if (not isinstance(participant, dict) or participant.get("status") != "available"
                or not _identity(row.get("identity")) or participant.get("identity") != row["identity"]
                or not isinstance(participant.get("value"), dict)):
            return None
        value = participant["value"]
        if (not _integer(value.get("driver_id"), 0, 65535)
                or not _integer(value.get("team_id"), 0, 65535)
                or not _integer(value.get("race_number"), 0, 255)):
            return None
        return display_driver_name(value.get("name"))

    def _resolve_target(self, target: str, identity=None) -> dict | None:
        rows = [row for row in self._drivers if isinstance(row, dict)
                and _integer(row.get("car_index"), 0, 23)
                and _identity(row.get("identity"))
                and row.get("car_index") == row["identity"]["car_index"]
                and isinstance(row.get("lap"), dict)]
        if identity is not None:
            matches = [row for row in rows if row["identity"] == identity]
        elif target == "self":
            matches = [row for row in rows if row["identity"] == self._player["identity"]]
        elif target.startswith("name:"):
            matches = [row for row in rows if (name := self._participant_name(row)) is not None
                       and target[5:] in driver_name_aliases(name)]
        else:
            position = self._player["lap"].get("car_position")
            if target in ("ahead", "behind"):
                if not _integer(position, 1, 24):
                    return None
                wanted = position + (1 if target == "behind" else -1)
            else:
                wanted = 1 if target == "leader" else int(target.split(":")[1])
            if not 1 <= wanted <= 24:
                return None
            if sum(isinstance(row, dict) and isinstance(row.get("lap"), dict)
                   and row["lap"].get("car_position") == wanted for row in self._drivers) != 1:
                return None
            matches = [row for row in rows if type(row["lap"].get("car_position")) is int
                       and row["lap"]["car_position"] == wanted]
        if len(matches) != 1:
            return None
        match = matches[0]
        if sum(isinstance(row, dict) and row.get("car_index") == match["car_index"]
               for row in self._drivers) != 1:
            return None
        return match

    def _clear_tasks(self, reason: str) -> None:
        if reason != "cancelled":
            self.lap_history.clear()
            self.pit_benchmarks.clear()
            self.scheduler.cancel_owner("pit_benchmark")
        self.tasks.clear(reason)
        self.watches.clear()
        self.pace.clear()
        self.regions.clear()
        self.region_progress.clear()
        self._reset_gaps()

    def _reset_gaps(self) -> None:
        self.gaps.clear()
        self.watches.reset_intervals()
        self.pace.interrupt()
        self.regions.interrupt()
        self.scheduler.cancel_owner("gap_changes")
        self.scheduler.cancel_owner("lap_reports")
        self.scheduler.cancel_owner("pace_sectors")

    def _aligned_observation(self, row: dict, *, now: float):
        observation = row.get("observation")
        if (row["identity"] != self._player["identity"] or not isinstance(observation, dict)
                or observation.get("status") != "available" or observation.get("identity") != row["identity"]
                or not isinstance(observation.get("value"), dict)):
            return None
        source = observation.get("source")
        expected = self._observation_frame
        if (not isinstance(source, dict) or expected is None
                or any(value is None or source.get(key) != value for key, value in expected.items())
                or any(not _integer(source.get(key), 0, 2**64 - 1) for key in
                       ("session_uid", "packet_format", "epoch", "frame_identifier", "frame_ordinal",
                        "lap_received_monotonic_ns", "telemetry_received_monotonic_ns", "telemetry_captured_at_ns"))
                or type(source.get("session_time_s")) not in (int, float) or not math.isfinite(source["session_time_s"])
                or any(observation["value"].get(key) is None
                       or type(observation["value"].get(key)) is not type(row["lap"].get(field))
                       or observation["value"].get(key) != row["lap"].get(field) for key, field in
                       (("lap_number", "current_lap_number"), ("lap_distance_m", "lap_distance_m"),
                        ("current_lap_time_ms", "current_lap_time_ms")))):
            return None
        telemetry_age = (self._age_s + now - self._received_at
                         + (source["lap_received_monotonic_ns"] - source["telemetry_received_monotonic_ns"]) / 1e9)
        return (observation, telemetry_age) if 0 <= telemetry_age < 3 else None

    def _region_sample(self, *, now: float):
        aligned = self._aligned_observation(self._player, now=now)
        if aligned is None:
            return None
        observation = aligned[0]
        values, source = observation["value"], observation["source"]
        if (not _integer(source["frame_identifier"], 0, 2**32 - 1)
                or not _integer(values.get("speed_kph"), 0, 65535)
                or not _integer(values.get("current_lap_time_ms"), 0, 86_400_000)
                or not _number(source["session_time_s"])
                or type(values.get("lap_distance_m")) not in (int, float)
                or not math.isfinite(values["lap_distance_m"])
                or self._track_length_m is None or not 0 <= values["lap_distance_m"] <= self._track_length_m
                or type(values.get("brake")) not in (int, float) or not math.isfinite(values["brake"])
                or not 0 <= values["brake"] <= 1):
            return None
        return TraceSample(frame_identifier=source["frame_identifier"], distance_m=values["lap_distance_m"],
                           time_s=values["current_lap_time_ms"] / 1000,
                           session_time_s=source["session_time_s"], speed_mps=values["speed_kph"] / 3.6,
                           brake=values["brake"], throttle=None, steering=None, gear=None, drs_active=None)

    def _timing_fact(self, metric: str, row: dict, *, now: float) -> str:
        if metric in UNAVAILABLE_FACTS:
            return UNAVAILABLE_FACTS[metric]
        if metric in ("tyre_age", "tyre_compound"):
            tyre = row.get("tyre")
            value = tyre.get("value") if isinstance(tyre, dict) and tyre.get("status") == "available" else None
            label = "current tyre age" if metric == "tyre_age" else "current tyre compound"
            if not isinstance(value, dict) or not _integer(value.get("actual_compound"), 1, 255):
                return label + " unavailable"
            if metric == "tyre_age":
                age = value.get("age_laps")
                return f"current tyre age {age} laps" if _integer(age, 0, 255) else label + " unavailable"
            visual = value.get("visual_compound")
            return (f"actual tyre compound {value['actual_compound']}, "
                    + (f"visual compound {visual}" if _integer(visual, 1, 255) else "visual compound unavailable"))
        if metric.startswith("lap_number:"):
            number = int(metric.split(":", 1)[1])
            state = self.lap_history.get(row)
            record = next((record for record in state.laps if record.lap_number == number), None) if state else None
            if record is None:
                return f"lap {number} unavailable in retained eligible radio history; not complete session history"
            return f"fully observed valid lap {number} {record.elapsed_ms / 1000:.3f} seconds, compound {record.compound}"
        if metric in ("observed_best", "last_sector1", "last_sector2", "last_sector3"):
            player = row["identity"] == self._player["identity"]
            state = self.lap_history.get(row)
            tracker = state.tracker if state else None
            if tracker is None or not player and not state.laps:
                return f"observed lap history unavailable for this {'player' if player else 'opponent'}"
            if metric == "observed_best":
                try:
                    reference = tracker.reference(row)
                except ValueError as error:
                    return str(error) if player else "best observed valid lap unavailable; no eligible reference on this driver's current compound"
                return (f"best fully observed valid radio lap on current compound {reference.compound}: "
                        f"lap {reference.lap_number} {reference.elapsed_ms / 1000:.3f} seconds; "
                        "not the complete session best")
            reference = tracker.last_completed
            number = row["lap"].get("current_lap_number")
            sector = int(metric[-1])
            if (reference is None or type(number) is not int or reference.lap_number != number - 1
                    or reference.sector_times_ms is None
                    or row["lap"].get("last_lap_time_ms") != reference.elapsed_ms):
                return f"last lap sector {sector} unavailable; complete observed {'player' if player else 'opponent'} sector history missing"
            elapsed = (reference.sector_times_ms[sector - 1] if sector < 3
                       else reference.elapsed_ms - sum(reference.sector_times_ms))
            source = "derived from lap minus sectors 1 and 2" if sector == 3 else "observed"
            return f"lap {reference.lap_number} sector {sector} {elapsed / 1000:.3f} seconds ({source})"
        if metric in ("speed", "brake_input"):
            aligned = self._aligned_observation(row, now=now)
            if aligned is None:
                return "aligned player telemetry unavailable; opponent detail is not exposed by this radio feed"
            observation, telemetry_age = aligned
            self._fact_age_s = max(self._fact_age_s or 0, telemetry_age)
            value = observation["value"].get("speed_kph" if metric == "speed" else "brake")
            if metric == "speed" and _integer(value, 0, 65535):
                return f"observed speed {value} kilometres per hour"
            if metric == "brake_input" and type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1:
                return f"observed brake input {value * 100:.1f} percent, not a braking point"
            return "aligned player telemetry unavailable"
        if metric == "consistency":
            return "use coach my consistency to track three consecutive valid observed laps"
        if metric == "target_pace":
            return "use help me stay within a tolerance of my best valid lap to set target pace"
        if metric == "race_watch":
            return "use watch followed by one driver to create a catch or defend watch"
        if metric in GAP_CHANGES:
            return self.gaps.answer(metric, is_player=row["identity"] == self._player["identity"])
        return timing_fact(metric, row["lap"], self._player["lap"])

    def answer_radio_control(self, text: str, *, now: float) -> None:
        self.check_freshness(now=now)
        if self.scheduler.scope is None:
            self.notify({"event": "radio_voice_control_unavailable", "reason": "session_unavailable"})
            return
        self._answer_id += 1
        self.scheduler.submit(RadioMessage(f"answer:{self._answer_id}", self.scheduler.scope,
                                          text, "answer", now + 15), now=now)

    def _control_answer(self, text: str, *, now: float) -> None:
        if len(text.encode("utf-8")) > 1024:
            text = "Task details are too long to speak. Use status to review them."
        self._answer_id += 1
        self.notify({"event": "radio_task_answer", "text": text, "tasks": self.tasks.views()})
        self.scheduler.submit(RadioMessage(f"answer:{self._answer_id}", self.scheduler.scope,
                                          text, "answer", now + 30), now=now)

    def handle_request(self, question: str, *, now: float) -> None:
        self.check_freshness(now=now)
        try:
            action = parse_task_request(question)
            if action is None:
                self.answer_question(question, now=now)
                return
            if action.kind == "list":
                active = [task for task in self.tasks.views() if task["state"] == "active"]
                lines = []
                for task in active:
                    targets = ", ".join(target.removeprefix("name:").replace("position:", "P") for target in task["targets"])
                    metrics = ", ".join(metric.replace("_", " ") for metric in task["metrics"])
                    if "race_watch" in task["metrics"]:
                        targets += " (fixed driver identity, not a refreshed race-position role)"
                    if task["pace_reference"] is not None:
                        reference = task["pace_reference"]
                        metrics += (f"; fixed observed lap {reference['lap_number']} "
                                    f"{reference['elapsed_ms'] / 1000:.3f} seconds, compound {reference['actual_compound']}, "
                                    f"tolerance {task['pace_tolerance_ms'] / 1000:.3f} seconds")
                    if task["region_window"] is not None:
                        window = task["region_window"]
                        metrics += f"; numeric region {window['start_m']:g} to {window['end_m']:g} metres"
                    if task["region_reference"] is not None:
                        reference = task["region_reference"]
                        metrics += f"; fixed observed lap {reference['lap_number']}, compound {reference['actual_compound']}"
                    expiry = f"through lap {task['until_lap']}" if task["until_lap"] is not None else "until cancelled or session change"
                    lines.append(f"Task {task['task_id']}: {targets}; {metrics}; {expiry}.")
                text = " ".join(lines) if lines else "No active lap reports."
            elif action.kind == "cancel_all":
                self._clear_tasks("cancelled")
                text = "All lap-report tracking cancelled."
            elif action.kind == "cancel":
                if self.tasks.cancel(action.task_id):
                    self.watches.remove(action.task_id)
                    self.regions.remove(action.task_id)
                    self.scheduler.cancel_owner("lap_reports")
                    self.scheduler.cancel_owner("pace_sectors")
                    text = f"Task {action.task_id} cancelled."
                else:
                    text = "That task does not exist."
            else:
                if self._player is None or not _integer(self._player["lap"].get("current_lap_number"), 1, 255):
                    raise ValueError("Fresh player telemetry and a known lap number are required to create a report.")
                aliases = tuple(alias for row in self._drivers if isinstance(row, dict)
                                and (name := self._participant_name(row)) is not None for alias in driver_name_aliases(name))
                plan = (QuestionPlan(("self",), ("regional_observations",)) if action.region_bounds_m is not None
                        else plan_question(action.question, names=aliases))
                if any(metric in UNAVAILABLE_FACTS for metric in plan.metrics):
                    raise ValueError("Recurring reports cannot track session-best laps, fastest sectors or fuel from this feed. Ask a timing question for partial available results.")
                region_window = None
                region_reference = None
                if action.region_bounds_m is not None:
                    if self._track_length_m is None:
                        raise ValueError("Regional tracking requires a known current track length.")
                    region_window = RegionWindow(*action.region_bounds_m, self._track_length_m)
                    if action.region_compare:
                        region_reference = self.region_progress.reference(region_window, self._player)
                pace_reference = self.pace.reference(self._player) if "target_pace" in plan.metrics else None
                watching = "race_watch" in plan.metrics
                if watching and (len(plan.targets) != 1 or plan.metrics != ("race_watch",)):
                    raise ValueError("Watch one explicit driver at a time without additional facts.")
                if watching and plan.targets == ("self",):
                    raise ValueError("Choose another driver to watch.")
                bindings = []
                for target in plan.targets:
                    row = self._resolve_target(target) if watching or target.startswith("name:") else None
                    if (watching or target.startswith("name:")) and row is None:
                        raise ValueError("A named report target is unavailable or ambiguous. Use a full name or race position.")
                    if watching and row["identity"] == self._player["identity"]:
                        raise ValueError("Choose another driver to watch.")
                    bindings.append(json.dumps(row["identity"], sort_keys=True) if row is not None else None)
                task = self.tasks.install(plan, tuple(bindings), cursor=self._cursor or 0,
                                          lap=self._player["lap"]["current_lap_number"] + (1 if region_window else 0),
                                          laps=action.laps, task_id=action.task_id,
                                          pace_reference=pace_reference, pace_tolerance_ms=action.tolerance_ms,
                                          region_window=region_window, region_reference=region_reference)
                if action.kind == "update":
                    self.regions.remove(task.task_id)
                self.scheduler.cancel_owner("lap_reports")
                self.scheduler.cancel_owner("pace_sectors")
                self.watches.observe(self.tasks.active(), self._player, self._resolve_target, now=now)
                expiry = f"through lap {task.until_lap}" if task.until_lap is not None else "until cancelled or the session changes"
                text = f"Task {task.task_id} active: end-of-lap report from lap {task.first_lap}, {expiry}."
                if "consistency" in plan.metrics:
                    text += (" Rolling three consecutive fully observed valid laps on one compound since this request. "
                             "Sector 1 and 2 fastest-to-slowest spreads, not pace deficits. "
                             "Missing evidence restarts warm-up; short tasks may expire before three samples.")
                if region_window is not None:
                    capture_policy = ("Backend capture admission pending; incomplete data may leave partial coverage. "
                                      if self._backend_regions else
                                      "Sparse polling may leave partial coverage or unavailable onset and region time. ")
                    text += (f" Tracking your sampled braking and minimum speed from {region_window.start_m:g} "
                             f"to {region_window.end_m:g} metres on upcoming laps, not this partial lap. "
                             f"{capture_policy}"
                             "Numeric observations only, not corner coaching or opponent tracking.")
                    if region_reference is not None:
                        text += (f" Fixed reference: previous complete observed region on lap {region_reference.lap_number}, "
                                 f"compound {region_reference.actual_compound}, {region_reference.elapsed_s:.3f} seconds. "
                                 "Reference stays fixed; matching compound does not establish matching tyre age, fuel, conditions or traffic.")
                if watching:
                    name = self._participant_name(row) or plan.targets[0].removeprefix("name:").replace("position:", "P")
                    text += f" Watching {name}; driver identity fixed. Three comparable adjacent-driver boundaries required. Alerts report trend changes."
                if pace_reference is not None:
                    text += (f" Fixed reference: best fully observed valid radio lap {pace_reference.lap_number}, "
                             f"{pace_reference.elapsed_ms / 1000:.3f} seconds on compound {pace_reference.compound}. "
                             f"Stay within {task.pace_tolerance_ms / 1000:.3f} seconds; reference does not update automatically.")
                    text += (" Observed sector feedback enabled." if pace_reference.sector_times_ms is not None
                             else " Sector feedback unavailable for this reference; lap feedback remains enabled.")
        except ValueError as error:
            text = str(error)
        self._control_answer(text, now=now)

    def _report_pace_sector(self, event: dict, *, now: float, age: float) -> None:
        tasks = [task for task in self.tasks.eligible(event_id=event["id"], lap=event["lap_number"])
                 if "target_pace" in task.plan.metrics]
        if not tasks:
            return
        reports = []
        for task in tasks:
            feedback = self.pace.answer_sector(event, task.pace_reference, task.pace_tolerance_ms, self._player)
            reports.append(f"Task {task.task_id}: " + feedback)
            self.notify({"event": "radio_target_pace_sector", "task_id": task.task_id, "version": task.version,
                         "lap_number": event["lap_number"], "sector": event.get("sector"),
                         "reference": task.pace_reference.view(), "text": feedback})
        text = f"Lap {event['lap_number']} sector progress. " + " ".join(reports)
        if len(text.encode("utf-8")) > 1024:
            text = "The sector pace reports are too long to speak safely. Request fewer tasks."
        self.scheduler.cancel_owner("pace_sectors")
        self.scheduler.submit(RadioMessage(f"pace-sector:{event['id']}", self.scheduler.scope, text, "proactive",
                                          now + 60, observation_age_s=age, dispatch_before=now + 3 - age,
                                          owner="pace_sectors"), now=now)

    def _report_tasks(self, event: dict, *, now: float, age: float) -> bool:
        self._fact_age_s = None
        tasks = self.tasks.eligible(event_id=event["id"], lap=event["lap_number"])
        if not tasks or self._player["lap"].get("current_lap_number") != event["lap_number"] + 1:
            return False
        elapsed = event.get("lap_time_ms")
        if elapsed is not None and (not _integer(elapsed, 1, 2**32 - 1)
                                   or elapsed != self._player["lap"].get("last_lap_time_ms")):
            return False
        reports = []
        for task in tasks:
            if task.region_window is not None:
                feedback, evidence = (self._captured_region(task, event, now=now) if self._backend_regions
                                      else self.regions.complete(task, event))
                comparison = None
                if task.region_reference is not None:
                    feedback, comparison = self.region_progress.compare(task.region_reference, event, evidence)
                if max(age, self._fact_age_s or 0) < 3:
                    self.region_progress.observe(task.region_window, event, evidence)
                reports.append(f"Task {task.task_id}: " + feedback)
                self.notify({"event": "radio_region_report", "task_id": task.task_id, "version": task.version,
                             "lap_number": event["lap_number"], "event_id": event["id"],
                             "binding": self.scheduler.scope, "player": deepcopy(self._player["identity"]),
                             "evidence": evidence, "comparison": comparison, "text": feedback})
                continue
            if "consistency" in task.plan.metrics:
                feedback, samples = self.pace.consistency(task)
                reports.append(f"Task {task.task_id}: " + feedback)
                self.notify({"event": "radio_consistency_report", "task_id": task.task_id, "version": task.version,
                             "lap_number": event["lap_number"], "samples": samples, "text": feedback})
                continue
            if "target_pace" in task.plan.metrics:
                feedback = self.pace.answer(event, task.pace_reference, task.pace_tolerance_ms)
                reports.append(f"Task {task.task_id}: " + feedback)
                self.notify({"event": "radio_target_pace_report", "task_id": task.task_id, "version": task.version,
                             "lap_number": event["lap_number"], "reference": task.pace_reference.view(),
                             "tolerance_ms": task.pace_tolerance_ms, "text": feedback})
                continue
            if "race_watch" in task.plan.metrics:
                watch = self.watches.complete(task, event["lap_number"])
                if watch is not None:
                    row = self._resolve_target(task.plan.targets[0], json.loads(task.fixed_bindings[0]))
                    label = self._participant_name(row) if row is not None else None
                    label = label or task.plan.targets[0].removeprefix("name:").replace("position:", "P")
                    reports.append(f"Task {task.task_id}, {label}: " + watch)
                    self.notify({"event": "radio_watch_notice", "task_id": task.task_id,
                                 "version": task.version, "lap_number": event["lap_number"], "text": watch})
                continue
            pieces = []
            for target, fixed in zip(task.plan.targets, task.fixed_bindings):
                row = self._resolve_target(target, json.loads(fixed)) if fixed is not None else self._resolve_target(target)
                label = "You" if target == "self" else target.removeprefix("name:").replace("position:", "P")
                if row is None:
                    pieces.append(f"{label}: driver unavailable.")
                else:
                    name = self._participant_name(row)
                    if name is not None and target != "self":
                        label = name
                    pieces.append(f"{label}: " + ", ".join(self._timing_fact(metric, row, now=now) for metric in task.plan.metrics) + ".")
            reports.append(f"Task {task.task_id}: " + " ".join(pieces))
        if not reports:
            self.tasks.complete_lap(event["lap_number"])
            return True
        text = f"Lap {event['lap_number']} reports. " + " ".join(reports)
        if len(text.encode("utf-8")) > 1024:
            text = "The lap reports are too long to speak safely. Request fewer drivers or facts."
        self.notify({"event": "radio_task_report", "task_ids": [task.task_id for task in tasks],
                     "lap_number": event["lap_number"], "text": text})
        age = max(age, self._fact_age_s or 0)
        self.scheduler.submit(RadioMessage(f"reports:{event['id']}", self.scheduler.scope, text, "proactive",
                                          now + 60, observation_age_s=age, dispatch_before=now + 3 - age,
                                          owner="lap_reports"), now=now)
        self.tasks.complete_lap(event["lap_number"])
        return True

    def answer_question(self, question: str, *, now: float) -> None:
        self._fact_age_s = None
        self.check_freshness(now=now)
        previous = self._history[-1] if self._history else None
        started = time.perf_counter()
        text = "No fresh player telemetry is available."
        results = []
        gap_question = False
        if self._player is not None:
            try:
                aliases = tuple(alias for row in self._drivers if isinstance(row, dict)
                                and (name := self._participant_name(row)) is not None
                                for alias in driver_name_aliases(name))
                plan = plan_question(question, previous["plan"] if previous else None, names=aliases)
            except ValueError as error:
                text = str(error)
            else:
                gap_question = any(metric in GAP_CHANGES for metric in plan.metrics)
                bindings = []
                for index, target in enumerate(plan.targets):
                    if plan.inherited_targets:
                        identity = previous["bindings"][index]
                        row = self._resolve_target(target, identity) if identity is not None else None
                    else:
                        row = self._resolve_target(target)
                        identity = row["identity"] if row else None
                    bindings.append(deepcopy(identity))
                    label = "You" if target == "self" else target.replace("position:", "P").capitalize()
                    if target.startswith("name:"):
                        label = target[5:]
                    name = self._participant_name(row) if row is not None else None
                    if name is not None and target != "self":
                        label = name
                    if plan.inherited_targets and target != "self":
                        label = f"Previously selected {label}"
                    if row is None:
                        result = f"{label}: driver unavailable or ambiguous."
                        if target.startswith("name:"):
                            result += " Use a full name or P1 to P24."
                    else:
                        result = f"{label} (car slot {row['car_index']}): " + ", ".join(
                            self._timing_fact(metric, row, now=now) for metric in plan.metrics) + "."
                    results.append(result)
                text = " ".join(results)
                self._history.append({"question": question, "plan": plan, "bindings": bindings})
                self._history = self._history[-8:]
        self._answer_id += 1
        self.notify({"event": "radio_question_answer", "question": question, "text": text,
                     "results": results, "planning_s": time.perf_counter() - started})
        age = self._age_s + now - self._received_at if self._player is not None else None
        if self._fact_age_s is not None:
            age = max(age or 0, self._fact_age_s)
        self.scheduler.submit(RadioMessage(f"answer:{self._answer_id}", self.scheduler.scope,
                                          text, "answer", now + 60, observation_age_s=age,
                                          dispatch_before=now + 3 - (age or 0),
                                          owner="gap_changes" if gap_question else None), now=now)
