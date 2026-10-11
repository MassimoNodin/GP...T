from __future__ import annotations

import asyncio
import socket
from dataclasses import replace

import pytest

from f1_engineer.cli import build_parser
from f1_engineer.recording.capture import CaptureWriter
from f1_engineer.recording.udp_replay import replay_udp_capture
from tests.helpers import make_datagram


def make_capture(tmp_path):
    path = tmp_path / "network.f1ecap"
    packets = [make_datagram(sequence=index, body=bytes([index, 0, 255])) for index in range(3)]
    with CaptureWriter(path) as writer:
        for packet in packets:
            writer.write(packet)
    return path, packets


def test_udp_replay_sends_exact_payloads_in_order_to_real_socket(tmp_path):
    path, packets = make_capture(tmp_path)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(2)
        result = asyncio.run(replay_udp_capture(
            path, "127.0.0.1", receiver.getsockname()[1], confirm_idle=True,
        ))
        assert [receiver.recv(65535) for _ in packets] == [packet.payload for packet in packets]
    assert result["sent_packets"] == 3
    assert result["sent_bytes"] == sum(len(packet.payload) for packet in packets)
    assert result["delivery_verified"] is False


def test_udp_replay_dry_run_does_not_create_socket(tmp_path, monkeypatch):
    path, packets = make_capture(tmp_path)
    def unexpected_socket(*args, **kwargs):
        pytest.fail("dry run opened a network socket")
    loop = asyncio.new_event_loop()
    try:
        monkeypatch.setattr("f1_engineer.recording.udp_replay.socket.socket", unexpected_socket)
        result = loop.run_until_complete(replay_udp_capture(path, "127.0.0.1", dry_run=True))
    finally:
        loop.close()
    assert result["validated_packets"] == len(packets)
    assert result["sent_packets"] == 0


@pytest.mark.parametrize("speed", [0, -1, float("nan"), float("inf")])
def test_udp_replay_rejects_invalid_speed(tmp_path, speed):
    path, _ = make_capture(tmp_path)
    with pytest.raises(ValueError, match="speed"):
        asyncio.run(replay_udp_capture(path, "127.0.0.1", speed=speed, dry_run=True))


@pytest.mark.parametrize("host", ["0.0.0.0", "255.255.255.255", "224.0.0.1", "::", "ff02::1"])
def test_udp_replay_rejects_non_unicast_destinations(tmp_path, host):
    path, _ = make_capture(tmp_path)
    with pytest.raises(ValueError, match="unicast"):
        asyncio.run(replay_udp_capture(path, host, dry_run=True))


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_udp_replay_rejects_invalid_ports(tmp_path, port):
    path, _ = make_capture(tmp_path)
    with pytest.raises(ValueError, match="port"):
        asyncio.run(replay_udp_capture(path, "127.0.0.1", port, dry_run=True))


def test_udp_replay_requires_explicit_idle_confirmation(tmp_path):
    path, _ = make_capture(tmp_path)
    with pytest.raises(ValueError, match="confirm-idle"):
        asyncio.run(replay_udp_capture(path, "127.0.0.1"))


def test_udp_replay_rejects_missing_footer_before_sending(tmp_path):
    path, _ = make_capture(tmp_path)
    path.write_bytes(path.read_bytes()[:-len(b'\x02\x00\x00\x00\x15{"status":"complete"}')])
    with pytest.raises(ValueError, match="complete capture"):
        asyncio.run(replay_udp_capture(path, "127.0.0.1", confirm_idle=True))


def test_udp_replay_rejects_empty_capture(tmp_path):
    path = tmp_path / "empty.f1ecap"
    with CaptureWriter(path):
        pass
    with pytest.raises(ValueError, match="no datagrams"):
        asyncio.run(replay_udp_capture(path, "127.0.0.1", dry_run=True))


def test_udp_replay_rejects_backwards_timing(tmp_path):
    path = tmp_path / "backwards.f1ecap"
    with CaptureWriter(path) as writer:
        writer.write(make_datagram(sequence=1))
        writer.write(make_datagram(sequence=0))
    with pytest.raises(ValueError, match="backwards"):
        asyncio.run(replay_udp_capture(path, "127.0.0.1", dry_run=True))


def test_udp_replay_reports_scaled_recorded_duration(tmp_path):
    path = tmp_path / "timed.f1ecap"
    with CaptureWriter(path) as writer:
        writer.write(make_datagram())
        writer.write(replace(make_datagram(sequence=1), monotonic_ns=7_000_000_000))
    result = asyncio.run(replay_udp_capture(path, "127.0.0.1", speed=2, dry_run=True))
    assert result["recorded_duration_s"] == 2
    assert result["expected_replay_duration_s"] == 1


def test_udp_replay_cli_defaults_are_real_time_and_not_confirmed():
    args = build_parser().parse_args(["replay-udp", "capture.f1ecap", "--host", "192.168.1.115"])
    assert args.speed == 1
    assert args.port == 20777
    assert not args.confirm_idle
    assert not args.dry_run


def test_udp_replay_paces_real_network_delivery(tmp_path):
    path = tmp_path / "paced.f1ecap"
    with CaptureWriter(path) as writer:
        writer.write(make_datagram())
        writer.write(replace(make_datagram(sequence=1), monotonic_ns=5_100_000_000))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        result = asyncio.run(replay_udp_capture(
            path, "127.0.0.1", receiver.getsockname()[1], speed=2, confirm_idle=True,
        ))
    assert result["sent_packets"] == 2
    assert result["elapsed_s"] >= 0.05
