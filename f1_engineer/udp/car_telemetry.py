from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable

from .models import DecodedPacket, PacketFormat, PacketId


CAR_COUNT = 22
_CAR_TELEMETRY_V1_CAR = struct.Struct("<HfffBbHBBH4H4B4BH4f4B")
_F1_25_CAR_TELEMETRY_V1_BODY_SIZE = CAR_COUNT * _CAR_TELEMETRY_V1_CAR.size + 3


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
        }

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
    body = packet.body
    if len(body) != _F1_25_CAR_TELEMETRY_V1_BODY_SIZE:
        raise ValueError(
            "F1 25 Car Telemetry v1 body must be "
            f"{_F1_25_CAR_TELEMETRY_V1_BODY_SIZE} bytes, got {len(body)}"
        )
    cars = tuple(
        _decode_car(_CAR_TELEMETRY_V1_CAR.unpack_from(body, index * _CAR_TELEMETRY_V1_CAR.size))
        for index in range(CAR_COUNT)
    )
    trailer = body[CAR_COUNT * _CAR_TELEMETRY_V1_CAR.size :]
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
