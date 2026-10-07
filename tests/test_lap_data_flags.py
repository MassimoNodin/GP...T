from __future__ import annotations

import struct

import pytest

from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.lap_data import LapDataDecoder
from tests.helpers import make_datagram


LAP_RECORD = struct.Struct("<IIHBHBHBHBfff15BHHBfB")
FORMAT_CAR_COUNTS = ((2025, 22), (2026, 24))
FLAG_OFFSETS = (
    ("pit_lane_timer_active", 46),
    ("pit_stop_should_serve_penalty", 51),
)


def _decode_with_flag(packet_format: int, flag_offset: int, value: int):
    car_count = dict(FORMAT_CAR_COUNTS)[packet_format]
    record = bytearray(LAP_RECORD.pack(*([0] * 33)))
    record[flag_offset] = value
    body = bytes(record) + bytes(LAP_RECORD.size * (car_count - 1)) + bytes((0, 255))
    raw = make_datagram(packet_format=packet_format, packet_id=2, body=body)
    packet = PacketDecoder().decode(raw)

    return raw, LapDataDecoder().decode(packet)


@pytest.mark.parametrize(("packet_format", "car_count"), FORMAT_CAR_COUNTS)
@pytest.mark.parametrize(("flag_name", "flag_offset"), FLAG_OFFSETS)
@pytest.mark.parametrize(("value", "expected"), ((0, False), (1, True)))
def test_lap_data_boolean_wire_flags_accept_only_canonical_values(
    packet_format: int,
    car_count: int,
    flag_name: str,
    flag_offset: int,
    value: int,
    expected: bool,
) -> None:
    raw, result = _decode_with_flag(packet_format, flag_offset, value)

    assert result.error is None
    assert result.lap_data is not None
    assert len(result.lap_data.cars) == car_count
    assert getattr(result.lap_data.cars[0], flag_name) is expected
    assert raw.payload[29 + flag_offset] == value


@pytest.mark.parametrize(("packet_format", "_car_count"), FORMAT_CAR_COUNTS)
@pytest.mark.parametrize(("flag_name", "flag_offset"), FLAG_OFFSETS)
@pytest.mark.parametrize("value", (2, 255))
def test_lap_data_boolean_wire_flags_reject_unsupported_values(
    packet_format: int,
    _car_count: int,
    flag_name: str,
    flag_offset: int,
    value: int,
) -> None:
    raw, result = _decode_with_flag(packet_format, flag_offset, value)

    assert raw.payload[29 + flag_offset] == value
    assert result.lap_data is None
    assert result.error is not None
    assert flag_name in result.error
    assert "0 or 1" in result.error
