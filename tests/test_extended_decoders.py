from __future__ import annotations

import struct
import math
import pytest

from f1_engineer.udp.car_telemetry import CarTelemetryDecoder
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.participants import ParticipantsDecoder
from tests.helpers import make_datagram


_CAR_TELEMETRY = struct.Struct("<HfffBbHBBH4H4B4BH4f4B")
_PARTICIPANT = struct.Struct("<7B32s2BH14B")


def test_car_telemetry_v1_decodes_all_records_and_normal_fields() -> None:
    fields = (
        200, 0.8, -0.25, 0.1, 20, 5, 12_000, 1, 90, 0x1234,
        400, 401, 402, 403,
        91, 92, 93, 94,
        101, 102, 103, 104,
        95,
        23.1, 23.2, 23.3, 23.4,
        0, 1, 2, 3,
    )
    body = b"".join(_CAR_TELEMETRY.pack(*fields) for _ in range(22)) + bytes((2, 255, 6))
    packet = PacketDecoder().decode(
        make_datagram(packet_id=6, body=body)
    )

    result = CarTelemetryDecoder().decode(packet)

    assert result.error is None
    assert result.telemetry is not None
    assert len(result.telemetry.cars) == 22
    assert result.telemetry.cars[0].speed_kph == 200
    assert math.isclose(result.telemetry.cars[0].throttle, 0.8, rel_tol=1e-6)
    assert result.telemetry.cars[0].steering == -0.25
    assert result.telemetry.cars[0].brake_temperature_c == (400, 401, 402, 403)
    assert result.telemetry.cars[0].tyre_pressure_psi == pytest.approx((23.1, 23.2, 23.3, 23.4))
    assert result.telemetry.mfd_panel_index == 2
    assert result.telemetry.mfd_panel_index_secondary_player == 255
    assert result.telemetry.suggested_gear == 6


def test_participants_v1_decodes_all_records_and_utf8_name() -> None:
    records = []
    for index in range(22):
        name = b"Driver One" if index == 0 else b""
        fields = (0, 7, 0, 3, 1, 44, 8, name.ljust(32, b"\0"), 1, 1, 99, 0, 3) + (0,) * 12
        records.append(_PARTICIPANT.pack(*fields))
    packet = PacketDecoder().decode(
        make_datagram(packet_id=4, body=bytes((1,)) + b"".join(records))
    )

    result = ParticipantsDecoder().decode(packet)

    assert result.error is None
    assert result.participants is not None
    assert result.participants.active_car_count == 1
    assert len(result.participants.cars) == 22
    assert result.participants.cars[0].name == "Driver One"
    assert result.participants.cars[0].team_id == 3
    assert result.participants.cars[0].colours_rgb == ((0, 0, 0),) * 4


def test_extended_packet_decoders_reject_wrong_body_lengths() -> None:
    telemetry_packet = PacketDecoder().decode(make_datagram(packet_id=6, body=b"short"))
    participant_packet = PacketDecoder().decode(make_datagram(packet_id=4, body=b"short"))

    assert "must be 1323 bytes" in CarTelemetryDecoder().decode(telemetry_packet).error
    assert "must be 1255 bytes" in ParticipantsDecoder().decode(participant_packet).error
