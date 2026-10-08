#!/usr/bin/env python3
"""Bounded metadata audit tool for session-aware engineer behaviour.

Cross-checks UDP packet structures, session decoders, context models,
and live observer snapshots across practice, qualifying, race, and time trial.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Ensure repository root is on sys.path when executed directly
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from f1_engineer.sessions.context import GameMode, RuleSet, SessionType
from f1_engineer.udp.lap_data import (
    _F1_25_CAR_COUNT,
    _F1_25_LAP_DATA_V1_BODY_SIZE,
    _LAP_DATA_V1_CAR,
    _SEASON_PACK_2026_CAR_COUNT,
    _SEASON_PACK_2026_LAP_DATA_V1_BODY_SIZE,
    CarLapData,
    LapDataDecoder,
)
from f1_engineer.udp.models import PacketFormat, PacketId
from f1_engineer.udp.session_context import (
    _F1_25_GAME_MODE_OFFSET,
    _F1_25_NETWORK_GAME_OFFSET,
    _F1_25_RULE_SET_OFFSET,
    _F1_25_SESSION_PREFIX,
    _F1_25_SESSION_V1_BODY_SIZE,
    _F1_25_WEATHER_SAMPLE_COUNT_OFFSET,
    _SEASON_PACK_2026_SESSION_V1_BODY_SIZE,
    _SESSION_TYPES,
    SessionContextDecoder,
)


@dataclass(frozen=True)
class SessionFormatAuditEntry:
    session_type_id: int
    session_type_name: str
    category: str
    knockout_stage: str | None
    is_one_shot: bool
    automatic_rival_type: str
    pace_comparison_metric: str
    advancement_cutoff_supported: bool
    cutoff_requires_external_rules: bool
    attempt_limit_enforced_by_rules: bool
    duration_field_reported_on_wire: bool
    duration_field_retained_in_context: bool
    time_left_field_reported_on_wire: bool
    time_left_field_retained_in_context: bool
    race_laps_reported_on_wire: bool
    race_laps_retained_in_context: bool


# Canonical session categories mapped to engineer behavior
def classify_session_type(st: SessionType) -> tuple[str, str | None, bool]:
    """Return (category, knockout_stage, is_one_shot)."""
    if st in (
        SessionType.PRACTICE_1,
        SessionType.PRACTICE_2,
        SessionType.PRACTICE_3,
        SessionType.SHORT_PRACTICE,
    ):
        return ("practice", None, False)
    if st is SessionType.QUALIFYING_1:
        return ("qualifying", "Q1", False)
    if st is SessionType.QUALIFYING_2:
        return ("qualifying", "Q2", False)
    if st is SessionType.QUALIFYING_3:
        return ("qualifying", "Q3", False)
    if st is SessionType.SHORT_QUALIFYING:
        return ("qualifying", "short", False)
    if st is SessionType.ONE_SHOT_QUALIFYING:
        return ("qualifying", "one_shot", True)
    if st is SessionType.SPRINT_SHOOTOUT_1:
        return ("qualifying", "SQ1", False)
    if st is SessionType.SPRINT_SHOOTOUT_2:
        return ("qualifying", "SQ2", False)
    if st is SessionType.SPRINT_SHOOTOUT_3:
        return ("qualifying", "SQ3", False)
    if st is SessionType.SHORT_SPRINT_SHOOTOUT:
        return ("qualifying", "short_shootout", False)
    if st is SessionType.ONE_SHOT_SPRINT_SHOOTOUT:
        return ("qualifying", "one_shot_shootout", True)
    if st in (SessionType.RACE, SessionType.RACE_2, SessionType.RACE_3):
        return ("race", None, False)
    if st is SessionType.TIME_TRIAL:
        return ("time_trial", None, False)
    return ("unknown", None, False)


def audit_session_formats() -> list[SessionFormatAuditEntry]:
    entries: list[SessionFormatAuditEntry] = []
    for raw_id, st in sorted(_SESSION_TYPES.items()):
        category, stage, is_one_shot = classify_session_type(st)

        if category == "practice":
            rival_type = "explicit_request_only"
            pace_metric = "representative_pace_consistency"
            cutoff_supp = False
            cutoff_ext = False
            attempt_limit = False
        elif category == "qualifying":
            rival_type = "timing_sheet_adjacent"
            pace_metric = "stage_best_valid_lap"
            cutoff_supp = stage in ("Q1", "Q2", "SQ1", "SQ2")
            cutoff_ext = cutoff_supp  # Cutoff position not on wire; requires external rules
            attempt_limit = is_one_shot
        elif category == "race":
            rival_type = "race_classification_adjacent"
            pace_metric = "recent_representative_pace"
            cutoff_supp = False
            cutoff_ext = False
            attempt_limit = False
        elif category == "time_trial":
            rival_type = "pb_or_rival_ghost"
            pace_metric = "best_valid_lap"
            cutoff_supp = False
            cutoff_ext = False
            attempt_limit = False
        else:
            rival_type = "none"
            pace_metric = "unknown"
            cutoff_supp = False
            cutoff_ext = False
            attempt_limit = False

        entries.append(
            SessionFormatAuditEntry(
                session_type_id=raw_id,
                session_type_name=st.value,
                category=category,
                knockout_stage=stage,
                is_one_shot=is_one_shot,
                automatic_rival_type=rival_type,
                pace_comparison_metric=pace_metric,
                advancement_cutoff_supported=cutoff_supp,
                cutoff_requires_external_rules=cutoff_ext,
                attempt_limit_enforced_by_rules=attempt_limit,
                duration_field_reported_on_wire=True,
                duration_field_retained_in_context=False,  # Unpacked as _session_duration, discarded
                time_left_field_reported_on_wire=True,
                time_left_field_retained_in_context=False,  # Unpacked as _session_time_left, discarded
                race_laps_reported_on_wire=True,
                race_laps_retained_in_context=True,  # Retained as total_laps
            )
        )
    return entries


def audit_decoder_field_status() -> dict[str, Any]:
    """Audit packet fields across decoders, contexts, and live monitors."""
    return {
        "packet_1_session": {
            "wire_body_size_f1_25": _F1_25_SESSION_V1_BODY_SIZE,
            "wire_body_size_2026": _SEASON_PACK_2026_SESSION_V1_BODY_SIZE,
            "prefix_unpack_format": _F1_25_SESSION_PREFIX.format,
            "prefix_unpack_size_bytes": _F1_25_SESSION_PREFIX.size,
            "fields": {
                "m_weather": {"unpacked": True, "retained_in_context": True, "field": "weather_id"},
                "m_trackTemperature": {"unpacked": True, "retained_in_context": True, "field": "track_temperature_c"},
                "m_airTemperature": {"unpacked": True, "retained_in_context": True, "field": "air_temperature_c"},
                "m_totalLaps": {"unpacked": True, "retained_in_context": True, "field": "total_laps"},
                "m_trackLength": {"unpacked": True, "retained_in_context": True, "field": "track_length_m"},
                "m_sessionType": {"unpacked": True, "retained_in_context": True, "field": "session_type / session_type_id"},
                "m_trackId": {"unpacked": True, "retained_in_context": True, "field": "track_id / track_name"},
                "m_formula": {"unpacked": True, "retained_in_context": True, "field": "formula_id"},
                "m_sessionTimeLeft": {
                    "unpacked": True,
                    "retained_in_context": False,
                    "status": "decoded_but_discarded",
                    "variable": "_session_time_left",
                    "impact": "Live engineer cannot assess remaining stage time or next attempt viability",
                },
                "m_sessionDuration": {
                    "unpacked": True,
                    "retained_in_context": False,
                    "status": "decoded_but_discarded",
                    "variable": "_session_duration",
                    "impact": "Live engineer cannot know planned session duration",
                },
                "m_pitSpeedLimit": {"unpacked": True, "retained_in_context": False, "status": "decoded_but_discarded"},
                "m_gamePaused": {"unpacked": True, "retained_in_context": False, "status": "decoded_but_discarded"},
                "m_isSpectating": {"unpacked": True, "retained_in_context": False, "status": "decoded_but_discarded"},
                "m_spectatorCarIndex": {"unpacked": True, "retained_in_context": False, "status": "decoded_but_discarded"},
                "m_sliProNativeSupport": {"unpacked": True, "retained_in_context": False, "status": "decoded_but_discarded"},
                "m_safetyCarStatus": {
                    "unpacked": False,
                    "retained_in_context": False,
                    "status": "skipped_on_wire",
                    "impact": "Safety car status not exposed in session context; relies on Event SCAR",
                },
                "m_networkGame": {"unpacked": True, "retained_in_context": True, "field": "network_game_id"},
                "m_gameMode": {"unpacked": True, "retained_in_context": True, "field": "game_mode / game_mode_id"},
                "m_ruleSet": {"unpacked": True, "retained_in_context": True, "field": "rule_set / rule_set_id"},
            },
        },
        "packet_2_lap_data": {
            "car_record_size_bytes": _LAP_DATA_V1_CAR.size,
            "f1_25_car_count": _F1_25_CAR_COUNT,
            "f1_25_body_size_bytes": _F1_25_LAP_DATA_V1_BODY_SIZE,
            "season_2026_car_count": _SEASON_PACK_2026_CAR_COUNT,
            "season_2026_body_size_bytes": _SEASON_PACK_2026_LAP_DATA_V1_BODY_SIZE,
            "fields": {
                "m_carPosition": {
                    "unpacked": True,
                    "stored_in_car_lap_data": True,
                    "published_in_live_snapshot": False,
                    "status": "decoded_but_discarded_from_live_snapshot",
                    "impact": "Engineer cannot automatically identify timing sheet or classification neighbours live",
                },
                "m_lapDistance": {
                    "unpacked": True,
                    "stored_in_car_lap_data": True,
                    "published_in_live_snapshot": False,
                    "status": "decoded_stored_in_canonical_telemetry_only",
                    "impact": "Physical track order requires consulting telemetry/motion instead of live snapshot",
                },
                "m_currentLapNum": {
                    "unpacked": True,
                    "stored_in_car_lap_data": True,
                    "published_in_live_snapshot": True,
                    "field": "lap_number",
                },
                "m_pitStatus": {
                    "unpacked": True,
                    "stored_in_car_lap_data": True,
                    "published_in_live_snapshot": False,
                    "status": "decoded_but_discarded_from_live_snapshot",
                    "impact": "Live engineer cannot know if player or rival is currently pitting",
                },
                "m_driverStatus": {
                    "unpacked": True,
                    "stored_in_car_lap_data": True,
                    "published_in_live_snapshot": False,
                    "status": "decoded_but_discarded_from_live_snapshot",
                    "impact": "Live engineer cannot distinguish out-lap, flying lap, or in-lap",
                },
                "m_currentLapInvalid": {
                    "unpacked": True,
                    "stored_in_car_lap_data": True,
                    "published_in_live_snapshot": False,
                    "status": "decoded_stored_in_attempt_model",
                    "impact": "Live snapshot uses synthetic validation_flags; game invalid flag not directly surfaced",
                },
                "m_lastLapTimeInMS": {
                    "unpacked": True,
                    "stored_in_car_lap_data": True,
                    "published_in_live_snapshot": True,
                    "field": "previous_lap_time_ms",
                },
                "m_currentLapTimeInMS": {
                    "unpacked": True,
                    "stored_in_car_lap_data": True,
                    "published_in_live_snapshot": True,
                    "field": "current_lap_time_ms",
                },
            },
        },
        "packet_11_session_history": {
            "player_car_handling": "admitted_and_buffered",
            "non_player_car_handling": "discarded_at_pipeline_boundary",
            "pipeline_filter_file_line": "f1_engineer/pipeline.py:909",
            "impact": (
                "Rival best valid lap times and sector histories are received on the wire "
                "but dropped by pipeline filtering, preventing automatic qualifying rival comparison."
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit session-format metadata contracts.")
    parser.add_argument("--json", action="store_true", help="Output full report as JSON.")
    args = parser.parse_args()

    format_entries = audit_session_formats()
    field_status = audit_decoder_field_status()

    if args.json:
        report = {
            "supported_session_formats": [asdict(e) for e in format_entries],
            "decoder_field_status": field_status,
        }
        print(json.dumps(report, indent=2))
        return

    print("=== SESSION FORMAT METADATA AUDIT ===")
    print(f"Total enumerated session types: {len(format_entries)}")
    print("\nFormat Summary Table:")
    print(
        f"{'ID':<3} | {'Type Name':<26} | {'Category':<11} | {'Stage':<6} | "
        f"{'OneShot':<7} | {'Rival Selection':<27} | {'Pace Metric':<32}"
    )
    print("-" * 125)
    for entry in format_entries:
        print(
            f"{entry.session_type_id:<3} | {entry.session_type_name:<26} | "
            f"{entry.category:<11} | {entry.knockout_stage or '-':<6} | "
            f"{str(entry.is_one_shot):<7} | {entry.automatic_rival_type:<27} | "
            f"{entry.pace_comparison_metric:<32}"
        )

    print("\nKey Gaps Identified:")
    print("1. Packet 1 m_sessionTimeLeft and m_sessionDuration are unpacked but discarded in SessionContext.")
    print("2. Packet 2 m_carPosition and m_driverStatus are decoded but discarded from live snapshots.")
    print("3. Packet 11 non-player Session History is discarded at pipeline boundary (pipeline.py:909).")
    print("4. Qualifying knockout cutoff positions are NOT reported in the UDP stream and require external rules.")


if __name__ == "__main__":
    main()
