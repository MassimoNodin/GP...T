from __future__ import annotations

import json
import struct

import pytest
from fastapi.testclient import TestClient

from f1_engineer.api.app import create_app
from f1_engineer.engineer_activity import AskActivityLog, EngineerActivityStore
from f1_engineer.processing.evidence import PROCESSOR_VERSION, EvidenceStore
from f1_engineer.processing.session_summary import SessionSummaryService, SummaryLimits
from f1_engineer.storage.database import Database
from tests.helpers import make_datagram
from tests.test_car_setups import _body, _record
from tests.test_engineer_activity import StubRequest


def summary_evidence(tmp_path, packets, *, committed=None):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    context = json.dumps({"packet_format": 2025, "track_length_m": 5276, "rule_set": "time_trial"})
    with store.connect() as database:
        database.execute("INSERT INTO generations VALUES ('generation','test',?,'active',?,0)",
                         (PROCESSOR_VERSION, len(packets) if committed is None else committed))
        database.execute("INSERT INTO sessions VALUES ('session','generation','10','active','receiving',?,1)",
                         (context,))
        database.execute("INSERT INTO metadata VALUES ('generation',1,'logical_session',0,?)",
                         (json.dumps({"session_id": "session"}),))
        for sequence, packet in enumerate(packets, 1):
            database.execute("INSERT INTO journal VALUES ('generation',?,'datagram',?,?)",
                             (sequence, packet.payload, json.dumps({"captured_at_ns": 1790000000000000000})))
    return store


@pytest.mark.parametrize("packet_format", [2025, 2026])
def test_summary_exposes_only_the_recorded_player_setup(tmp_path, packet_format):
    packets = [make_datagram(packet_format=packet_format, packet_id=5, player_car_index=3,
                            body=_body(packet_format=packet_format, player=_record(front_wing=wing)),
                            session_time=float(sequence))
               for sequence, wing in enumerate((24, 24, 25), 1)]
    store = summary_evidence(tmp_path, packets)
    with store.connect() as database:
        before = list(database.iterdump())
    result = SessionSummaryService(store).summary("session")
    setup = result["player_car_setup"]
    assert setup["status"] == "available"
    assert setup["first_observed"]["setup"]["front_wing"] == 24
    assert setup["last_observed"]["setup"]["front_wing"] == 25
    assert setup["first_observed"]["player_car_index"] == 3
    assert setup["observed_change_count"] == 1
    assert setup["first_observed"]["session_time_s"] == 1
    with store.connect() as database:
        assert list(database.iterdump()) == before


def test_setup_does_not_read_uncommitted_or_other_occurrence_packets(tmp_path):
    packets = [make_datagram(packet_id=5, body=_body(player=_record(front_wing=wing)), player_car_index=3)
               for wing in (24, 99)]
    store = summary_evidence(tmp_path, packets, committed=1)
    setup = SessionSummaryService(store).summary("session")["player_car_setup"]
    assert setup["last_observed"]["setup"]["front_wing"] == 24
    with store.connect() as database:
        database.execute("INSERT INTO sessions VALUES ('other','generation','10','active','receiving',NULL,2)")
        database.execute("INSERT INTO metadata VALUES ('generation',2,'logical_session',0,?)",
                         (json.dumps({"session_id": "other"}),))
        database.execute("UPDATE generations SET committed_sequence=2")
    service = SessionSummaryService(store)
    assert service.summary("session")["player_car_setup"]["last_observed"]["setup"]["front_wing"] == 24
    assert service.summary("other")["player_car_setup"]["first_observed"]["setup"]["front_wing"] == 99


@pytest.mark.parametrize("player,body,reason", [
    (3, b"bad", "car_setup_decode_error"),
    (255, _body(), "car_setup_decode_error"),
    (3, _body(player=_record(fuel_load=float("nan"))), "car_setup_fields_invalid"),
])
def test_setup_reports_malformed_or_invalid_evidence(tmp_path, player, body, reason):
    store = summary_evidence(tmp_path, [make_datagram(packet_id=5, body=body, player_car_index=player)])
    app = create_app(tmp_path / "archive.sqlite3", evidence_database_path=store.path,
                     automatic_acquisition=False)
    with TestClient(app) as client:
        response = client.get("/api/v2/session-evidence/sessions/session/summary")
    assert response.status_code == 200
    setup = response.json()["data"]["player_car_setup"]
    assert setup["reason"] == reason
    assert setup["status"] in ("partial", "unavailable")
    if reason == "car_setup_fields_invalid":
        assert setup["first_observed"]["setup"]["fuel_load"] is None


