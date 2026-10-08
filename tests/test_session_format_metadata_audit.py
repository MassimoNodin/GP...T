from __future__ import annotations

import json
from f1_engineer.sessions.context import SessionType
from scripts.audits.session_format_metadata import (
    audit_decoder_field_status,
    audit_session_formats,
    classify_session_type,
)


def test_all_session_types_enumerated_and_classified() -> None:
    entries = audit_session_formats()
    assert len(entries) == len(SessionType)

    by_type = {e.session_type_name: e for e in entries}

    # Verify practice
    for pt in ("practice_1", "practice_2", "practice_3", "short_practice"):
        assert by_type[pt].category == "practice"
        assert by_type[pt].automatic_rival_type == "explicit_request_only"
        assert by_type[pt].pace_comparison_metric == "representative_pace_consistency"

    # Verify race
    for rt in ("race", "race_2", "race_3"):
        assert by_type[rt].category == "race"
        assert by_type[rt].automatic_rival_type == "race_classification_adjacent"
        assert by_type[rt].pace_comparison_metric == "recent_representative_pace"

    # Verify time trial
    assert by_type["time_trial"].category == "time_trial"
    assert by_type["time_trial"].automatic_rival_type == "pb_or_rival_ghost"


def test_qualifying_variants_distinguish_knockout_and_oneshot() -> None:
    entries = {e.session_type_name: e for e in audit_session_formats()}

    # Knockout qualifying
    assert entries["qualifying_1"].knockout_stage == "Q1"
    assert entries["qualifying_1"].advancement_cutoff_supported is True
    assert entries["qualifying_1"].cutoff_requires_external_rules is True
    assert entries["qualifying_1"].is_one_shot is False

    assert entries["qualifying_2"].knockout_stage == "Q2"
    assert entries["qualifying_2"].advancement_cutoff_supported is True
    assert entries["qualifying_2"].cutoff_requires_external_rules is True
    assert entries["qualifying_2"].is_one_shot is False

    assert entries["qualifying_3"].knockout_stage == "Q3"
    assert entries["qualifying_3"].advancement_cutoff_supported is False  # Pole shootout, no next stage
    assert entries["qualifying_3"].is_one_shot is False

    # Sprint shootout
    assert entries["sprint_shootout_1"].knockout_stage == "SQ1"
    assert entries["sprint_shootout_1"].advancement_cutoff_supported is True
    assert entries["sprint_shootout_2"].knockout_stage == "SQ2"
    assert entries["sprint_shootout_2"].advancement_cutoff_supported is True
    assert entries["sprint_shootout_3"].knockout_stage == "SQ3"
    assert entries["sprint_shootout_3"].advancement_cutoff_supported is False

    # One-shot qualifying variants
    assert entries["one_shot_qualifying"].is_one_shot is True
    assert entries["one_shot_qualifying"].attempt_limit_enforced_by_rules is True
    assert entries["one_shot_sprint_shootout"].is_one_shot is True
    assert entries["one_shot_sprint_shootout"].attempt_limit_enforced_by_rules is True

    # Short qualifying variants
    assert entries["short_qualifying"].is_one_shot is False
    assert entries["short_qualifying"].advancement_cutoff_supported is False
    assert entries["short_sprint_shootout"].is_one_shot is False
    assert entries["short_sprint_shootout"].advancement_cutoff_supported is False


def test_field_status_audit_catches_discarded_fields_and_pipeline_filters() -> None:
    status = audit_decoder_field_status()

    # Packet 1 fields
    p1 = status["packet_1_session"]["fields"]
    assert p1["m_sessionTimeLeft"]["status"] == "decoded_but_discarded"
    assert p1["m_sessionDuration"]["status"] == "decoded_but_discarded"
    assert p1["m_safetyCarStatus"]["status"] == "skipped_on_wire"

    # Packet 2 fields
    p2 = status["packet_2_lap_data"]["fields"]
    assert p2["m_carPosition"]["published_in_live_snapshot"] is False
    assert p2["m_driverStatus"]["published_in_live_snapshot"] is False
    assert p2["m_pitStatus"]["published_in_live_snapshot"] is False

    # Packet 11 session history
    p11 = status["packet_11_session_history"]
    assert p11["non_player_car_handling"] == "discarded_at_pipeline_boundary"
    assert "pipeline.py:909" in p11["pipeline_filter_file_line"]


def test_audit_json_serializability() -> None:
    entries = audit_session_formats()
    status = audit_decoder_field_status()
    payload = json.dumps({"entries": [e.__dict__ for e in entries], "status": status})
    assert len(payload) > 1000
