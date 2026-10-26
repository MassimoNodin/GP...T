from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass

from .questions import QuestionPlan


@dataclass(frozen=True, slots=True)
class TaskContext:
    scope: str | None
    selected: tuple[int, int] | None
    versions: tuple[tuple[int, int], ...]
    revision: int = 0
    clarification_id: int | None = None


@dataclass(frozen=True, slots=True)
class QuestionContext:
    scope: str | None
    revision: int
    plan: QuestionPlan
    bindings: tuple[str | None, ...]


class ConversationMemory:
    def __init__(self):
        self.clear()

    def clear(self):
        self.revision = getattr(self, "revision", -1) + 1
        self.turns = deque(maxlen=16)
        self.task_reference = None

    def record(self, kind, *, request=None, response, tasks=(), message_key=None, queued=None, select=True,
               plan=None, bindings=None):
        if request is not None and (not isinstance(request, str) or len(request.encode("utf-8")) > 1024):
            request = "Request omitted: invalid or exceeds the retained 1024-byte limit."
        if not isinstance(response, str) or len(response.encode("utf-8")) > 1024:
            response = "Response omitted: invalid or exceeds the retained 1024-byte limit."
        views = [task.view() if hasattr(task, "view") else deepcopy(task) for task in tasks]
        selected = (views[0]["task_id"], views[0]["version"]) if len(views) == 1 else None
        turn = {"kind": kind, "request": request, "response": response, "tasks": views,
                "message_key": message_key, "queued": queued, "task_reference": selected}
        if plan is not None:
            turn["plan"] = {"targets": list(plan.targets), "metrics": list(plan.metrics)}
            turn["bindings"] = deepcopy(bindings)
        self.turns.append(turn)
        if select:
            self.task_reference = selected

    def context(self):
        return deepcopy(list(self.turns))

    def capture_tasks(self, scope, tasks, message=None):
        selected = self.task_reference
        if message is not None:
            turn = next((turn for turn in reversed(self.turns) if turn["message_key"] == message.key), None)
            selected = turn["task_reference"] if turn else None
        elif self.turns and self.turns[-1]["kind"] != "instruction":
            selected = None
        return TaskContext(scope, selected, tuple((task.task_id, task.version) for task in tasks), self.revision)

    def previous_request(self):
        return deepcopy(next((turn for turn in reversed(self.turns) if turn["kind"] in ("question", "instruction")
                              and turn["request"] is not None), None))

    def previous_tracking_result(self):
        return deepcopy(next((turn for turn in reversed(self.turns) if turn["kind"] == "task_result"), None))
