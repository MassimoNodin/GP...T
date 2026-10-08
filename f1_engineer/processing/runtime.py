from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..udp.source import UDPSource
from .coordinator import SessionCoordinator
from .evidence import EvidenceStore


class LiveSessionRuntime:
    """Exclusive listener with bounded admission and a single persistence owner."""

    def __init__(self, store: EvidenceStore, *, host: str, port: int,
                 queue_size: int = 1024, stale_after_s: float = 3.0) -> None:
        if not 1 <= queue_size <= 4096 or stale_after_s <= 0:
            raise ValueError("invalid live acquisition budget")
        self.store = store
        self.host = host
        self.port = port
        self.queue_size = queue_size
        self.stale_after_s = stale_after_s
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = "stopped"
        self.error: str | None = None
        self.source: UDPSource | None = None
        self.processed = 0
        self.max_queue_depth = 0
        self.last_publication_s = 0.0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("runtime already started")
        self._stop.clear()
        self._ready.clear()
        self.error = None
        self.processed = 0
        self.max_queue_depth = 0
        self.state = "starting"
        self._thread = threading.Thread(target=self._run, name="session-udp", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=15)
            if self._thread.is_alive():
                raise RuntimeError("live persistence owner did not stop")

    def status(self) -> dict[str, Any]:
        source = self.source
        return {"state": self.state, "error": self.error, "host": self.host, "port": self.port,
                "processed": self.processed, "queue_limit": self.queue_size,
                "max_queue_depth": self.max_queue_depth, "last_publication_s": self.last_publication_s,
                "received": source.stats.received if source else 0,
                "dropped": source.stats.dropped if source else 0,
                "socket_errors": source.stats.socket_errors if source else 0}

    def _run(self) -> None:
        try:
            asyncio.run(self._listen())
        except Exception as exception:
            self.state = "failed"
            self.error = f"{type(exception).__name__}: {exception}"
        finally:
            self._ready.set()

    async def _listen(self) -> None:
        source = UDPSource(self.host, self.port, self.queue_size)
        self.source = source
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="session-persistence") as executor:
            coordinator = await loop.run_in_executor(executor, lambda: SessionCoordinator(self.store, f"udp:{self.host}:{self.port}"))
            async def persist(function, *arguments):
                return await loop.run_in_executor(executor, function, *arguments)
            try:
                await source.open()
                await persist(coordinator.gap, "listener_started_or_reconnected")
                self.state = "listening"
                self._ready.set()
                previous_sequence = -1
                previous_socket_errors = 0
                last_received = time.monotonic()
                stale = False
                while not self._stop.is_set():
                    if source.stats.socket_errors != previous_socket_errors:
                        await persist(coordinator.gap, "socket_error")
                        previous_socket_errors = source.stats.socket_errors
                    try:
                        raw = await asyncio.wait_for(source.receive(), timeout=min(0.25, self.stale_after_s))
                    except asyncio.TimeoutError:
                        if not stale and time.monotonic() - last_received >= self.stale_after_s:
                            await persist(coordinator.gap, "telemetry_silence")
                            stale = True
                            self.state = "stale"
                        continue
                    if raw.sequence != previous_sequence + 1:
                        await persist(coordinator.gap, "queue_pressure_or_socket_gap")
                    previous_sequence = raw.sequence
                    last_received = time.monotonic()
                    stale = False
                    self.state = "receiving"
                    self.max_queue_depth = max(self.max_queue_depth, source.pending_count)
                    started = time.perf_counter()
                    await persist(coordinator.ingest, raw)
                    self.last_publication_s = time.perf_counter() - started
                    self.processed += 1
                source.close()
                pending = source.drain_pending()
                if pending:
                    await persist(coordinator.gap, "shutdown_discarded_unadmitted_queue")
                await persist(coordinator.gap, "listener_stopped")
                self.state = "stopped"
            finally:
                source.close()
                await persist(coordinator.close)
