from __future__ import annotations

import asyncio
import json
import sqlite3
import struct
import sys
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest

import f1_engineer.api.app as api_module
import f1_engineer.cli as cli_module
from f1_engineer.analysis.reference_selection import (
    ReferenceKind,
    ReferenceRequest,
    ReferenceSelectionStatus,
    select_reference,
)
from f1_engineer.analysis.resampling import ResamplingConfig
from f1_engineer.analysis.service import ComparisonPolicy, compare_attempts
from f1_engineer.api.app import create_app
from f1_engineer.recording.capture import CaptureWriter
from f1_engineer.storage.importer import get_lap, import_capture, list_laps
from f1_engineer.tracks import registry
from f1_engineer.tracks.loader import load_track_model
from f1_engineer.tracks.model import CornerDefinition, TrackModel
from tests.helpers import make_datagram
from tests.test_lap_tracking import SESSION_UID, _lap_packet
from f1_engineer.udp.car_telemetry import _F1_25_CAR_TELEMETRY_V1_CAR


_SESSION_FIXTURE = Path(__file__).parent / "fixtures" / "f1_25_session_packet_v1.bin"
_TRACK_LENGTH_M = 1_000
_LAP_DURATIONS_MS = {1: 15_000, 2: 16_000, 3: 17_000}


def _session_body(session_type_id: int) -> bytes:
    body = bytearray(_SESSION_FIXTURE.read_bytes()[29:])
    struct.pack_into("<H", body, 4, _TRACK_LENGTH_M)
    body[6] = session_type_id
    body[7] = 0
    body[665] = 5 if session_type_id == 18 else 27
    body[666] = 2 if session_type_id == 18 else 1 if session_type_id in (15, 16, 17) else 0
    return bytes(body)


def _telemetry_body(speed_kph: int, throttle: float, brake: float) -> bytes:
    player = _F1_25_CAR_TELEMETRY_V1_CAR.pack(
        speed_kph,
        throttle,
        0.0,
        brake,
        0,
        5,
        9_000,
        0,
        0,
        0,
        300,
        300,
        300,
        300,
        80,
        80,
        80,
        80,
        90,
        90,
        90,
        90,
        90,
        22.0,
        22.0,
        22.0,
        22.0,
        0,
        0,
        0,
        0,
    )
    empty = _F1_25_CAR_TELEMETRY_V1_CAR.pack(
        0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0,
        0, 0, 0, 0,
        0, 0, 0, 0,
        0,
        0.0, 0.0, 0.0, 0.0,
        0, 0, 0, 0,
    )
    return player + empty * 21 + bytes((0, 255, 5))


def _interpolate_speed(distance_m: float, points: tuple[tuple[float, float], ...]) -> int:
    for (start, start_speed), (end, end_speed) in zip(points, points[1:]):
        if start <= distance_m <= end:
            portion = (distance_m - start) / (end - start)
            return round(start_speed + portion * (end_speed - start_speed))
    return round(points[0][1] if distance_m < points[0][0] else points[-1][1])


def _lap_controls(lap_number: int, distance_m: float) -> tuple[int, float, float]:
    if lap_number == 3:
        speed_points = (
            (0.0, 212.0),
            (330.0, 212.0),
            (380.0, 200.0),
            (430.0, 200.0),
            (485.0, 150.0),
            (520.0, 172.0),
            (600.0, 210.0),
            (650.0, 212.0),
            (1_000.0, 212.0),
        )
        brake = 1.0 if 380.0 <= distance_m <= 430.0 else 0.0
        throttle = 0.8 if distance_m >= 520.0 else 0.0
    elif lap_number == 2:
        speed_points = (
            (0.0, 225.0),
            (1_000.0, 225.0),
        )
        brake = 0.0
        throttle = 0.8
    else:
        speed_points = (
            (0.0, 240.0),
            (350.0, 240.0),
            (400.0, 230.0),
            (430.0, 230.0),
            (470.0, 160.0),
            (500.0, 185.0),
            (600.0, 230.0),
            (650.0, 240.0),
            (1_000.0, 240.0),
        )
        brake = 1.0 if 400.0 <= distance_m <= 430.0 else 0.0
        throttle = 0.8 if distance_m >= 500.0 else 0.0
    return _interpolate_speed(distance_m, speed_points), throttle, brake


