from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable

from .models import DecodedPacket, PacketFormat, PacketId


CAR_COUNT = 22
_PARTICIPANT_V1_RECORD_SIZE = 57
_F1_25_PARTICIPANTS_V1_BODY_SIZE = 1 + CAR_COUNT * _PARTICIPANT_V1_RECORD_SIZE
_PARTICIPANT_PREFIX = struct.Struct("<7B32s2BH14B")


@dataclass(frozen=True, slots=True)
class ParticipantData:
    ai_controlled: bool
    driver_id: int
    network_id: int
    team_id: int
    my_team: bool
    race_number: int
    nationality_id: int
    name: str
    your_telemetry: int
    show_online_names: bool
    tech_level: int
    platform_id: int
    num_colours: int
    colours_rgb: tuple[tuple[int, int, int], ...]


@dataclass(frozen=True, slots=True)
class ParticipantsPacket:
    active_car_count: int
    cars: tuple[ParticipantData, ...]


@dataclass(frozen=True, slots=True)
class ParticipantsDecodeResult:
    participants: ParticipantsPacket | None = None
    error: str | None = None


ParticipantsParser = Callable[[DecodedPacket], ParticipantsPacket]


class ParticipantsDecoder:
    def __init__(self) -> None:
        self._parsers: dict[tuple[PacketFormat, PacketId, int], ParticipantsParser] = {
            (PacketFormat.F1_25, PacketId.PARTICIPANTS, 1): _decode_f1_25_v1,
        }

    def decode(self, packet: DecodedPacket) -> ParticipantsDecodeResult:
        if packet.packet_kind is not PacketId.PARTICIPANTS:
            return ParticipantsDecodeResult()
        parser = self._parsers.get(
            (packet.packet_format, PacketId.PARTICIPANTS, packet.header.packet_version)
        )
        if parser is None:
            return ParticipantsDecodeResult(
                error=(
                    "unsupported participants packet adapter for "
                    f"format {packet.packet_format.value}, version "
                    f"{packet.header.packet_version}"
                )
            )
        try:
            return ParticipantsDecodeResult(participants=parser(packet))
        except ValueError as exc:
            return ParticipantsDecodeResult(error=str(exc))


def _decode_f1_25_v1(packet: DecodedPacket) -> ParticipantsPacket:
    body = packet.body
    if len(body) != _F1_25_PARTICIPANTS_V1_BODY_SIZE:
        raise ValueError(
            "F1 25 Participants v1 body must be "
            f"{_F1_25_PARTICIPANTS_V1_BODY_SIZE} bytes, got {len(body)}"
        )
    active_count = body[0]
    if active_count > CAR_COUNT:
        raise ValueError(f"invalid active car count {active_count}")
    cars = tuple(
        _decode_participant(
            _PARTICIPANT_PREFIX.unpack_from(
                body, 1 + index * _PARTICIPANT_V1_RECORD_SIZE
            ),
            index,
        )
        for index in range(CAR_COUNT)
    )
    return ParticipantsPacket(active_count, cars)


def _decode_participant(fields: tuple[object, ...], index: int) -> ParticipantData:
    name_bytes = bytes(fields[7])
    name = name_bytes.split(b"\0", 1)[0].decode("utf-8", errors="replace")
    colours = tuple(int(value) for value in fields[13:])
    if len(colours) != 12:
        raise ValueError(f"invalid participant colour data for car {index}")
    return ParticipantData(
        ai_controlled=bool(fields[0]),
        driver_id=int(fields[1]),
        network_id=int(fields[2]),
        team_id=int(fields[3]),
        my_team=bool(fields[4]),
        race_number=int(fields[5]),
        nationality_id=int(fields[6]),
        name=name,
        your_telemetry=int(fields[8]),
        show_online_names=bool(fields[9]),
        tech_level=int(fields[10]),
        platform_id=int(fields[11]),
        num_colours=int(fields[12]),
        colours_rgb=tuple(
            (colours[index], colours[index + 1], colours[index + 2])
            for index in range(0, len(colours), 3)
        ),
    )
