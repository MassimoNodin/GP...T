from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from f1_engineer.analysis.session_comparison import (
    SessionComparisonPolicy,
    compare_session_laps,
    measure_completed_laps,
)
from f1_engineer.api import app as app_module
from f1_engineer.api.app import create_app
from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.evidence import EvidenceStore
from f1_engineer.processing.evidence import EvidenceUnavailable
from tests.helpers import make_datagram
from tests.test_lap_tracking import SESSION_FIXTURE, SESSION_UID
from tests.test_session_evidence import admitted_packets


def _context(**updates: object) -> dict[str, object]:
    context: dict[str, object] = {
        "packet_format": 2026,
        "session_type": "short_practice",
        "game_mode": "driver_career_25",
        "rule_set": "practice_qualifying",
        "track_id": 5,
        "track_name": "Austria",
        "track_length_m": 4318,
        "formula_id": 0,
        "equal_car_performance_id": 0,
        "steering_assist_id": 0,
        "braking_assist_id": 0,
        "gearbox_assist_id": 1,
    }
    context.update(updates)
    return context


def _attempt(identifier: str, *, context: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "id": identifier,
        "session": "session-1",
        "role": "player",
        "driver": "driver-1",
        "readiness": {"state": "published", "reason": "completed_observed_lap", "sequence": 1},
        "manifest": {"format": 2026, "epoch": 1, "qualifications": []},
        "payload": {
            "disposition": "completed",
            "lap_time_ms": 70_000,
            "game_valid": True,
            "start_observed": True,
            "pit_encountered": False,
            "car_index": 0,
            "context_segments": [
                {"from_frame_identifier": 1, "context": context or _context()}
            ],
        },
    }


def _samples(offset: int = 0) -> list[dict[str, object]]:
    return [
        {
            "frame_identifier": index + offset,
            "current_lap_time_ms": index * 1000,
            "lap_distance_m": index * 100,
            "speed_mps": 40 + index,
            "throttle": 0.5,
            "brake": 0.0,
            "steering": 0.0,
            "gear": 4,
            "drs_active": False,
            "session_time_s": float(index),
        }
        for index in range(5)
    ]


class _Provider:
    def __init__(self, target: dict[str, object], reference: dict[str, object]) -> None:
        self.attempts = {target["id"]: target, reference["id"]: reference}

    def evidence(self, attempt_id: str, *, session: str | None = None):
        attempt = deepcopy(self.attempts[attempt_id])
        return attempt, _samples(0 if attempt_id == "target" else 1)


def test_practice_qualifying_published_pair_is_explicitly_diagnostic() -> None:
    provider = _Provider(_attempt("target"), _attempt("reference"))

    report = measure_completed_laps(
        provider, "session-1", "target", "reference",
        policy=SessionComparisonPolicy.PRACTICE_QUALIFYING,
    )

    assert report["comparison_policy"] == "practice_qualifying"
    assert report["diagnostic_only"] is True
    assert report["policy"] == "practice-qualifying-diagnostic-v1"
    assert {
        "practice_qualifying_diagnostic_only",
        "fuel_load_uncontrolled",
        "tyre_condition_uncontrolled",
        "traffic_uncontrolled",
        "cooldown_intent_uncontrolled",
        "no_coaching_or_ranking_claim",
        "measurement_not_reference_ranking",
    } <= set(report["qualifications"])


def test_practice_qualifying_comparison_persists_coordinator_evidence_report(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "practice-comparison")
    context_body = bytearray(SESSION_FIXTURE.read_bytes()[29:])
    context_body[6] = 1
    context_body[665] = 28
    context_body[666] = 0
    coordinator.ingest(make_datagram(
        packet_format=2025,
        packet_id=1,
        session_uid=SESSION_UID,
        frame=0,
        session_time=0,
        body=bytes(context_body),
        sequence=0,
    ))
    for packet in admitted_packets(frames=70):
        coordinator.ingest(replace(packet, sequence=packet.sequence + 1))

    session = store.sessions()[0]["id"]
    player_attempts = [
        attempt for attempt in store.attempts(session)
        if attempt["role"] == "player"
        and attempt["readiness"]["state"] == "published"
        and attempt["payload"]["disposition"] == "completed"
        and attempt["payload"]["start_observed"] is True
        and attempt["payload"]["game_valid"] is True
        and attempt["payload"]["pit_encountered"] is False
        and attempt["payload"]["lap_time_ms"] > 0
    ]
    assert len(player_attempts) >= 2
    target, reference = player_attempts[-2:]
    assert target["session"] == reference["session"] == session
    assert target["driver"] == reference["driver"]
    assert target["payload"]["car_index"] == reference["payload"]["car_index"]

    report = compare_session_laps(
        store, session, target["id"], reference["id"],
        policy=SessionComparisonPolicy.PRACTICE_QUALIFYING,
    )

    assert report["comparison_policy"] == "practice_qualifying"
    assert report["diagnostic_only"] is True
    assert store.report(report["id"]) == report
    coordinator.close()