def _write_synthetic_capture(
    path: Path,
    *,
    session_type_id: int = 18,
    invalid_lap: int | None = None,
    completion_status: str = "complete",
    flashback_after_laps: bool = False,
    missing_telemetry: tuple[int, float, float] | None = None,
    complete_final_lap: bool = False,
) -> None:
    sequence = 0
    frame = 0
    datagram_count = 0
    elapsed_before_lap_s = 0.0

    with CaptureWriter(path, {"fixture": "synthetic-phase-e-positive-integration"}) as writer:
        session = make_datagram(
            packet_id=1,
            session_uid=SESSION_UID,
            frame=0,
            session_time=0.0,
            body=_session_body(session_type_id),
            sequence=sequence,
        )
        writer.write(session)
        datagram_count += 1
        sequence += 1

        def write_sample(
            lap_number: int,
            distance_m: float,
            *,
            last_lap_time_ms: int = 0,
        ) -> None:
            nonlocal sequence, frame, datagram_count
            duration_ms = _LAP_DURATIONS_MS.get(lap_number, 17_000)
            current_lap_time_ms = max(
                0, round(distance_m / _TRACK_LENGTH_M * duration_ms)
            )
            session_time_s = elapsed_before_lap_s + max(
                0.0, distance_m / _TRACK_LENGTH_M * duration_ms / 1_000.0
            )
            speed_kph, throttle, brake = _lap_controls(lap_number, distance_m)
            invalid = 1 if invalid_lap == lap_number else 0
            lap_data = _lap_packet(
                frame=frame + 1,
                lap_number=lap_number,
                distance_m=distance_m,
                session_time=session_time_s,
                current_lap_time_ms=current_lap_time_ms,
                last_lap_time_ms=last_lap_time_ms,
                invalid=invalid,
                sequence=sequence,
            )
            writer.write(lap_data)
            sequence += 1
            datagram_count += 1

            omit_telemetry = (
                missing_telemetry is not None
                and missing_telemetry[0] == lap_number
                and missing_telemetry[1] <= distance_m < missing_telemetry[2]
            )
            if not omit_telemetry:
                telemetry = make_datagram(
                    packet_id=6,
                    session_uid=SESSION_UID,
                    frame=frame + 1,
                    session_time=session_time_s,
                    body=_telemetry_body(speed_kph, throttle, brake),
                    sequence=sequence,
                )
                writer.write(telemetry)
                sequence += 1
                datagram_count += 1
            frame += 1

        final_partial_lap = 5 if complete_final_lap else 4
        for lap_number in range(1, final_partial_lap + 1):
            prior_duration_ms = _LAP_DURATIONS_MS.get(lap_number - 1, 0)
            if lap_number == 1:
                write_sample(1, -0.5)
            write_sample(
                lap_number,
                0.5,
                last_lap_time_ms=prior_duration_ms,
            )
            if lap_number == final_partial_lap:
                break
            for index in range(1, 200):
                write_sample(
                    lap_number,
                    0.5 + index * 5.0,
                    last_lap_time_ms=prior_duration_ms,
                )
            elapsed_before_lap_s += _LAP_DURATIONS_MS[lap_number] / 1_000.0

        if flashback_after_laps:
            event = make_datagram(
                packet_id=3,
                session_uid=SESSION_UID,
                frame=frame + 1,
                session_time=elapsed_before_lap_s + 0.02,
                body=b"FLBK" + struct.pack("<If", 1, 0.0) + b"\x00" * 4,
                sequence=sequence,
            )
            writer.write(event)
            sequence += 1
            datagram_count += 1

        writer.close(
            {
                "status": completion_status,
                "received": datagram_count,
                "recorded": datagram_count,
                "queue_dropped": 0,
                "frame_overflow_packets_dropped": 0,
                "late_packets_ignored": 0,
                "socket_errors": 0,
                "unpersisted_on_shutdown": 0,
            }
        )


