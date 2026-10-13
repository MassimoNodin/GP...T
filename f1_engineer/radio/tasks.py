from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ..driver_names import driver_name_key
from .questions import QuestionPlan
from .pace import PaceReference


@dataclass(frozen=True, slots=True)
class TaskAction:
    kind: str
    question: str = ""
    task_id: int | None = None
    laps: int | None = None
    tolerance_ms: int | None = None


def parse_task_request(request: str) -> TaskAction | None:
    if not isinstance(request, str) or not 1 <= len(request.encode("utf-8")) <= 1024:
        return None
    text = driver_name_key(request).rstrip("?.!")
    if text in ("what are you tracking", "list tasks", "tasks"):
        return TaskAction("list")
    if text == "cancel all tracking":
        return TaskAction("cancel_all")
    cancel = re.fullmatch(r"cancel task ([1-9][0-9]{0,8})", text)
    if cancel:
        return TaskAction("cancel", task_id=int(cancel.group(1)))
    update = re.match(r"update task ([1-9][0-9]{0,8}) (.+)", text)
    task_id = None
    if update:
        task_id, text = int(update.group(1)), update.group(2)
    duration = re.fullmatch(r"(.+) for (?:the )?next ([1-9][0-9]?) laps?", text)
    payload = duration.group(1) if duration else text
    pace = re.fullmatch(r"help me stay within (?:(one|two|three|four|five|six|seven|eight|nine|[1-9]) tenths?|([0-9](?:\.[0-9]{1,3})?) seconds?) of my best valid lap", payload)
    if pace:
        if pace.group(1):
            tenths = pace.group(1)
            words = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
            tolerance = (int(tenths) if tenths.isdecimal() else words.index(tenths) + 1) * 100
        else:
            seconds, separator, fraction = pace.group(2).partition(".")
            tolerance = int(seconds) * 1000 + int(fraction.ljust(3, "0") or "0")
        laps = int(duration.group(2)) if duration else None
        if not 1 <= tolerance <= 5000 or laps is not None and laps > 20:
            raise ValueError("Choose a target tolerance from 0.001 to 5.000 seconds and one to twenty upcoming laps.")
        return TaskAction("update" if task_id is not None else "create", "my target pace", task_id, laps, tolerance)
    watch = re.fullmatch(r"(?:watch (.+)|tell me if (.+) starts closing)", duration.group(1) if duration else text)
    if watch:
        target = watch.group(1) or watch.group(2)
        laps = None
        if duration:
            laps = int(duration.group(2))
            if laps > 20:
                raise ValueError("Please choose one to twenty upcoming laps.")
        return TaskAction("update" if task_id is not None else "create", target + " catch or defend watch", task_id, laps)
    report = re.fullmatch(r"(?:tell me|report)(?: at)? (?:the )?end of (?:(each|next|every|the) )?lap (.+)", text)
    if not report:
        return None
    cadence, question = report.groups()
    duration = re.fullmatch(r"(.+) for (?:the )?next ([1-9][0-9]?) laps?", question)
    laps = 1 if cadence == "next" else None
    if duration:
        question, count = duration.groups()
        laps = int(count)
        if not 1 <= laps <= 20 or (cadence == "next" and laps != 1):
            raise ValueError("Please choose one to twenty upcoming laps, or just the next lap.")
    return TaskAction("update" if task_id is not None else "create", question, task_id, laps)


@dataclass(frozen=True, slots=True)
class LapReportTask:
    task_id: int
    version: int
    plan: QuestionPlan
    fixed_bindings: tuple[str | None, ...]
    created_cursor: int
    first_lap: int
    until_lap: int | None
    state: str = "active"
    pace_reference: PaceReference | None = None
    pace_tolerance_ms: int | None = None

    def view(self) -> dict:
        return {"task_id": self.task_id, "version": self.version, "state": self.state,
                "targets": list(self.plan.targets), "metrics": list(self.plan.metrics),
                "first_lap": self.first_lap, "until_lap": self.until_lap,
                "pace_reference": self.pace_reference.view() if self.pace_reference is not None else None,
                "pace_tolerance_ms": self.pace_tolerance_ms,
                "reference_policy": ("valid observed player lap and tolerance fixed" if "target_pace" in self.plan.metrics
                                     else "watch driver identity fixed; pauses outside adjacency" if "race_watch" in self.plan.metrics
                                     else "named drivers fixed; race-position roles refreshed each lap")}


