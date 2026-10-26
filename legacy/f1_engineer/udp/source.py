from __future__ import annotations

import asyncio
import math
import os
import queue
import socket
import sys
import threading
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
    """Bounded UDP input with optional reception independent of the consumer loop."""

    def __init__(self, host: str = "0.0.0.0", port: int = 20777, queue_size: int = 8192,
                 *, independent_receiver: bool = False) -> None:
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
        self._kernel_drops: int | None = None
        self._kernel_drops_sampled_at: float | None = None
        self.independent_receiver = independent_receiver
        self._receiver_queue: queue.Queue[RawDatagram] = queue.Queue(maxsize=queue_size)
        self._receiver_socket: socket.socket | None = None
        self._receiver_thread: threading.Thread | None = None
        self._receiver_stop = threading.Event()
        self._wake_pending = threading.Event()
        self._receiver_ready = asyncio.Event()
        self.receive_buffer_bytes: int | None = None

    @property
    def kernel_receive_drops(self) -> int | None:
        """Return drops attributed to this socket, or None when unavailable.

        Linux procfs is sampled at most once every 250 ms. The last observed
        count remains available after close; unsupported or unreadable procfs
        data is reported as None rather than as zero.
        """
        now = time.monotonic()
        if (
            self._kernel_drops_sampled_at is not None
            and now - self._kernel_drops_sampled_at < 0.25
        ):
            return self._kernel_drops
        self._kernel_drops_sampled_at = now
        if self._transport is None and self._receiver_socket is None:
            return self._kernel_drops
        if sys.platform != "linux":
            self._kernel_drops = None
            return None
        try:
            udp_socket = self._receiver_socket or self._transport.get_extra_info("socket")
            if udp_socket is None:
                self._kernel_drops = None
                return None
            target_inode = _socket_inode(udp_socket.fileno())
            if target_inode is None:
                self._kernel_drops = None
                return None
            table = Path("/proc/net/udp6" if udp_socket.family == socket.AF_INET6 else "/proc/net/udp")
            self._kernel_drops = _socket_drop_count(table, target_inode)
        except (OSError, ValueError, IndexError, AttributeError, TypeError):
            self._kernel_drops = None
        return self._kernel_drops

    async def open(self) -> None:
        self._kernel_drops = None
        self._kernel_drops_sampled_at = None
        loop = asyncio.get_running_loop()
        if self.independent_receiver:
            self._open_independent_receiver(loop)
            return
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
            self.receive_buffer_bytes = udp_socket.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            udp_socket.setblocking(False)
            transport, _ = await loop.create_datagram_endpoint(Receiver, sock=udp_socket)
        except BaseException:
            udp_socket.close()
            raise
        self._transport = transport

    def _open_independent_receiver(self, loop: asyncio.AbstractEventLoop) -> None:
        udp_socket = socket.socket(socket.AF_INET6 if ":" in self.host else socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            udp_socket.bind((self.host, self.port))
            self.receive_buffer_bytes = udp_socket.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            udp_socket.settimeout(0.05)
            self._receiver_stop.clear()
            self._wake_pending.clear()
            self._receiver_ready.clear()
            self._receiver_socket = udp_socket
            self._receiver_thread = threading.Thread(
                target=self._receive_independently, args=(udp_socket, loop),
                name="udp-reception", daemon=True,
            )
            self._receiver_thread.start()
        except BaseException:
            self._receiver_socket = None
            udp_socket.close()
            raise

    def _notify_receiver_ready(self) -> None:
        self._receiver_ready.set()
        self._wake_pending.clear()

    def _receive_independently(self, udp_socket: socket.socket,
                             loop: asyncio.AbstractEventLoop) -> None:
        while not self._receiver_stop.is_set():
            try:
                payload, address = udp_socket.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                if self._receiver_stop.is_set():
                    return
                self.stats.socket_errors += 1
                continue
            raw = RawDatagram(
                sequence=self._sequence, captured_at_ns=time.time_ns(),
                monotonic_ns=time.perf_counter_ns(), source_host=str(address[0]),
                source_port=int(address[1]), payload=payload,
            )
            self._sequence += 1
            self.stats.received += 1
            try:
                self._receiver_queue.put_nowait(raw)
                self.stats.queued += 1
            except queue.Full:
                self.stats.dropped += 1
            if not self._wake_pending.is_set():
                self._wake_pending.set()
                try:
                    loop.call_soon_threadsafe(self._notify_receiver_ready)
                except RuntimeError:
                    self._receiver_stop.set()
                    return

    @property
    def pending_count(self) -> int:
        return self._receiver_queue.qsize() if self.independent_receiver else self._queue.qsize()

    async def receive(self) -> RawDatagram:
        if self.independent_receiver:
            while True:
                try:
                    return self._receiver_queue.get_nowait()
                except queue.Empty:
                    self._receiver_ready.clear()
                try:
                    return self._receiver_queue.get_nowait()
                except queue.Empty:
                    await self._receiver_ready.wait()
        return await self._queue.get()

    async def receive_batch(self, maximum: int) -> tuple[RawDatagram, ...]:
        if maximum < 1:
            raise ValueError("receive batch must contain at least one datagram")
        pending = [await self.receive()]
        while len(pending) < maximum:
            try:
                pending.append(self._receiver_queue.get_nowait() if self.independent_receiver else self._queue.get_nowait())
            except (asyncio.QueueEmpty, queue.Empty):
                break
        return tuple(pending)

    async def packets(self) -> AsyncIterator[RawDatagram]:
        if self._transport is None and self._receiver_socket is None:
            raise RuntimeError("UDPSource.open() must be called before reading packets")
        while True:
            yield await self.receive()

    def close(self) -> None:
        if self._receiver_socket is not None:
            _ = self.kernel_receive_drops
            self._receiver_stop.set()
            self._receiver_socket.close()
            if self._receiver_thread is not None:
                self._receiver_thread.join(timeout=1)
                if self._receiver_thread.is_alive():
                    raise RuntimeError("UDP receiver did not stop")
            self._receiver_socket = None
            self._receiver_thread = None
        if self._transport is not None:
            _ = self.kernel_receive_drops
            self._transport.close()
            self._transport = None

    def drain_pending(self) -> tuple[RawDatagram, ...]:
        pending: list[RawDatagram] = []
        while True:
            try:
                pending.append(self._receiver_queue.get_nowait() if self.independent_receiver else self._queue.get_nowait())
            except (asyncio.QueueEmpty, queue.Empty):
                return tuple(pending)


def _socket_inode(fd: int) -> int | None:
    try:
        target = os.readlink(f"/proc/self/fd/{fd}")
        if not target.startswith("socket:[") or not target.endswith("]"):
            return None
        return int(target[8:-1])
    except (OSError, ValueError):
        return None


def _socket_drop_count(table: Path, target_inode: int) -> int | None:
    try:
        with table.open("r", encoding="ascii") as stream:
            header = stream.readline(4097)
            if not header or len(header) > 4096 or not header.endswith("\n"):
                return None
            for _ in range(4096):
                line = stream.readline(4097)
                if not line:
                    return None
                if len(line) > 4096 or not line.endswith("\n"):
                    return None
                fields = line.split()
                if len(fields) <= 9:
                    continue
                try:
                    inode = int(fields[9])
                except ValueError:
                    continue
                if inode == target_inode:
                    if len(fields) <= 12:
                        return None
                    try:
                        drops = int(fields[12])
                    except ValueError:
                        return None
                    return drops if drops >= 0 else None
            if stream.readline(1):
                return None
    except (OSError, UnicodeError):
        return None
    return None


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
