import asyncio
import ctypes
import json
import multiprocessing
import os
import socket
import subprocess
import sys
import textwrap
import time

import pytest

from f1_engineer.udp.isolated import IsolatedUDPSource


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process-isolated receiver")


def abandon_lock(state, ready):
    state.get_lock().acquire()
    ready.set()
    os._exit(0)


async def wait_received(source, count):
    deadline = time.monotonic() + 3
    while source.stats.received < count:
        if time.monotonic() > deadline:
            pytest.fail(f"receiver did not admit {count} packets: {source.stats.received}")
        await asyncio.sleep(0.005)


def test_parent_interpreter_stall_does_not_stop_socket_reception():
    async def exercise():
        source = IsolatedUDPSource("127.0.0.1", 0, 1024)
        await source.open()
        sender = None
        try:
            source._receiver_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32768)
            port = source._receiver_socket.getsockname()[1]
            code = (
                "import socket,sys,time;sender=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);"
                "print('ready',flush=True);time.sleep(.05);started=time.perf_counter();"
                "\nfor index in range(240):"
                "\n time.sleep(max(0,started+index/530-time.perf_counter()));"
                "sender.sendto(index.to_bytes(2,'little')+bytes(1458),('127.0.0.1',int(sys.argv[1])))"
            )
            sender = subprocess.Popen([sys.executable, "-c", code, str(port)], stdout=subprocess.PIPE, text=True)
            assert await asyncio.to_thread(sender.stdout.readline) == "ready\n"
            ctypes.PyDLL(None).usleep(300000)
            await wait_received(source, 240)
            assert sender.wait(timeout=3) == 0
            packets = await source.receive_batch(240)
            assert [raw.sequence for raw in packets] == list(range(240))
            assert [int.from_bytes(raw.payload[:2], "little") for raw in packets] == list(range(240))
            assert source.stats.dropped == 0
            assert source.stats.socket_errors == 0
            assert source.kernel_receive_drops == 0
        finally:
            if sender is not None:
                if sender.poll() is None:
                    sender.kill()
                sender.wait(timeout=3)
                sender.stdout.close()
            source.close()

    asyncio.run(exercise())


def test_bounded_queue_preserves_sequence_gaps_and_shutdown_packets():
    async def exercise():
        source = IsolatedUDPSource("127.0.0.1", 0, 2)
        await source.open()
        process = source._process
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                address = source._receiver_socket.getsockname()
                for index in range(5):
                    sender.sendto(bytes([index]), address)
                await wait_received(source, 5)
                assert source.pending_count == 2
                assert source.stats.dropped == 3
                assert [raw.sequence for raw in await source.receive_batch(2)] == [0, 1]
                sender.sendto(b"next", address)
                await wait_received(source, 6)
            source.close()
            assert source._process is None
            assert source._mapping is None
            assert source._backing is None
            assert process._closed
            assert source.stats.received == 6
            assert source.stats.queued == 3
            assert source.stats.socket_errors == 0
            assert [raw.sequence for raw in source.drain_pending()] == [5]
            assert source.pending_count == 0
        finally:
            source.close()

    asyncio.run(exercise())


def test_cancelled_receive_and_large_payload_round_trip():
    async def exercise():
        source = IsolatedUDPSource("127.0.0.1", 0, 4)
        await source.open()
        try:
            for _ in range(3):
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(source.receive(), timeout=0.005)
            payload = bytes(range(256)) * 200
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(payload, source._receiver_socket.getsockname())
                port = sender.getsockname()[1]
            raw = await asyncio.wait_for(source.receive(), timeout=3)
            assert raw.payload == payload
            assert raw.source_host == "127.0.0.1"
            assert raw.source_port == port
            assert raw.sequence == 0
            assert raw.captured_at_ns > 0
            assert raw.monotonic_ns > 0
        finally:
            source.close()

    asyncio.run(exercise())


def test_receiver_crash_is_reported_and_port_is_released():
    async def exercise():
        source = IsolatedUDPSource("127.0.0.1", 0, 4)
        await source.open()
        address = source._receiver_socket.getsockname()
        try:
            source._process.terminate()
            source._process.join(timeout=3)
            with pytest.raises(RuntimeError, match="receiver exited"):
                await asyncio.wait_for(source.receive(), timeout=2)
        finally:
            source.close()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(address)

    asyncio.run(exercise())


def test_receiver_collision_leaves_no_resources():
    async def exercise():
        source = IsolatedUDPSource("127.0.0.1", 0, 4)
        await source.open()
        other = IsolatedUDPSource("127.0.0.1", source._receiver_socket.getsockname()[1], 4)
        try:
            with pytest.raises(OSError):
                await other.open()
            assert other._receiver_socket is None
            assert other._process is None
            assert other._backing is None
        finally:
            other.close()
            source.close()

    asyncio.run(exercise())


def test_startup_mapping_failure_releases_socket_and_backing(monkeypatch):
    import f1_engineer.udp.isolated as isolated

    def fail_mapping(*args, **kwargs):
        raise OSError("mapping unavailable")

    monkeypatch.setattr(isolated.mmap, "mmap", fail_mapping)
    source = IsolatedUDPSource("127.0.0.1", 0, 4)
    with pytest.raises(OSError, match="mapping unavailable"):
        asyncio.run(source.open())
    assert source._receiver_socket is None
    assert source._backing is None
    assert source._mapping is None
    assert source._process is None


def test_abrupt_parent_exit_does_not_leave_a_listener():
    code = textwrap.dedent("""
        import asyncio,json
        from f1_engineer.udp.isolated import IsolatedUDPSource
        async def run():
            source=IsolatedUDPSource('127.0.0.1',0,4)
            await source.open()
            print(json.dumps(source._receiver_socket.getsockname()),flush=True)
            await asyncio.sleep(60)
        asyncio.run(run())
    """)
    parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True)
    try:
        address = tuple(json.loads(parent.stdout.readline()))
        parent.kill()
        parent.wait(timeout=3)
        deadline = time.monotonic() + 3
        while True:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                try:
                    probe.bind(address)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        pytest.fail("receiver survived its parent")
                    time.sleep(0.01)
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=3)
        parent.stdout.close()


def test_abandoned_queue_lock_fails_closed_and_releases_resources():
    async def exercise():
        source = IsolatedUDPSource("127.0.0.1", 0, 4)
        await source.open()
        address = source._receiver_socket.getsockname()
        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        fault = context.Process(target=abandon_lock, args=(source._receiver_queue.state, ready))
        fault.start()
        try:
            assert await asyncio.to_thread(ready.wait, 3)
            fault.join(timeout=3)
            assert fault.exitcode == 0
            with pytest.raises(RuntimeError, match="shutdown failed"):
                source.close()
            assert source._receiver_socket is None
            assert source._process is None
            assert source._mapping is None
            assert source._backing is None
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind(address)
        finally:
            if fault.is_alive():
                fault.kill()
                fault.join(timeout=3)
            fault.close()
            source.close()

    asyncio.run(exercise())
