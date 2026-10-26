from __future__ import annotations

import asyncio
import ctypes
import mmap
import multiprocessing
from multiprocessing import reduction
import os
import queue
import signal
import socket
import struct
import sys
import tempfile
import time
from contextlib import contextmanager
from typing import Any, Iterator

from .models import RawDatagram
from .source import UDPSource, UDPSourceStats


_HEADER = struct.Struct("<QQQHHI")
_HOST_BYTES = 64
_SLOT_BYTES = _HEADER.size + _HOST_BYTES + 65535


@contextmanager
def _locked(state: Any) -> Iterator[Any]:
    lock = state.get_lock()
    if not lock.acquire(timeout=1):
        raise RuntimeError("isolated UDP queue lock unavailable")
    try:
        yield state.get_obj()
    finally:
        lock.release()


class _SharedQueue:
    def __init__(self, mapping: mmap.mmap, state: Any, capacity: int) -> None:
        self.mapping = mapping
        self.state = state
        self.capacity = capacity

    def qsize(self) -> int:
        with _locked(self.state) as values:
            return values[6]

    def put(self, payload: bytes, address: tuple[Any, ...]) -> None:
        host = str(address[0]).encode("ascii")
        captured = time.time_ns()
        monotonic = time.perf_counter_ns()
        with _locked(self.state) as values:
            sequence = values[0]
            values[0] += 1
            if values[6] == self.capacity:
                values[2] += 1
                return
            offset = values[5] * _SLOT_BYTES
            _HEADER.pack_into(self.mapping, offset, sequence, captured, monotonic,
                              int(address[1]), len(host), len(payload))
            host_offset = offset + _HEADER.size
            self.mapping[host_offset:host_offset + len(host)] = host
            payload_offset = host_offset + _HOST_BYTES
            self.mapping[payload_offset:payload_offset + len(payload)] = payload
            values[5] = (values[5] + 1) % self.capacity
            values[6] += 1
            values[1] += 1

    def get_nowait(self) -> RawDatagram:
        with _locked(self.state) as values:
            if not values[6]:
                raise queue.Empty
            offset = values[4] * _SLOT_BYTES
            sequence, captured, monotonic, port, host_length, payload_length = _HEADER.unpack_from(self.mapping, offset)
            host_offset = offset + _HEADER.size
            host = self.mapping[host_offset:host_offset + host_length].decode("ascii")
            payload_offset = host_offset + _HOST_BYTES
            payload = self.mapping[payload_offset:payload_offset + payload_length]
            values[4] = (values[4] + 1) % self.capacity
            values[6] -= 1
        return RawDatagram(sequence, captured, monotonic, host, port, payload)


class _SharedStats:
    def __init__(self, state: Any) -> None:
        self.state = state

    def _read(self, index: int) -> int:
        with _locked(self.state) as values:
            return values[index]

    received = property(lambda self: self._read(0))
    queued = property(lambda self: self._read(1))
    dropped = property(lambda self: self._read(2))

    @property
    def socket_errors(self) -> int:
        return self._read(3)

    @socket_errors.setter
    def socket_errors(self, value: int) -> None:
        with _locked(self.state) as values:
            values[3] = value


def _receive(udp_socket: socket.socket, descriptor: Any, state: Any, capacity: int,
             stop: Any, ready: Any, parent_pid: int) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    if library.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if os.getppid() != parent_pid:
        return
    udp_socket.settimeout(0.05)
    with os.fdopen(descriptor.detach(), "r+b") as backing, mmap.mmap(backing.fileno(), capacity * _SLOT_BYTES) as mapping:
        pending = _SharedQueue(mapping, state, capacity)
        ready.set()
        try:
            while not stop.is_set():
                try:
                    payload, address = udp_socket.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    if stop.is_set():
                        return
                    with _locked(state) as values:
                        values[3] += 1
                    continue
                pending.put(payload, address)
        finally:
            udp_socket.close()


class IsolatedUDPSource(UDPSource):
    """Linux socket owner isolated from the API/processing interpreter.

    A sparse mapped ring preserves the existing packet count limit, without a
    multiprocessing queue feeder or an additional hidden admission queue.
    """

    isolated_receiver = True

    def __init__(self, host: str = "0.0.0.0", port: int = 20777, queue_size: int = 4096,
                 *, independent_receiver: bool = True) -> None:
        if sys.platform != "linux":
            raise ValueError("isolated reception requires Linux")
        if not independent_receiver:
            raise ValueError("isolated reception requires independent_receiver")
        super().__init__(host, port, queue_size, independent_receiver=True)
        self._capacity = queue_size
        self._process = None
        self._backing = None
        self._mapping = None
        self._process_stop = None

    async def open(self) -> None:
        if self._receiver_socket is not None:
            raise RuntimeError("isolated UDP receiver is already open")
        self._kernel_drops = None
        self._kernel_drops_sampled_at = None
        udp_socket = socket.socket(socket.AF_INET6 if ":" in self.host else socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp_socket.bind((self.host, self.port))
            udp_socket.settimeout(0.05)
            self.receive_buffer_bytes = udp_socket.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            self._receiver_socket = udp_socket
            context = multiprocessing.get_context("spawn")
            state = context.Array("Q", [0] * 7)
            self._backing = tempfile.TemporaryFile(prefix="f1-udp-", suffix=".ring")
            self._backing.truncate(self._capacity * _SLOT_BYTES)
            self._mapping = mmap.mmap(self._backing.fileno(), self._capacity * _SLOT_BYTES)
            self._receiver_queue = _SharedQueue(self._mapping, state, self._capacity)
            self.stats = _SharedStats(state)
            self._process_stop = context.Event()
            ready = context.Event()
            self._process = context.Process(target=_receive, name="udp-reception", daemon=True,
                                            args=(udp_socket, reduction.DupFd(self._backing.fileno()), state, self._capacity,
                                                  self._process_stop, ready, os.getpid()))
            self._process.start()
            deadline = time.monotonic() + 5
            while not ready.is_set():
                self._check_receiver()
                if time.monotonic() >= deadline:
                    raise RuntimeError("isolated UDP receiver did not become ready")
                await asyncio.sleep(0.005)
        except BaseException:
            self.close()
            udp_socket.close()
            raise

    def _check_receiver(self) -> None:
        if self._process is not None and self._process.exitcode is not None:
            raise RuntimeError(f"isolated UDP receiver exited: {self._process.exitcode}")

    async def receive(self) -> RawDatagram:
        while True:
            self._check_receiver()
            try:
                return self._receiver_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.001)

    def close(self) -> None:
        failed = False
        if self._process is not None:
            if self._process_stop is not None:
                self._process_stop.set()
            if self._process.pid is not None:
                self._process.join(timeout=1)
                if self._process.is_alive():
                    failed = True
                    self._process.terminate()
                    self._process.join(timeout=1)
            self._process.close()
            self._process = None
        if self._mapping is not None:
            try:
                retained = super().drain_pending()
            except RuntimeError:
                retained = ()
                failed = True
            values = self.stats.state.get_obj()
            snapshot = UDPSourceStats(*values[:4])
            self._receiver_queue = queue.Queue(maxsize=self._capacity)
            for raw in retained:
                self._receiver_queue.put_nowait(raw)
            self.stats = snapshot
            self._mapping.close()
            self._mapping = None
        if self._backing is not None:
            self._backing.close()
            self._backing = None
        self._kernel_drops_sampled_at = None
        super().close()
        if failed:
            raise RuntimeError("isolated UDP receiver shutdown failed")
