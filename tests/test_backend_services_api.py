from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from f1_engineer.api.app import create_app
from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.evidence import EvidenceStore
from tests.helpers import make_datagram
from tests.test_lap_tracking import SESSION_FIXTURE, SESSION_UID
from tests.test_session_evidence import admitted_packets
from tests.test_materialization import deferred_fixture, snapshot


TOKEN = "backend-service-test-token-0123456789abcdef"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def application(tmp_path, store):
    return create_app(tmp_path / "archive.sqlite3", evidence_database_path=store.path,
                      recordings_root=tmp_path / "recordings", control_token=TOKEN,
                      automatic_acquisition=False)


@pytest.fixture
def practice_evidence(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    coordinator = SessionCoordinator(store, "backend-api-practice")
    context_body = bytearray(SESSION_FIXTURE.read_bytes()[29:])
    context_body[6] = 1
    context_body[665] = 28
    context_body[666] = 0
    coordinator.ingest(make_datagram(packet_format=2025, packet_id=1, session_uid=SESSION_UID,
                                    frame=0, session_time=0, body=bytes(context_body), sequence=0))
    for raw in admitted_packets(frames=70):
        coordinator.ingest(replace(raw, sequence=raw.sequence + 1))
    session = store.sessions()[0]["id"]
    attempts = sorted((item for item in store.attempts(session)
                       if item["role"] == "player" and item["readiness"]["state"] == "published"
                       and item["payload"]["start_observed"]), key=lambda item: item["payload"]["lap_number"])
    assert len(attempts) >= 2
    try:
        yield store, coordinator, session, attempts[-1]["id"], attempts[-2]["id"]
    finally:
        coordinator.close()


def test_backend_reference_analysis_and_engineer_are_grounded_and_cached(tmp_path, practice_evidence):
    store, _, session, target, reference = practice_evidence
    before = snapshot(store)
    with TestClient(application(tmp_path, store)) as client:
        base = f"/api/v2/session-evidence/sessions/{session}"
        selected = client.post(base + "/reference", params={"target": target}, headers=HEADERS)
        assert selected.status_code == 200
        assert selected.json()["data"]["status"] == "available"
        assert selected.json()["data"]["reference"] == reference
        parameters = {"target": target, "reference": reference, "comparison_policy": "practice_qualifying"}
        first = client.post(base + "/analyze", params=parameters, headers=HEADERS)
        assert first.status_code == 200
        report = first.json()["data"]
        assert report["diagnostic_only"] is True
        assert report["coaching_eligible"] is False
        assert report["target"]["revision"] == target
        assert report["reference"]["revision"] == reference
        second = client.post(base + "/analyze", params=parameters, headers=HEADERS)
        assert second.json() == first.json()
        status = client.get("/api/v2/session-evidence/services/status").json()["data"]
        assert status["measurement_cache"]["entries"] == 1
        assert status["measurement_cache"]["hits"] == 1
        answer = client.post(base + "/ask", headers=HEADERS,
                             json={"target": target, "reference": reference, "question": "Compare these laps"})
        assert answer.status_code == 200
        data = answer.json()["data"]
        assert data["status"] == "available"
        assert data["diagnostic_only"] is True
        assert data["coaching_eligible"] is False
        assert data["ranking_eligible"] is False
    assert snapshot(store) == before


def test_backend_materialization_is_ephemeral_and_source_immutable(tmp_path):
    store, coordinator, session, attempt = deferred_fixture(tmp_path)
    try:
        before = snapshot(store)
        with TestClient(application(tmp_path, store)) as client:
            url = f"/api/v2/session-evidence/sessions/{session}/attempts/{attempt['id']}/materialize"
            assert client.post(url).status_code == 403
            response = client.post(url, headers=HEADERS)
            assert response.status_code == 200
            data = response.json()["data"]
            assert data["attempt"]["id"] == attempt["id"]
            assert data["attempt"]["materialization"]["source_revision"] == attempt["id"]
            assert data["persistence"] == "request_scoped_ephemeral"
            assert data["samples"]
            trace = client.get(f"/api/v2/session-evidence/sessions/{session}/attempts/{attempt['id']}")
            assert trace.status_code == 422
            assert trace.json()["reason"] == "trace_not_selected"
        assert snapshot(store) == before
    finally:
        coordinator.close()


@pytest.mark.parametrize("endpoint", ["reference", "analyze", "ask"])
def test_backend_expensive_routes_require_control_token(tmp_path, practice_evidence, endpoint):
    store, _, session, target, reference = practice_evidence
    with TestClient(application(tmp_path, store)) as client:
        response = client.post(f"/api/v2/session-evidence/sessions/{session}/{endpoint}",
                               params={"target": target, "reference": reference},
                               json={"target": target, "question": "Compare"})
        assert response.status_code == 403


@pytest.mark.parametrize("payload", [
    {"target": "target", "question": "x", "unexpected": True},
    {"target": "target", "question": "x" * 2001},
    {"question": "x"},
    {"target": "target", "binding_id": "a" * 64, "question": "x"},
    {"target": "target", "question": "x", "use_model": "yes"},
])
def test_backend_ask_rejects_invalid_or_ambiguous_requests(tmp_path, practice_evidence, payload):
    store, _, session, _, _ = practice_evidence
    with TestClient(application(tmp_path, store)) as client:
        response = client.post(f"/api/v2/session-evidence/sessions/{session}/ask", json=payload, headers=HEADERS)
        assert response.status_code == 422


def test_backend_ask_request_bytes_are_bounded(tmp_path, practice_evidence):
    store, _, session, _, _ = practice_evidence
    with TestClient(application(tmp_path, store)) as client:
        response = client.post(f"/api/v2/session-evidence/sessions/{session}/ask", content=b" " * 8193, headers=HEADERS)
        assert response.status_code == 413


def test_backend_cross_session_lookup_fails_closed(tmp_path, practice_evidence):
    store, _, _, target, reference = practice_evidence
    with TestClient(application(tmp_path, store)) as client:
        response = client.post("/api/v2/session-evidence/sessions/other/reference", headers=HEADERS,
                               params={"target": target, "reference": reference})
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "unavailable"
        assert response.json()["data"]["reference"] is None


def test_backend_future_request_without_listener_does_not_invoke_model(tmp_path, practice_evidence):
    store, _, session, _, _ = practice_evidence
    with TestClient(application(tmp_path, store)) as client:
        response = client.post(f"/api/v2/session-evidence/sessions/{session}/ask", headers=HEADERS,
                               json={"binding_id": "a" * 64, "question": "Observe the next lap", "use_model": True})
        assert response.status_code == 200
        assert response.json()["data"]["status"] in {"pending", "unavailable"}


def test_backend_work_admission_does_not_block_ingestion(tmp_path, practice_evidence, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from f1_engineer.api import app as module

    store, coordinator, session, target, reference = practice_evidence
    original = module.analyze_session_pair
    started = threading.Barrier(3)
    release = threading.Event()

    def blocked(*args, **kwargs):
        started.wait(timeout=5)
        assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "analyze_session_pair", blocked)
    with TestClient(application(tmp_path, store)) as client, ThreadPoolExecutor(max_workers=2) as workers:
        def request():
            return client.post(f"/api/v2/session-evidence/sessions/{session}/analyze", headers=HEADERS,
                               params={"target": target, "reference": reference})
        requests = [workers.submit(request) for _ in range(2)]
        try:
            started.wait(timeout=5)
            rejected = request()
            assert rejected.status_code == 409
            assert rejected.json()["reason"] == "comparison_read_budget_busy"
            before = coordinator.sequence
            coordinator.ingest(make_datagram(packet_id=255, session_uid=SESSION_UID,
                                            frame=71, session_time=3.55, sequence=1000))
            assert coordinator.sequence == before + 1
        finally:
            release.set()
        assert all(result.result(timeout=5).status_code == 200 for result in requests)