def _test_model() -> TrackModel:
    return TrackModel(
        model_id="synthetic-phase-e-model",
        revision=1,
        packet_format=2025,
        track_id=0,
        track_name="Melbourne",
        layout_id="synthetic-acceptance-layout",
        track_length_m=_TRACK_LENGTH_M,
        distance_origin_m=0.0,
        provenance="synthetic test fixture; no real circuit boundaries asserted",
        validation_status="validated",
        corners=(
            CornerDefinition(
                identifier="synthetic-turn",
                label="Synthetic Turn",
                start_distance_m=340.0,
                end_distance_m=650.0,
                braking_search_window_m=(350.0, 450.0),
                throttle_pickup_window_m=(450.0, 570.0),
                nominal_apex_m=485.0,
                exit_distance_m=600.0,
            ),
        ),
    )


def _register_test_approval(monkeypatch, model: TrackModel) -> None:
    key = (model.model_id, model.revision)
    fingerprint = registry._model_fingerprint(model)
    monkeypatch.setattr(registry, "TRACK_MODEL_REGISTRY", {key: model})
    monkeypatch.setattr(
        registry,
        "CORNER_CANDIDATE_RANKING_APPROVALS",
        {
            key: {
                "model_content_sha256": fingerprint,
                "approval_provenance": {"review_record": "synthetic-test-only"},
            }
        },
    )


def _attempt_keys(database: Path, run_id: str) -> tuple[str, str]:
    attempts = [
        lap
        for lap in list_laps(database, run_id=run_id)
        if lap["disposition"] == "completed"
    ]
    return (
        next(lap["attempt_key"] for lap in attempts if lap["attempt_number"] == 3),
        next(lap["attempt_key"] for lap in attempts if lap["attempt_number"] == 1),
    )


