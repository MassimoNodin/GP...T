from __future__ import annotations

import struct

from f1_engineer.udp.models import RawDatagram


HEADER = struct.Struct("<HBBBBBQfIIBB")


def make_datagram(
    *,
    packet_format: int = 2025,
    packet_id: int = 0,
    packet_version: int = 1,
    session_uid: int = 10,
    frame: int = 1,
    session_time: float = 1.25,
    body: bytes = b"body",
    sequence: int = 0,
) -> RawDatagram:
    header = HEADER.pack(
        packet_format,
        25,
        0,
        1,
        packet_version,
        packet_id,
        session_uid,
        session_time,
        frame,
        frame,
        0,
        255,
    )
    return RawDatagram(
        sequence=sequence,
        captured_at_ns=1_000_000_000 + sequence,
        monotonic_ns=5_000_000_000 + sequence,
        source_host="127.0.0.1",
        source_port=20777,
        payload=header + body,
    )
