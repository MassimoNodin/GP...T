from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable

from .models import DecodedPacket, PacketFormat, PacketId


_F1_25_CAR_COUNT = 22
_SEASON_PACK_2026_CAR_COUNT = 24
_LAP_DATA_V1_CAR = struct.Struct("<IIHBHBHBHBfff15BHHBfB")
_F1_25_LAP_DATA_V1_BODY_SIZE = _F1_25_CAR_COUNT * _LAP_DATA_V1_CAR.size + 2
_SEASON_PACK_2026_LAP_DATA_V1_BODY_SIZE = (
    _SEASON_PACK_2026_CAR_COUNT * _LAP_DATA_V1_CAR.size + 2
)


@dataclass(frozen=True, slots=True)
class CarLapData:
    last_lap_time_ms: int
    current_lap_time_ms: int
    sector1_time_ms: int
    sector2_time_ms: int
    delta_to_car_in_front_ms: int
    delta_to_race_leader_ms: int
    lap_distance_m: float
    total_distance_m: float
    safety_car_delta_s: float
    car_position: int
    current_lap_number: int
    pit_status_id: int
    pit_stop_count: int
    sector_id: int
    current_lap_invalid_id: int
    penalties_s: int
    total_warnings: int
    corner_cutting_warnings: int
    drive_through_penalties_remaining: int
    stop_go_penalties_remaining: int
    grid_position: int
    driver_status_id: int
    result_status_id: int
    pit_lane_timer_active: bool
    pit_lane_time_ms: int
    pit_stop_timer_ms: int
    pit_stop_should_serve_penalty: bool
    speed_trap_fastest_speed_kph: float
    speed_trap_fastest_lap_number: int


@dataclass(frozen=True, slots=True)
class LapDataPacket:
    cars: tuple[CarLapData, ...]
    time_trial_pb_car_index: int
    time_trial_rival_car_index: int


@dataclass(frozen=True, slots=True)
class LapDataDecodeResult:
    lap_data: LapDataPacket | None = None
    error: str | None = None


LapDataParser = Callable[[DecodedPacket], LapDataPacket]


class LapDataDecoder:
    """Decode versioned Lap Data bodies while retaining all car records."""

    def __init__(self) -> None:
        self._parsers: dict[tuple[PacketFormat, PacketId, int], LapDataParser] = {
            (PacketFormat.F1_25, PacketId.LAP_DATA, 1): _decode_f1_25_lap_data_v1,
            (PacketFormat.SEASON_PACK_2026, PacketId.LAP_DATA, 1): (
                _decode_season_pack_2026_lap_data_v1
            ),
        }

    def supports(
        self, packet_format: PacketFormat, packet_id: PacketId, packet_version: int
    ) -> bool:
        return (packet_format, packet_id, packet_version) in self._parsers

    def decode(self, packet: DecodedPacket) -> LapDataDecodeResult:
        if packet.packet_kind is not PacketId.LAP_DATA:
            return LapDataDecodeResult()

        key = (packet.packet_format, PacketId.LAP_DATA, packet.header.packet_version)
        parser = self._parsers.get(key)
        if parser is None:
            return LapDataDecodeResult(
                error=(
                    "unsupported lap data packet adapter for "
                    f"format {packet.packet_format.value}, version "
                    f"{packet.header.packet_version}"
                )
            )
        try:
            return LapDataDecodeResult(lap_data=parser(packet))
        except ValueError as exc:
            return LapDataDecodeResult(error=str(exc))


def _decode_f1_25_lap_data_v1(packet: DecodedPacket) -> LapDataPacket:
    return _decode_lap_data_v1(
        packet,
        car_count=_F1_25_CAR_COUNT,
        body_size=_F1_25_LAP_DATA_V1_BODY_SIZE,
        format_name="F1 25",
    )


def _decode_season_pack_2026_lap_data_v1(packet: DecodedPacket) -> LapDataPacket:
    return _decode_lap_data_v1(
        packet,
        car_count=_SEASON_PACK_2026_CAR_COUNT,
        body_size=_SEASON_PACK_2026_LAP_DATA_V1_BODY_SIZE,
        format_name="2026 Season Pack",
    )


def _decode_lap_data_v1(
    packet: DecodedPacket, *, car_count: int, body_size: int, format_name: str
) -> LapDataPacket:
    body = packet.body
    if len(body) != body_size:
        raise ValueError(
            f"{format_name} Lap Data v1 body must be {body_size} bytes, got {len(body)}"
        )

    cars = tuple(
        _decode_car(_LAP_DATA_V1_CAR.unpack_from(body, index * _LAP_DATA_V1_CAR.size))
        for index in range(car_count)
    )
    return LapDataPacket(
        cars=cars,
        time_trial_pb_car_index=body[-2],
        time_trial_rival_car_index=body[-1],
    )


def _decode_car(fields: tuple[object, ...]) -> CarLapData:
    pit_lane_timer_active = int(fields[27])
    if pit_lane_timer_active not in (0, 1):
        raise ValueError(
            "Lap Data pit_lane_timer_active must be 0 or 1, "
            f"got {pit_lane_timer_active}"
        )
    pit_stop_should_serve_penalty = int(fields[30])
    if pit_stop_should_serve_penalty not in (0, 1):
        raise ValueError(
            "Lap Data pit_stop_should_serve_penalty must be 0 or 1, "
            f"got {pit_stop_should_serve_penalty}"
        )
    sector1_ms = int(fields[2]) + int(fields[3]) * 60_000
    sector2_ms = int(fields[4]) + int(fields[5]) * 60_000
    delta_front_ms = int(fields[6]) + int(fields[7]) * 60_000
    delta_leader_ms = int(fields[8]) + int(fields[9]) * 60_000
    flags = tuple(int(value) for value in fields[13:28])
    return CarLapData(
        last_lap_time_ms=int(fields[0]),
        current_lap_time_ms=int(fields[1]),
        sector1_time_ms=sector1_ms,
        sector2_time_ms=sector2_ms,
        delta_to_car_in_front_ms=delta_front_ms,
        delta_to_race_leader_ms=delta_leader_ms,
        lap_distance_m=float(fields[10]),
        total_distance_m=float(fields[11]),
        safety_car_delta_s=float(fields[12]),
        car_position=flags[0],
        current_lap_number=flags[1],
        pit_status_id=flags[2],
        pit_stop_count=flags[3],
        sector_id=flags[4],
        current_lap_invalid_id=flags[5],
        penalties_s=flags[6],
        total_warnings=flags[7],
        corner_cutting_warnings=flags[8],
        drive_through_penalties_remaining=flags[9],
        stop_go_penalties_remaining=flags[10],
        grid_position=flags[11],
        driver_status_id=flags[12],
        result_status_id=flags[13],
        pit_lane_timer_active=bool(pit_lane_timer_active),
        pit_lane_time_ms=int(fields[28]),
        pit_stop_timer_ms=int(fields[29]),
        pit_stop_should_serve_penalty=bool(pit_stop_should_serve_penalty),
        speed_trap_fastest_speed_kph=float(fields[31]),
        speed_trap_fastest_lap_number=int(fields[32]),
    )
