import asyncio
from copy import deepcopy

from fastapi.testclient import TestClient

from f1_engineer.api import app as api
from f1_engineer.analysis.engineer_ask import EngineerAskRoute
from f1_engineer.analysis.session_engineer import answer_session_question
from tests.test_race_diagnostics import _v2_attempt


TOKEN = "race-diagnostic-api-token-0123456789abcdef"


class Provider:
    def __init__(self, metadata, rows):
        self.metadata = metadata
        self.rows = rows

    def attempt(self, revision, *, session):
        assert revision == self.metadata["id"]
        assert session == self.metadata["session"]
        return deepcopy(self.metadata)

    def evidence(self, revision, *, session):
        return self.attempt(revision, session=session), deepcopy(self.rows)


class Runtime:
    def __init__(self, route="attempt_summary"):
        self.route = route
        self.calls = []

    async def route_question(self, question, **kwargs):
        self.calls.append(kwargs)
        return EngineerAskRoute(self.route, "overview"), {"model_name": "gpt-6-luna"}


def test_explicit_race_question_uses_single_lap_not_race_reference_policy():
    metadata, rows = _v2_attempt()
    runtime = Runtime()
    result = asyncio.run(answer_session_question(
        Provider(metadata, rows), "session-1", "race-lap", "Summarize my lap", runtime=runtime
    ))
    assert result["status"] == "available"
    assert result["selection"]["reference"] is None
    assert result["report"]["policy"] == "session-race-diagnostics-v1"
    assert result["model"]["model_name"] == "gpt-6-luna"
    assert runtime.calls == [{"selection_kind": "attempt", "allowed_routes": ("attempt_summary",)}]
    assert result["coaching_eligible"] is False


def test_uncertain_race_lap_abstains_before_model_use():
    metadata, rows = _v2_attempt()
    metadata["manifest"]["qualifications"] = ["acquisition_gap"]
    runtime = Runtime()
    result = asyncio.run(answer_session_question(
        Provider(metadata, rows), "session-1", "race-lap", "Summarize", runtime=runtime
    ))
    assert result["status"] == "unavailable"
    assert runtime.calls == []


def test_race_strategy_is_not_generated_from_single_lap():
    metadata, rows = _v2_attempt()
    result = asyncio.run(answer_session_question(
        Provider(metadata, rows), "session-1", "race-lap", "When should I pit?",
        runtime=Runtime("unsupported")
    ))
    assert result["status"] == "unsupported"
    assert result["reason"] == "question_unsupported"
    assert result["coaching_eligible"] is False


def test_race_diagnostics_route_requires_control_auth_and_returns_service_report(tmp_path, monkeypatch):
    calls = []

    def diagnostic(provider, session, target):
        calls.append((session, target))
        return {"status": "unavailable", "reason": "game_validity_required", "diagnostic_only": True}

    monkeypatch.setattr(api, "build_session_race_diagnostics", diagnostic)
    application = api.create_app(tmp_path / "archive.sqlite3", recordings_root=tmp_path / "recordings",
                                 control_token=TOKEN, automatic_acquisition=False)
    url = "/api/v2/session-evidence/sessions/session-1/race-diagnostics"
    with TestClient(application) as client:
        rejected = client.post(url, params={"target": "race-lap"})
        assert rejected.status_code == 403
        assert calls == []
        response = client.post(url, params={"target": "race-lap"},
                               headers={"Authorization": "Bearer " + TOKEN})
        assert response.status_code == 200
        assert response.json()["data"]["reason"] == "game_validity_required"
        assert calls == [("session-1", "race-lap")]