def test_setup_missing_and_scan_budget_are_explicit(tmp_path):
    store = summary_evidence(tmp_path, [make_datagram(packet_id=3, body=b"SSTA" + bytes(12)),
                                       make_datagram(packet_id=5, body=_body())])
    result = SessionSummaryService(store, limits=SummaryLimits(scan_rows=1)).summary("session")
    assert result["player_car_setup"]["reason"] == "summary_scan_incomplete"
    assert result["player_car_setup"]["first_observed"] is None
    assert result["assists"]["reason"] == "summary_scan_incomplete"


def test_invalid_setup_evidence_between_valid_observations_stays_partial(tmp_path):
    store = summary_evidence(tmp_path, [
        make_datagram(packet_id=5, body=_body(player=_record(fuel_load=fuel)), player_car_index=3)
        for fuel in (25.0, float("nan"), 25.0)
    ])
    setup = SessionSummaryService(store).summary("session")["player_car_setup"]
    assert setup["status"] == "partial"
    assert setup["reason"] == "car_setup_fields_invalid"
    assert setup["observed_change_count"] == 0


def test_malformed_assists_are_not_reported_as_missing(tmp_path):
    store = summary_evidence(tmp_path, [make_datagram(packet_id=1, body=b"bad")])
    assists = SessionSummaryService(store).summary("session")["assists"]
    assert assists["status"] == "unavailable"
    assert assists["reason"] == "session_settings_decode_error"
    assert assists["decode_errors"] == 1


@pytest.mark.parametrize("packet_format,body_size", [(2025, 724), (2026, 897)])
def test_assist_ids_and_rule_facts_are_measured_not_guessed(tmp_path, packet_format, body_size):
    body = bytearray(body_size)
    body[656:659] = bytes((1, 2, 3))
    body[666] = 2
    body[679] = 1
    store = summary_evidence(tmp_path, [make_datagram(packet_format=packet_format, packet_id=1,
                                                    body=bytes(body), session_time=0)])
    result = SessionSummaryService(store).summary("session")
    assists = result["assists"]
    assert assists["status"] == "available"
    assert assists["first_observed"]["steering_assist_id"] == 1
    assert assists["first_observed"]["braking_assist_id"] == 2
    assert assists["first_observed"]["gearbox_assist_id"] == 3
    assert assists["first_observed"]["rule_set"] == "time_trial"
    assert assists["damage_setting"] is None
    assert assists["damage_setting_reason"] == "damage_setting_not_in_telemetry"
    assert result["session"]["track_length_m"] == 5276
    assert result["player_car_setup"]["reason"] == "car_setup_not_recorded"


def test_event_names_survive_later_telemetry_and_setup_packets(tmp_path):
    participants = bytearray(1 + 22 * 57)
    participants[0] = 22
    participants[8:14] = b"Driver"
    store = summary_evidence(tmp_path, [
        make_datagram(packet_id=4, body=bytes(participants)),
        make_datagram(packet_id=5, body=_body()),
        make_datagram(packet_id=6, body=b"irrelevant telemetry"),
        make_datagram(packet_id=3, body=b"FTLP" + struct.pack("<Bf", 0, 90.5) + bytes(7)),
    ])
    event = SessionSummaryService(store).summary("session")["events"][0]
    assert event["driver_name"] == "Driver"
    assert "Driver" in event["label"]


def test_history_can_page_back_more_than_two_hundred_events(tmp_path):
    app = create_app(tmp_path / "archive.sqlite3", automatic_acquisition=False)
    store = app.state.engineer_activity.store
    for index in range(205):
        store.append(kind="proactive", text=str(index), source="live_rules", session_id="session")
    with TestClient(app) as client:
        newest = client.get("/api/v2/engineer/events?limit=200&session_id=session").json()["data"]
        assert len(newest["events"]) == 200
        oldest_id = newest["events"][0]["id"]
        response = client.get(f"/api/v2/engineer/events?before={oldest_id}&limit=200&session_id=session")
        older = response.json()["data"]
        assert [event["id"] for event in older["events"]] == list(range(1, 6))
        assert older["next_before"] == 1
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/v2/engineer/events?before=1").json()["data"]["events"] == []
        assert client.get("/api/v2/engineer/events?after=205").json()["data"]["next_after"] == 205
        conflict = client.get("/api/v2/engineer/events?after=0&before=5")
        assert conflict.status_code == 422
        assert conflict.json()["reason"] == "engineer_activity_cursor_conflict"
        for query in ("before=-1", "before=invalid", "limit=201"):
            assert client.get(f"/api/v2/engineer/events?{query}").status_code == 422