def _normalize_session_uids(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: str(item)
            if key == "session_uid" and isinstance(item, int)
            else _normalize_session_uids(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_session_uids(item) for item in value]
    return value


def test_synthetic_capture_reaches_positive_phase_e_api_cli_and_idempotent_import(
    tmp_path, monkeypatch, capsys
) -> None:
    capture = tmp_path / "three-valid-laps.f1ecap"
    database = tmp_path / "analysis.sqlite3"
    model = _test_model()
    model_path = tmp_path / "test-only-model.json"
    model_path.write_text(
        json.dumps({"schema_version": 1, **asdict(model)}), encoding="utf-8"
    )
    model = load_track_model(model_path)
    _write_synthetic_capture(capture)

    imported = import_capture(capture, database)
    laps = list_laps(database, run_id=imported.run_id)
    completed = [lap for lap in laps if lap["disposition"] == "completed"]
    assert imported.status == "complete"
    assert imported.attempts == 4
    assert [lap["lap_time_ms"] for lap in completed] == [15_000, 16_000, 17_000]
    assert all(lap["reference_eligible"] for lap in completed)
    assert all(
        lap["player_participant_context"]["status"] == "unknown"
        and lap["player_participant_context"]["at_start"]["status"] == "unknown"
        for lap in completed
    )
    standalone_attempt = get_lap(
        database,
        next(lap["attempt_key"] for lap in completed if lap["attempt_number"] == 3),
    )
    assert standalone_attempt is not None
    assert standalone_attempt["player_participant_context"]["status"] == "unknown"

    target_key = next(
        lap["attempt_key"] for lap in completed if lap["attempt_number"] == 3
    )
    reference_key = next(
        lap["attempt_key"] for lap in completed if lap["attempt_number"] == 1
    )
    selected = select_reference(
        database,
        ReferenceRequest(target_key, reference_kind=ReferenceKind.SESSION_BEST),
    )
    assert selected.status is ReferenceSelectionStatus.SELECTED
    assert selected.selected_reference is not None
    assert selected.selected_reference["attempt_key"] == reference_key

    _register_test_approval(monkeypatch, model)
    result = compare_attempts(
        database,
        target_key,
        reference_key,
        config=ResamplingConfig(),
        track_model=model,
        policy=ComparisonPolicy.TIME_TRIAL,
    )
    assert result["official_lap_time_difference_s"] == pytest.approx(2.0, abs=0.001)
    assert result["corner_loss_candidates"]["status"] == "ranked"
    assert result["corner_loss_candidates"]["coaching_eligible"] is False
    candidate = result["corner_loss_candidates"]["ranked_candidates"][0]
    assert candidate["recorded_time_difference_s"] == pytest.approx(0.620, abs=0.005)
    assert result["corner_loss_candidates"]["source"]["reference_selection"][
        "selected_reference"
    ]["attempt_key"] == reference_key
    assert result["corner_comparison_brief"]["status"] == "available"
    facts = {
        fact["kind"]: fact
        for fact in result["corner_comparison_brief"]["regions"][0]["facts"]
    }
    assert facts["recorded_interval_time_difference"]["value"] == pytest.approx(
        0.620, abs=0.005
    )
    assert facts["braking_threshold_onset_difference"][
        "difference_bounds_m"
    ] == pytest.approx([-25.0, -15.0], abs=0.001)
    assert facts["throttle_50_threshold_onset_difference"][
        "difference_bounds_m"
    ] == pytest.approx([15.0, 25.0], abs=0.001)
    assert facts["configured_exit_anchor_speed_difference"]["value"] == pytest.approx(
        -20.0, abs=0.05
    )
    assert result["driving_pattern_assessment"]["regions"][0]["status"] == "matched"
    assert result["throttle_pattern_assessment"]["regions"][0]["status"] == "matched"
    driving_evidence = result["driving_pattern_assessment"]["regions"][0]["evidence"]
    assert driving_evidence["recorded_interval_time_difference_s"] == pytest.approx(
        0.620, abs=0.005
    )
    assert driving_evidence["brake_onset"][
        "target_minus_reference_bounds_m"
    ] == pytest.approx([-25.0, -15.0], abs=0.001)
    assert driving_evidence["exit_speed"][
        "target_minus_reference_kph"
    ] == pytest.approx(-20.0, abs=0.05)
    throttle_evidence = result["throttle_pattern_assessment"]["regions"][0]["evidence"]
    assert throttle_evidence["throttle_onset"][
        "target_minus_reference_bounds_m"
    ] == pytest.approx([15.0, 25.0], abs=0.001)
    assert throttle_evidence["exit_speed"][
        "target_minus_reference_kph"
    ] == pytest.approx(-20.0, abs=0.05)
    assert result["driving_pattern_assessment"]["action"] is None
    assert result["throttle_pattern_assessment"]["action"] is None
    assert result["driving_pattern_assessment"]["coaching_admission"]["eligible"] is False
    assert result["throttle_pattern_assessment"]["coaching_admission"]["eligible"] is False
    assert result["lap_debrief"]["status"] == "partial"
    assert result["lap_debrief"]["official_lap_time"] is not None
    assert result["lap_debrief"]["official_lap_time"]["value_s"] == pytest.approx(
        2.0, abs=0.001
    )
    assert len(result["lap_debrief"]["ranked_regions"]) == 1
    assert result["lap_debrief"]["ranked_regions"][0][
        "recorded_time_difference_s"
    ] == pytest.approx(0.620, abs=0.005)
    assert "reported_sector_timing_unavailable" in {
        item["code"] for item in result["lap_debrief"]["limitations"]
    }
    assert result["lap_debrief"]["coaching_eligible"] is False
    assert result["target"]["player_participant_context"]["status"] == "unknown"
    assert result["reference"]["player_participant_context"]["status"] == "unknown"
    target_checksum = result["target"]["trace_sha256"]
    reference_checksum = result["reference"]["trace_sha256"]
    assert result["driving_pattern_assessment"]["source"]["target"][
        "trace_sha256"
    ] == target_checksum
    assert result["driving_pattern_assessment"]["source"]["reference"][
        "trace_sha256"
    ] == reference_checksum

    monkeypatch.setattr(api_module, "resolve_track_model", lambda *_: model)
    app = create_app(database)

    async def request_api() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get(
                "/api/v1/compare/laps",
                params={
                    "target_attempt_key": target_key,
                    "reference_attempt_key": reference_key,
                    "track_model_id": model.model_id,
                    "track_model_revision": str(model.revision),
                },
            )

    api_response = asyncio.run(request_api())
    assert api_response.status_code == 200
    api_result = api_response.json()["data"]
    assert api_result["target"]["player_participant_context"]["status"] == "unknown"
    assert api_result["reference"]["player_participant_context"]["status"] == "unknown"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "f1-engineer",
            "compare",
            target_key,
            reference_key,
            "--database",
            str(database),
            "--track-model",
            str(model_path),
        ],
    )
    try:
        cli_module.main()
    except SystemExit as exc:
        assert exc.code == 0
    cli_result = json.loads(capsys.readouterr().out)
    for field in (
        "corner_loss_candidates",
        "corner_comparison_brief",
        "driving_pattern_assessment",
        "throttle_pattern_assessment",
        "lap_debrief",
    ):
        assert _normalize_session_uids(api_result[field]) == _normalize_session_uids(
            cli_result[field]
        ), {
            "field": field,
            "api_status": api_result[field].get("status"),
            "cli_status": cli_result[field].get("status"),
            "api_gate_reasons": api_result[field].get("gate_reasons"),
            "cli_gate_reasons": cli_result[field].get("gate_reasons"),
        }
    assert cli_result["target"]["player_participant_context"]["status"] == "unknown"

    repeated = import_capture(capture, database)
    assert repeated.run_id == imported.run_id
    assert repeated.already_imported is True
    assert repeated.attempts == imported.attempts


