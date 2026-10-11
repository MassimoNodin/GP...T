from __future__ import annotations

import ipaddress
import math
import socket
import time
from pathlib import Path
from typing import Any

from .capture import CaptureReader
from ..udp.source import ReplaySource


async def replay_udp_capture(
    path: str | Path,
    host: str,
    port: int = 20777,
    *,
    speed: float = 1.0,
    confirm_idle: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    address = ipaddress.ip_address(host)
    if address.is_multicast or address.is_unspecified or str(address) == "255.255.255.255":
        raise ValueError("replay destination must be a unicast IP address")
    if not 1 <= port <= 65535:
        raise ValueError("UDP port must be between 1 and 65535")
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("UDP replay speed must be finite and greater than zero")
    if not dry_run and not confirm_idle:
        raise ValueError("confirm the game is off and the receiver is idle with --confirm-idle")
    capture_path = Path(path)
    before = capture_path.stat()
    packet_count = 0
    payload_bytes = 0
    first_timestamp: int | None = None
    last_timestamp: int | None = None
    with CaptureReader(capture_path) as capture:
        for raw in capture:
            if not raw.payload or len(raw.payload) > 65507:
                raise ValueError("capture contains a payload outside UDP replay limits")
            if last_timestamp is not None and raw.monotonic_ns < last_timestamp:
                raise ValueError("capture monotonic timestamps go backwards")
            if first_timestamp is None:
                first_timestamp = raw.monotonic_ns
            last_timestamp = raw.monotonic_ns
            packet_count += 1
            payload_bytes += len(raw.payload)
        if not capture.complete:
            raise ValueError("UDP replay requires a complete capture footer")
    if not packet_count:
        raise ValueError("capture contains no datagrams")
    after = capture_path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("capture changed during validation")
    assert first_timestamp is not None and last_timestamp is not None
    result: dict[str, Any] = {
        "capture": str(capture_path),
        "destination": host,
        "port": port,
        "speed": speed,
        "validated_packets": packet_count,
        "payload_bytes": payload_bytes,
        "recorded_duration_s": (last_timestamp - first_timestamp) / 1_000_000_000,
        "expected_replay_duration_s": (last_timestamp - first_timestamp) / 1_000_000_000 / speed,
        "dry_run": dry_run,
        "sent_packets": 0,
        "sent_bytes": 0,
        "delivery_verified": False,
    }
    if dry_run:
        return result
    started = time.perf_counter()
    source = ReplaySource(capture_path, speed=speed)
    with socket.socket(socket.AF_INET6 if address.version == 6 else socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.connect((host, port))
        sender.setblocking(False)
        async for raw in source.packets():
            sent = sender.send(raw.payload)
            if sent != len(raw.payload):
                raise OSError("UDP send did not accept the complete datagram")
            result["sent_packets"] += 1
            result["sent_bytes"] += sent
    final = capture_path.stat()
    if (
        not source.complete
        or result["sent_packets"] != packet_count
        or result["sent_bytes"] != payload_bytes
        or (after.st_size, after.st_mtime_ns) != (final.st_size, final.st_mtime_ns)
    ):
        raise ValueError("capture changed during replay; discard this test run")
    result["elapsed_s"] = time.perf_counter() - started
    return result
