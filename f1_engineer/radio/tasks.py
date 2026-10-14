from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace

from ..driver_names import driver_name_key
from .questions import QuestionPlan
from .pace import PaceReference
from .regions import RegionWindow
from .region_progress import RegionReference


def _fixed_identity(binding):
    if not isinstance(binding, str) or len(binding.encode("utf-8")) > 512:
        return None
    try:
        identity = json.loads(binding)
    except (ValueError, RecursionError):
        return None
    if (not isinstance(identity, dict)
            or set(identity) != {"car_index", "tenure_ordinal", "participant_identity_fingerprint"}
            or type(identity["car_index"]) is not int or not 0 <= identity["car_index"] < 24
            or type(identity["tenure_ordinal"]) is not int or not 1 <= identity["tenure_ordinal"] <= 2**64 - 1
            or not isinstance(identity["participant_identity_fingerprint"], str)
            or re.fullmatch(r"[a-f0-9]{64}", identity["participant_identity_fingerprint"]) is None):
        return None
    return identity


@dataclass(frozen=True, slots=True)
class TaskAction:
    kind: str
    question: str = ""
    task_id: int | None = None
    laps: int | None = None
    tolerance_ms: int | None = None
    region_bounds_m: tuple[float, float] | None = None
    region_compare: bool = False


