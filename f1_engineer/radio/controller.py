from __future__ import annotations

import json
import math
import re
import time
from copy import deepcopy

from ..driver_names import display_driver_name, driver_name_aliases
from .questions import plan_question, timing_fact
from .scheduler import RadioMessage, RadioScheduler
from .tasks import LapReportRegistry, parse_task_request


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
    def __init__(self, scheduler: RadioScheduler, *, notify=None) -> None:
        self.scheduler = scheduler
        self.notify = notify or (lambda event: None)
        self._cursor: int | None = None
        self._player = None
        self._received_at = 0.0
        self._age_s = 0.0
        self._answer_id = 0
        self._status = None
        self._drivers = []
        self._history = []
        self.tasks = LapReportRegistry(notify=self.notify)

    def _unavailable(self, reason: str) -> None:
        self.scheduler.set_scope(None)
        self._cursor = None
        self._player = None
        self._drivers = []
        self._history.clear()
        safe_reason = reason if re.fullmatch(r"[a-z0-9_]{1,96}", reason) else "unavailable"
        self.tasks.clear(safe_reason)
        self._report("unavailable", safe_reason)

    def _report(self, status: str, reason=None) -> None:
        if (status, reason) != self._status:
            self._status = (status, reason)
            self.notify({"event": "radio_backend", "status": status, "reason": reason})

    def feed(self, snapshot: dict, *, now: float, transport_age_s: float = 0.0) -> None:
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
        cursor = snapshot["event_cursor"]
        self._report("available")
        if (changed or self._cursor is None or cursor < self._cursor
                or snapshot["event_floor"] > self._cursor):
            if not changed and self._cursor is not None:
                self.scheduler.cancel()
                self.tasks.clear("event_history_gap")
                self.notify({"event": "radio_resynchronised", "reason": "event_history_gap"})
            self._cursor = cursor
            return
        seen_events = set()
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
            if (event.get("kind") != "lap_completed"
                    or event.get("qualification") != "observed_lap_transition"
                    or not _integer(event.get("lap_number"), 1, 255)
                    or not _integer(event.get("received_monotonic_ns"))):
                continue
            elapsed_ns = snapshot["received_monotonic_ns"] - event["received_monotonic_ns"]
            if elapsed_ns < 0:
                continue
            event_age = age + elapsed_ns / 1e9
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
        self.tasks.clear(reason)
        self.scheduler.cancel_owner("lap_reports")

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
                    expiry = f"through lap {task['until_lap']}" if task["until_lap"] is not None else "until cancelled or session change"
                    lines.append(f"Task {task['task_id']}: {targets}; {metrics}; {expiry}.")
                text = " ".join(lines) if lines else "No active lap reports."
            elif action.kind == "cancel_all":
                self._clear_tasks("cancelled")
                text = "All lap-report tracking cancelled."
            elif action.kind == "cancel":
                if self.tasks.cancel(action.task_id):
                    self.scheduler.cancel_owner("lap_reports")
                    text = f"Task {action.task_id} cancelled."
                else:
                    text = "That task does not exist."
            else:
                if self._player is None or not _integer(self._player["lap"].get("current_lap_number"), 1, 255):
                    raise ValueError("Fresh player telemetry and a known lap number are required to create a report.")
                aliases = tuple(alias for row in self._drivers if isinstance(row, dict)
                                and (name := self._participant_name(row)) is not None for alias in driver_name_aliases(name))
                plan = plan_question(action.question, names=aliases)
                bindings = []
                for target in plan.targets:
                    row = self._resolve_target(target) if target.startswith("name:") else None
                    if target.startswith("name:") and row is None:
                        raise ValueError("A named report target is unavailable or ambiguous. Use a full name or race position.")
                    bindings.append(json.dumps(row["identity"], sort_keys=True) if row is not None else None)
                task = self.tasks.install(plan, tuple(bindings), cursor=self._cursor or 0,
                                          lap=self._player["lap"]["current_lap_number"], laps=action.laps, task_id=action.task_id)
                self.scheduler.cancel_owner("lap_reports")
                expiry = f"through lap {task.until_lap}" if task.until_lap is not None else "until cancelled or the session changes"
                text = f"Task {task.task_id} active: end-of-lap report from lap {task.first_lap}, {expiry}."
        except ValueError as error:
            text = str(error)
        self._control_answer(text, now=now)

    def _report_tasks(self, event: dict, *, now: float, age: float) -> bool:
        tasks = self.tasks.eligible(event_id=event["id"], lap=event["lap_number"])
        if not tasks or self._player["lap"].get("current_lap_number") != event["lap_number"] + 1:
            return False
        elapsed = event.get("lap_time_ms")
        if elapsed is not None and (not _integer(elapsed, 1, 2**32 - 1)
                                   or elapsed != self._player["lap"].get("last_lap_time_ms")):
            return False
        reports = []
        for task in tasks:
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
                    pieces.append(f"{label}: " + ", ".join(timing_fact(metric, row["lap"]) for metric in task.plan.metrics) + ".")
            reports.append(f"Task {task.task_id}: " + " ".join(pieces))
        text = f"Lap {event['lap_number']} reports. " + " ".join(reports)
        if len(text.encode("utf-8")) > 1024:
            text = "The lap reports are too long to speak safely. Request fewer drivers or facts."
        self.notify({"event": "radio_task_report", "task_ids": [task.task_id for task in tasks],
                     "lap_number": event["lap_number"], "text": text})
        self.scheduler.submit(RadioMessage(f"reports:{event['id']}", self.scheduler.scope, text, "proactive",
                                          now + 60, observation_age_s=age, dispatch_before=now + 3 - age,
                                          owner="lap_reports"), now=now)
        self.tasks.complete_lap(event["lap_number"])
        return True

    def answer_question(self, question: str, *, now: float) -> None:
        self.check_freshness(now=now)
        previous = self._history[-1] if self._history else None
        started = time.perf_counter()
        text = "No fresh player telemetry is available."
        results = []
        if self._player is not None:
            try:
                aliases = tuple(alias for row in self._drivers if isinstance(row, dict)
                                and (name := self._participant_name(row)) is not None
                                for alias in driver_name_aliases(name))
                plan = plan_question(question, previous["plan"] if previous else None, names=aliases)
            except ValueError as error:
                text = str(error)
            else:
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
                            timing_fact(metric, row["lap"]) for metric in plan.metrics) + "."
                    results.append(result)
                text = " ".join(results)
                self._history.append({"question": question, "plan": plan, "bindings": bindings})
                self._history = self._history[-8:]
        self._answer_id += 1
        self.notify({"event": "radio_question_answer", "question": question, "text": text,
                     "results": results, "planning_s": time.perf_counter() - started})
        age = self._age_s + now - self._received_at if self._player is not None else None
        self.scheduler.submit(RadioMessage(f"answer:{self._answer_id}", self.scheduler.scope,
                                          text, "answer", now + 60, observation_age_s=age,
                                          dispatch_before=now + 3 - (age or 0)), now=now)
