from __future__ import annotations

import struct
from dataclasses import dataclass

from .models import DecodedPacket, PacketFormat, PacketId


SESSION_HISTORY_PACKET_SIZE = 1460
SESSION_HISTORY_BODY_SIZE = SESSION_HISTORY_PACKET_SIZE - 29
MAX_LAPS_IN_HISTORY = 100
MAX_TYRE_STINTS_IN_HISTORY = 8
_F1_25_CAR_COUNT = 22
_SEASON_PACK_CAR_COUNT = 24
_LAP_RECORD = struct.Struct("<IHBHBHBB")
_TYRE_STINT_RECORD = struct.Struct("<BBB")


@dataclass(frozen=True, slots=True)
class SessionHistoryLap:
    lap_index: int
    lap_time_ms: int
    sector1_time_ms_part: int
    sector1_time_minutes_part: int
    sector2_time_ms_part: int
    sector2_time_minutes_part: int
    sector3_time_ms_part: int
    sector3_time_minutes_part: int
    validity_flags: int

    @property
    def sector1_time_ms(self) -> int:
        return self.sector1_time_minutes_part * 60_000 + self.sector1_time_ms_part

    @property
    def sector1_time_available(self) -> bool:
        return 0 < self.sector1_time_ms and self.sector1_time_ms_part < 60_000

    @property
    def sector2_time_ms(self) -> int:
        return self.sector2_time_minutes_part * 60_000 + self.sector2_time_ms_part

    @property
    def sector2_time_available(self) -> bool:
        return 0 < self.sector2_time_ms and self.sector2_time_ms_part < 60_000

    @property
    def sector3_time_ms(self) -> int:
        return self.sector3_time_minutes_part * 60_000 + self.sector3_time_ms_part

    @property
    def sector3_time_available(self) -> bool:
        return 0 < self.sector3_time_ms and self.sector3_time_ms_part < 60_000

    @property
    def sector_sum_residual_ms(self) -> int:
        return (
            self.sector1_time_ms
            + self.sector2_time_ms
            + self.sector3_time_ms
            - self.lap_time_ms
        )

    @property
    def unknown_validity_bits(self) -> int:
        return self.validity_flags & ~0x0F

    def to_dict(self) -> dict[str, int | bool | str | None]:
        return {
            "lap_index": self.lap_index,
            "lap_number": self.lap_index + 1,
            "lap_time_ms": self.lap_time_ms,
            "sector1_time_ms_part": self.sector1_time_ms_part,
            "sector1_time_minutes_part": self.sector1_time_minutes_part,
            "sector1_time_ms": self.sector1_time_ms,
            "sector1_time_available": self.sector1_time_available,
            "sector1_time_unavailable_reason": _sector_unavailable_reason(
                self.sector1_time_ms_part, self.sector1_time_ms
            ),
            "sector2_time_ms_part": self.sector2_time_ms_part,
            "sector2_time_minutes_part": self.sector2_time_minutes_part,
            "sector2_time_ms": self.sector2_time_ms,
            "sector2_time_available": self.sector2_time_available,
            "sector2_time_unavailable_reason": _sector_unavailable_reason(
                self.sector2_time_ms_part, self.sector2_time_ms
            ),
            "sector3_time_ms_part": self.sector3_time_ms_part,
            "sector3_time_minutes_part": self.sector3_time_minutes_part,
            "sector3_time_ms": self.sector3_time_ms,
            "sector3_time_available": self.sector3_time_available,
            "sector3_time_unavailable_reason": _sector_unavailable_reason(
                self.sector3_time_ms_part, self.sector3_time_ms
            ),
            "validity_flags": self.validity_flags,
            "lap_valid": bool(self.validity_flags & 0x01),
            "sector1_valid": bool(self.validity_flags & 0x02),
            "sector2_valid": bool(self.validity_flags & 0x04),
            "sector3_valid": bool(self.validity_flags & 0x08),
            "unknown_validity_bits": self.unknown_validity_bits,
            "sector_sum_residual_ms": self.sector_sum_residual_ms,
        }


def _sector_unavailable_reason(milliseconds_part: int, total_ms: int) -> str | None:
    if milliseconds_part >= 60_000:
        return "millisecond_component_out_of_range"
    if total_ms <= 0:
        return "reported_zero"
    return None


@dataclass(frozen=True, slots=True)
class SessionHistoryTyreStint:
    stint_index: int
    end_lap: int
    actual_compound: int
    visual_compound: int

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "stint_index": self.stint_index,
            "end_lap": self.end_lap,
            "current_stint": self.end_lap == 255,
            "actual_compound": self.actual_compound,
            "visual_compound": self.visual_compound,
        }


