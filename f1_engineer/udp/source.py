from __future__ import annotations

import asyncio
import math
import socket
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

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

        udp_socket = socket.socket(socket.AF_INET6 if ":" in self.host else socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            udp_socket.bind((self.host, self.port))
            udp_socket.setblocking(False)
            transport, _ = await loop.create_datagram_endpoint(Receiver, sock=udp_socket)
        except BaseException:
            udp_socket.close()
            raise
        self._transport = transport

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    async def receive(self) -> RawDatagram:
        return await self._queue.get()

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

    def __init__(
        self,
        path: str | Path,
        speed: float = 1.0,
        *,
        start_paused: bool = False,
        on_control_state: Callable[[str], None] | None = None,
    ) -> None:
        if not math.isfinite(speed) or speed < 0:
            raise ValueError("speed must be zero (maximum speed) or a positive value")
        self.path = Path(path)
        self.speed = speed
        self.metadata: dict[str, object] | None = None
        self.completion: dict[str, object] | None = None
        self.complete = False
        self._condition: asyncio.Condition | None = None
        self._controlled = start_paused or on_control_state is not None
        self._clock = _ReplayClock(start_paused)
        self._pause_acknowledged = False
        self._step_credits = 0
        self._step_in_progress = False
        self._control_state = "starting" if start_paused else "playing"
        self._on_control_state = on_control_state

    async def pause(self) -> None:
        condition = self._condition
        if condition is None:
            raise ValueError("replay_not_ready")
        async with condition:
            if self._clock.paused:
                return
            self._clock.pause(time.perf_counter_ns())
            self._pause_acknowledged = False
            self._set_control_state("pausing")
            condition.notify_all()

    async def resume(self) -> None:
        condition = self._condition
        if condition is None:
            raise ValueError("replay_not_ready")
        async with condition:
            if not self._clock.paused:
                raise ValueError("replay_not_paused")
            if self._step_in_progress or self._step_credits:
                raise ValueError("replay_step_in_progress")
            self._clock.resume(time.perf_counter_ns())
            self._pause_acknowledged = False
            self._set_control_state("resuming")
            self._set_control_state("playing")
            condition.notify_all()

    async def step(self) -> None:
        condition = self._condition
        if condition is None:
            raise ValueError("replay_not_ready")
        async with condition:
            if not self._clock.paused or not self._pause_acknowledged:
                raise ValueError("replay_not_paused")
            if self._step_in_progress or self._step_credits:
                raise ValueError("replay_step_in_progress")
            self._step_credits = 1
            self._step_in_progress = True
            self._set_control_state("stepping")
            condition.notify_all()

    def _set_control_state(self, state: str) -> None:
        if state == self._control_state:
            return
        self._control_state = state
        if self._on_control_state is not None:
            self._on_control_state(state)

    async def _wait_for_delivery(
        self,
        target_elapsed_ns: int,
    ) -> None:
        condition = self._condition
        if condition is None:
            raise RuntimeError("ReplaySource iterator has not started")
        async with condition:
            while True:
                if self._clock.paused:
                    if self._step_credits:
                        self._step_credits -= 1
                        self._pause_acknowledged = False
                        self._clock.advance_to(target_elapsed_ns)
                        return
                    self._step_in_progress = False
                    self._pause_acknowledged = True
                    self._set_control_state("paused")
                    condition.notify_all()
                    await condition.wait()
                    continue

                elapsed_ns = self._clock.elapsed(time.perf_counter_ns())
                remaining_ns = target_elapsed_ns - elapsed_ns
                if remaining_ns <= 0:
                    return
                try:
                    await asyncio.wait_for(
                        condition.wait(), timeout=remaining_ns / 1_000_000_000
                    )
                except TimeoutError:
                    pass

    async def packets(self) -> AsyncIterator[RawDatagram]:
        with CaptureReader(self.path) as capture:
            self.metadata = capture.metadata
            if self._controlled:
                self._condition = asyncio.Condition()
            first_monotonic_ns: int | None = None
            replay_started_ns: int | None = None
            for raw in capture:
                if first_monotonic_ns is None:
                    first_monotonic_ns = raw.monotonic_ns
                    replay_started_ns = time.perf_counter_ns()
                    self._clock.start(replay_started_ns)
                assert replay_started_ns is not None
                target_elapsed_ns = max(0, raw.monotonic_ns - first_monotonic_ns)
                if self.speed > 0:
                    target_elapsed_ns = int(target_elapsed_ns / self.speed)
                else:
                    target_elapsed_ns = 0
                if self._controlled:
                    await self._wait_for_delivery(target_elapsed_ns)
                elif self.speed > 0:
                    actual_elapsed_ns = (
                        time.perf_counter_ns() - replay_started_ns
                    )
                    remaining_ns = target_elapsed_ns - actual_elapsed_ns
                    if remaining_ns > 0:
                        await asyncio.sleep(remaining_ns / 1_000_000_000)
                yield raw
            self.completion = capture.completion
            self.complete = capture.complete


class _ReplayClock:
    """Track capture time while excluding pauses and incorporating stepped packets."""

    def __init__(self, start_paused: bool) -> None:
        self._started = False
        self._paused = start_paused
        self._position_ns = 0
        self._running_since_ns: int | None = None

    @property
    def paused(self) -> bool:
        return self._paused

    def start(self, now_ns: int) -> None:
        if self._started:
            return
        self._started = True
        if not self._paused:
            self._running_since_ns = now_ns

    def elapsed(self, now_ns: int) -> int:
        if not self._started or self._paused:
            return self._position_ns
        assert self._running_since_ns is not None
        return self._position_ns + max(0, now_ns - self._running_since_ns)

    def pause(self, now_ns: int) -> None:
        if self._paused:
            return
        self._position_ns = self.elapsed(now_ns)
        self._running_since_ns = None
        self._paused = True

    def resume(self, now_ns: int) -> None:
        if not self._paused:
            raise ValueError("replay_not_paused")
        self._running_since_ns = now_ns
        self._paused = False

    def advance_to(self, position_ns: int) -> None:
        if self._paused:
            self._position_ns = max(self._position_ns, position_ns)
