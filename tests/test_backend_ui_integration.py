from __future__ import annotations

from fastapi.testclient import TestClient

from f1_engineer.api.app import create_app
from f1_engineer.analysis.engineer_preferences import DEFAULT_PREFERENCES


def test_application_passes_persisted_style_to_both_ask_paths(tmp_path, monkeypatch):
    from f1_engineer.api import app as module

    captured = []

    async def answer(*args, **kwargs):
        captured.append(kwargs["style"])
        return {"message": "Diagnostic only.", "status": "available"}

    monkeypatch.setattr(module, "answer_engineer_question", answer)
    monkeypatch.setattr(module, "answer_session_question", answer)
    app = create_app(tmp_path / "archive.sqlite3", control_token="test-token", automatic_acquisition=False)
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(app) as client:
        prefs = {**DEFAULT_PREFERENCES.to_dict(), "verbosity": "concise", "depth": "quick", "interpretations": False}
        assert client.put("/api/v1/engineer/preferences", json=prefs, headers=headers).status_code == 200
        assert client.post("/api/v1/engineer/ask", headers=headers, json={
            "question": "Summarize the lap", "selection": {"intent": "attempt_summary", "target_attempt_key": "missing"},
        }).status_code == 200
        assert client.post("/api/v2/session-evidence/sessions/test/ask", headers=headers, json={
            "question": "Summarize the lap", "binding_id": "a" * 64,
        }).status_code == 200
    assert len(captured) == 2
    assert all(style["verbosity"] == "concise" and not style["include_interpretations"] for style in captured)


def test_activity_filter_reads_updated_preferences_and_preserves_health_notices(tmp_path):
    app = create_app(tmp_path / "archive.sqlite3", control_token="test-token", automatic_acquisition=False)
    with TestClient(app) as client:
        prefs = DEFAULT_PREFERENCES.to_dict()
        prefs["topics"] = dict.fromkeys(prefs["topics"], False)
        assert client.put("/api/v1/engineer/preferences", json=prefs,
                          headers={"Authorization": "Bearer test-token"}).status_code == 200
        assert app.state.engineer_activity.current_topics() == {"telemetry"}
        prefs["topics"]["driving_technique"] = True
        assert client.put("/api/v1/engineer/preferences", json=prefs,
                          headers={"Authorization": "Bearer test-token"}).status_code == 200
        assert app.state.engineer_activity.current_topics() == {"telemetry", "lap_time"}


def test_lap_enrichment_route_is_registered(tmp_path):
    app = create_app(tmp_path / "archive.sqlite3", automatic_acquisition=False)
    with TestClient(app) as client:
        response = client.get("/api/v2/session-evidence/sessions/missing/laps")
        assert response.status_code == 404
        assert response.json()["reason"] == "session_not_found"


def test_lap_enrichment_uses_published_occurrence_boundaries(tmp_path):
    from f1_engineer.processing.lap_enrichment import LapEnrichmentService
    from tests.test_session_summary import summary_store

    store = summary_store(tmp_path)
    with store.connect() as database:
        sessions = {row["id"]: row for row in database.execute("SELECT * FROM sessions")}
        assert LapEnrichmentService._journal_range(database, sessions["first"], 5) == (
            1, 3, "published_session_boundaries",
        )
        assert LapEnrichmentService._journal_range(database, sessions["second"], 5) == (
            4, 5, "published_session_boundaries",
        )