def test_default_policy_keeps_existing_diagnostic_measurement_behavior() -> None:
    target = _attempt("target")
    target["payload"]["game_valid"] = False
    target["payload"]["start_observed"] = False
    target["manifest"]["qualifications"] = ["lap_start_unobserved"]

    report = measure_completed_laps(
        _Provider(target, _attempt("reference")), "session-1", "target", "reference"
    )

    assert report["policy"] == "observed-session-distance-v1"
    assert "comparison_policy" not in report
    assert "diagnostic_only" not in report
    assert "invalid_or_unknown_game_validity" in report["qualifications"]
    assert "lap_start_unobserved" in report["qualifications"]


@pytest.mark.parametrize(
    ("payload_update", "reason"),
    [
        ({"disposition": "partial"}, "not_completed"),
        ({"lap_time_ms": 0}, "positive_lap_time"),
        ({"game_valid": False}, "game_validity"),
        ({"game_valid": None}, "game_validity"),
        ({"start_observed": False}, "start_unobserved"),
        ({"pit_encountered": True}, "pit_encountered"),
    ],
)
def test_practice_policy_rejects_unqualified_attempts(payload_update, reason) -> None:
    target = _attempt("target")
    target["payload"].update(payload_update)

    with pytest.raises(EvidenceUnavailable, match=reason):
        measure_completed_laps(
            _Provider(target, _attempt("reference")), "session-1", "target", "reference",
            policy="practice_qualifying",
        )


def test_practice_policy_rejects_acquisition_gap_and_different_player() -> None:
    target = _attempt("target")
    target["manifest"]["qualifications"] = ["acquisition_gap"]
    with pytest.raises(EvidenceUnavailable, match="acquisition_gap"):
        measure_completed_laps(
            _Provider(target, _attempt("reference")), "session-1", "target", "reference",
            policy="practice_qualifying",
        )

    target = _attempt("target")
    reference = _attempt("reference")
    reference["driver"] = "driver-2"
    with pytest.raises(EvidenceUnavailable, match="share_player"):
        measure_completed_laps(
            _Provider(target, reference), "session-1", "target", "reference",
            policy="practice_qualifying",
        )


def test_practice_policy_rejects_ai_evidence_even_when_car_and_driver_match() -> None:
    target = _attempt("target")
    reference = _attempt("reference")
    target["role"] = reference["role"] = "opponent"

    with pytest.raises(EvidenceUnavailable, match="player_evidence"):
        measure_completed_laps(
            _Provider(target, reference), "session-1", "target", "reference",
            policy="practice_qualifying",
        )


def test_practice_policy_rejects_evidence_outside_requested_session() -> None:
    target = _attempt("target")
    reference = _attempt("reference")
    reference["session"] = "session-2"

    with pytest.raises(EvidenceUnavailable, match="share_session"):
        measure_completed_laps(
            _Provider(target, reference), "session-1", "target", "reference",
            policy="practice_qualifying",
        )


@pytest.mark.parametrize(
    "context_update",
    [
        {"rule_set": "race"},
        {"session_type": None},
        {"track_name": None},
        {"track_id": 7},
    ],
)
def test_practice_policy_rejects_unknown_or_incompatible_context(context_update) -> None:
    target = _attempt("target")
    reference = _attempt("reference", context=_context(**context_update))

    with pytest.raises(EvidenceUnavailable):
        measure_completed_laps(
            _Provider(target, reference), "session-1", "target", "reference",
            policy="practice_qualifying",
        )


def test_quarantined_attempt_remains_unavailable_for_practice_policy() -> None:
    target = _attempt("target")
    target["readiness"]["state"] = "quarantined"

    with pytest.raises(EvidenceUnavailable, match="measurement_ready"):
        measure_completed_laps(
            _Provider(target, _attempt("reference")), "session-1", "target", "reference",
            policy="practice_qualifying",
        )


def test_v2_compare_route_defaults_and_forwards_explicit_policy(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    def compare(_store, _session, _target, _reference, *, policy):
        calls.append(policy)
        return {"comparison_policy": policy}

    monkeypatch.setattr(app_module, "compare_session_laps", compare)
    app = create_app(
        tmp_path / "archive.sqlite3",
        recordings_root=tmp_path / "recordings",
        control_token="test-token",
        automatic_acquisition=False,
        evidence_database_path=tmp_path / "evidence.sqlite3",
    )
    headers = {"Authorization": "Bearer test-token"}
    base_url = "/api/v2/session-evidence/sessions/session-1/compare?target=t&reference=r"
    with TestClient(app) as client:
        default_response = client.post(base_url, headers=headers)
        explicit_response = client.post(
            f"{base_url}&comparison_policy=practice_qualifying", headers=headers
        )

    assert default_response.json()["data"]["comparison_policy"] == "observed_session_distance"
    assert explicit_response.json()["data"]["comparison_policy"] == "practice_qualifying"
    assert calls == ["observed_session_distance", "practice_qualifying"]
