from __future__ import annotations

import struct

import pytest

from f1_engineer.errors import ProtocolError
from f1_engineer.cli import _packet_summary
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.header import HEADER_SIZE, parse_header
from f1_engineer.udp.models import PacketFormat, PacketId
from tests.helpers import make_datagram


def test_common_header_is_29_bytes_and_packet_body_is_preserved() -> None:
    raw = make_datagram(packet_format=2026, packet_id=16, body=b"telemetry-body")

    packet = PacketDecoder().decode(raw)

    assert HEADER_SIZE == 29
    assert packet.packet_format is PacketFormat.SEASON_PACK_2026
    assert packet.packet_kind is PacketId.CAR_TELEMETRY_2
    assert packet.header.session_uid == 10
    assert packet.body == b"telemetry-body"


def test_motion_packet_id_zero_has_a_name_in_cli_summaries() -> None:
    packet = PacketDecoder().decode(make_datagram(packet_id=0))

    assert _packet_summary(packet)["packet_name"] == "motion"


def test_format_specific_packet_id_is_unknown_in_original_format() -> None:
    raw = make_datagram(packet_format=2025, packet_id=16)

    packet = PacketDecoder().decode(raw)

    assert packet.packet_format is PacketFormat.F1_25
    assert packet.packet_kind is None


def test_unknown_packet_format_is_rejected() -> None:
    raw = make_datagram(packet_format=2024)

    with pytest.raises(ProtocolError, match="unsupported packet format 2024"):
        PacketDecoder().decode(raw)


def test_short_packet_is_rejected_before_unpacking() -> None:
    with pytest.raises(ProtocolError, match="requires 29"):
        parse_header(b"too short")


def test_non_finite_session_time_is_rejected() -> None:
    raw = make_datagram()
    fields = list(struct.unpack("<HBBBBBQfIIBB", raw.payload[:HEADER_SIZE]))
    fields[7] = float("nan")
    packet = struct.pack("<HBBBBBQfIIBB", *fields) + raw.payload[HEADER_SIZE:]

    with pytest.raises(ProtocolError, match="non-finite"):
        parse_header(packet)