@dataclass(frozen=True, slots=True)
class SessionHistoryPacket:
    session_uid: int
    frame_identifier: int
    overall_frame_identifier: int
    session_time_s: float
    frame_ordinal: int
    source_sequence: int | None
    packet_format: int
    packet_version: int
    header_player_car_index: int
    car_index: int
    num_laps: int
    num_tyre_stints: int
    best_lap_time_lap_number: int
    best_sector1_lap_number: int
    best_sector2_lap_number: int
    best_sector3_lap_number: int
    lap_history: tuple[SessionHistoryLap, ...]
    tyre_stints: tuple[SessionHistoryTyreStint, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "session_uid": self.session_uid,
            "frame_identifier": self.frame_identifier,
            "overall_frame_identifier": self.overall_frame_identifier,
            "session_time_s": self.session_time_s,
            "frame_ordinal": self.frame_ordinal,
            "source_sequence": self.source_sequence,
            "packet_format": self.packet_format,
            "packet_version": self.packet_version,
            "header_player_car_index": self.header_player_car_index,
            "car_index": self.car_index,
            "num_laps": self.num_laps,
            "num_tyre_stints": self.num_tyre_stints,
            "best_lap_time_lap_number": self.best_lap_time_lap_number,
            "best_sector1_lap_number": self.best_sector1_lap_number,
            "best_sector2_lap_number": self.best_sector2_lap_number,
            "best_sector3_lap_number": self.best_sector3_lap_number,
            "lap_history": [lap.to_dict() for lap in self.lap_history],
            "tyre_stints": [stint.to_dict() for stint in self.tyre_stints],
        }


@dataclass(frozen=True, slots=True)
class SessionHistoryDecodeResult:
    history: SessionHistoryPacket | None
    error: str | None


class SessionHistoryDecoder:
    """Decode the documented Session History v1 snapshot for both wire formats."""

    def decode(
        self, packet: DecodedPacket, *, frame_ordinal: int
    ) -> SessionHistoryDecodeResult:
        if packet.packet_kind is not PacketId.SESSION_HISTORY:
            return SessionHistoryDecodeResult(None, "not_session_history_packet")
        if packet.packet_format not in (
            PacketFormat.F1_25,
            PacketFormat.SEASON_PACK_2026,
        ):
            return SessionHistoryDecodeResult(None, "unsupported_session_history_format")
        if packet.header.packet_version != 1:
            return SessionHistoryDecodeResult(None, "unsupported_session_history_version")
        if len(packet.body) != SESSION_HISTORY_BODY_SIZE:
            return SessionHistoryDecodeResult(None, "malformed_session_history_body_size")

        car_index, num_laps, num_stints, best_lap, best_s1, best_s2, best_s3 = (
            packet.body[:7]
        )
        if num_laps > MAX_LAPS_IN_HISTORY:
            return SessionHistoryDecodeResult(None, "invalid_session_history_lap_count")
        if num_stints > MAX_TYRE_STINTS_IN_HISTORY:
            return SessionHistoryDecodeResult(None, "invalid_session_history_stint_count")
        max_cars = (
            _F1_25_CAR_COUNT
            if packet.packet_format is PacketFormat.F1_25
            else _SEASON_PACK_CAR_COUNT
        )
        if car_index >= max_cars:
            return SessionHistoryDecodeResult(None, "invalid_session_history_car_index")

        lap_history: list[SessionHistoryLap] = []
        offset = 7
        for lap_index in range(num_laps):
            values = _LAP_RECORD.unpack_from(packet.body, offset)
            lap_history.append(SessionHistoryLap(lap_index, *values))
            offset += _LAP_RECORD.size

        offset = 7 + MAX_LAPS_IN_HISTORY * _LAP_RECORD.size
        tyre_stints: list[SessionHistoryTyreStint] = []
        for stint_index in range(num_stints):
            values = _TYRE_STINT_RECORD.unpack_from(packet.body, offset)
            tyre_stints.append(SessionHistoryTyreStint(stint_index, *values))
            offset += _TYRE_STINT_RECORD.size

        return SessionHistoryDecodeResult(
            SessionHistoryPacket(
                session_uid=packet.header.session_uid,
                frame_identifier=packet.header.frame_identifier,
                overall_frame_identifier=packet.header.overall_frame_identifier,
                session_time_s=packet.header.session_time,
                frame_ordinal=frame_ordinal,
                source_sequence=packet.source_sequence,
                packet_format=int(packet.packet_format),
                packet_version=packet.header.packet_version,
                header_player_car_index=packet.header.player_car_index,
                car_index=car_index,
                num_laps=num_laps,
                num_tyre_stints=num_stints,
                best_lap_time_lap_number=best_lap,
                best_sector1_lap_number=best_s1,
                best_sector2_lap_number=best_s2,
                best_sector3_lap_number=best_s3,
                lap_history=tuple(lap_history),
                tyre_stints=tuple(tyre_stints),
            ),
            None,
        )
