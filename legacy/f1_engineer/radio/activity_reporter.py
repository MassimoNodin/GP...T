from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone


def session_from_scope(scope):
    try:
        binding = json.loads(scope)
        session = binding[1] if isinstance(binding, list) and len(binding) >= 2 else None
        return session if isinstance(session, str) and 1 <= len(session) <= 256 else None
    except (ValueError, TypeError):
        return None


class ActivityReporter:
    """Non-blocking playback observer with a bounded, reconnect-friendly backlog."""

    def __init__(self, send, *, notify=None, capacity=64, retry_delay=1.0, max_attempts=12):
        self.send = send
        self.notify = notify or (lambda event: None)
        self.capacity = capacity
        self.retry_delay = retry_delay
        self.max_attempts = max_attempts
        self._pending = deque()
        self._condition = threading.Condition()
        self._closing = False
        self._deadline = None
        self._thread = threading.Thread(target=self._run, name="radio-activity", daemon=True)
        self._thread.start()

    def observe(self, event):
        if event.get("event") != "radio_started":
            return
        scope = event.get("scope")
        item = {"client_event_id": uuid.uuid4().hex, "kind": event["kind"],
                "owner": event.get("owner"), "text": event["text"][:2000], "scope": scope,
                "session_id": session_from_scope(scope), "lap": event.get("lap"),
                "related_id": event.get("related_id"),
                "started_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "repeat": str(event.get("key", "")).startswith("repeat:")}
        dropped = False
        with self._condition:
            if self._closing:
                return
            if len(self._pending) >= self.capacity:
                self._pending.popleft()
                dropped = True
            self._pending.append((item, 0))
            self._condition.notify()
        if dropped:
            self.notify({"event": "radio_activity_dropped", "reason": "queue_full"})

    def _run(self):
        while True:
            with self._condition:
                while not self._pending and not self._closing:
                    self._condition.wait()
                if self._closing and (not self._pending or time.monotonic() >= self._deadline):
                    return
                item, attempts = self._pending[0]
            try:
                success = self.send([item])
            except Exception:
                success = False
            dropped = False
            with self._condition:
                if self._pending and self._pending[0][0] is item:
                    if success or attempts + 1 >= self.max_attempts:
                        self._pending.popleft()
                        dropped = not success
                    else:
                        self._pending[0] = (item, attempts + 1)
                if not success and not self._closing:
                    self._condition.wait_for(lambda: self._closing,
                                             timeout=min(5.0, self.retry_delay * 2**min(attempts, 4)))
            if dropped:
                self.notify({"event": "radio_activity_dropped", "reason": "retry_limit"})

    def close(self):
        with self._condition:
            self._closing = True
            self._deadline = time.monotonic() + 2.0
            self._condition.notify_all()
        self._thread.join(timeout=2.5)
