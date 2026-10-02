from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable

from .models import DecodedPacket, PacketFormat, PacketId


_F1_25_CAR_COUNT = 22
_SEASON_PACK_2026_CAR_COUNT = 24
_F1_25_CAR_TELEMETRY_V1_CAR = struct.Struct("<HfffBbHBBH4H4B4BH4f4B")
_SEASON_PACK_2026_CAR_TELEMETRY_V1_CAR = struct.Struct(
    "<HfffBbHBBH4H4B4BB4f4B"
)
_F1_25_CAR_TELEMETRY_V1_BODY_SIZE = (
    _F1_25_CAR_COUNT * _F1_25_CAR_TELEMETRY_V1_CAR.size + 3
)
_SEASON_PACK_2026_CAR_TELEMETRY_V1_BODY_SIZE = (
    _SEASON_PACK_2026_CAR_COUNT * _SEASON_PACK_2026_CAR_TELEMETRY_V1_CAR.size + 3
)


@dataclass(frozen=True, slots=True)
class CarTelemetryData:
    speed_kph: int
    throttle: float
    steering: float
    brake: float
    clutch: int
    gear: int
    engine_rpm: int
    drs: int
    rev_lights_percent: int
    rev_lights_bit_value: int
    brake_temperature_c: tuple[int, int, int, int]
    tyre_surface_temperature_c: tuple[int, int, int, int]
    tyre_inner_temperature_c: tuple[int, int, int, int]
    engine_temperature_c: int
    tyre_pressure_psi: tuple[float, float, float, float]
    surface_type: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class CarTelemetryPacket:
    cars: tuple[CarTelemetryData, ...]
    mfd_panel_index: int
    mfd_panel_index_secondary_player: int
    suggested_gear: int


@dataclass(frozen=True, slots=True)
class CarTelemetryDecodeResult:
    telemetry: CarTelemetryPacket | None = None
    error: str | None = None


CarTelemetryParser = Callable[[DecodedPacket], CarTelemetryPacket]


class CarTelemetryDecoder:
    """Decode explicitly supported versioned Car Telemetry bodies."""

    def __init__(self) -> None:
        self._parsers: dict[tuple[PacketFormat, PacketId, int], CarTelemetryParser] = {
            (PacketFormat.F1_25, PacketId.CAR_TELEMETRY, 1): _decode_f1_25_v1,
            (PacketFormat.SEASON_PACK_2026, PacketId.CAR_TELEMETRY, 1): (
                _decode_season_pack_2026_v1
            ),
        }

    def supports(
        self, packet_format: PacketFormat, packet_id: PacketId, packet_version: int
    ) -> bool:
        return (packet_format, packet_id, packet_version) in self._parsers

    def decode(self, packet: DecodedPacket) -> CarTelemetryDecodeResult:
        if packet.packet_kind is not PacketId.CAR_TELEMETRY:
            return CarTelemetryDecodeResult()
        parser = self._parsers.get(
            (packet.packet_format, PacketId.CAR_TELEMETRY, packet.header.packet_version)
        )
        if parser is None:
            return CarTelemetryDecodeResult(
                error=(
                    "unsupported car telemetry packet adapter for "
                    f"format {packet.packet_format.value}, version "
                    f"{packet.header.packet_version}"
                )
            )
        try:
            return CarTelemetryDecodeResult(telemetry=parser(packet))
        except ValueError as exc:
            return CarTelemetryDecodeResult(error=str(exc))


def _decode_f1_25_v1(packet: DecodedPacket) -> CarTelemetryPacket:
    return _decode_car_telemetry_v1(
        packet,
        car_count=_F1_25_CAR_COUNT,
        record=_F1_25_CAR_TELEMETRY_V1_CAR,
        body_size=_F1_25_CAR_TELEMETRY_V1_BODY_SIZE,
        format_name="F1 25",
    )


def _decode_season_pack_2026_v1(packet: DecodedPacket) -> CarTelemetryPacket:
    return _decode_car_telemetry_v1(
        packet,
        car_count=_SEASON_PACK_2026_CAR_COUNT,
        record=_SEASON_PACK_2026_CAR_TELEMETRY_V1_CAR,
        body_size=_SEASON_PACK_2026_CAR_TELEMETRY_V1_BODY_SIZE,
        format_name="2026 Season Pack",
    )


def _decode_car_telemetry_v1(
    packet: DecodedPacket,
    *,
    car_count: int,
    record: struct.Struct,
    body_size: int,
    format_name: str,
) -> CarTelemetryPacket:
    body = packet.body
    if len(body) != body_size:
        raise ValueError(
            f"{format_name} Car Telemetry v1 body must be {body_size} bytes, got {len(body)}"
        )
    cars = tuple(
        _decode_car(record.unpack_from(body, index * record.size))
        for index in range(car_count)
    )
    trailer = body[car_count * record.size :]
    return CarTelemetryPacket(cars, trailer[0], trailer[1], struct.unpack("<b", trailer[2:])[0])


def _decode_car(fields: tuple[object, ...]) -> CarTelemetryData:
    offset = 0
    speed = int(fields[offset]); offset += 1
    throttle = float(fields[offset]); offset += 1
    steering = float(fields[offset]); offset += 1
    brake = float(fields[offset]); offset += 1
    clutch = int(fields[offset]); offset += 1
    gear = int(fields[offset]); offset += 1
    rpm = int(fields[offset]); offset += 1
    drs = int(fields[offset]); offset += 1
    rev_percent = int(fields[offset]); offset += 1
    rev_bits = int(fields[offset]); offset += 1
    brake_temps = tuple(int(value) for value in fields[offset : offset + 4]); offset += 4
    surface_temps = tuple(int(value) for value in fields[offset : offset + 4]); offset += 4
    inner_temps = tuple(int(value) for value in fields[offset : offset + 4]); offset += 4
    engine_temp = int(fields[offset]); offset += 1
    pressures = tuple(float(value) for value in fields[offset : offset + 4]); offset += 4
    surface_types = tuple(int(value) for value in fields[offset : offset + 4])
    return CarTelemetryData(
        speed, throttle, steering, brake, clutch, gear, rpm, drs, rev_percent,
        rev_bits, brake_temps, surface_temps, inner_temps, engine_temp,
        pressures, surface_types,
    )
