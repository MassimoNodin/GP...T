from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Callable

from .models import DecodedPacket, PacketFormat, PacketId


CAR_COUNT = 22
SEASON_PACK_2026_CAR_COUNT = 24
_MOTION_CAR_V1 = struct.Struct("<6f6h6f")
_F1_25_MOTION_V1_BODY_SIZE = CAR_COUNT * _MOTION_CAR_V1.size
_SEASON_PACK_2026_MOTION_CAR_V1 = struct.Struct("<6f9h3f")
_SEASON_PACK_2026_MOTION_V1_BODY_SIZE = (
    SEASON_PACK_2026_CAR_COUNT * _SEASON_PACK_2026_MOTION_CAR_V1.size
)


@dataclass(frozen=True, slots=True)
class CarMotionData:
    world_position_m: tuple[float, float, float] | None
    world_velocity_mps: tuple[float, float, float] | None
    world_forward: tuple[float, float, float] | None
    world_right: tuple[float, float, float] | None
    g_force: tuple[float, float, float] | None
    yaw_rad: float | None
    pitch_rad: float | None
    roll_rad: float | None
    validation_flags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MotionPacket:
    cars: tuple[CarMotionData, ...]


@dataclass(frozen=True, slots=True)
class MotionDecodeResult:
    motion: MotionPacket | None = None
    error: str | None = None


class MotionDecoder:
    """Decode explicitly supported versioned Motion packet bodies."""

    def __init__(self) -> None:
        self._parsers: dict[
            tuple[PacketFormat, PacketId, int], Callable[[DecodedPacket], MotionPacket]
        ] = {
            (PacketFormat.F1_25, PacketId.MOTION, 1): _decode_f1_25_v1,
            (PacketFormat.SEASON_PACK_2026, PacketId.MOTION, 1): (
                _decode_season_pack_2026_v1
            ),
        }

    def decode(self, packet: DecodedPacket) -> MotionDecodeResult:
        if packet.packet_kind is not PacketId.MOTION:
            return MotionDecodeResult()
        parser = self._parsers.get(
            (packet.packet_format, PacketId.MOTION, packet.header.packet_version)
        )
        if parser is None:
            return MotionDecodeResult(
                error=(
                    "unsupported motion packet adapter for format "
                    f"{packet.packet_format.value}, version {packet.header.packet_version}"
                )
            )
        try:
            return MotionDecodeResult(motion=parser(packet))
        except ValueError as exc:
            return MotionDecodeResult(error=str(exc))


def _decode_f1_25_v1(packet: DecodedPacket) -> MotionPacket:
    return _decode_records(
        packet,
        record=_MOTION_CAR_V1,
        car_count=CAR_COUNT,
        body_size=_F1_25_MOTION_V1_BODY_SIZE,
        format_name="F1 25",
        quantized_g_force=False,
    )


def _decode_season_pack_2026_v1(packet: DecodedPacket) -> MotionPacket:
    return _decode_records(
        packet,
        record=_SEASON_PACK_2026_MOTION_CAR_V1,
        car_count=SEASON_PACK_2026_CAR_COUNT,
        body_size=_SEASON_PACK_2026_MOTION_V1_BODY_SIZE,
        format_name="2026 Season Pack",
        quantized_g_force=True,
    )


def _decode_records(
    packet: DecodedPacket,
    *,
    record: struct.Struct,
    car_count: int,
    body_size: int,
    format_name: str,
    quantized_g_force: bool,
) -> MotionPacket:
    if len(packet.body) != body_size:
        raise ValueError(
            f"{format_name} Motion v1 body must be {body_size} bytes, got {len(packet.body)}"
        )
    cars = tuple(
        _decode_car(
            record.unpack_from(packet.body, index * record.size),
            g_force_scale=0.001 if quantized_g_force else 1.0,
        )
        for index in range(car_count)
    )
    return MotionPacket(cars=cars)


def _decode_car(
    fields: tuple[float | int, ...], *, g_force_scale: float = 1.0
) -> CarMotionData:
    flags: list[str] = []
    position = _finite_vector(fields[0:3], "world_position", flags)
    velocity = _finite_vector(fields[3:6], "world_velocity", flags)
    forward = _direction_vector(fields[6:9], "world_forward", flags)
    right = _direction_vector(fields[9:12], "world_right", flags)
    g_force = _finite_vector(
        tuple(float(value) * g_force_scale for value in fields[12:15]),
        "g_force",
        flags,
    )
    yaw = _finite_scalar(fields[15], "yaw", flags)
    pitch = _finite_scalar(fields[16], "pitch", flags)
    roll = _finite_scalar(fields[17], "roll", flags)
    return CarMotionData(
        world_position_m=position,
        world_velocity_mps=velocity,
        world_forward=forward,
        world_right=right,
        g_force=g_force,
        yaw_rad=yaw,
        pitch_rad=pitch,
        roll_rad=roll,
        validation_flags=tuple(flags),
    )


def _finite_vector(
    values: tuple[float | int, ...], name: str, flags: list[str]
) -> tuple[float, float, float] | None:
    vector = tuple(float(value) for value in values)
    if len(vector) != 3 or not all(math.isfinite(value) for value in vector):
        flags.append(f"invalid_motion_{name}")
        return None
    return vector  # type: ignore[return-value]


def _direction_vector(
    values: tuple[float | int, ...], name: str, flags: list[str]
) -> tuple[float, float, float] | None:
    vector = tuple(float(value) / 32767.0 for value in values)
    norm = math.sqrt(sum(value * value for value in vector))
    if len(vector) != 3 or not all(math.isfinite(value) for value in vector):
        flags.append(f"invalid_motion_{name}")
        return None
    if not 0.9 <= norm <= 1.1:
        flags.append(f"invalid_motion_{name}")
        return None
    return vector  # type: ignore[return-value]


def _finite_scalar(value: float, name: str, flags: list[str]) -> float | None:
    if not math.isfinite(value):
        flags.append(f"invalid_motion_{name}")
        return None
    return value
