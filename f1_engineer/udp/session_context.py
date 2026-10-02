from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable

from ..sessions.context import GameMode, RuleSet, SessionContext, SessionType
from .models import DecodedPacket, PacketFormat, PacketId


_F1_25_SESSION_V1_BODY_SIZE = 724
_F1_25_SESSION_PREFIX = struct.Struct("<BbbBHBbBHHBBBBBB")
_F1_25_NETWORK_GAME_OFFSET = 125
_F1_25_WEATHER_SAMPLE_COUNT_OFFSET = 126
_F1_25_STEERING_ASSIST_OFFSET = 656
_F1_25_BRAKING_ASSIST_OFFSET = 657
_F1_25_GEARBOX_ASSIST_OFFSET = 658
_F1_25_GAME_MODE_OFFSET = 665
_F1_25_RULE_SET_OFFSET = 666
_F1_25_EQUAL_CAR_PERFORMANCE_OFFSET = 679
_F1_25_NUM_WEEKEND_SESSIONS_OFFSET = 703

_SESSION_TYPES: dict[int, SessionType] = {
    0: SessionType.UNKNOWN,
    1: SessionType.PRACTICE_1,
    2: SessionType.PRACTICE_2,
    3: SessionType.PRACTICE_3,
    4: SessionType.SHORT_PRACTICE,
    5: SessionType.QUALIFYING_1,
    6: SessionType.QUALIFYING_2,
    7: SessionType.QUALIFYING_3,
    8: SessionType.SHORT_QUALIFYING,
    9: SessionType.ONE_SHOT_QUALIFYING,
    10: SessionType.SPRINT_SHOOTOUT_1,
    11: SessionType.SPRINT_SHOOTOUT_2,
    12: SessionType.SPRINT_SHOOTOUT_3,
    13: SessionType.SHORT_SPRINT_SHOOTOUT,
    14: SessionType.ONE_SHOT_SPRINT_SHOOTOUT,
    15: SessionType.RACE,
    16: SessionType.RACE_2,
    17: SessionType.RACE_3,
    18: SessionType.TIME_TRIAL,
}

_GAME_MODES: dict[int, GameMode] = {
    4: GameMode.GRAND_PRIX_23,
    5: GameMode.TIME_TRIAL,
    6: GameMode.SPLITSCREEN,
    7: GameMode.ONLINE_CUSTOM,
    15: GameMode.ONLINE_WEEKLY_EVENT,
    17: GameMode.STORY_BRAKING_POINT,
    27: GameMode.MY_TEAM_CAREER_25,
    28: GameMode.DRIVER_CAREER_25,
    29: GameMode.CAREER_25_ONLINE,
    30: GameMode.CHALLENGE_CAREER_25,
    75: GameMode.STORY_APXGP,
    127: GameMode.BENCHMARK,
}

_RULE_SETS: dict[int, RuleSet] = {
    0: RuleSet.PRACTICE_QUALIFYING,
    1: RuleSet.RACE,
    2: RuleSet.TIME_TRIAL,
    12: RuleSet.ELIMINATION,
}

_WEATHER_NAMES = {
    0: "clear",
    1: "light_cloud",
    2: "overcast",
    3: "light_rain",
    4: "heavy_rain",
    5: "storm",
}

_F1_25_TRACK_NAMES = {
    0: "Melbourne",
    2: "Shanghai",
    3: "Sakhir (Bahrain)",
    4: "Catalunya",
    5: "Monaco",
    6: "Montreal",
    7: "Silverstone",
    9: "Hungaroring",
    10: "Spa",
    11: "Monza",
    12: "Singapore",
    13: "Suzuka",
    14: "Abu Dhabi",
    15: "Texas",
    16: "Brazil",
    17: "Austria",
    19: "Mexico",
    20: "Baku (Azerbaijan)",
    26: "Zandvoort",
    27: "Imola",
    29: "Jeddah",
    30: "Miami",
    31: "Las Vegas",
    32: "Losail",
    39: "Silverstone (Reverse)",
    40: "Austria (Reverse)",
    41: "Zandvoort (Reverse)",
}


