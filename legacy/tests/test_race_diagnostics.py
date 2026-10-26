from copy import deepcopy
import struct

import pytest

from f1_engineer.analysis.race_diagnostics import build_session_race_diagnostics
from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.evidence import EvidenceStore
from f1_engineer.processing.evidence import EvidenceUnavailable
from tests.helpers import make_datagram
from tests.test_car_lap_inventory import _participants_body
from tests.test_lap_tracking import SESSION_FIXTURE, SESSION_UID, _lap_body
from tests.test_session_reference import _attempt


def _v2_attempt(revision="race-lap", race_type=15):
    attempt = _attempt(revision)
    attempt["role"] = "player"
    attempt["binding_verified"] = True
    attempt["manifest"]["qualifications"] = []
    attempt["manifest"].update(start_frame=100, end_frame=102)
    attempt["payload"].update(session_uid="session-uid", lap_number=4)
    context = attempt["payload"]["context_segments"][0]["context"]
    context.update(session_type_id=race_type, session_type={15: "race", 16: "race_2", 17: "race_3"}[race_type])
    rows = [
        {
            "session_uid": "session-uid",
            "car_index": 0,
            "lap_number": 4,
            "lifecycle_epoch": 1,
            "packet_format": 2026,
            "gap_epoch": 3,
            "car_status_available": False,
            "frame_ordinal": ordinal,
            "frame_identifier": frame,
            "session_time_s": timestamp,
            "lap_distance_m": distance,
            "current_lap_time_ms": clock,
            "speed_mps": speed,
            "fuel_in_tank_reported": None,
            "visual_tyre_compound": None,
        }
        for ordinal, frame, timestamp, distance, clock, speed in (
            (100, 50, 1.0, 0.0, 0, 40.0),
            (101, 51, 1.02, 20.0, 20, 45.0),
            (102, 52, 1.04, 40.0, 40, 50.0),
        )
    ]
    attempt["rows"] = attempt["payload"]["sample_count"] = len(rows)
    return attempt, rows


class Provider:
    def __init__(self, attempt, rows):
        self.attempt = attempt
        self.rows = rows
        self.calls = []

    def evidence(self, revision, *, session=None):
        self.calls.append((revision, session))
        if revision != self.attempt["id"] or session != self.attempt["session"]:
            raise EvidenceUnavailable("attempt_not_in_session")
        return deepcopy(self.attempt), deepcopy(self.rows)


def test_v2_provider_interface_reports_one_race_lap_and_unknown_context():
    attempt, rows = _v2_attempt()
    provider = Provider(attempt, rows)
    report = build_session_race_diagnostics(provider, "session-1", "race-lap")
    assert provider.calls == [("race-lap", "session-1")]
    assert report["status"] == "available"
    assert report["source"]["epoch"] == 1
    assert report["source"]["lap_number"] == 4
    assert report["lap"]["sample_count"] == 3
    assert report["lap"]["distance_end_m"] == 40
    assert report["context"]["fuel"]["status"] == "unknown"
    assert report["context"]["tyres"]["status"] == "unknown"
    assert report["context"]["traffic"]["status"] == "unknown"
    assert report["context"]["tyre_stints"]["status"] == "unknown"
    assert report["qualifications"] == [
        "fuel_context_unknown", "tyre_context_unknown", "traffic_context_unknown", "tyre_stints_unknown"
    ]
    assert "samples" not in report["lap"]


@pytest.mark.parametrize("race_type", [15, 16, 17])
def test_supported_race_types_are_admitted(race_type):
    attempt, rows = _v2_attempt(race_type=race_type)
    assert build_session_race_diagnostics(Provider(attempt, rows), "session-1", "race-lap")["status"] == "available"


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda attempt, rows: attempt.update(binding_verified=False), "driver_binding_unverified"),
        (lambda attempt, rows: attempt["readiness"].update(state="deferred"), "evidence_not_published"),
        (lambda attempt, rows: attempt["manifest"].update(epoch=2), "trace_session_identity_mismatch"),
        (lambda attempt, rows: attempt["manifest"].update(qualifications=["acquisition_gap"]), "acquisition_gap"),
        (lambda attempt, rows: attempt["payload"]["context_segments"][0]["context"].update(session_type_id=1), "not_supported_race_session_type"),
        (lambda attempt, rows: rows[1].update(gap_epoch=4), "trace_acquisition_gap_or_unknown"),
        (lambda attempt, rows: rows.pop(), "missing_observation_rows"),
        (lambda attempt, rows: rows[1].update(frame_ordinal=105), "trace_frame_regression"),
        (lambda attempt, rows: attempt["payload"]["context_segments"][0].update(context=None), "unknown_context"),
    ],
)
def test_uncertain_or_incompatible_v2_evidence_fails_closed(mutate, reason):
    attempt, rows = _v2_attempt()
    mutate(attempt, rows)
    report = build_session_race_diagnostics(Provider(attempt, rows), "session-1", "race-lap")
    assert report["status"] == "unavailable"
    assert isinstance(report["reason"], str)
    assert reason == report["reason"] or reason in report["reasons"]
    assert "lap" not in report


