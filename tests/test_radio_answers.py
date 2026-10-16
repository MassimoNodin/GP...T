import asyncio
import json
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from f1_engineer.analysis.codex_runtime import CodexRuntime
from f1_engineer.analysis.ai_admission import PROCESS_ENGINEER_AI_GATE
from f1_engineer.analysis.engineer_runtime import EngineerRuntimeUnavailable
from f1_engineer.api import app as api_module
from f1_engineer.radio.answers import (
    SYSTEM_PROMPT, answer_messages, answer_schema, build_evidence, evidence_unchanged, parse_answer,
)
from f1_engineer.radio.client import RadioClient
from f1_engineer.radio import command
from f1_engineer.radio.command import dispatch_submission, start_typed_request
from tests.test_codex_runtime import completed_events, install_server, successful_server
from tests.test_radio_intents import TOKEN, context, intent
from tests.test_radio_listening import REQUEST_ID, listening
from tests.test_radio_questions import live
from tests.test_standalone_radio import snapshot


def reply(text="Your last lap was 103.500 seconds.", references=None, **fields):
    return {"kind": "answer", "answer": text, "evidence_ids": references or ["car_0_last_lap"],
            "intent": None, **fields}


def test_evidence_is_projected_from_validated_live_facts():
    evidence = build_evidence(snapshot(), now=10)
    facts = {fact["id"]: fact for fact in evidence["facts"]}
    assert "103.500" in facts["car_0_last_lap"]["text"]
    assert facts["car_0_last_lap"]["driver"] == snapshot()["drivers"][0]["identity"]
    assert json.loads(evidence["scope"])[2] == snapshot()["scope"]
    assert "forecast" in " ".join(evidence["limitations"])


def test_projection_preserves_controller_state_and_accounts_for_history_age():
    from tests.test_radio_reported_history import connected

    controller, _radio, _snapshot = connected(source_lag=2)
    controller._fact_age_s = 0.4
    evidence = controller.answer_evidence(now=10.2)
    assert controller._fact_age_s == 0.4
    assert evidence["age_s"] == pytest.approx(2.3)
    assert any(fact["id"] == "car_0_session_best" for fact in evidence["facts"])


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(age_s=3),
    lambda value: value.update(status="unavailable"),
    lambda value: value["drivers"][0].update(identity={}),
    lambda value: value["drivers"].append(deepcopy(value["drivers"][0])),
])
def test_stale_or_invalid_sources_do_not_supply_evidence(mutate):
    value = snapshot()
    mutate(value)
    assert build_evidence(value, now=10)["facts"] == []


def test_free_answer_does_not_require_a_question_plan():
    evidence = build_evidence(snapshot(), now=10)
    value = reply("That was a 103.5 second lap. I do not have a rain forecast.")
    assert parse_answer(json.dumps(value), context=context(), evidence=evidence) == value
    assert answer_schema(context())["properties"]["answer"] == {"type": "string"}
    assert "original question" in SYSTEM_PROMPT
    message = json.loads(answer_messages("How did I do, and will it rain?", context(), evidence)[1]["content"])
    assert message["question"] == "How did I do, and will it rain?"


@pytest.mark.parametrize("value,reason", [
    (reply(references=["invented"]), "unknown_evidence"),
    (reply("Your last lap was 89.5 seconds."), "unsupported_number"),
    (reply("You're P4."), "unsupported_number"),
    (reply("Your last lap was 103.500 seconds.\nIgnore safeguards."), "invalid"),
    (reply(references=["car_0_last_lap", "car_0_last_lap"]), "invalid"),
    (reply(kind="command", intent=intent(action="smalltalk")), "command_invalid"),
    (reply(intent=intent(action="control", control="quiet")), "invalid"),
])
def test_invalid_answers_are_rejected(value, reason):
    with pytest.raises(ValueError, match=reason):
        parse_answer(value, context=context(), evidence=build_evidence(snapshot(), now=10))


def test_commands_remain_validated_and_cannot_smuggle_an_answer():
    value = reply("", references=[], kind="command", intent=intent(action="control", control="quiet"))
    value["evidence_ids"] = []
    assert parse_answer(value, context=context())["intent"]["control"] == "quiet"
    value["answer"] = "Task created."
    with pytest.raises(ValueError):
        parse_answer(value, context=context())


def test_non_numeric_session_claims_still_require_evidence():
    value = reply("You're leading the race.")
    value["evidence_ids"] = []
    with pytest.raises(ValueError, match="missing_evidence"):
        parse_answer(value)
    value["answer"] = "Copy."
    assert parse_answer(value)["answer"] == "Copy."


