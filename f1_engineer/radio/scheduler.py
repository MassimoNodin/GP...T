from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Callable, Literal, Protocol


class Playback(Protocol):
    def start(self, text: str) -> None: ...
    def busy(self) -> bool: ...
    def cancel(self) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RadioMessage:
    key: str
    scope: str | None
    text: str
    kind: Literal["proactive", "answer"]
    expires_at: float
    queued_at: float | None = None
    observation_age_s: float | None = None
    dispatch_before: float | None = None
    owner: str | None = None
    related_id: int | None = None
    dispatch_guard: Callable[[float], str | None] | None = field(default=None, compare=False, repr=False)
    on_started: Callable[[float], None] | None = field(default=None, compare=False, repr=False)

    @property
    def priority(self) -> int:
        return 2 if self.kind == "answer" else 1 if self.owner == "automatic_gaps" else 0


@dataclass(frozen=True, slots=True)
class RepeatReference:
    message: RadioMessage
    revision: int


class RadioScheduler:
    def __init__(self, playback: Playback, *, queue_limit: int = 16,
                 notify: Callable[[dict], None] | None = None) -> None:
        if not 1 <= queue_limit <= 16:
            raise ValueError("invalid radio queue limit")
        self.playback = playback
        self.queue_limit = queue_limit
        self.notify = notify or (lambda event: None)
        self.scope: str | None = None
        self.related_question_id: int | None = None
        self.muted = False
        self.suspended = False
        self.pending: list[RadioMessage] = []
        self.active: RadioMessage | None = None
        self.last_completed: RadioMessage | None = None
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._started_at = 0.0
        self._repeat_id = 0
        self._repeat_revision = 0
        self._completed_at: OrderedDict[str, float] = OrderedDict()

    def set_scope(self, scope: str | None) -> None:
        if scope != self.scope:
            self._repeat_revision += 1
            self.cancel()
            self.scope = scope
            self.last_completed = None
            self._completed_at.clear()
            self._seen.clear()

    def _drop(self, message: RadioMessage, reason: str) -> bool:
        self.notify({"event": "radio_dropped", "key": message.key, "reason": reason})
        return False

    def submit(self, message: RadioMessage, *, now: float) -> bool:
        if message.kind == "answer" and message.related_id is None and self.related_question_id is not None:
            message = replace(message, related_id=self.related_question_id)
        if (message.kind not in ("answer", "proactive") or not message.text.strip()
                or len(message.text.encode("utf-8")) > 1024 or not message.key
                or len(message.key) > 128 or not math.isfinite(message.expires_at)):
            raise ValueError("invalid radio message")
        if message.observation_age_s is not None and (
                not math.isfinite(message.observation_age_s) or message.observation_age_s < 0):
            raise ValueError("invalid radio observation age")
        if message.dispatch_before is not None and not math.isfinite(message.dispatch_before):
            raise ValueError("invalid radio dispatch deadline")
        if message.scope != self.scope:
            return self._drop(message, "scope_changed")
        if message.expires_at <= now:
            return self._drop(message, "expired")
        if message.dispatch_before is not None and message.dispatch_before <= now:
            return self._drop(message, "observations_stale")
        if self.muted and message.kind == "proactive":
            return self._drop(message, "quiet")
        if self.suspended and message.kind == "proactive":
            return self._drop(message, "listening")
        if message.key in self._seen:
            return self._drop(message, "duplicate")
        if len(self.pending) >= self.queue_limit:
            lowest = min(item.priority for item in self.pending)
            if message.priority < lowest:
                return self._drop(message, "queue_full")
            victim = next(item for item in self.pending if item.priority == lowest)
            self.pending.remove(victim)
            self._drop(victim, "queue_full")
        self._seen[message.key] = None
        while len(self._seen) > 128:
            self._seen.popitem(last=False)
        if self.active is not None and message.kind == "answer" and message.priority > self.active.priority:
            self.playback.cancel()
            self._drop(self.active, "interrupted_by_answer")
            self.active = None
        self.pending.append(replace(message, queued_at=now))
        return True

    def tick(self, *, now: float) -> None:
        for message in tuple(self.pending):
            if message.expires_at <= now or (message.dispatch_before is not None and message.dispatch_before <= now):
                self.pending.remove(message)
                self._drop(message, "expired")
        if self.suspended:
            return
        if self.active is not None:
            if self.active.expires_at <= now:
                self.playback.cancel()
                self._drop(self.active, "expired")
                self.active = None
            elif self.playback.busy():
                return
            else:
                self.last_completed = self.active
                self._completed_at[self.active.key] = now
                while len(self._completed_at) > 128:
                    self._completed_at.popitem(last=False)
                self.notify({"event": "radio_completed", "key": self.active.key,
                             "completion_observed_s": now - self._started_at})
                self.active = None
        if self.pending:
            message = max(self.pending, key=lambda item: item.priority)
            self.pending.remove(message)
            if message.dispatch_guard is not None:
                reason = message.dispatch_guard(now)
                if reason is not None:
                    self._drop(message, reason)
                    return
            self.playback.start(message.text)
            self.active = message
            self._started_at = now
            if message.on_started is not None:
                message.on_started(now)
            self.notify({"event": "radio_started", "key": message.key, "kind": message.kind,
                         "scope": message.scope, "owner": message.owner, "related_id": message.related_id,
                         "text": message.text, "queue_wait_s": now - message.queued_at,
                         "observation_to_dispatch_s": (message.observation_age_s + now - message.queued_at
                                                       if message.observation_age_s is not None else None)})

    def cancel(self) -> None:
        if self.active is not None:
            self.playback.cancel()
            self.active = None
        self.pending.clear()

    def cancel_pending(self, key, *, reason="development_superseded") -> None:
        for message in tuple(self.pending):
            if message.key == key:
                self.pending.remove(message)
                self._drop(message, reason)

    def cancel_owner(self, owner: str) -> None:
        if (self.active is not None and self.active.owner == owner
                or self.last_completed is not None and self.last_completed.owner == owner):
            self._repeat_revision += 1
        if self.active is not None and self.active.owner == owner:
            self.playback.cancel()
            self.active = None
        self.pending = [message for message in self.pending if message.owner != owner]
        if self.last_completed is not None and self.last_completed.owner == owner:
            self.last_completed = None

    def quiet(self) -> None:
        self.cancel()
        self.muted = True

    def repeat(self, *, now: float) -> bool:
        return self.repeat_captured(self.capture_repeat_reference(), now=now)

    def capture_repeat_reference(self) -> RepeatReference | None:
        source = self.active or self.last_completed
        if source is None or source.scope != self.scope:
            return None
        return RepeatReference(source, self._repeat_revision)

    def repeat_captured(self, reference: RepeatReference | None, *, now: float) -> bool:
        if not isinstance(reference, RepeatReference) or reference.revision != self._repeat_revision:
            return False
        source = reference.message
        completed = self._completed_at.get(source.key)
        deadline = completed + 15 if completed is not None else source.expires_at
        if source.scope != self.scope or deadline <= now:
            return False
        self.cancel()
        self._repeat_id += 1
        return self.submit(replace(source, key=f"repeat:{self._repeat_id}", kind="answer",
                                   expires_at=now + 15, observation_age_s=None, dispatch_before=None,
                                   dispatch_guard=None, on_started=None), now=now)

    def close(self) -> None:
        try:
            self.cancel()
        finally:
            self.playback.close()
