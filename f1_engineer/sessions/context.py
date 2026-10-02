from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..udp.models import PacketFormat


class SessionType(str, Enum):
    UNKNOWN = "unknown"
    PRACTICE_1 = "practice_1"
    PRACTICE_2 = "practice_2"
    PRACTICE_3 = "practice_3"
    SHORT_PRACTICE = "short_practice"
    QUALIFYING_1 = "qualifying_1"
    QUALIFYING_2 = "qualifying_2"
    QUALIFYING_3 = "qualifying_3"
    SHORT_QUALIFYING = "short_qualifying"
    ONE_SHOT_QUALIFYING = "one_shot_qualifying"
    SPRINT_SHOOTOUT_1 = "sprint_shootout_1"
    SPRINT_SHOOTOUT_2 = "sprint_shootout_2"
    SPRINT_SHOOTOUT_3 = "sprint_shootout_3"
    SHORT_SPRINT_SHOOTOUT = "short_sprint_shootout"
    ONE_SHOT_SPRINT_SHOOTOUT = "one_shot_sprint_shootout"
    RACE = "race"
    RACE_2 = "race_2"
    RACE_3 = "race_3"
    TIME_TRIAL = "time_trial"


class GameMode(str, Enum):
    GRAND_PRIX_23 = "grand_prix_23"
    TIME_TRIAL = "time_trial"
    SPLITSCREEN = "splitscreen"
    ONLINE_CUSTOM = "online_custom"
    ONLINE_WEEKLY_EVENT = "online_weekly_event"
    STORY_BRAKING_POINT = "story_braking_point"
    MY_TEAM_CAREER_25 = "my_team_career_25"
    DRIVER_CAREER_25 = "driver_career_25"
    CAREER_25_ONLINE = "career_25_online"
    CHALLENGE_CAREER_25 = "challenge_career_25"
    STORY_APXGP = "story_apxgp"
    BENCHMARK = "benchmark"


class RuleSet(str, Enum):
    PRACTICE_QUALIFYING = "practice_qualifying"
    RACE = "race"
    TIME_TRIAL = "time_trial"
    ELIMINATION = "elimination"


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Canonical session metadata decoded from a versioned game packet."""

    session_uid: int
    packet_format: PacketFormat
    packet_version: int
    weather_id: int
    weather_name: str | None
    track_temperature_c: int
    air_temperature_c: int
    total_laps: int
    track_length_m: int
    session_type_id: int
    session_type: SessionType | None
    track_id: int
    track_name: str | None
    formula_id: int
    network_game_id: int
    game_mode_id: int
    game_mode: GameMode | None
    rule_set_id: int
    rule_set: RuleSet | None
    steering_assist_id: int
    braking_assist_id: int
    gearbox_assist_id: int
    equal_car_performance_id: int

    def to_dict(self) -> dict[str, object]:
        return {
            "session_uid": self.session_uid,
            "packet_format": self.packet_format.value,
            "packet_version": self.packet_version,
            "weather_id": self.weather_id,
            "weather_name": self.weather_name,
            "track_temperature_c": self.track_temperature_c,
            "air_temperature_c": self.air_temperature_c,
            "total_laps": self.total_laps,
            "track_length_m": self.track_length_m,
            "session_type_id": self.session_type_id,
            "session_type": self.session_type.value if self.session_type else None,
            "track_id": self.track_id,
            "track_name": self.track_name,
            "formula_id": self.formula_id,
            "network_game_id": self.network_game_id,
            "game_mode_id": self.game_mode_id,
            "game_mode": self.game_mode.value if self.game_mode else None,
            "rule_set_id": self.rule_set_id,
            "rule_set": self.rule_set.value if self.rule_set else None,
            "steering_assist_id": self.steering_assist_id,
            "braking_assist_id": self.braking_assist_id,
            "gearbox_assist_id": self.gearbox_assist_id,
            "equal_car_performance_id": self.equal_car_performance_id,
        }
