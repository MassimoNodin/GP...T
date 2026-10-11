from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict, deque
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Callable


MAX_ACTIVE_DETAIL_TASKS = 4
MAX_DETAIL_COMMANDS = 16
DETAIL_TASK_TTL_S = 300
MAX_RECENT_DETAIL_TASKS = 32
_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
_BINDING_ID = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class DetailTaskSnapshot:
    task_id: str
    session_id: str
    binding_id: str
    state: str
    reason: str | None = None
    activation_ordinal: int = 0
    effective_sequence: int | None = None
    expires_at: float | None = None
    last_command_state: str | None = None
    last_command_reason: str | None = None
    activation_frame_ordinal: int | None = None
    coverage_state: str = "waiting_for_complete_lap"


@dataclass(frozen=True, slots=True)
class DetailCommand:
    command: str
    task_id: str
    session_id: str
    binding_id: str


class DetailDemandQueue:
    """Bounded API snapshot/command queue; only the owner applies commands."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._commands: deque[DetailCommand] = deque()
        self._tasks: OrderedDict[str, DetailTaskSnapshot] = OrderedDict()
        self._owner_tasks: dict[str, DetailTaskSnapshot] = {}
        self._next_activation: dict[str, int] = {}
        self._targets: dict[str, dict[str, dict[str, object]]] = {}

    @staticmethod
    def validate(task_id: object, session_id: object, binding_id: object) -> bool:
        return (isinstance(task_id, str) and _TASK_ID.fullmatch(task_id) is not None
                and isinstance(session_id, str) and _SESSION_ID.fullmatch(session_id) is not None
                and isinstance(binding_id, str) and _BINDING_ID.fullmatch(binding_id) is not None)

    @contextmanager
    def owner_transaction(self):
        with self._lock:
            yield

    def publish_targets(self, session_id: str, targets: tuple[dict[str, object], ...]) -> None:
        copied = {str(item["binding_id"]): dict(item) for item in targets[:24]}
        with self._lock:
            self._targets.clear()
            self._targets[session_id] = copied

    def targets(self, session_id: str) -> tuple[dict[str, object], ...] | None:
        with self._lock:
            targets = self._targets.get(session_id)
            return None if targets is None else tuple(dict(item) for item in targets.values())

    def clear_targets(self) -> None:
        with self._lock:
            self._targets.clear()

    def enqueue(self, command: DetailCommand) -> tuple[int, dict[str, object]]:
        with self._lock:
            if command.command == "post":
                known = self._tasks.get(command.task_id)
                if known and known.state in {"queued", "active"}:
                    if (known.session_id, known.binding_id) != (command.session_id, command.binding_id):
                        return 409, {"reason": "detail_task_identity_conflict"}
                    pending = [item for item in self._commands if item.task_id == command.task_id]
                    if pending and pending[-1] == command:
                        return 202, self._command_result(known, "queued")
                if len(self._commands) >= MAX_DETAIL_COMMANDS:
                    return 409, {"reason": "detail_command_budget_busy"}
                self._commands.append(command)
                if not known or known.state not in {"active", "queued"}:
                    self._tasks[command.task_id] = DetailTaskSnapshot(
                        command.task_id, command.session_id, command.binding_id, "queued",
                        last_command_state="queued",
                    )
                else:
                    self._tasks[command.task_id] = replace(
                        known, last_command_state="queued", last_command_reason=None
                    )
                return 202, self._command_result(self._tasks[command.task_id], "queued")
            known = self._tasks.get(command.task_id)
            if known is None:
                return 404, {"reason": "detail_task_not_found"}
            if known.state in {"released", "expired", "rejected"}:
                return 202, self._command_result(known, known.state)
            if len(self._commands) >= MAX_DETAIL_COMMANDS:
                return 409, {"reason": "detail_command_budget_busy"}
            self._commands.append(command)
            self._tasks[command.task_id] = replace(
                known, last_command_state="queued", last_command_reason=None
            )
            return 202, self._command_result(self._tasks[command.task_id], "queued")

    @staticmethod
    def _command_result(task: DetailTaskSnapshot, command_state: str) -> dict[str, object]:
        result = dict(task.__dict__) if hasattr(task, "__dict__") else {
            key: getattr(task, key) for key in task.__dataclass_fields__
        }
        result["command_state"] = command_state
        return result

    def pop(self) -> DetailCommand | None:
        with self._lock:
            return self._commands.popleft() if self._commands else None

    def apply(self, command: DetailCommand, *, effective_sequence: int,
              target_current: bool, is_player: bool = False,
              frame_ordinal: int | None = None, expires_at: float | None = None) -> DetailTaskSnapshot:
        with self._lock:
            prior = self._owner_tasks.get(command.task_id)
            if prior is not None and prior.state == "active" and (
                prior.session_id, prior.binding_id
            ) != (command.session_id, command.binding_id):
                return self._reject(command, prior, "detail_task_identity_conflict", effective_sequence)
            if command.command == "release":
                state = (replace(prior, state="released", reason=None,
                                 effective_sequence=effective_sequence,
                                 last_command_state="applied", last_command_reason=None)
                         if prior is not None else DetailTaskSnapshot(
                             command.task_id, command.session_id, command.binding_id,
                             "released", effective_sequence=effective_sequence,
                             last_command_state="applied"))
                self._save(state)
                return state
            if is_player:
                return self._reject(command, prior, "detail_target_is_player", effective_sequence)
            if not target_current:
                return self._reject(command, prior, "detail_target_not_current", effective_sequence)
            active = sum(item.state == "active" for item in self._owner_tasks.values())
            renewal = prior is not None and prior.state == "active"
            if not renewal and active >= MAX_ACTIVE_DETAIL_TASKS:
                return self._reject(command, prior, "detail_task_budget_exceeded", effective_sequence)
            activation = (prior.activation_ordinal if renewal else self._next_activation.get(command.task_id, 0) + 1)
            self._next_activation[command.task_id] = activation
            state = DetailTaskSnapshot(
                command.task_id, command.session_id, command.binding_id, "active",
                activation_ordinal=activation, effective_sequence=effective_sequence,
                expires_at=self.lease_deadline() if expires_at is None else expires_at,
                last_command_state="applied",
                activation_frame_ordinal=(prior.activation_frame_ordinal if renewal else frame_ordinal),
                coverage_state=(prior.coverage_state if renewal else "waiting_for_complete_lap"),
            )
            self._save(state)
            return state

    def _reject(self, command: DetailCommand, prior: DetailTaskSnapshot | None,
                reason: str, sequence: int) -> DetailTaskSnapshot:
        if prior is not None and prior.state == "active":
            state = replace(prior, last_command_state="rejected", last_command_reason=reason)
        else:
            ordinal = self._next_activation.get(command.task_id, 0) + 1
            self._next_activation[command.task_id] = ordinal
            state = DetailTaskSnapshot(command.task_id, command.session_id, command.binding_id,
                                        "rejected", reason, ordinal, sequence,
                                        last_command_state="rejected", last_command_reason=reason)
        self._save(state)
        return state

    def expire(self, *, effective_sequence: int) -> tuple[DetailTaskSnapshot, ...]:
        now = self._clock()
        expired = []
        with self._lock:
            for key, task in tuple(self._owner_tasks.items()):
                if task.state == "active" and task.expires_at is not None and now >= task.expires_at:
                    state = replace(task, state="expired", reason="detail_task_expired",
                                    effective_sequence=effective_sequence)
                    self._save(state)
                    expired.append(state)
        return tuple(expired)

    def due_tasks(self) -> tuple[DetailTaskSnapshot, ...]:
        now = self._clock()
        with self._lock:
            return tuple(replace(task) for task in self._owner_tasks.values()
                         if task.state == "active" and task.expires_at is not None
                         and now >= task.expires_at)

    def lease_deadline(self) -> float:
        return self._clock() + DETAIL_TASK_TTL_S

    def expire_one(self, task_id: str, *, effective_sequence: int) -> DetailTaskSnapshot | None:
        return self.terminate_one(task_id, reason="detail_task_expired",
                                  effective_sequence=effective_sequence)

    def terminate_one(self, task_id: str, *, reason: str,
                      effective_sequence: int) -> DetailTaskSnapshot | None:
        with self._lock:
            task = self._owner_tasks.get(task_id)
            if task is None or task.state != "active":
                return task
            state = replace(task, state="expired", reason=reason,
                            effective_sequence=effective_sequence)
            self._save(state)
            return state

    def get(self, task_id: str) -> DetailTaskSnapshot | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return replace(task) if task else None

    def counts(self) -> tuple[int, int]:
        with self._lock:
            return (sum(item.state == "active" for item in self._owner_tasks.values()), len(self._commands))

    def active_tasks(self) -> tuple[DetailTaskSnapshot, ...]:
        with self._lock:
            return tuple(replace(item) for item in self._owner_tasks.values() if item.state == "active")

    def mark_available(self, task_id: str, binding_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.state == "active" and task.binding_id == binding_id:
                self._save(replace(task, coverage_state="available"))

    def reject_queued_on_shutdown(self) -> None:
        with self._lock:
            while self._commands:
                command = self._commands.popleft()
                task = self._tasks.get(command.task_id)
                if task is None:
                    continue
                self._save(replace(task, state=("expired" if task.state == "active" else "rejected"),
                                   reason="detail_acquisition_interrupted",
                                   last_command_state="rejected",
                                   last_command_reason="detail_acquisition_interrupted"))
            for task in tuple(self._owner_tasks.values()):
                if task.state == "active":
                    self._save(replace(task, state="expired", reason="detail_acquisition_interrupted"))
            self._targets.clear()

    def _save(self, task: DetailTaskSnapshot) -> None:
        self._owner_tasks[task.task_id] = task
        self._tasks[task.task_id] = (replace(task, last_command_state="queued", last_command_reason=None)
                                    if any(item.task_id == task.task_id for item in self._commands) else task)
        self._tasks.move_to_end(task.task_id)
        terminal = [key for key, value in self._tasks.items()
                    if value.state in {"released", "expired", "rejected"}]
        while len(terminal) > MAX_RECENT_DETAIL_TASKS:
            key = terminal.pop(0)
            self._tasks.pop(key, None)
            self._owner_tasks.pop(key, None)
            self._next_activation.pop(key, None)
