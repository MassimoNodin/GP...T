from __future__ import annotations

import asyncio
import json
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
from f1_engineer.storage.importer import import_capture, list_laps
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

        for lap_number in range(1, 5):
            prior_duration_ms = _LAP_DURATIONS_MS.get(lap_number - 1, 0)
            if lap_number == 1:
                write_sample(1, -0.5)
            write_sample(
                lap_number,
                0.5,
                last_lap_time_ms=prior_duration_ms,
            )
            if lap_number == 4:
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

    repeated = import_capture(capture, database)
    assert repeated.run_id == imported.run_id
    assert repeated.already_imported is True
    assert repeated.attempts == imported.attempts


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
