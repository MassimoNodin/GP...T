from __future__ import annotations

import asyncio
import time

from .ai_admission import PROCESS_ENGINEER_AI_GATE
from .engineer_runtime import EngineerRuntimeUnavailable
from ..radio.proactive_selection import SELECTION_INTERVAL_S, SELECTION_TIMEOUT_S, parse_selection


class ProactiveAdvisor:
    def __init__(self, runtime):
        self.runtime = runtime
        self.task = None
        self.last_started = float("-inf")

    async def preempt(self):
        task = self.task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=2.5)

    async def select(self, context, disconnected):
        select = getattr(self.runtime, "select_proactive", None)
        if select is None:
            raise EngineerRuntimeUnavailable("proactive_runtime_unsupported")
        if (self.task is not None and not self.task.done()
                or time.monotonic() - self.last_started < SELECTION_INTERVAL_S):
            raise EngineerRuntimeUnavailable("proactive_busy")
        lease = PROCESS_ENGINEER_AI_GATE.try_acquire()
        if lease is None:
            raise EngineerRuntimeUnavailable("proactive_busy")
        try:
            task = asyncio.create_task(select(context))
        except BaseException:
            lease.close()
            raise
        self.task, self.last_started = task, time.monotonic()
        lease.retain_until(task)
        lease.close()
        try:
            async with asyncio.timeout(SELECTION_TIMEOUT_S):
                while True:
                    done, _pending = await asyncio.wait({task}, timeout=0.05)
                    if done:
                        if task.cancelled():
                            raise EngineerRuntimeUnavailable("proactive_preempted")
                        selection = task.result()
                        return parse_selection(selection, context)
                    if await disconnected():
                        raise EngineerRuntimeUnavailable("proactive_client_disconnected")
        except TimeoutError as exc:
            raise EngineerRuntimeUnavailable("proactive_timeout") from exc
        finally:
            if not task.done():
                task.cancel()
