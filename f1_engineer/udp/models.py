from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class PacketFormat(IntEnum):
    F1_25 = 2025
    SEASON_PACK_2026 = 2026


class PacketId(IntEnum):
    MOTION = 0
    SESSION = 1
    LAP_DATA = 2
    EVENT = 3
    PARTICIPANTS = 4
    CAR_SETUPS = 5
    CAR_TELEMETRY = 6
    CAR_STATUS = 7
    FINAL_CLASSIFICATION = 8
    LOBBY_INFO = 9
    CAR_DAMAGE = 10
    SESSION_HISTORY = 11
    TYRE_SETS = 12
    MOTION_EX = 13
    TIME_TRIAL = 14
    LAP_POSITIONS = 15
    CAR_TELEMETRY_2 = 16


@dataclass(frozen=True, slots=True)
class RawDatagram:
    sequence: int
    captured_at_ns: int
    monotonic_ns: int
    source_host: str
    source_port: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class PacketHeader:
    packet_format: int
    game_year: int
    game_major_version: int
    game_minor_version: int
    packet_version: int
    packet_id: int
    session_uid: int
    session_time: float
    frame_identifier: int
    overall_frame_identifier: int
    player_car_index: int
    secondary_player_car_index: int

    @property
    def packet_kind(self) -> PacketId | None:
        try:
            return PacketId(self.packet_id)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class DecodedPacket:
    """Format-neutral packet envelope; body decoding is added by packet adapters."""

    header: PacketHeader
    packet_format: PacketFormat
    packet_kind: PacketId | None
    body: bytes
    wire_fingerprint: bytes


@dataclass(frozen=True, slots=True)
class PacketFrame:
    session_uid: int
    overall_frame_identifier: int
    packets: tuple[DecodedPacket, ...]


@dataclass(frozen=True, slots=True)
class SessionEvent:
    kind: str
    session_uid: int
    previous_session_uid: int | None = None
