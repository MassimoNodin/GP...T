from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .store import EngineerActivityStore

LOGGER = logging.getLogger(__name__)

POLL_INTERVAL_S = 2.0
ATTEMPT_BATCH_SIZE = 25
TELEMETRY_CALL_MIN_INTERVAL_S = 10.0
# Topics that describe the health of the link rather than driving content; the
# assistance-topic filter never suppresses them.
ALWAYS_ALLOWED_TOPICS = frozenset({"telemetry"})
CURSOR_KEY = "attempt_cursor"

TopicProvider = Callable[[], "set[str] | frozenset[str] | None"]


@dataclass(frozen=True, slots=True)
class PlayerAttempt:
    cursor: int
    revision_id: str
    session_id: str
    payload: dict[str, Any]


class AttemptSource(Protocol):
    def high_water(self) -> int: ...

    def player_attempts(self, after: int, limit: int) -> tuple[list[PlayerAttempt], int]:
        """Published player attempts with cursor > after, plus the cursor to resume from."""


class EvidenceAttemptSource:
    """Read-only view of newly published PLAYER attempts in the evidence store."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def high_water(self) -> int:
        with self.store.connect() as database:
            row = database.execute("SELECT COALESCE(MAX(rowid),0) FROM attempts").fetchone()
            return int(row[0])

    def player_attempts(self, after: int, limit: int) -> tuple[list[PlayerAttempt], int]:
        with self.store.connect() as database:
            database.execute("BEGIN")
            high = int(database.execute("SELECT COALESCE(MAX(rowid),0) FROM attempts").fetchone()[0])
            if high <= after:
                return [], after
            rows = database.execute(
                """SELECT a.rowid AS cursor, a.id, a.session, a.payload,
                          (SELECT d.state FROM dispositions d WHERE d.attempt=a.id
                           ORDER BY d.sequence DESC LIMIT 1) AS state
                   FROM attempts a WHERE a.role='player' AND a.rowid>? AND a.rowid<=?
                   ORDER BY a.rowid LIMIT ?""",
                (after, high, limit),
            ).fetchall()
        attempts: list[PlayerAttempt] = []
        for row in rows:
            if row["state"] != "published":
                continue
            try:
                payload = json.loads(row["payload"])
            except ValueError:
                continue
            if isinstance(payload, dict):
                attempts.append(PlayerAttempt(row["cursor"], row["id"], row["session"], payload))
        resume = rows[-1]["cursor"] if len(rows) == limit else high
        return attempts, resume


def format_lap_time(milliseconds: int) -> str:
    minutes, remainder = divmod(int(milliseconds), 60_000)
    return f"{minutes}:{remainder / 1000:06.3f}"


def format_delta(milliseconds: int) -> str:
    sign = "-" if milliseconds < 0 else "+"
    return f"{sign}{abs(int(milliseconds)) / 1000:.3f}"


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class EngineerCallObserver:
    """Deterministic, template-based engineer calls from existing runtime state.

    Never calls a model. `poll_once` is the unit of work; `start` runs it on a daemon
    thread. `topics` returns the enabled assistance topics (None means all).
    """

    def __init__(
        self,
        store: EngineerActivityStore,
        runtime: Any,
        attempts: AttemptSource,
        *,
        topics: TopicProvider | None = None,
        interval_s: float = POLL_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.attempts = attempts
        self.topics = topics
        self.interval_s = interval_s
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cursor: int | None = None
        self._previous_state: str | None = None
        self._last_telemetry_call: dict[str, float] = {}

    def set_topics(self, topics: TopicProvider | None) -> None:
        self.topics = topics

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="engineer-calls", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout_s)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                LOGGER.warning("engineer call poll failed", exc_info=True)
            self._stop.wait(self.interval_s)

    def poll_once(self) -> None:
        try:
            self._observe_telemetry()
        except Exception:
            LOGGER.warning("engineer telemetry observation failed", exc_info=True)
        try:
            self._observe_attempts()
        except Exception:
            LOGGER.warning("engineer attempt observation failed", exc_info=True)

    def _allowed(self, topic: str) -> bool:
        if topic in ALWAYS_ALLOWED_TOPICS or self.topics is None:
            return True
        try:
            enabled = self.topics()
        except Exception:
            return True
        return enabled is None or topic in enabled

    # Telemetry gap and recovery -------------------------------------------------

    def _observe_telemetry(self) -> None:
        status = self.runtime.status()
        state = status.get("state") if isinstance(status, dict) else None
        if not isinstance(state, str):
            return
        previous, self._previous_state = self._previous_state, state
        if previous is None or previous == state:
            return
        if state == "stale" and previous == "receiving":
            self._telemetry_call(
                "gap", "issue", "high", "Telemetry has gone quiet. No packets are arriving from the game.",
                state, previous)
        elif state == "receiving" and previous == "stale":
            self._telemetry_call(
                "recovery", "proactive", "normal", "Telemetry is back. Data is flowing again.",
                state, previous)
        elif state == "receiving" and previous == "listening":
            self._telemetry_call(
                "connected", "proactive", "low", "Telemetry connected. Receiving data from the game.",
                state, previous)
        elif state == "failed":
            error = status.get("error") if isinstance(status.get("error"), str) else None
            self._telemetry_call(
                "failed", "issue", "high", "The live telemetry listener has stopped.",
                state, previous, error=error[:300] if error else None)

    def _telemetry_call(self, rule: str, kind: str, priority: str, text: str, state: str,
                        previous: str, **extra: Any) -> None:
        now = self._clock()
        last = self._last_telemetry_call.get(rule)
        if last is not None and now - last < TELEMETRY_CALL_MIN_INTERVAL_S:
            return
        self._last_telemetry_call[rule] = now
        self.store.append(
            kind=kind, topic="telemetry", text=text, source="live_rules", priority=priority,
            detail={"rule": rule, "state": state, "previous_state": previous, **extra},
        )

    # Player lap results ---------------------------------------------------------

    def _initial_cursor(self) -> int:
        stored = self.store.get_meta(CURSOR_KEY)
        if stored is not None and stored.isdigit():
            return int(stored)
        # First run against this activity file: do not announce historical laps.
        cursor = self.attempts.high_water()
        self.store.set_meta(CURSOR_KEY, str(cursor))
        return cursor

    def _observe_attempts(self) -> None:
        if self._cursor is None:
            self._cursor = self._initial_cursor()
        batch, resume = self.attempts.player_attempts(self._cursor, ATTEMPT_BATCH_SIZE)
        for attempt in batch:
            self._announce(attempt)
            self._cursor = attempt.cursor
            self.store.set_meta(CURSOR_KEY, str(self._cursor))
        if resume > self._cursor:
            self._cursor = resume
            self.store.set_meta(CURSOR_KEY, str(resume))

    def _announce(self, attempt: PlayerAttempt) -> None:
        payload = attempt.payload
        lap = payload.get("lap_number")
        lap_ms = payload.get("lap_time_ms")
        if payload.get("disposition") != "completed" or not _is_int(lap) or not _is_int(lap_ms) or lap_ms <= 0:
            return
        valid = payload.get("game_valid")
        pit = payload.get("pit_encountered") is True
        clock = format_lap_time(lap_ms)
        base = {"attempt_revision": attempt.revision_id, "lap_time_ms": lap_ms}

        def call(group: str, rule: str, topic: str, priority: str, text: str, **detail: Any) -> None:
            if not self._allowed(topic):
                return
            self.store.append(
                kind="proactive", topic=topic, text=text, source="live_rules",
                session_id=attempt.session_id, lap=lap, priority=priority,
                detail={"rule": rule, **base, **detail},
                dedupe_key=f"attempt:{attempt.revision_id}:{group}",
            )

        if valid is False:
            call("invalid", "lap_invalidated", "track_limits", "high",
                 f"Lap {lap}, {clock}, invalidated. That one will not count.")
        if pit:
            call("pit", "pit_lap", "pit", "normal", f"Lap {lap}, {clock}, included a pit stop.")
        if valid is not True or pit:
            return

        best_key = f"best_ms:{attempt.session_id}"
        stored = self.store.get_meta(best_key)
        best = int(stored) if stored is not None and stored.isdigit() else None
        if best is None:
            call("result", "first_timed_lap", "lap_time", "normal",
                 f"Lap {lap}, {clock}. First timed lap, that sets the benchmark.", best_ms=lap_ms)
        elif lap_ms < best:
            call("result", "personal_best", "lap_time", "high",
                 f"Lap {lap}, {clock}. New personal best, {(best - lap_ms) / 1000:.3f} faster than before.",
                 previous_best_ms=best, delta_ms=lap_ms - best)
        else:
            call("result", "lap_completed", "lap_time", "normal",
                 f"Lap {lap}, {clock}, {format_delta(lap_ms - best)} to your best.",
                 best_ms=best, delta_ms=lap_ms - best)
        if best is None or lap_ms < best:
            self.store.set_meta(best_key, str(lap_ms))
