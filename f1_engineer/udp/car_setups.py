from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Callable

from .models import DecodedPacket, PacketFormat, PacketId


F1_25_CAR_COUNT = 22
SEASON_PACK_2026_CAR_COUNT = 24
CAR_SETUP_V1_RECORD_SIZE = 50
F1_25_CAR_SETUPS_V1_BODY_SIZE = F1_25_CAR_COUNT * CAR_SETUP_V1_RECORD_SIZE + 4
SEASON_PACK_2026_CAR_SETUPS_V1_BODY_SIZE = (
    SEASON_PACK_2026_CAR_COUNT * CAR_SETUP_V1_RECORD_SIZE + 4
)
_CAR_SETUP_V1_RECORD = struct.Struct("<4B4f9B4fBf")
_NEXT_FRONT_WING_VALUE = struct.Struct("<f")


@dataclass(frozen=True, slots=True)
class CarSetupData:
    front_wing: int
    rear_wing: int
    on_throttle_differential: int
    off_throttle_differential: int
    front_camber: float | None
    rear_camber: float | None
    front_toe: float | None
    rear_toe: float | None
    front_suspension: int
    rear_suspension: int
    front_anti_roll_bar: int
    rear_anti_roll_bar: int
    front_suspension_height: int
    rear_suspension_height: int
    brake_pressure_percent: int
    brake_bias_percent: int
    engine_braking_percent: int
    rear_left_tyre_pressure_psi: float | None
    rear_right_tyre_pressure_psi: float | None
    front_left_tyre_pressure_psi: float | None
    front_right_tyre_pressure_psi: float | None
    ballast: int
    fuel_load: float | None
    invalid_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CarSetupsPacket:
    cars: tuple[CarSetupData, ...]
    next_front_wing_value: float | None
    invalid_packet_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CarSetupsDecodeResult:
    setups: CarSetupsPacket | None = None
    error: str | None = None


CarSetupsParser = Callable[[DecodedPacket], CarSetupsPacket]


class CarSetupsDecoder:
    """Decode the versioned fixed-size Car Setups packet for supported formats."""

    def __init__(self) -> None:
        self._parsers: dict[
            tuple[PacketFormat, PacketId, int], CarSetupsParser
        ] = {
            (PacketFormat.F1_25, PacketId.CAR_SETUPS, 1): _decode_f1_25_v1,
            (PacketFormat.SEASON_PACK_2026, PacketId.CAR_SETUPS, 1): (
                _decode_season_pack_2026_v1
            ),
        }

    def supports(
        self, packet_format: PacketFormat, packet_id: PacketId, packet_version: int
    ) -> bool:
        return (packet_format, packet_id, packet_version) in self._parsers

    def decode(self, packet: DecodedPacket) -> CarSetupsDecodeResult:
        if packet.packet_kind is not PacketId.CAR_SETUPS:
            return CarSetupsDecodeResult()
        parser = self._parsers.get(
            (packet.packet_format, PacketId.CAR_SETUPS, packet.header.packet_version)
        )
        if parser is None:
            return CarSetupsDecodeResult(
                error=(
                    "unsupported Car Setups packet adapter for "
                    f"format {packet.packet_format.value}, version "
                    f"{packet.header.packet_version}"
                )
            )
        try:
            return CarSetupsDecodeResult(setups=parser(packet))
        except ValueError as exc:
            return CarSetupsDecodeResult(error=str(exc))


def _decode_f1_25_v1(packet: DecodedPacket) -> CarSetupsPacket:
    return _decode_records(
        packet,
        car_count=F1_25_CAR_COUNT,
        body_size=F1_25_CAR_SETUPS_V1_BODY_SIZE,
        format_name="F1 25",
    )


def _decode_season_pack_2026_v1(packet: DecodedPacket) -> CarSetupsPacket:
    return _decode_records(
        packet,
        car_count=SEASON_PACK_2026_CAR_COUNT,
        body_size=SEASON_PACK_2026_CAR_SETUPS_V1_BODY_SIZE,
        format_name="2026 Season Pack",
    )


def _finite_or_none(value: float, field: str, invalid_fields: list[str]) -> float | None:
    if not math.isfinite(value):
        invalid_fields.append(field)
        return None
    return value


def _decode_records(
    packet: DecodedPacket,
    *,
    car_count: int,
    body_size: int,
    format_name: str,
) -> CarSetupsPacket:
    body = packet.body
    if len(body) != body_size:
        raise ValueError(
            f"{format_name} Car Setups v1 body must be {body_size} bytes, "
            f"got {len(body)}"
        )

    cars: list[CarSetupData] = []
    float_fields = (
        "front_camber",
        "rear_camber",
        "front_toe",
        "rear_toe",
        "rear_left_tyre_pressure_psi",
        "rear_right_tyre_pressure_psi",
        "front_left_tyre_pressure_psi",
        "front_right_tyre_pressure_psi",
        "fuel_load",
    )
    uint_fields = (
        "front_suspension",
        "rear_suspension",
        "front_anti_roll_bar",
        "rear_anti_roll_bar",
        "front_suspension_height",
        "rear_suspension_height",
        "brake_pressure_percent",
        "brake_bias_percent",
        "engine_braking_percent",
    )
    for index in range(car_count):
        fields = _CAR_SETUP_V1_RECORD.unpack_from(
            body, index * CAR_SETUP_V1_RECORD_SIZE
        )
        invalid_fields: list[str] = []
        floats = tuple(
            _finite_or_none(float(value), field, invalid_fields)
            for field, value in zip(float_fields[:4], fields[4:8], strict=True)
        )
        trailing_floats = tuple(
            _finite_or_none(float(value), field, invalid_fields)
            for field, value in zip(float_fields[4:8], fields[17:21], strict=True)
        )
        fuel_load = _finite_or_none(
            float(fields[22]), "fuel_load", invalid_fields
        )
        cars.append(
            CarSetupData(
                front_wing=int(fields[0]),
                rear_wing=int(fields[1]),
                on_throttle_differential=int(fields[2]),
                off_throttle_differential=int(fields[3]),
                front_camber=floats[0],
                rear_camber=floats[1],
                front_toe=floats[2],
                rear_toe=floats[3],
                **{
                    name: int(value)
                    for name, value in zip(uint_fields, fields[8:17], strict=True)
                },
                rear_left_tyre_pressure_psi=trailing_floats[0],
                rear_right_tyre_pressure_psi=trailing_floats[1],
                front_left_tyre_pressure_psi=trailing_floats[2],
                front_right_tyre_pressure_psi=trailing_floats[3],
                ballast=int(fields[21]),
                fuel_load=fuel_load,
                invalid_fields=tuple(invalid_fields),
            )
        )

    next_front_wing_raw = _NEXT_FRONT_WING_VALUE.unpack_from(
        body, car_count * CAR_SETUP_V1_RECORD_SIZE
    )[0]
    invalid_packet_fields: list[str] = []
    next_front_wing_value = _finite_or_none(
        next_front_wing_raw, "next_front_wing_value", invalid_packet_fields
    )
    return CarSetupsPacket(
        cars=tuple(cars),
        next_front_wing_value=next_front_wing_value,
        invalid_packet_fields=tuple(invalid_packet_fields),
    )