class LapReportRegistry:
    def __init__(self, *, notify=None) -> None:
        self._tasks = {}
        self._next_id = 1
        self.notify = notify or (lambda event: None)

    def views(self) -> list[dict]:
        return [task.view() for task in self._tasks.values()]

    def active(self) -> tuple[LapReportTask, ...]:
        return tuple(task for task in self._tasks.values() if task.state == "active")

    def clear(self, reason: str) -> None:
        for task in self._tasks.values():
            self.notify({"event": "radio_task_ended", "reason": reason, **task.view()})
        self._tasks.clear()

    def cancel(self, task_id: int) -> bool:
        task = self._tasks.pop(task_id, None)
        if task is None:
            return False
        self.notify({"event": "radio_task_ended", "reason": "cancelled", **task.view()})
        return True

    def install(self, plan: QuestionPlan, bindings: tuple[str | None, ...], *, cursor: int,
                lap: int, laps: int | None, task_id: int | None = None,
                pace_reference: PaceReference | None = None, pace_tolerance_ms: int | None = None) -> LapReportTask:
        existing = self._tasks.get(task_id) if task_id is not None else None
        if task_id is not None and existing is None:
            raise ValueError("That task does not exist.")
        active = [task for task in self._tasks.values() if task.state == "active" and task.task_id != task_id]
        if laps is not None and not 1 <= laps <= 20:
            raise ValueError("Please choose one to twenty upcoming laps.")
        if len(bindings) != len(plan.targets):
            raise ValueError("Task references do not match its driver targets.")
        if "target_pace" in plan.metrics:
            if (plan.targets != ("self",) or plan.metrics != ("target_pace",)
                    or not isinstance(pace_reference, PaceReference) or type(pace_tolerance_ms) is not int
                    or not 1 <= pace_tolerance_ms <= 5000):
                raise ValueError("Target pace requires a valid fixed player-lap reference and tolerance.")
        elif pace_reference is not None or pace_tolerance_ms is not None:
            raise ValueError("Only target-pace tasks can own a pace reference.")
        if "race_watch" in plan.metrics and (plan.metrics != ("race_watch",) or len(plan.targets) != 1
                                            or not isinstance(bindings[0], str)):
            raise ValueError("A watch requires one driver with a fixed identity and no additional facts.")
        if task_id is None:
            for task in active:
                if (task.plan == plan and task.fixed_bindings == bindings
                        and task.pace_reference == pace_reference and task.pace_tolerance_ms == pace_tolerance_ms
                        and task.until_lap == (lap + laps - 1 if laps is not None else None)):
                    return task
        if len(active) >= 3 or sum(len(task.plan.targets) * len(task.plan.metrics) for task in active) + len(plan.targets) * len(plan.metrics) > 9:
            raise ValueError("Keep at most three active reports and nine total driver facts per lap.")
        self._tasks = {key: task for key, task in self._tasks.items() if task.state == "active" or key == task_id}
        identifier = task_id if task_id is not None else self._next_id
        if task_id is None:
            self._next_id += 1
        task = LapReportTask(identifier, existing.version + 1 if existing else 1, plan, bindings, cursor, lap,
                             lap + laps - 1 if laps is not None else None,
                             pace_reference=pace_reference, pace_tolerance_ms=pace_tolerance_ms)
        self._tasks[identifier] = task
        self.notify({"event": "radio_task_updated" if existing else "radio_task_created", **task.view()})
        return task

    def eligible(self, *, event_id: int, lap: int) -> tuple[LapReportTask, ...]:
        return tuple(task for task in self._tasks.values() if task.state == "active"
                     and event_id > task.created_cursor and lap >= task.first_lap
                     and (task.until_lap is None or lap <= task.until_lap))

    def complete_lap(self, lap: int) -> None:
        for task_id, task in tuple(self._tasks.items()):
            if task.state == "active" and task.until_lap is not None and lap >= task.until_lap:
                completed = replace(task, state="completed")
                self._tasks[task_id] = completed
                self.notify({"event": "radio_task_ended", "reason": "lap_limit_reached", **completed.view()})