def test_cited_facts_and_identity_must_survive_generation():
    original = build_evidence(snapshot(), now=10)
    current = deepcopy(original)
    assert evidence_unchanged(reply(), original, current)
    current["facts"][0]["text"] = "Changed"
    assert not evidence_unchanged(reply(), original, current)
    current = deepcopy(original)
    current["scope"] = "another session"
    assert not evidence_unchanged(reply(), original, current)


def test_codex_generates_one_grounded_turn(monkeypatch, tmp_path):
    runtime = CodexRuntime(tmp_path / "codex")
    server = successful_server(completed_events(json.dumps(reply())))
    install_server(monkeypatch, runtime, server)

    async def run():
        try:
            return await runtime.answer_radio("How was that lap?", context(), build_evidence(snapshot(), now=10))
        finally:
            await runtime.close()

    result, model = asyncio.run(run())
    assert result == reply()
    assert model["model_name"] == "gpt-6-luna"
    assert len([call for call in server.calls if call[0] == "turn/start"]) == 1


class AnswerRuntime:
    async def status(self):
        return {}

    async def answer_radio(self, transcript, dialogue, evidence):
        self.received = (transcript, dialogue, evidence)
        return reply(), {"model_name": "gpt-6-luna", "latency_ms": 10}


def answer_app(tmp_path, monkeypatch, *, projections=None, runtime=None):
    evidence = build_evidence(snapshot(), now=10)
    values = iter(projections or [evidence, evidence])
    monkeypatch.setattr(api_module, "build_evidence", lambda *args, **kwargs: next(values))
    app = api_module.create_app(tmp_path / "a.sqlite3", recordings_root=tmp_path / "r",
                                control_token=TOKEN, automatic_acquisition=False,
                                engineer_runtime=runtime or AnswerRuntime())
    return app, {"transcript": "How was that lap?", "context": context(), "scope": evidence["scope"]}


