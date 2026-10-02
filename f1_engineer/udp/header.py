from __future__ import annotations

import math
import struct

from ..errors import ProtocolError
from .models import PacketHeader


# EA's packed common header is 29 bytes. Multi-byte fields use little-endian order.
_HEADER = struct.Struct("<HBBBBBQfIIBB")
HEADER_SIZE = _HEADER.size


def parse_header(payload: bytes) -> PacketHeader:
    if len(payload) < HEADER_SIZE:
        raise ProtocolError(
            f"datagram is {len(payload)} bytes; the common header requires {HEADER_SIZE}"
        )

    values = _HEADER.unpack_from(payload)
    header = PacketHeader(*values)
    if not math.isfinite(header.session_time):
        raise ProtocolError("packet header contains a non-finite session time")
    return header