def test_session_history_excludes_unbound_answers_but_keeps_health_notices(tmp_path):
    store = EngineerActivityStore(tmp_path / "activity.sqlite3")
    store.append(kind="question", text="unbound", source="v1_ask")
    store.append(kind="question", text="other session", source="v2_ask", session_id="other")
    store.append(kind="issue", text="health", source="system")
    store.append(kind="question", text="this session", source="v1_ask", session_id="session")
    assert [event["text"] for event in store.events(session_id="session")[0]] == ["health", "this session"]
    assert [event["text"] for event in store.events(before=4, session_id="session")[0]] == ["health"]
    assert len(store.events()[0]) == 4


def test_imported_questions_keep_exact_session_and_lap(tmp_path):
    path = tmp_path / "archive.sqlite3"
    with Database(path) as database:
        database.connection.execute("INSERT INTO captures VALUES ('capture','test',1,1,NULL,'{}',CURRENT_TIMESTAMP)")
        database.connection.execute(
            "INSERT INTO processing_runs(run_id,capture_sha256,pipeline_version,config_json,status) "
            "VALUES ('run','capture','test','{}','complete')")
        database.connection.execute("INSERT INTO sessions VALUES ('session','run','10',2025,NULL)")
        database.connection.execute(
            "INSERT INTO lap_attempts(attempt_key,session_key,car_index,attempt_number,lap_number,"
            "disposition,start_frame_identifier,start_session_time_s,start_observed,pit_encountered,"
            "sample_count,reference_eligible,exclusion_reasons_json,attempt_json) "
            "VALUES ('attempt','session',0,1,7,'completed',1,0,1,0,0,0,'[]','{}')")
        database.connection.commit()
    store = EngineerActivityStore(tmp_path / "activity.sqlite3")
    log = AskActivityLog(store, legacy_database_path=path)
    log.note_question(StubRequest(), source="v1_ask", question="Summarise this lap",
                      detail={"selection": {"target_attempt_key": "attempt"}})
    event = store.events()[0][0]
    assert event["session_id"] == "session"
    assert event["lap"] == 7


def test_all_current_and_planned_ui_backend_routes_are_registered(tmp_path):
    app = create_app(tmp_path / "archive.sqlite3", automatic_acquisition=False)
    available = {(method, route.path) for route in app.routes
                 for method in getattr(route, "methods", ())}
    areas = {
        "overview": [
            ("GET", "/api/v1/sessions"), ("GET", "/api/v1/laps"),
            ("GET", "/api/v2/session-evidence/sessions"),
            ("GET", "/api/v2/session-evidence/sessions/{session_id}/attempts"),
            ("GET", "/api/v2/session-evidence/sessions/{session_id}/summary"),
            ("GET", "/api/v2/session-evidence/sessions/{session_id}/laps"),
        ],
        "lap_analysis": [
            ("GET", "/api/v1/attempts/{attempt_key}/traces"),
            ("GET", "/api/v2/session-evidence/sessions/{session_id}/attempts/{revision_id}"),
            ("POST", "/api/v2/session-evidence/sessions/{session_id}/attempts/{revision_id}/materialize"),
            ("GET", "/api/v2/session-evidence/sessions/{session_id}/track-models"),
            ("GET", "/api/v1/track-models/{model_id}/regions"),
            ("POST", "/api/v1/engineer/ask"),
            ("POST", "/api/v2/session-evidence/sessions/{session_id}/ask"),
        ],
        "library": [
            ("GET", "/api/v1/recording-sources/page"),
            ("GET", "/api/v1/recording-sources/{capture_id}/download"),
            ("POST", "/api/v1/recording-sources/upload"),
            ("POST", "/api/v1/import-jobs"), ("GET", "/api/v1/import-jobs/queue"),
            ("GET", "/api/v1/import-jobs/{job_id}"),
            ("POST", "/api/v1/import-jobs/{job_id}/cancel"),
            ("POST", "/api/v1/import-jobs/{job_id}/retry"),
        ],
        "drive_and_settings": [
            ("GET", "/api/v2/engineer/events"), ("GET", "/api/v2/session-evidence/status"),
            ("GET", "/api/v1/engineer/runtime"), ("GET", "/api/v1/engineer/transcribe"),
            ("POST", "/api/v1/engineer/transcribe"),
            ("GET", "/api/v1/engineer/preferences"), ("PUT", "/api/v1/engineer/preferences"),
            ("GET", "/api/v1/storage/usage"), ("GET", "/api/v1/track-models"),
            ("GET", "/api/v1/telemetry/service"),
            ("GET", "/api/v1/recording-groups/current"),
            ("POST", "/api/v1/recording-groups/start"),
            ("GET", "/api/v1/replays/current"), ("POST", "/api/v1/replays/start"),
        ],
    }
    for area, required in areas.items():
        assert set(required) <= available, f"Missing backend dependencies for {area}: {set(required) - available}"
