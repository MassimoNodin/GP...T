from __future__ import annotations

import asyncio
import threading


class AIGateLease:
    """One process-local AI admission slot, retained for detached work."""

    def __init__(self, gate: "EngineerAIGate") -> None:
        self._gate = gate
        self._state_lock = threading.Lock()
        self._retained = 0
        self._closed = False
        self._released = False

    def retain_until(self, task: asyncio.Future[object]) -> None:
        with self._state_lock:
            if self._released:
                raise RuntimeError("cannot retain a released AI gate")
            self._retained += 1
        task.add_done_callback(self._retained_work_finished)

    def close(self) -> None:
        release = False
        with self._state_lock:
            self._closed = True
            if self._retained == 0 and not self._released:
                self._released = True
                release = True
        if release:
            self._gate._release()

    def _retained_work_finished(self, task: asyncio.Future[object]) -> None:
        try:
            task.exception()
        except BaseException:
            pass
        release = False
        with self._state_lock:
            self._retained = max(0, self._retained - 1)
            if self._closed and self._retained == 0 and not self._released:
                self._released = True
                release = True
        if release:
            self._gate._release()


class EngineerAIGate:
    """A non-waiting gate shared by Engineer inference and local transcription."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def try_acquire(self) -> AIGateLease | None:
        if not self._lock.acquire(blocking=False):
            return None
        return AIGateLease(self)

    def _release(self) -> None:
        self._lock.release()


PROCESS_ENGINEER_AI_GATE = EngineerAIGate()
