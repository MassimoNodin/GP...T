from __future__ import annotations

import asyncio
import struct
import socket
import threading
from argparse import Namespace

import pytest

from f1_engineer.cli import _record
from f1_engineer.recording.capture import CaptureReader, CaptureWriter
from f1_engineer.udp.source import ReplaySource
from tests.helpers import make_datagram


def test_capture_round_trip_records_exact_datagrams_and_footer(tmp_path) -> None:
    path = tmp_path / "session.f1ecap"
    expected = [make_datagram(sequence=n, body=bytes([n, 0, 255])) for n in range(3)]

    with CaptureWriter(path, {"track": "melbourne"}) as writer:
        for raw in expected:
            writer.write(raw)

    with CaptureReader(path) as reader:
        actual = list(reader)
        assert actual == expected
        assert reader.metadata["track"] == "melbourne"
        assert reader.complete
        assert reader.completion == {"status": "complete"}


def test_capture_creation_does_not_overwrite_without_explicit_flag(tmp_path) -> None:
    path = tmp_path / "existing.f1ecap"
    path.write_bytes(b"keep me")

    with pytest.raises(FileExistsError):
        CaptureWriter(path)

    assert path.read_bytes() == b"keep me"


def test_capture_can_be_replaced_only_with_explicit_flag(tmp_path) -> None:
    path = tmp_path / "replace.f1ecap"
    path.write_bytes(b"old capture")

    with CaptureWriter(path, overwrite=True) as writer:
        writer.write(make_datagram())

    with CaptureReader(path) as reader:
        assert len(list(reader)) == 1
        assert reader.complete


def test_truncated_capture_is_read_as_incomplete_when_tail_is_clean_eof(tmp_path) -> None:
    path = tmp_path / "interrupted.f1ecap"
    with CaptureWriter(path) as writer:
        writer.write(make_datagram())

    content = path.read_bytes()
    # Remove the footer entry, leaving a valid sequence of complete packet records.
    footer = (
        b"\x02"
        + struct.pack("!I", len(b'{"status":"complete"}'))
        + b'{"status":"complete"}'
    )
    assert content.endswith(footer)
    path.write_bytes(content[:-len(footer)])

    with CaptureReader(path) as reader:
        assert len(list(reader)) == 1
        assert not reader.complete


def test_replay_source_yields_capture_in_original_order(tmp_path) -> None:
    path = tmp_path / "replay.f1ecap"
    expected = [make_datagram(sequence=n) for n in range(3)]
    with CaptureWriter(path) as writer:
        for raw in expected:
            writer.write(raw)

    async def collect():
        result = []
        async for raw in ReplaySource(path, speed=0).packets():
            result.append(raw)
        return result

    assert asyncio.run(collect()) == expected


def test_record_duration_stops_cleanly_when_no_datagrams_arrive(tmp_path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    path = tmp_path / "quiet-session.f1ecap"
    args = Namespace(
        output=str(path),
        host="127.0.0.1",
        port=port,
        queue_size=4,
        duration=0.02,
        overwrite=False,
    )

    assert asyncio.run(_record(args)) == 0
    with CaptureReader(path) as reader:
        assert list(reader) == []
        assert reader.complete
        assert reader.completion["status"] == "complete"
        assert reader.completion["received"] == 0


def test_cancel_during_disk_write_counts_the_persisted_packet(tmp_path, monkeypatch) -> None:
    original_write = CaptureWriter.write
    writer_entered = threading.Event()
    release_writer = threading.Event()

    def slow_write(writer, raw):
        writer_entered.set()
        if not release_writer.wait(timeout=3):
            raise TimeoutError("test writer was not released")
        original_write(writer, raw)

    monkeypatch.setattr(CaptureWriter, "write", slow_write)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as port_probe:
        port_probe.bind(("127.0.0.1", 0))
        port = port_probe.getsockname()[1]
    path = tmp_path / "cancelled-session.f1ecap"
    args = Namespace(
        output=str(path),
        host="127.0.0.1",
        port=port,
        queue_size=32,
        duration=None,
        overwrite=False,
    )

    async def record_and_cancel():
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.setblocking(False)
        task = asyncio.create_task(_record(args))
        try:
            await asyncio.sleep(0.05)
            await asyncio.get_running_loop().sock_sendto(
                sender, make_datagram().payload, ("127.0.0.1", port)
            )
            assert await asyncio.to_thread(writer_entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            release_writer.set()
            return await task
        finally:
            release_writer.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            sender.close()

    assert asyncio.run(record_and_cancel()) == 0
    with CaptureReader(path) as reader:
        datagrams = list(reader)
        assert reader.complete
        assert reader.completion["status"] == "complete"
        assert reader.completion["recorded"] == len(datagrams)
        assert reader.completion["unpersisted_on_shutdown"] == 0