def test_participant_snapshot_first_seen_mid_attempt_does_not_backfill_start(
    tmp_path,
) -> None:
    capture = tmp_path / "mid-attempt-participant.f1ecap"
    database = tmp_path / "mid-attempt.sqlite3"
    _write_synthetic_capture(capture)
    imported = import_capture(capture, database)
    target = next(
        lap for lap in list_laps(database, run_id=imported.run_id)
        if lap["attempt_number"] == 3 and lap["disposition"] == "completed"
    )
    assert target["player_participant_context"]["status"] == "unknown"
    was_reference_eligible = target["reference_eligible"]

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        attempt = connection.execute(
            """SELECT l.start_frame_ordinal,l.end_frame_ordinal,l.car_index,
                      l.attempt_json,s.run_id,s.session_uid
                 FROM lap_attempts l JOIN sessions s USING(session_key)
                WHERE l.attempt_key = ?""",
            (target["attempt_key"],),
        ).fetchone()
        assert attempt is not None
        assert attempt["start_frame_ordinal"] is not None
        assert attempt["end_frame_ordinal"] is not None
        ordinal = int(attempt["start_frame_ordinal"]) + 1
        assert ordinal <= int(attempt["end_frame_ordinal"])
        scope = json.loads(attempt["attempt_json"])
        participant = {
            "ai_controlled": False,
            "driver_id": 123,
            "network_id": 456,
            "team_id": 7,
            "my_team": True,
            "race_number": 1,
            "nationality_id": 8,
            "name": "Mid-lap only",
            "your_telemetry": 1,
            "tech_level": 99,
            "platform_id": 0,
        }
        connection.execute(
            """INSERT INTO player_participant_observations(
                       run_id,session_uid,frame_ordinal,frame_identifier,
                       overall_frame_identifier,packet_format,association_epoch,
                       association_scope_assessable,player_car_index,session_time_s,
                       status,reason,active_car_count,participant_json,
                       source_packet_count)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                attempt["run_id"],
                attempt["session_uid"],
                ordinal,
                999,
                999,
                scope["start_association_packet_format"],
                scope["start_association_epoch"],
                1,
                attempt["car_index"],
                1.0,
                "observed",
                None,
                22,
                json.dumps(participant),
                1,
            ),
        )

    after = next(
        lap for lap in list_laps(database, run_id=imported.run_id)
        if lap["attempt_key"] == target["attempt_key"]
    )
    context = after["player_participant_context"]
    assert context["status"] == "unknown"
    assert context["at_start"]["status"] == "unknown"
    assert context["at_start"]["reason"] == "participant_not_reported"
    assert context["observations"][0]["status"] == "reported"
    assert context["observations"][0]["participant"]["name"] == "Mid-lap only"
    assert after["reference_eligible"] is was_reference_eligible


def test_player_participant_context_validates_bounded_sqlite_json_inputs(
    tmp_path,
) -> None:
    capture = tmp_path / "bounded-participant-json.f1ecap"
    database = tmp_path / "bounded-participant-json.sqlite3"
    _write_synthetic_capture(capture)
    imported = import_capture(capture, database)
    target = next(
        lap for lap in list_laps(database, run_id=imported.run_id)
        if lap["attempt_number"] == 3 and lap["disposition"] == "completed"
    )

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """SELECT l.attempt_json,l.start_frame_ordinal,l.car_index,
                      s.run_id,s.session_uid,r.metrics_json
                 FROM lap_attempts l JOIN sessions s USING(session_key)
                 JOIN processing_runs r USING(run_id)
                WHERE l.attempt_key = ?""",
            (target["attempt_key"],),
        ).fetchone()
        assert row is not None
        original_attempt_json = row["attempt_json"]
        original_metrics_json = row["metrics_json"]

        connection.execute(
            "UPDATE lap_attempts SET attempt_json = ? WHERE attempt_key = ?",
            ("{" + " " * 32_800, target["attempt_key"]),
        )
    oversized_attempt = next(
        lap for lap in list_laps(database, run_id=imported.run_id)
        if lap["attempt_key"] == target["attempt_key"]
    )["player_participant_context"]
    assert oversized_attempt["status"] == "unknown"
    assert oversized_attempt["reason"] == "attempt_scope_exceeds_byte_limit"

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE lap_attempts SET attempt_json = ? WHERE attempt_key = ?",
            ("{", target["attempt_key"]),
        )
    malformed_attempt = next(
        lap for lap in list_laps(database, run_id=imported.run_id)
        if lap["attempt_key"] == target["attempt_key"]
    )["player_participant_context"]
    assert malformed_attempt["status"] == "unknown"
    assert malformed_attempt["reason"] == "attempt_scope_json_invalid"

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE lap_attempts SET attempt_json = ? WHERE attempt_key = ?",
            (original_attempt_json, target["attempt_key"]),
        )
        connection.execute(
            "UPDATE processing_runs SET metrics_json = ? WHERE run_id = ?",
            (" " * 70_000, row["run_id"]),
        )
    oversized_metrics = next(
        lap for lap in list_laps(database, run_id=imported.run_id)
        if lap["attempt_key"] == target["attempt_key"]
    )["player_participant_context"]
    assert oversized_metrics["status"] == "incomplete"
    assert "processing_metrics_unavailable" in oversized_metrics["reasons"]

    with sqlite3.connect(database) as connection:
        run_metrics = json.loads(original_metrics_json)
        run_metrics["capture_quality"][
            "player_participant_observation_run_truncated"
        ] = True
        connection.execute(
            "UPDATE processing_runs SET metrics_json = ? WHERE run_id = ?",
            (json.dumps(run_metrics), row["run_id"]),
        )
    run_truncated = next(
        lap for lap in list_laps(database, run_id=imported.run_id)
        if lap["attempt_key"] == target["attempt_key"]
    )["player_participant_context"]
    assert run_truncated["status"] == "incomplete"
    assert "run_participant_observation_history_truncated" in run_truncated[
        "reasons"
    ]

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE processing_runs SET metrics_json = ? WHERE run_id = ?",
            (original_metrics_json, row["run_id"]),
        )
        attempt_scope = json.loads(original_attempt_json)
        malformed_participant = json.dumps(
            {
                "ai_controlled": False,
                "driver_id": 123,
                "network_id": 456,
                "team_id": 7,
                "my_team": "false",
                "race_number": 1,
                "nationality_id": 8,
                "name": {"unexpected": "object"},
                "your_telemetry": 1,
                "tech_level": 99,
                "platform_id": 0,
            }
        )
        connection.execute(
            """INSERT INTO player_participant_observations(
                       run_id,session_uid,frame_ordinal,frame_identifier,
                       overall_frame_identifier,packet_format,association_epoch,
                       association_scope_assessable,player_car_index,session_time_s,
                       status,reason,active_car_count,participant_json,
                       source_packet_count)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row["run_id"],
                row["session_uid"],
                row["start_frame_ordinal"],
                100,
                100,
                attempt_scope["start_association_packet_format"],
                attempt_scope["start_association_epoch"],
                1,
                row["car_index"],
                1.0,
                "observed",
                None,
                22,
                malformed_participant,
                1,
            ),
        )
    invalid_participant = next(
        lap for lap in list_laps(database, run_id=imported.run_id)
        if lap["attempt_key"] == target["attempt_key"]
    )["player_participant_context"]
    assert invalid_participant["status"] == "incomplete"
    assert invalid_participant["at_start"]["status"] == "unknown"
    assert invalid_participant["at_start"]["reason"] == "participant_payload_unavailable"

    nested_json = '{"name":' + "[" * 2_000 + "0" + "]" * 2_000 + "}"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """UPDATE player_participant_observations SET participant_json = ?
                WHERE run_id = ? AND session_uid = ? AND frame_ordinal = ?""",
            (nested_json, row["run_id"], row["session_uid"], row["start_frame_ordinal"]),
        )
    nested_participant = next(
        lap for lap in list_laps(database, run_id=imported.run_id)
        if lap["attempt_key"] == target["attempt_key"]
    )["player_participant_context"]
    assert nested_participant["at_start"]["reason"] == "participant_payload_unavailable"

    with sqlite3.connect(database) as connection:
        connection.execute(
            """UPDATE player_participant_observations SET participant_json = ?
                WHERE run_id = ? AND session_uid = ? AND frame_ordinal = ?""",
            (
                json.dumps({"name": "x" * 5_000}),
                row["run_id"],
                row["session_uid"],
                row["start_frame_ordinal"],
            ),
        )
    oversized_participant = next(
        lap for lap in list_laps(database, run_id=imported.run_id)
        if lap["attempt_key"] == target["attempt_key"]
    )["player_participant_context"]
    assert oversized_participant["status"] == "incomplete"
    assert oversized_participant["at_start"]["status"] == "unknown"
    assert oversized_participant["at_start"]["reason"] == "participant_payload_exceeds_byte_limit"


