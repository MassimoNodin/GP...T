from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable

from .models import DecodedPacket, PacketFormat, PacketId


F1_25_CAR_COUNT = 22
SEASON_PACK_2026_CAR_COUNT = 24
_CAR_DAMAGE_V1_CAR = struct.Struct("<4f30B")
_F1_25_CAR_DAMAGE_V1_BODY_SIZE = F1_25_CAR_COUNT * _CAR_DAMAGE_V1_CAR.size
_SEASON_PACK_2026_CAR_DAMAGE_V1_BODY_SIZE = (
    SEASON_PACK_2026_CAR_COUNT * _CAR_DAMAGE_V1_CAR.size
)


@dataclass(frozen=True, slots=True)
class CarDamageData:
    tyres_wear_percent: tuple[float, float, float, float]
    tyres_damage_percent: tuple[int, int, int, int]
    brakes_damage_percent: tuple[int, int, int, int]
    tyre_blisters_percent: tuple[int, int, int, int]
    front_left_wing_damage_percent: int
    front_right_wing_damage_percent: int
    rear_wing_damage_percent: int
    floor_damage_percent: int
    diffuser_damage_percent: int
    sidepod_damage_percent: int
    drs_fault: int
    ers_fault: int
    gearbox_damage_percent: int
    engine_damage_percent: int
    engine_mguh_wear_percent: int
    engine_es_wear_percent: int
    engine_ce_wear_percent: int
    engine_ice_wear_percent: int
    engine_mguk_wear_percent: int
    engine_tc_wear_percent: int
    engine_blown: int
    engine_seized: int
    raw_record: bytes


@dataclass(frozen=True, slots=True)
class CarDamagePacket:
    cars: tuple[CarDamageData, ...]


@dataclass(frozen=True, slots=True)
class CarDamageDecodeResult:
    car_damage: CarDamagePacket | None = None
    error: str | None = None


CarDamageParser = Callable[[DecodedPacket], CarDamagePacket]


class CarDamageDecoder:
    """Decode explicitly supported versioned Car Damage packet bodies."""

    def __init__(self) -> None:
        self._parsers: dict[tuple[PacketFormat, PacketId, int], CarDamageParser] = {
            (PacketFormat.F1_25, PacketId.CAR_DAMAGE, 1): _decode_f1_25_v1,
            (PacketFormat.SEASON_PACK_2026, PacketId.CAR_DAMAGE, 1): (
                _decode_season_pack_2026_v1
            ),
        }

    def supports(
        self, packet_format: PacketFormat, packet_id: PacketId, packet_version: int
    ) -> bool:
        return (packet_format, packet_id, packet_version) in self._parsers

    def decode(self, packet: DecodedPacket) -> CarDamageDecodeResult:
        if packet.packet_kind is not PacketId.CAR_DAMAGE:
            return CarDamageDecodeResult()
        parser = self._parsers.get(
            (packet.packet_format, PacketId.CAR_DAMAGE, packet.header.packet_version)
        )
        if parser is None:
            return CarDamageDecodeResult(
                error=(
                    "unsupported car damage packet adapter for format "
                    f"{packet.packet_format.value}, version {packet.header.packet_version}"
                )
            )
        try:
            return CarDamageDecodeResult(car_damage=parser(packet))
        except ValueError as exc:
            return CarDamageDecodeResult(error=str(exc))


def _decode_f1_25_v1(packet: DecodedPacket) -> CarDamagePacket:
    return _decode_records(
        packet,
        car_count=F1_25_CAR_COUNT,
        body_size=_F1_25_CAR_DAMAGE_V1_BODY_SIZE,
        format_name="F1 25",
    )


def _decode_season_pack_2026_v1(packet: DecodedPacket) -> CarDamagePacket:
    return _decode_records(
        packet,
        car_count=SEASON_PACK_2026_CAR_COUNT,
        body_size=_SEASON_PACK_2026_CAR_DAMAGE_V1_BODY_SIZE,
        format_name="2026 Season Pack",
    )


def _decode_records(
    packet: DecodedPacket, *, car_count: int, body_size: int, format_name: str
) -> CarDamagePacket:
    if len(packet.body) != body_size:
        raise ValueError(
            f"{format_name} Car Damage v1 body must be {body_size} bytes, "
            f"got {len(packet.body)}"
        )
    cars = tuple(
        _decode_car(
            _CAR_DAMAGE_V1_CAR.unpack_from(
                packet.body, index * _CAR_DAMAGE_V1_CAR.size
            ),
            packet.body[
                index * _CAR_DAMAGE_V1_CAR.size : (index + 1) * _CAR_DAMAGE_V1_CAR.size
            ],
        )
        for index in range(car_count)
    )
    return CarDamagePacket(cars=cars)


def _decode_car(fields: tuple[int | float, ...], raw_record: bytes) -> CarDamageData:
    return CarDamageData(
        tyres_wear_percent=tuple(float(value) for value in fields[0:4]),  # type: ignore[arg-type]
        tyres_damage_percent=tuple(int(value) for value in fields[4:8]),  # type: ignore[arg-type]
        brakes_damage_percent=tuple(int(value) for value in fields[8:12]),  # type: ignore[arg-type]
        tyre_blisters_percent=tuple(int(value) for value in fields[12:16]),  # type: ignore[arg-type]
        front_left_wing_damage_percent=int(fields[16]),
        front_right_wing_damage_percent=int(fields[17]),
        rear_wing_damage_percent=int(fields[18]),
        floor_damage_percent=int(fields[19]),
        diffuser_damage_percent=int(fields[20]),
        sidepod_damage_percent=int(fields[21]),
        drs_fault=int(fields[22]),
        ers_fault=int(fields[23]),
        gearbox_damage_percent=int(fields[24]),
        engine_damage_percent=int(fields[25]),
        engine_mguh_wear_percent=int(fields[26]),
        engine_es_wear_percent=int(fields[27]),
        engine_ce_wear_percent=int(fields[28]),
        engine_ice_wear_percent=int(fields[29]),
        engine_mguk_wear_percent=int(fields[30]),
        engine_tc_wear_percent=int(fields[31]),
        engine_blown=int(fields[32]),
        engine_seized=int(fields[33]),
        raw_record=raw_record,
    )
