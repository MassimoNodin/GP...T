from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from ..errors import ProtocolError
from .header import HEADER_SIZE, parse_header
from .models import DecodedPacket, PacketFormat, PacketId, RawDatagram


class FormatAdapter(Protocol):
    packet_format: PacketFormat

    def decode(self, raw: RawDatagram) -> DecodedPacket: ...


@dataclass(frozen=True, slots=True)
class _BaseFormatAdapter:
    packet_format: PacketFormat

    def decode(self, raw: RawDatagram) -> DecodedPacket:
        header = parse_header(raw.payload)
        if header.packet_format != self.packet_format:
            raise ProtocolError(
                f"adapter for {self.packet_format.value} received format {header.packet_format}"
            )

        packet_kind = header.packet_kind
        if (
            self.packet_format is PacketFormat.F1_25
            and packet_kind is PacketId.CAR_TELEMETRY_2
        ):
            packet_kind = None

        return DecodedPacket(
            header=header,
            packet_format=self.packet_format,
            packet_kind=packet_kind,
            body=raw.payload[HEADER_SIZE:],
            wire_fingerprint=hashlib.blake2s(raw.payload, digest_size=16).digest(),
        )


class PacketDecoder:
    """Select a format adapter from the packet header and preserve the body."""

    def __init__(self) -> None:
        self._adapters: dict[int, FormatAdapter] = {
            PacketFormat.F1_25.value: _BaseFormatAdapter(PacketFormat.F1_25),
            PacketFormat.SEASON_PACK_2026.value: _BaseFormatAdapter(
                PacketFormat.SEASON_PACK_2026
            ),
        }

    def decode(self, raw: RawDatagram) -> DecodedPacket:
        header = parse_header(raw.payload)
        adapter = self._adapters.get(header.packet_format)
        if adapter is None:
            raise ProtocolError(f"unsupported packet format {header.packet_format}")
        return adapter.decode(raw)