@dataclass(frozen=True, slots=True)
class SessionContextDecodeResult:
    context: SessionContext | None = None
    error: str | None = None


SessionParser = Callable[[DecodedPacket], SessionContext]


class SessionContextDecoder:
    """Decode known session packet versions into canonical session context."""

    def __init__(self) -> None:
        self._parsers: dict[tuple[PacketFormat, PacketId, int], SessionParser] = {
            (PacketFormat.F1_25, PacketId.SESSION, 1): _decode_f1_25_session_v1,
        }

    def decode(self, packet: DecodedPacket) -> SessionContextDecodeResult:
        if packet.packet_kind is not PacketId.SESSION:
            return SessionContextDecodeResult()

        key = (packet.packet_format, PacketId.SESSION, packet.header.packet_version)
        parser = self._parsers.get(key)
        if parser is None:
            return SessionContextDecodeResult(
                error=(
                    "unsupported session packet adapter for "
                    f"format {packet.packet_format.value}, version "
                    f"{packet.header.packet_version}"
                )
            )
        try:
            return SessionContextDecodeResult(context=parser(packet))
        except ValueError as exc:
            return SessionContextDecodeResult(error=str(exc))


def _decode_f1_25_session_v1(packet: DecodedPacket) -> SessionContext:
    body = packet.body
    if len(body) != _F1_25_SESSION_V1_BODY_SIZE:
        raise ValueError(
            "F1 25 Session v1 body must be "
            f"{_F1_25_SESSION_V1_BODY_SIZE} bytes, got {len(body)}"
        )

    fields = _F1_25_SESSION_PREFIX.unpack_from(body)
    (
        weather_id,
        track_temperature_c,
        air_temperature_c,
        total_laps,
        track_length_m,
        session_type_id,
        track_id,
        formula_id,
        _session_time_left,
        _session_duration,
        _pit_speed_limit,
        _game_paused,
        _is_spectating,
        _spectator_car_index,
        _sli_pro_native_support,
        num_marshal_zones,
    ) = fields

    if num_marshal_zones > 21:
        raise ValueError(f"invalid marshal zone count {num_marshal_zones}")
    if body[_F1_25_WEATHER_SAMPLE_COUNT_OFFSET] > 64:
        raise ValueError("invalid weather forecast sample count")
    if body[_F1_25_NUM_WEEKEND_SESSIONS_OFFSET] > 12:
        raise ValueError("invalid weekend session count")

    game_mode_id = body[_F1_25_GAME_MODE_OFFSET]
    rule_set_id = body[_F1_25_RULE_SET_OFFSET]
    return SessionContext(
        session_uid=packet.header.session_uid,
        packet_format=packet.packet_format,
        packet_version=packet.header.packet_version,
        weather_id=weather_id,
        weather_name=_WEATHER_NAMES.get(weather_id),
        track_temperature_c=track_temperature_c,
        air_temperature_c=air_temperature_c,
        total_laps=total_laps,
        track_length_m=track_length_m,
        session_type_id=session_type_id,
        session_type=_SESSION_TYPES.get(session_type_id),
        track_id=track_id,
        track_name=_F1_25_TRACK_NAMES.get(track_id),
        formula_id=formula_id,
        network_game_id=body[_F1_25_NETWORK_GAME_OFFSET],
        game_mode_id=game_mode_id,
        game_mode=_GAME_MODES.get(game_mode_id),
        rule_set_id=rule_set_id,
        rule_set=_RULE_SETS.get(rule_set_id),
        steering_assist_id=body[_F1_25_STEERING_ASSIST_OFFSET],
        braking_assist_id=body[_F1_25_BRAKING_ASSIST_OFFSET],
        gearbox_assist_id=body[_F1_25_GEARBOX_ASSIST_OFFSET],
        equal_car_performance_id=body[_F1_25_EQUAL_CAR_PERFORMANCE_OFFSET],
    )