def test_wrong_session_and_unavailable_trace_return_guarded_reports():
    attempt, rows = _v2_attempt()
    report = build_session_race_diagnostics(Provider(attempt, rows), "other-session", "race-lap")
    assert report["status"] == "unavailable"
    assert report["reason"] == "attempt_not_in_session"


def test_compounds_are_categorical_and_false_car_status_blocks_stale_values():
    attempt, rows = _v2_attempt()
    for row, compound, fuel in zip(rows, (7, 8, 7), (8.0, 9.0, 10.0)):
        row.update(car_status_available=True, visual_tyre_compound=compound, fuel_in_tank_reported=fuel)
    rows[1]["fuel_in_tank_reported"] = -1.0
    report = build_session_race_diagnostics(Provider(attempt, rows), "session-1", "race-lap")
    assert report["context"]["tyres"]["compounds"] == [
        {"compound_id": 7, "sample_count": 2}, {"compound_id": 8, "sample_count": 1}
    ]
    assert "minimum" not in report["context"]["tyres"]
    assert report["context"]["fuel"]["minimum"] == 8.0
    rows[1]["car_status_available"] = False
    partial = build_session_race_diagnostics(Provider(attempt, rows), "session-1", "race-lap")
    assert partial["context"]["fuel"]["sample_count"] == 2
    assert partial["context"]["tyres"]["compounds"] == [
        {"compound_id": 7, "sample_count": 2}
    ]
    for row in rows:
        row["car_status_available"] = False
    blocked = build_session_race_diagnostics(Provider(attempt, rows), "session-1", "race-lap")
    assert blocked["context"]["fuel"]["status"] == "unknown"
    assert blocked["context"]["tyres"]["status"] == "unknown"


