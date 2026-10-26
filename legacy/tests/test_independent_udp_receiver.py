import asyncio
import socket
import threading
import time

import pytest

from f1_engineer.udp.source import UDPSource


def test_receiver_keeps_draining_socket_while_processing_loop_is_blocked():
    async def exercise():
        source = UDPSource("127.0.0.1", 0, 256, independent_receiver=True)
        await source.open()
        address = source._receiver_socket.getsockname()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as default_socket:
            assert source.receive_buffer_bytes == default_socket.getsockopt(
                socket.SOL_SOCKET, socket.SO_RCVBUF
            )

        def send():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                for index in range(96):
                    sender.sendto(bytes([index]) * 1024, address)
                    time.sleep(0.001)

        sender = threading.Thread(target=send)
        try:
            sender.start()
            sender.join(timeout=3)
            assert not sender.is_alive()
            deadline = time.monotonic() + 2
            while source.stats.received < 96 and time.monotonic() < deadline:
                time.sleep(0.001)
            assert source.stats.received == 96
            assert source.pending_count == 96
            assert source.stats.dropped == 0
            packets = await source.receive_batch(96)
            assert [raw.sequence for raw in packets] == list(range(96))
            assert [raw.payload[0] for raw in packets] == list(range(96))
        finally:
            source.close()
            sender.join(timeout=3)
        assert source.stats.socket_errors == 0
        assert source._receiver_thread is None

    asyncio.run(exercise())


def test_receiver_queue_is_bounded_and_preserves_loss_sequences():
    async def exercise():
        source = UDPSource("127.0.0.1", 0, 2, independent_receiver=True)
        await source.open()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                address = source._receiver_socket.getsockname()
                for index in range(5):
                    sender.sendto(bytes([index]), address)
                deadline = time.monotonic() + 2
                while source.stats.received < 5 and time.monotonic() < deadline:
                    await asyncio.sleep(0.001)
                assert source.stats.received == 5
                assert source.pending_count == 2
                assert source.stats.queued == 2
                assert source.stats.dropped == 3
                assert [raw.sequence for raw in await source.receive_batch(2)] == [0, 1]
                sender.sendto(b"next", address)
                raw = await asyncio.wait_for(source.receive(), timeout=2)
                assert raw.sequence == 5
                assert raw.payload == b"next"
        finally:
            source.close()

    asyncio.run(exercise())


def test_cancelled_receive_does_not_consume_a_future_packet():
    async def exercise():
        source = UDPSource("127.0.0.1", 0, 2, independent_receiver=True)
        await source.open()
        try:
            for _ in range(3):
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(source.receive(), timeout=0.005)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(b"after cancellation", source._receiver_socket.getsockname())
            raw = await asyncio.wait_for(source.receive(), timeout=2)
            assert raw.payload == b"after cancellation"
            source.close()
            assert source.stats.socket_errors == 0
        finally:
            source.close()

    asyncio.run(exercise())


def test_exclusive_receiver_collision_does_not_start_a_thread():
    async def exercise():
        source = UDPSource("127.0.0.1", 0, independent_receiver=True)
        await source.open()
        other = UDPSource("127.0.0.1", source._receiver_socket.getsockname()[1], independent_receiver=True)
        try:
            with pytest.raises(OSError):
                await other.open()
            assert other._receiver_socket is None
            assert other._receiver_thread is None
        finally:
            other.close()
            source.close()

    asyncio.run(exercise())