@pytest.mark.parametrize(
    ("capture_options", "expected_reason"),
    [
        ({"completion_status": "incomplete"}, "capture_not_finalized"),
        ({"flashback_after_laps": True}, "target_superseded_by_flashback"),
    ],
)
def test_synthetic_capture_abstains_when_capture_or_lifecycle_gate_fails(
    tmp_path, capture_options, expected_reason
) -> None:
    capture = tmp_path / "gated.f1ecap"
    database = tmp_path / "gated.sqlite3"
    _write_synthetic_capture(capture, **capture_options)
    imported = import_capture(capture, database)
    target_key, _reference_key = _attempt_keys(database, imported.run_id)

    selection = select_reference(database, ReferenceRequest(target_key))

    assert selection.status is not ReferenceSelectionStatus.SELECTED
    assert expected_reason in selection.reasons


def test_game_invalid_target_stays_diagnostic_even_with_a_valid_prior_reference(
    tmp_path, monkeypatch
) -> None:
    capture = tmp_path / "invalid-target.f1ecap"
    database = tmp_path / "invalid-target.sqlite3"
    model = _test_model()
    _write_synthetic_capture(capture, invalid_lap=3)
    imported = import_capture(capture, database)
    target_key, reference_key = _attempt_keys(database, imported.run_id)
    _register_test_approval(monkeypatch, model)

    selection = select_reference(database, ReferenceRequest(target_key))
    result = compare_attempts(
        database, target_key, reference_key, track_model=model
    )

    assert selection.status is ReferenceSelectionStatus.SELECTED
    assert selection.diagnostic_only is True
    assert result["corner_loss_candidates"]["status"] == "abstained"
    assert "target_not_game_valid" in result["corner_loss_candidates"]["gate_reasons"]
    assert result["driving_pattern_assessment"]["coaching_eligible"] is False
    assert result["throttle_pattern_assessment"]["coaching_eligible"] is False