def parse_task_request(request: str) -> TaskAction | None:
    if not isinstance(request, str) or not 1 <= len(request.encode("utf-8")) <= 1024:
        return None
    text = driver_name_key(request).rstrip("?.!")
    if text in ("what are you tracking", "list tasks", "tasks"):
        return TaskAction("list")
    if text == "cancel all tracking":
        return TaskAction("cancel_all")
    if text in ("cancel that", "cancel that tracking", "cancel that task"):
        return TaskAction("cancel_reference")
    extension = re.fullmatch(r"(?:track that|keep tracking that|continue tracking that) for another ([1-9][0-9]?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty) laps?", text)
    explicit_extension = re.fullmatch(r"extend task ([1-9][0-9]{0,8}) by ([1-9][0-9]?) laps?", text)
    if extension or explicit_extension:
        if explicit_extension:
            task_id, count = explicit_extension.groups()
            task_id, count = int(task_id), int(count)
        else:
            task_id, count = None, extension.group(1)
            words = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                     "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty")
            count = int(count) if count.isdecimal() else words.index(count) + 1
        if not 1 <= count <= 20:
            raise ValueError("Please choose one to twenty additional laps.")
        return TaskAction("extend", task_id=task_id, laps=count)
    cancel = re.fullmatch(r"cancel task ([1-9][0-9]{0,8})", text)
    if cancel:
        return TaskAction("cancel", task_id=int(cancel.group(1)))
    update = re.match(r"update task ([1-9][0-9]{0,8}) (.+)", text)
    task_id = None
    if update:
        task_id, text = int(update.group(1)), update.group(2)
    comparison = re.fullmatch(
        r"compare my braking and minimum speed from ([0-9]{1,5}(?:\.[0-9]{1,2})?) to ([0-9]{1,5}(?:\.[0-9]{1,2})?) (?:metres|meters) with my previous observed region for (?:the )?next(?: ([1-9][0-9]?))? laps?", text
    )
    region = comparison or re.fullmatch(
        r"track my braking and minimum speed from ([0-9]{1,5}(?:\.[0-9]{1,2})?) to ([0-9]{1,5}(?:\.[0-9]{1,2})?) (?:metres|meters) for (?:the )?next(?: ([1-9][0-9]?))? laps?", text
    )
    if region:
        laps = int(region.group(3) or "1")
        if laps > 20:
            raise ValueError("Please choose one to twenty upcoming laps.")
        return TaskAction("update" if task_id is not None else "create", "my regional observations",
                          task_id, laps, region_bounds_m=(float(region.group(1)), float(region.group(2))),
                          region_compare=comparison is not None)
    duration = re.fullmatch(r"(.+) for (?:the )?next ([1-9][0-9]?) laps?", text)
    payload = duration.group(1) if duration else text
    if re.fullmatch(r"(?:coach|track|monitor) my consistency", payload):
        laps = int(duration.group(2)) if duration else None
        if laps is not None and laps > 20:
            raise ValueError("Please choose one to twenty upcoming laps.")
        return TaskAction("update" if task_id is not None else "create", "my consistency", task_id, laps)
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
    region_window: RegionWindow | None = None
    region_reference: RegionReference | None = None
    opponent_first_lap: int | None = None
    reported_through_lap: int | None = None

    def view(self) -> dict:
        return {"task_id": self.task_id, "version": self.version, "state": self.state,
                "targets": list(self.plan.targets), "metrics": list(self.plan.metrics),
                "first_lap": self.first_lap, "until_lap": self.until_lap,
                "pace_reference": self.pace_reference.view() if self.pace_reference is not None else None,
                "pace_tolerance_ms": self.pace_tolerance_ms,
                "region_window": self.region_window.view() if self.region_window is not None else None,
                "region_reference": self.region_reference.view() if self.region_reference is not None else None,
                "opponent_first_lap": self.opponent_first_lap,
                "reported_through_lap": self.reported_through_lap,
                "reference_policy": ("valid observed player lap and tolerance fixed" if "target_pace" in self.plan.metrics
                                     else "both driver identities and independent numeric lap windows fixed; diagnostic only" if self.opponent_first_lap is not None
                                     else "previous observed region fixed; same-compound diagnostic differences only" if self.region_reference is not None
                                     else "numeric player-window fixed; diagnostic observations only" if self.region_window is not None
                                     else "rolling three consecutive valid observed player laps on one compound" if "consistency" in self.plan.metrics
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
                pace_reference: PaceReference | None = None, pace_tolerance_ms: int | None = None,
                region_window: RegionWindow | None = None,
                region_reference: RegionReference | None = None,
                opponent_first_lap: int | None = None) -> LapReportTask:
        if (type(cursor) is not int or cursor < 0 or type(lap) is not int or not 1 <= lap <= 255
                or task_id is not None and (type(task_id) is not int or task_id < 1)):
            raise ValueError("A report requires a valid current lap, event cursor and task identifier.")
        if laps is not None and (type(laps) is not int or not 1 <= laps <= 20 or lap + laps - 1 > 255):
            raise ValueError("Please choose one to twenty upcoming laps, within lap 255.")
        existing = self._tasks.get(task_id) if task_id is not None else None
        if task_id is not None and existing is None:
            raise ValueError("That task does not exist.")
        if existing is not None and existing.state != "active":
            raise ValueError("That task has ended. Create a new task rather than reviving an expired report.")
        active = [task for task in self._tasks.values() if task.state == "active" and task.task_id != task_id]
        if len(bindings) != len(plan.targets):
            raise ValueError("Task references do not match its driver targets.")
        if "regional_observations" in plan.metrics:
            if (plan.targets != ("self",) or plan.metrics != ("regional_observations",)
                    or not isinstance(region_window, RegionWindow) or type(laps) is not int
                    or type(lap) is not int or not 1 <= lap <= lap + laps - 1 <= 255):
                raise ValueError("Regional tracking requires your own bounded upcoming laps and an explicit numeric window.")
        elif "opponent_regional_observations" in plan.metrics:
            identities = tuple(_fixed_identity(binding) for binding in bindings)
            if (plan.metrics != ("opponent_regional_observations",) or len(plan.targets) != 2
                    or plan.targets[0] != "self" or not isinstance(plan.targets[1], str)
                    or not plan.targets[1].startswith("name:") or len(plan.targets[1]) <= 5
                    or not isinstance(region_window, RegionWindow) or type(laps) is not int
                    or not 2 <= lap <= lap + laps - 1 <= 255
                    or type(opponent_first_lap) is not int or not 2 <= opponent_first_lap <= opponent_first_lap + laps - 1 <= 255
                    or any(identity is None for identity in identities)
                    or identities[0]["car_index"] == identities[1]["car_index"]):
                raise ValueError("Paired regional tracking requires two fixed drivers, independent upcoming laps and one numeric window.")
        elif region_window is not None:
            raise ValueError("Only regional tracking tasks can own a numeric window.")
        if opponent_first_lap is not None and "opponent_regional_observations" not in plan.metrics:
            raise ValueError("Only a paired regional task can own an opponent lap window.")
        if region_reference is not None and (not isinstance(region_reference, RegionReference)
                or region_window != region_reference.window or "regional_observations" not in plan.metrics
                or region_reference.lap_number >= lap):
            raise ValueError("A regional comparison needs an earlier fixed reference for the same numeric window.")
        if "consistency" in plan.metrics and (plan.targets != ("self",) or plan.metrics != ("consistency",)):
            raise ValueError("Consistency coaching requires only your own laps, without additional facts.")
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
                        and task.region_window == region_window
                        and task.region_reference == region_reference
                        and task.opponent_first_lap == opponent_first_lap
                        and (opponent_first_lap is None or task.first_lap == lap)
                        and task.pace_reference == pace_reference and task.pace_tolerance_ms == pace_tolerance_ms
                        and task.until_lap == (lap + laps - 1 if laps is not None else None)):
                    return task
        if len(active) >= 3 or sum(len(task.plan.targets) * len(task.plan.metrics) for task in active) + len(plan.targets) * len(plan.metrics) > 9:
            raise ValueError("Keep at most three active reports and nine total driver facts per lap.")
        region_owners = sum(2 if task.opponent_first_lap is not None else 1
                            for task in active if task.region_window is not None)
        requested_owners = (2 if opponent_first_lap is not None else 1) if region_window is not None else 0
        if region_owners + requested_owners > 3:
            raise ValueError("Keep at most three regional collection leases; one opponent comparison needs two.")
        self._tasks = {key: task for key, task in self._tasks.items() if task.state == "active" or key == task_id}
        identifier = task_id if task_id is not None else self._next_id
        if task_id is None:
            self._next_id += 1
        task = LapReportTask(identifier, existing.version + 1 if existing else 1, plan, bindings, cursor, lap,
                             lap + laps - 1 if laps is not None else None,
                             pace_reference=pace_reference, pace_tolerance_ms=pace_tolerance_ms,
                             region_window=region_window, region_reference=region_reference,
                             opponent_first_lap=opponent_first_lap,
                             reported_through_lap=lap - 1 if opponent_first_lap is not None else None)
        self._tasks[identifier] = task
        self.notify({"event": "radio_task_updated" if existing else "radio_task_created", **task.view()})
        return task

    def extend(self, task_id: int, version: int, *, additional_laps: int, cursor: int, lap: int) -> LapReportTask:
        if (type(task_id) is not int or type(version) is not int or task_id < 1 or version < 1
                or type(cursor) is not int or cursor < 0 or type(lap) is not int or not 1 <= lap <= 255):
            raise ValueError("An extension requires a current task version and valid lap and event cursor.")
        task = self._tasks.get(task_id)
        if task is not None and task.opponent_first_lap is not None:
            raise ValueError("Create or update a bounded opponent request instead of extending its independent lap windows.")
        if (task is None or task.state != "active" or task.version != version
                or task.until_lap is not None and task.until_lap < lap):
            raise ValueError("That task is no longer active or its version changed. Specify a current task; cancelled or expired tasks are not revived.")
        if type(additional_laps) is not int or not 1 <= additional_laps <= 20:
            raise ValueError("Please choose one to twenty additional laps.")
        first = max(task.first_lap, lap + (1 if task.region_window is not None else 0))
        until = task.until_lap + additional_laps if task.until_lap is not None else first + additional_laps - 1
        if not 1 <= first <= until <= 255 or until - first + 1 > 20:
            raise ValueError("The extended task must cover at most twenty upcoming laps, within lap 255.")
        if task.region_window is None:
            extended = replace(task, version=task.version + 1, until_lap=until)
            self._tasks[task.task_id] = extended
            self.notify({"event": "radio_task_updated", **extended.view()})
            return extended
        return self.install(task.plan, task.fixed_bindings, cursor=cursor, lap=first, laps=until - first + 1,
                            task_id=task.task_id, pace_reference=task.pace_reference,
                            pace_tolerance_ms=task.pace_tolerance_ms, region_window=task.region_window,
                            region_reference=task.region_reference)

    def eligible(self, *, event_id: int, lap: int) -> tuple[LapReportTask, ...]:
        return tuple(task for task in self._tasks.values() if task.state == "active"
                     and task.opponent_first_lap is None
                     and event_id > task.created_cursor and lap >= task.first_lap
                     and (task.until_lap is None or lap <= task.until_lap))

    def complete_lap(self, lap: int) -> None:
        for task_id, task in tuple(self._tasks.items()):
            if task.state == "active" and task.opponent_first_lap is None and task.until_lap is not None and lap >= task.until_lap:
                completed = replace(task, state="completed")
                self._tasks[task_id] = completed
                self.notify({"event": "radio_task_ended", "reason": "lap_limit_reached", **completed.view()})

    def consume_region_pair(self, task_id: int, version: int, lap: int) -> bool:
        if any(type(value) is not int or value < 1 for value in (task_id, version, lap)):
            return False
        task = self._tasks.get(task_id)
        if (task is None or task.state != "active" or task.version != version
                or task.opponent_first_lap is None or lap != task.reported_through_lap + 1
                or not task.first_lap <= lap <= task.until_lap):
            return False
        updated = replace(task, reported_through_lap=lap, state="completed" if lap == task.until_lap else "active")
        self._tasks[task_id] = updated
        if updated.state == "completed":
            self.notify({"event": "radio_task_ended", "reason": "paired_lap_limit_reached", **updated.view()})
        return True

    def end_region_pair(self, task_id: int, version: int, reason: str) -> bool:
        if any(type(value) is not int or value < 1 for value in (task_id, version)):
            return False
        task = self._tasks.get(task_id)
        if (task is None or task.state != "active" or task.version != version
                or task.opponent_first_lap is None or not isinstance(reason, str)
                or re.fullmatch(r"[a-z0-9_]{1,96}", reason) is None):
            return False
        ended = replace(task, state="completed")
        self._tasks[task_id] = ended
        self.notify({"event": "radio_task_ended", "reason": reason, **ended.view()})
        return True
