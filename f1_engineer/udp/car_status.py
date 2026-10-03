from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable

from .models import DecodedPacket, PacketFormat, PacketId


CAR_COUNT = 22
SEASON_PACK_2026_CAR_COUNT = 24
_F1_25_CAR_STATUS_V1_CAR = struct.Struct("<5B3f2H2BH3Bb3fB3fB")
_SEASON_PACK_2026_CAR_STATUS_V1_CAR = struct.Struct("<5B3f2H2BH3Bb3fB4fB")
_F1_25_CAR_STATUS_V1_BODY_SIZE = CAR_COUNT * _F1_25_CAR_STATUS_V1_CAR.size
_SEASON_PACK_2026_CAR_STATUS_V1_BODY_SIZE = (
    SEASON_PACK_2026_CAR_COUNT * _SEASON_PACK_2026_CAR_STATUS_V1_CAR.size
)


@dataclass(frozen=True, slots=True)
class CarStatusData:
    traction_control: int
    anti_lock_brakes: int
    fuel_mix: int
    front_brake_bias_percent: int
    pit_limiter_active: int
    fuel_in_tank_reported: float
    fuel_capacity_reported: float
    fuel_remaining_laps: float
    max_rpm: int
    idle_rpm: int
    max_gears: int
    drs_allowed: int
    drs_activation_distance_m: int
    actual_tyre_compound: int
    visual_tyre_compound: int
    tyre_age_laps: int
    vehicle_fia_flag: int
    engine_power: float
    mgu_k_power: float
    ers_store_energy: float
    ers_deploy_mode: int
    ers_harvested_this_lap_mgu_k: float
    ers_harvested_this_lap_mgu_h: float
    ers_harvest_limit_per_lap: float | None
    ers_deployed_this_lap: float
    network_paused: int


@dataclass(frozen=True, slots=True)
class CarStatusPacket:
    cars: tuple[CarStatusData, ...]


@dataclass(frozen=True, slots=True)
class CarStatusDecodeResult:
    car_status: CarStatusPacket | None = None
    error: str | None = None


CarStatusParser = Callable[[DecodedPacket], CarStatusPacket]


class CarStatusDecoder:
    """Decode explicitly supported versioned Car Status packet bodies."""

    def __init__(self) -> None:
        self._parsers: dict[tuple[PacketFormat, PacketId, int], CarStatusParser] = {
            (PacketFormat.F1_25, PacketId.CAR_STATUS, 1): _decode_f1_25_v1,
            (PacketFormat.SEASON_PACK_2026, PacketId.CAR_STATUS, 1): (
                _decode_season_pack_2026_v1
            ),
        }

    def decode(self, packet: DecodedPacket) -> CarStatusDecodeResult:
        if packet.packet_kind is not PacketId.CAR_STATUS:
            return CarStatusDecodeResult()
        parser = self._parsers.get(
            (packet.packet_format, PacketId.CAR_STATUS, packet.header.packet_version)
        )
        if parser is None:
            return CarStatusDecodeResult(
                error=(
                    "unsupported car status packet adapter for format "
                    f"{packet.packet_format.value}, version {packet.header.packet_version}"
                )
            )
        try:
            return CarStatusDecodeResult(car_status=parser(packet))
        except ValueError as exc:
            return CarStatusDecodeResult(error=str(exc))


def _decode_f1_25_v1(packet: DecodedPacket) -> CarStatusPacket:
    return _decode_records(
        packet,
        record=_F1_25_CAR_STATUS_V1_CAR,
        car_count=CAR_COUNT,
        body_size=_F1_25_CAR_STATUS_V1_BODY_SIZE,
        format_name="F1 25",
        has_2026_harvest_limit=False,
    )


def _decode_season_pack_2026_v1(packet: DecodedPacket) -> CarStatusPacket:
    return _decode_records(
        packet,
        record=_SEASON_PACK_2026_CAR_STATUS_V1_CAR,
        car_count=SEASON_PACK_2026_CAR_COUNT,
        body_size=_SEASON_PACK_2026_CAR_STATUS_V1_BODY_SIZE,
        format_name="2026 Season Pack",
        has_2026_harvest_limit=True,
    )


def _decode_records(
    packet: DecodedPacket,
    *,
    record: struct.Struct,
    car_count: int,
    body_size: int,
    format_name: str,
    has_2026_harvest_limit: bool,
) -> CarStatusPacket:
    if len(packet.body) != body_size:
        raise ValueError(
            f"{format_name} Car Status v1 body must be {body_size} bytes, "
            f"got {len(packet.body)}"
        )
    cars = tuple(
        _decode_car(
            record.unpack_from(packet.body, index * record.size),
            has_2026_harvest_limit=has_2026_harvest_limit,
        )
        for index in range(car_count)
    )
    return CarStatusPacket(cars=cars)


def _decode_car(
    fields: tuple[int | float, ...], *, has_2026_harvest_limit: bool
) -> CarStatusData:
    # Both layouts share fields through harvested-MGU-H. The 2026 format inserts
    # the harvest limit before deployed-this-lap.
    if has_2026_harvest_limit:
        harvest_limit = float(fields[23])
        deployed = float(fields[24])
        paused = int(fields[25])
    else:
        harvest_limit = None
        deployed = float(fields[23])
        paused = int(fields[24])
    return CarStatusData(
        traction_control=int(fields[0]),
        anti_lock_brakes=int(fields[1]),
        fuel_mix=int(fields[2]),
        front_brake_bias_percent=int(fields[3]),
        pit_limiter_active=int(fields[4]),
        fuel_in_tank_reported=float(fields[5]),
        fuel_capacity_reported=float(fields[6]),
        fuel_remaining_laps=float(fields[7]),
        max_rpm=int(fields[8]),
        idle_rpm=int(fields[9]),
        max_gears=int(fields[10]),
        drs_allowed=int(fields[11]),
        drs_activation_distance_m=int(fields[12]),
        actual_tyre_compound=int(fields[13]),
        visual_tyre_compound=int(fields[14]),
        tyre_age_laps=int(fields[15]),
        vehicle_fia_flag=int(fields[16]),
        engine_power=float(fields[17]),
        mgu_k_power=float(fields[18]),
        ers_store_energy=float(fields[19]),
        ers_deploy_mode=int(fields[20]),
        ers_harvested_this_lap_mgu_k=float(fields[21]),
        ers_harvested_this_lap_mgu_h=float(fields[22]),
        ers_harvest_limit_per_lap=harvest_limit,
        ers_deployed_this_lap=deployed,
        network_paused=paused,
    )