def test_missing_regional_telemetry_and_unapproved_model_do_not_rank_or_match(
    tmp_path, monkeypatch
) -> None:
    capture = tmp_path / "unsupported-region.f1ecap"
    database = tmp_path / "unsupported-region.sqlite3"
    model = _test_model()
    _write_synthetic_capture(capture, missing_telemetry=(3, 340.0, 650.0))
    imported = import_capture(capture, database)
    target_key, reference_key = _attempt_keys(database, imported.run_id)

    registry_key = (model.model_id, model.revision)
    monkeypatch.setattr(registry, "TRACK_MODEL_REGISTRY", {registry_key: model})
    monkeypatch.setattr(registry, "CORNER_CANDIDATE_RANKING_APPROVALS", {})
    unapproved = compare_attempts(database, target_key, reference_key, track_model=model)
    assert unapproved["corner_loss_candidates"]["status"] == "abstained"
    assert "track_model_not_approved_for_candidate_ranking" in unapproved[
        "corner_loss_candidates"
    ]["gate_reasons"]
    assert all(
        item["status"] != "matched"
        for report in (
            unapproved["driving_pattern_assessment"],
            unapproved["throttle_pattern_assessment"],
        )
        for item in report.get("regions", [])
    )

    _register_test_approval(monkeypatch, model)
    unsupported = compare_attempts(
        database, target_key, reference_key, track_model=model
    )
    assert len(unsupported["corner_loss_candidates"]["ranked_candidates"]) == 1
    assert unsupported["driving_pattern_assessment"]["regions"][0][
        "status"
    ] == "unavailable"
    assert unsupported["throttle_pattern_assessment"]["regions"][0][
        "status"
    ] == "unavailable"


