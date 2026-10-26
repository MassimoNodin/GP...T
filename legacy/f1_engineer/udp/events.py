from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from .models import DecodedPacket, PacketFormat, PacketId


EVENT_BODY_SIZE = 16
EVENT_DETAIL_SIZE = 12
_F1_25_EVENT_CODES = frozenset(
    {
        "SSTA", "SEND", "FTLP", "RTMT", "DRSE", "DRSD", "TMPT", "CHQF",
        "RCWN", "PENA", "SPTP", "STLG", "LGOT", "DTSV", "SGSV", "FLBK",
        "BUTN", "RDFL", "OVTK", "SCAR", "COLL",
    }
)
_SEASON_PACK_2026_EVENT_CODES = _F1_25_EVENT_CODES | frozenset(
    {"PMEN", "PMDI", "OVEN", "OVDI"}
)


@dataclass(frozen=True, slots=True)
class EventData:
    code_bytes: bytes | None
    code: str | None
    details: bytes
    target_frame_identifier: int | None = None
    target_session_time_s: float | None = None
    error: str | None = None
    details_length_bytes: int = 0
    details_truncated: bool = False


class EventDecoder:
    """Decode documented Event v1 lifecycle fields while preserving raw details."""

    def decode(self, packet: DecodedPacket) -> EventData:
        if packet.packet_kind is not PacketId.EVENT:
            return EventData(None, None, b"", error="not_event_packet")
        code_bytes = packet.body[:4] if len(packet.body) >= 4 else None
        try:
            code = code_bytes.decode("ascii") if code_bytes is not None else None
        except UnicodeDecodeError:
            code = None
        raw_details = packet.body[4:] if len(packet.body) >= 4 else b""
        details = raw_details[:EVENT_DETAIL_SIZE]
        detail_metadata = {
            "details_length_bytes": len(raw_details),
            "details_truncated": len(raw_details) > EVENT_DETAIL_SIZE,
        }

        if packet.packet_format not in (
            PacketFormat.F1_25,
            PacketFormat.SEASON_PACK_2026,
        ):
            return EventData(
                code_bytes, code, details, error="unsupported_event_format", **detail_metadata
            )
        if packet.header.packet_version != 1:
            return EventData(
                code_bytes, code, details, error="unsupported_event_version", **detail_metadata
            )
        if len(packet.body) != EVENT_BODY_SIZE:
            return EventData(
                code_bytes, code, details, error="malformed_event_body_size", **detail_metadata
            )
        if code is None or len(code) != 4:
            return EventData(
                code_bytes, code, details, error="malformed_event_code", **detail_metadata
            )
        known_codes = (
            _F1_25_EVENT_CODES
            if packet.packet_format is PacketFormat.F1_25
            else _SEASON_PACK_2026_EVENT_CODES
        )
        if code not in known_codes:
            return EventData(
                code_bytes, code, details, error="unknown_event_code", **detail_metadata
            )
        if code != "FLBK":
            return EventData(code_bytes, code, details, **detail_metadata)

        target_frame, target_time = struct.unpack_from("<If", details)
        if not math.isfinite(target_time) or target_time < 0:
            return EventData(
                code_bytes,
                code,
                details,
                target_frame,
                target_time,
                error="malformed_flashback_target",
                **detail_metadata,
            )
        return EventData(
            code_bytes,
            code,
            details,
            target_frame,
            target_time,
            **detail_metadata,
        )
