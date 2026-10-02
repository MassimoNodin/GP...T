from __future__ import annotations

import asyncio
import math
import socket
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..recording.capture import CaptureReader
from .models import RawDatagram


class TelemetrySource(Protocol):
    def packets(self) -> AsyncIterator[RawDatagram]: ...


@dataclass(slots=True)
class UDPSourceStats:
    received: int = 0
    queued: int = 0
    dropped: int = 0
    socket_errors: int = 0


class UDPSource:
    """Bounded asynchronous UDP input. Socket callbacks only enqueue datagrams."""

    def __init__(self, host: str = "0.0.0.0", port: int = 20777, queue_size: int = 8192) -> None:
        if not 0 <= port <= 65535:
            raise ValueError("UDP port must be between 0 and 65535")
        if queue_size < 1:
            raise ValueError("queue_size must be at least 1")
        self.host = host
        self.port = port
        self.stats = UDPSourceStats()
        self._queue: asyncio.Queue[RawDatagram] = asyncio.Queue(maxsize=queue_size)
        self._transport: asyncio.DatagramTransport | None = None
        self._sequence = 0

    async def open(self) -> None:
        loop = asyncio.get_running_loop()
        source = self

        class Receiver(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: tuple[object, ...]) -> None:
                source.stats.received += 1
                sequence = source._sequence
                source._sequence += 1
                host = str(addr[0]) if addr else "unknown"
                port = int(addr[1]) if len(addr) > 1 else 0
                raw = RawDatagram(
                    sequence=sequence,
                    captured_at_ns=time.time_ns(),
                    monotonic_ns=time.perf_counter_ns(),
                    source_host=host,
                    source_port=port,
                    payload=bytes(data),
                )
                try:
                    source._queue.put_nowait(raw)
                    source.stats.queued += 1
                except asyncio.QueueFull:
                    source.stats.dropped += 1

            def error_received(self, exc: OSError) -> None:
                source.stats.socket_errors += 1

        transport, _ = await loop.create_datagram_endpoint(
            Receiver,
            local_addr=(self.host, self.port),
            family=socket.AF_INET6 if ":" in self.host else socket.AF_INET,
        )
        self._transport = transport

    async def packets(self) -> AsyncIterator[RawDatagram]:
        if self._transport is None:
            raise RuntimeError("UDPSource.open() must be called before reading packets")
        while True:
            yield await self._queue.get()

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def drain_pending(self) -> tuple[RawDatagram, ...]:
        pending: list[RawDatagram] = []
        while True:
            try:
                pending.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                return tuple(pending)


class ReplaySource:
    """Replay a capture as raw datagrams, optionally preserving packet timing."""

    def __init__(self, path: str | Path, speed: float = 1.0) -> None:
        if not math.isfinite(speed) or speed < 0:
            raise ValueError("speed must be zero (maximum speed) or a positive value")
        self.path = Path(path)
        self.speed = speed
        self.metadata: dict[str, object] | None = None
        self.completion: dict[str, object] | None = None
        self.complete = False

    async def packets(self) -> AsyncIterator[RawDatagram]:
        with CaptureReader(self.path) as capture:
            self.metadata = capture.metadata
            first_monotonic_ns: int | None = None
            replay_started_ns: int | None = None
            for raw in capture:
                if first_monotonic_ns is None:
                    first_monotonic_ns = raw.monotonic_ns
                    replay_started_ns = time.perf_counter_ns()
                elif self.speed > 0:
                    assert replay_started_ns is not None
                    target_elapsed_ns = max(0, raw.monotonic_ns - first_monotonic_ns)
                    target_elapsed_ns = int(target_elapsed_ns / self.speed)
                    actual_elapsed_ns = time.perf_counter_ns() - replay_started_ns
                    remaining_ns = target_elapsed_ns - actual_elapsed_ns
                    if remaining_ns > 0:
                        await asyncio.sleep(remaining_ns / 1_000_000_000)
                yield raw
            self.completion = capture.completion
            self.complete = capture.complete