def test_practice_qualifying_is_diagnostic_and_race_unknown_do_not_select_tt_references(
    tmp_path,
) -> None:
    practice_capture = tmp_path / "practice.f1ecap"
    practice_database = tmp_path / "practice.sqlite3"
    _write_synthetic_capture(practice_capture, session_type_id=1)
    practice_import = import_capture(practice_capture, practice_database)
    practice_target, practice_reference = _attempt_keys(
        practice_database, practice_import.run_id
    )
    practice_selection = select_reference(
        practice_database, ReferenceRequest(practice_target)
    )
    practice_comparison = compare_attempts(
        practice_database,
        practice_target,
        practice_reference,
        policy=ComparisonPolicy.PRACTICE_QUALIFYING,
    )
    assert practice_selection.selected_reference is None
    assert practice_comparison["diagnostic_only"] is True
    assert practice_comparison["corner_loss_candidates"]["status"] == "abstained"
    assert "unsupported_comparison_policy" in practice_comparison[
        "corner_loss_candidates"
    ]["gate_reasons"]

    for name, session_type_id in (("race", 15), ("unknown", 99)):
        capture = tmp_path / f"{name}.f1ecap"
        database = tmp_path / f"{name}.sqlite3"
        _write_synthetic_capture(capture, session_type_id=session_type_id)
        imported = import_capture(capture, database)
        target_key, _reference_key = _attempt_keys(database, imported.run_id)
        selection = select_reference(database, ReferenceRequest(target_key))
        assert selection.selected_reference is None
        assert selection.status in {
            ReferenceSelectionStatus.NO_ELIGIBLE_REFERENCE,
            ReferenceSelectionStatus.UNSUPPORTED_POLICY,
        }