def test_real_evidence_store_race_rows_are_admitted(tmp_path):
    store = EvidenceStore(tmp_path / "race-evidence.sqlite3")
    coordinator = SessionCoordinator(store, "race-diagnostics-integration")
    try:
        race_context_body = bytearray(SESSION_FIXTURE.read_bytes()[29:])
        race_context_body[6] = 15
        race_context_body[665] = 28
        race_context_body[666] = 1
        coordinator.ingest(make_datagram(
            packet_format=2025,
            packet_id=1,
            session_uid=SESSION_UID,
            frame=0,
            session_time=0,
            body=bytes(race_context_body),
            sequence=0,
            player_car_index=0,
        ))
        coordinator.ingest(make_datagram(
            packet_format=2025,
            packet_id=4,
            session_uid=SESSION_UID,
            frame=0,
            session_time=0,
            body=_participants_body(),
            sequence=1,
        ))
        telemetry_format = struct.Struct("<HfffBbHBBH4H4B4BH4f4B")
        sequence = 2
        for frame in range(1, 72):
            if frame <= 5:
                lap_number, position = 1, -1
            else:
                lap_number = 1 + (frame - 6) // 20
                position = (frame - 6) % 20
            lap_body = _lap_body(
                lap_number=lap_number,
                distance_m=position * 5.0,
                current_lap_time_ms=max(0, position * 50),
                last_lap_time_ms=1_000 if lap_number > 1 else 0,
            )
            coordinator.ingest(make_datagram(
                packet_format=2025, packet_id=2, session_uid=SESSION_UID,
                frame=frame, session_time=frame * 0.05, body=lap_body, sequence=sequence,
            ))
            sequence += 1
            telemetry_records = [
                telemetry_format.pack(
                    100 + car, 0.5, 0.0, 0.0, 0, 4, 8000, 0, 50, 0,
                    *([300] * 4), *([90] * 4), *([80] * 4), 100,
                    *([22.0] * 4), *([0] * 4),
                )
                for car in range(22)
            ]
            coordinator.ingest(make_datagram(
                packet_format=2025, packet_id=6, session_uid=SESSION_UID,
                frame=frame, session_time=frame * 0.05,
                body=b"".join(telemetry_records) + bytes((0, 0, 0)), sequence=sequence,
            ))
            sequence += 1
        session = store.sessions()[0]["id"]
        attempts = store.attempts(session)
        published_player_laps = [
            attempt for attempt in attempts
            if attempt["role"] == "player"
            and attempt["readiness"]["state"] == "published"
            and attempt["payload"]["start_observed"]
            and attempt["payload"]["disposition"] == "completed"
        ]
        assert published_player_laps, [
            (item["payload"]["lap_number"], item["payload"]["start_observed"],
             item["payload"]["exclusion_reasons"],
             [(segment["context"] or {}).get("session_type_id")
              for segment in item["payload"]["context_segments"]])
            for item in attempts
        ]
        metadata, rows = store.evidence(published_player_laps[0]["id"], session=session)
        actual_context = metadata["payload"]["context_segments"][0]["context"]
        assert actual_context["session_type_id"] == 15
        assert actual_context["session_type"] == "race"
        assert rows and len({row["gap_epoch"] for row in rows}) == 1
        assert all("frame_ordinal" in row and "lifecycle_epoch" in row for row in rows)

        report = build_session_race_diagnostics(store, session, published_player_laps[0]["id"])
        assert report["status"] == "available", report
        assert report["context"]["race_session_type_id"] == 15
        assert report["lap"]["sample_count"] == len(rows)
    finally:
        coordinator.close()


def test_sparse_observation_frames_are_qualified_without_fabricating_samples():
    attempt, rows = _v2_attempt()
    attempt["manifest"]["end_frame"] = 104
    for index, row in enumerate(rows):
        row["frame_ordinal"] = 100 + index * 2
        row["frame_identifier"] = 50 + index * 2
    original = deepcopy(rows)
    report = build_session_race_diagnostics(Provider(attempt, rows), "session-1", "race-lap")
    assert report["status"] == "available"
    assert report["lap"]["sample_count"] == 3
    assert report["lap"]["nonconsecutive_frame_transitions"] == 2
    assert "nonconsecutive_observation_frames" in report["qualifications"]
    assert rows == original


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("frame_ordinal", 100, "trace_frame_regression"),
        ("frame_identifier", 50, "trace_frame_discontinuity"),
        ("frame_identifier", 49, "trace_frame_discontinuity"),
        ("session_time_s", 5.0, "trace_time_gap_or_regression"),
    ],
)
def test_sparse_admission_still_rejects_ordering_and_time_failures(field, value, reason):
    attempt, rows = _v2_attempt()
    rows[1][field] = value
    report = build_session_race_diagnostics(Provider(attempt, rows), "session-1", "race-lap")
    assert report["status"] == "unavailable"
    assert reason in report["reasons"]


@pytest.mark.parametrize(
    "frames,expected_sparse",
    [([0xFFFFFFFE, 0xFFFFFFFF, 0], 0), ([0xFFFFFFFD, 0xFFFFFFFF, 1], 2)],
)
def test_forward_frame_counter_wrap_is_admitted(frames, expected_sparse):
    attempt, rows = _v2_attempt()
    for row, frame in zip(rows, frames):
        row["frame_identifier"] = frame
    report = build_session_race_diagnostics(Provider(attempt, rows), "session-1", "race-lap")
    assert report["status"] == "available"
    assert report["lap"]["nonconsecutive_frame_transitions"] == expected_sparse
    assert ("nonconsecutive_observation_frames" in report["qualifications"]) == bool(expected_sparse)


def test_half_range_frame_counter_jump_is_rejected():
    attempt, rows = _v2_attempt()
    rows[1]["frame_identifier"] = rows[0]["frame_identifier"] + (1 << 31)
    report = build_session_race_diagnostics(Provider(attempt, rows), "session-1", "race-lap")
    assert report["status"] == "unavailable"
    assert "trace_frame_discontinuity" in report["reasons"]
