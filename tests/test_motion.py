from __future__ import annotations

import math
import struct

import pytest

from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.motion import CAR_COUNT, MotionDecoder
from tests.helpers import make_datagram


MOTION_CAR = struct.Struct("<6f6h6f")


def _motion_body(
    *,
    position: tuple[float, float, float] = (10.0, 20.0, 30.0),
    velocity: tuple[float, float, float] = (1.0, 2.0, 3.0),
    forward: tuple[int, int, int] = (32767, 0, 0),
    right: tuple[int, int, int] = (0, 32767, 0),
    g_force: tuple[float, float, float] = (0.1, -0.2, 1.0),
    yaw_pitch_roll: tuple[float, float, float] = (0.4, 0.5, 0.6),
) -> bytes:
    packed = MOTION_CAR.pack(
        *position,
        *velocity,
        *forward,
        *right,
        *g_force,
        *yaw_pitch_roll,
    )
    return packed * CAR_COUNT


def test_motion_decoder_reads_packed_v1_vectors_and_signed_directions() -> None:
    packet = PacketDecoder().decode(
        make_datagram(packet_id=0, body=_motion_body(forward=(-32767, 0, 0)))
    )

    result = MotionDecoder().decode(packet)

    assert result.error is None
    assert result.motion is not None
    assert len(result.motion.cars) == 22
    player = result.motion.cars[0]
    assert player.world_position_m == (10.0, 20.0, 30.0)
    assert player.world_velocity_mps == (1.0, 2.0, 3.0)
    assert player.world_forward == (-1.0, 0.0, 0.0)
    assert player.world_right == (0.0, 1.0, 0.0)
    assert player.g_force == pytest.approx((0.1, -0.2, 1.0))
    assert (player.yaw_rad, player.pitch_rad, player.roll_rad) == pytest.approx(
        (0.4, 0.5, 0.6)
    )
    assert player.validation_flags == ()


@pytest.mark.parametrize("packet_version,packet_format", [(1, 2026), (2, 2025)])
def test_motion_decoder_rejects_unsupported_format_or_version(
    packet_version: int, packet_format: int
) -> None:
    packet = PacketDecoder().decode(
        make_datagram(
            packet_id=0,
            packet_version=packet_version,
            packet_format=packet_format,
            body=_motion_body(),
        )
    )

    result = MotionDecoder().decode(packet)

    assert result.motion is None
    assert result.error == (
        f"unsupported motion packet adapter for format {packet_format}, version {packet_version}"
    )


def test_motion_decoder_rejects_wrong_body_size() -> None:
    packet = PacketDecoder().decode(make_datagram(packet_id=0, body=b"short"))

    result = MotionDecoder().decode(packet)

    assert result.motion is None
    assert result.error == "F1 25 Motion v1 body must be 1320 bytes, got 5"


def test_motion_decoder_nulls_invalid_vectors_without_discarding_valid_groups() -> None:
    body = _motion_body(
        position=(math.nan, 20.0, 30.0),
        forward=(0, 0, 0),
        right=(0, 32767, 0),
        g_force=(0.0, math.inf, 1.0),
        yaw_pitch_roll=(math.nan, 0.5, 0.6),
    )
    result = MotionDecoder().decode(PacketDecoder().decode(make_datagram(packet_id=0, body=body)))

    assert result.motion is not None
    player = result.motion.cars[0]
    assert player.world_position_m is None
    assert player.world_velocity_mps == (1.0, 2.0, 3.0)
    assert player.world_forward is None
    assert player.world_right == (0.0, 1.0, 0.0)
    assert player.g_force is None
    assert player.yaw_rad is None
    assert player.pitch_rad == pytest.approx(0.5)
    assert set(player.validation_flags) == {
        "invalid_motion_world_position",
        "invalid_motion_world_forward",
        "invalid_motion_g_force",
        "invalid_motion_yaw",
    }