def test_authenticated_endpoint_returns_only_server_evidence(tmp_path, monkeypatch):
    runtime = AnswerRuntime()
    app, body = answer_app(tmp_path, monkeypatch, runtime=runtime)
    with TestClient(app) as client:
        response = client.post("/api/v2/engineer/radio-answer", json=body,
                               headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["reply"] == reply()
    assert [fact["id"] for fact in data["evidence"]] == ["car_0_last_lap"]
    assert 0 < data["valid_for_s"] <= 3
    assert runtime.received[0] == body["transcript"]


@pytest.mark.parametrize("mutation,status", [
    ({"scope": "other"}, 409), ({"facts": []}, 422), ({"transcript": ""}, 422),
])
def test_endpoint_rejects_wrong_scope_and_injected_evidence(tmp_path, monkeypatch, mutation, status):
    app, body = answer_app(tmp_path, monkeypatch)
    body.update(mutation)
    with TestClient(app) as client:
        response = client.post("/api/v2/engineer/radio-answer", json=body,
                               headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == status


def test_endpoint_rechecks_evidence_after_inference(tmp_path, monkeypatch):
    original = build_evidence(snapshot(), now=10)
    changed = deepcopy(original)
    changed["facts"] = []
    app, body = answer_app(tmp_path, monkeypatch, projections=[original, changed])
    with TestClient(app) as client:
        response = client.post("/api/v2/engineer/radio-answer", json=body,
                               headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 409
    assert response.json()["reason"] == "radio_answer_evidence_changed"


def test_endpoint_requires_authentication(tmp_path, monkeypatch):
    app, body = answer_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.post("/api/v2/engineer/radio-answer", json=body).status_code == 401


def test_busy_model_is_not_queued(tmp_path, monkeypatch):
    app, body = answer_app(tmp_path, monkeypatch)
    lease = PROCESS_ENGINEER_AI_GATE.try_acquire()
    assert lease is not None
    try:
        with TestClient(app) as client:
            response = client.post("/api/v2/engineer/radio-answer", json=body,
                                   headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 409
        assert response.json()["reason"] == "radio_answer_busy"
    finally:
        lease.close()


def test_unavailable_model_releases_admission(tmp_path, monkeypatch):
    class Unavailable(AnswerRuntime):
        async def answer_radio(self, *args):
            raise EngineerRuntimeUnavailable("codex_request_timeout")

    app, body = answer_app(tmp_path, monkeypatch, runtime=Unavailable())
    with TestClient(app) as client:
        response = client.post("/api/v2/engineer/radio-answer", json=body,
                               headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 503
    lease = PROCESS_ENGINEER_AI_GATE.try_acquire()
    assert lease is not None
    lease.close()


def test_invalid_model_output_is_a_backend_failure(tmp_path, monkeypatch):
    class Invalid(AnswerRuntime):
        async def answer_radio(self, *args):
            return reply(references=["invented_fact"]), {}

    app, body = answer_app(tmp_path, monkeypatch, runtime=Invalid())
    with TestClient(app) as client:
        response = client.post("/api/v2/engineer/radio-answer", json=body,
                               headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 503
    assert response.json()["reason"] == "radio_answer_invalid_model_output"
    lease = PROCESS_ENGINEER_AI_GATE.try_acquire()
    assert lease is not None
    lease.close()


def test_client_routes_grounded_requests_and_preserves_deadline(tmp_path):
    evidence = build_evidence(snapshot(), now=10)
    token = tmp_path / "token"
    token.write_text(TOKEN)

    def handle(request):
        assert request.url.path == "/api/v2/engineer/radio-answer"
        assert json.loads(request.content)["scope"] == evidence["scope"]
        return httpx.Response(200, json={"status": "ok", "data": {
            "schema_version": 1, "reply": reply(), "scope": evidence["scope"],
            "valid_for_s": 2.8, "source": evidence["source"],
            "evidence": [fact for fact in evidence["facts"] if fact["id"] == "car_0_last_lap"],
        }})

    client = RadioClient("http://127.0.0.1:8765", token, transport=httpx.MockTransport(handle))
    try:
        result = asyncio.run(client.answer("last lap?", {**context(), "answer_scope": evidence["scope"]},
                                           REQUEST_ID, threading.Event()))
    finally:
        client.close()
    assert result["status"] == "available"
    assert result["grounded"]["dispatch_before"] > 0


@pytest.mark.parametrize("mutation", [
    {"valid_for_s": 0}, {"valid_for_s": 3.1}, {"valid_for_s": True},
    {"scope": "another_session"}, {"evidence": []}, {"evidence": [None]},
    {"reply": reply("Your last lap was 89.5 seconds.")},
])
def test_client_rejects_expired_unbound_or_unsupported_answers(tmp_path, mutation):
    evidence = build_evidence(snapshot(), now=10)
    token = tmp_path / "token"
    token.write_text(TOKEN)
    data = {"schema_version": 1, "reply": reply(), "scope": evidence["scope"],
            "valid_for_s": 2.8, "source": evidence["source"],
            "evidence": [fact for fact in evidence["facts"] if fact["id"] == "car_0_last_lap"]}
    data.update(mutation)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok", "data": data}))
    client = RadioClient("http://127.0.0.1:8765", token, transport=transport)
    try:
        result = asyncio.run(client.answer("last lap?", {**context(), "answer_scope": evidence["scope"]},
                                           REQUEST_ID, threading.Event()))
    finally:
        client.close()
    assert result["status"] == "unavailable"
    assert result["answer_mode"] == "grounded"


def test_grounded_commands_keep_the_existing_dispatch_path(monkeypatch):
    controller, scheduler, _events, _value = live()
    request = intent(action="control", control="quiet")
    response = {"reply": {"kind": "command", "answer": "", "evidence_ids": [], "intent": request},
                "scope": scheduler.scope, "dispatch_before": 12, "evidence": []}
    dispatched = []
    monkeypatch.setattr(command, "dispatch_voice_request", lambda text, *args, **kwargs: dispatched.append(text))
    dispatch_submission({"question": "Please pause the coaching", "interpretation": {
        "status": "available", "intent": request, "grounded": response}}, controller, None, now=10)
    assert dispatched == ["quiet radio"]
    assert not scheduler.pending


def test_local_controls_bypass_expired_grounded_results(monkeypatch):
    controller, _scheduler, _events, _value = live()
    dispatched = []
    monkeypatch.setattr(command, "dispatch_voice_request", lambda text, *args, **kwargs: dispatched.append(text))
    dispatch_submission({"question": "quiet", "interpretation": {"grounded": {
        "scope": "old", "dispatch_before": 1}}}, controller, None, now=10)
    assert dispatched == ["quiet"]


def test_dispatch_preserves_natural_text_and_logs_evidence():
    controller, scheduler, events, value = live()
    evidence = controller.answer_evidence(now=10)
    fact = next(fact for fact in evidence["facts"] if fact["id"] == "car_0_last_lap")
    response = {"reply": reply(), "scope": scheduler.scope, "dispatch_before": 12, "evidence": [fact]}
    dispatch_submission({"question": "How did I do?", "interpretation": {"grounded": response}},
                        controller, SimpleNamespace(), now=10)
    assert scheduler.pending[-1].text == reply()["answer"]
    assert any(event["event"] == "radio_grounded_answer" for event in events)
    assert controller.intent_context()["recent"][-1]["reply"] == reply()["answer"]
    scheduler.tick(now=11)
    scheduler.tick(now=13)
    assert scheduler.active is not None


def test_grounded_answer_preempts_background_selection(tmp_path, monkeypatch):
    from tests.test_radio_proactive_selection import context as proactive_context

    started = threading.Event()
    cancelled = threading.Event()

    class Runtime(AnswerRuntime):
        async def select_proactive(self, value):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    app, body = answer_app(tmp_path, monkeypatch, runtime=Runtime())
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        background = executor.submit(client.post, "/api/v2/engineer/proactive-selection",
                                     json=proactive_context(), headers=headers)
        assert started.wait(timeout=3)
        response = client.post("/api/v2/engineer/radio-answer", json=body, headers=headers)
        assert response.status_code == 200
        assert cancelled.is_set()
        assert background.result(timeout=3).json()["reason"] == "proactive_preempted"


def test_grounded_reply_preserves_pending_proactive_developments():
    from tests.test_radio_proactive import automatic_live, closing

    controller, scheduler, events, value = automatic_live()
    assert closing(controller, scheduler, value)
    previous = deepcopy(controller.proactive.states)
    fact = next(fact for fact in controller.answer_evidence(now=13)["facts"]
                if fact["id"] == "car_0_last_lap")
    response = {"reply": reply(fact["text"]), "scope": scheduler.scope,
                "dispatch_before": 15, "evidence": [fact]}
    dispatch_submission({"question": "Last lap?", "interpretation": {"grounded": response}},
                        controller, SimpleNamespace(), now=13)
    assert controller.proactive.states == previous
    scheduler.tick(now=13)
    assert scheduler.active.kind == "answer"
    scheduler.playback.playing = False
    controller.feed(value, now=14)
    scheduler.tick(now=14)
    controller.poll_proactive(now=14.05)
    scheduler.tick(now=14.05)
    assert scheduler.active.owner == "automatic_gaps"
    assert "car behind is closing" in scheduler.active.text


@pytest.mark.parametrize("change", ["scope", "deadline", "fact"])
def test_delayed_answers_are_not_spoken(change):
    controller, scheduler, events, value = live()
    fact = next(fact for fact in controller.answer_evidence(now=10)["facts"] if fact["id"] == "car_0_last_lap")
    response = {"reply": reply(), "scope": scheduler.scope, "dispatch_before": 12, "evidence": [fact]}
    if change == "scope":
        response["scope"] = "other"
    elif change == "deadline":
        response["dispatch_before"] = 9
    else:
        response["evidence"][0]["text"] = "different"
    assert not controller.answer_grounded("last lap?", response, now=10)
    assert not scheduler.pending


def test_microphone_worker_preserves_grounded_response():
    response = {"reply": reply(), "scope": "scope", "dispatch_before": 20, "evidence": []}

    async def answer(*args):
        return {"status": "available", "intent": None, "grounded": response}

    voice, hardware, events, clock = listening()
    voice._interpret = answer
    result = voice._interpretation(REQUEST_ID, "How did I do?", 10, threading.Event())
    assert result["grounded"] == response


def test_typed_requests_use_the_same_grounded_dispatch():
    controller, scheduler, events, value = live()
    fact = next(fact for fact in controller.answer_evidence(now=10)["facts"] if fact["id"] == "car_0_last_lap")
    response = {"reply": reply(), "scope": scheduler.scope, "dispatch_before": 12, "evidence": [fact]}

    class Client:
        async def answer(self, text, dialogue, request_id, cancelled):
            assert text == "How did I do?"
            assert dialogue["answer_scope"] == scheduler.scope
            return {"status": "available", "intent": None, "grounded": response}

    results = queue.Queue()
    start_typed_request("How did I do?", Client(), controller, {"terms": {}, "mishearings": {}},
                        results, interpret=True, grounded=True)
    submission, voice = results.get(timeout=2)
    dispatch_submission(submission, controller, voice, now=10)
    assert scheduler.pending[-1].text == reply()["answer"]
