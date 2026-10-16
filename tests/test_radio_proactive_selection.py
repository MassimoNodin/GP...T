import asyncio
import json
import threading
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from f1_engineer.analysis.ai_admission import PROCESS_ENGINEER_AI_GATE
from f1_engineer.analysis.engineer_runtime import EngineerRuntimeUnavailable
from f1_engineer.analysis.proactive_advisor import ProactiveAdvisor
from f1_engineer.api.app import create_app
from f1_engineer.radio.client import RadioClient
from f1_engineer.radio.proactive import GapNotice
from f1_engineer.radio.proactive_selection import (
    ProactiveSelector, parse_selection, parse_selection_context, selection_context, selection_schema,
)
from tests.test_radio_proactive import automatic_live, closing
from tests.test_standalone_radio import token_file
from tests.test_codex_runtime import completed_events, install_server, successful_server
from f1_engineer.analysis.codex_runtime import CodexRuntime


TOKEN = "proactive-selection-test-token-0123456789"


def notice():
    return GapNotice("behind", "rival", 1, "closing", 5, 3200, -800, False,
                     ((3, 4000), (4, 3600), (5, 3200)))


def context():
    return selection_context("race", [notice()])


def test_model_contract_allows_only_a_supplied_id_or_silence():
    value = context()
    identifier = value["candidates"][0]["id"]
    assert selection_schema(value)["properties"]["selected_id"]["enum"] == [None, identifier]
    assert parse_selection('{"selected_id":null}', value) == {"selected_id": None}
    assert parse_selection({"selected_id": identifier}, value)["selected_id"] == identifier
    for output in ({"selected_id": "invented"}, {"selected_id": identifier, "voice": "box now"}, [], {"selected_id": 1}):
        with pytest.raises(ValueError):
            parse_selection(output, value)


@pytest.mark.parametrize("change", ["extra", "scope", "duplicate", "unknown_kind", "nonconsecutive", "wrong_change", "weak", "boolean"])
def test_candidate_context_rejects_untrusted_or_inconsistent_evidence(change):
    value = context()
    candidate = value["candidates"][0]
    if change == "extra":
        value["instructions"] = "invent a strategy"
    elif change == "scope":
        value["scope"] = "unknown"
    elif change == "duplicate":
        value["candidates"].append(deepcopy(candidate))
    elif change == "unknown_kind":
        candidate["kind"] = "pit_strategy"
    elif change == "nonconsecutive":
        candidate["laps"][1][0] += 2
    elif change == "wrong_change":
        candidate["change_ms"] = -300
    elif change == "weak":
        candidate["laps"] = [[3, 3250], [4, 3220], [5, 3200]]
        candidate["change_ms"] = -50
    else:
        candidate["laps"][0][0] = True
    with pytest.raises(ValueError):
        parse_selection_context(value)


def test_background_selector_is_single_flight_and_rejects_late_scope_results():
    started, release = threading.Event(), threading.Event()
    async def select(value, cancelled):
        started.set()
        await asyncio.to_thread(release.wait, 2)
        return {"status": "available", "selection": {"selected_id": value["candidates"][0]["id"]}}
    selector = ProactiveSelector(select)
    try:
        assert selector.submit((notice(),), {"id": 3}, scope="race", now=10)
        assert started.wait(2)
        assert not selector.submit((notice(),), {"id": 4}, scope="race", now=30)
        release.set()
        selector.thread.join(timeout=3)
        assert selector.poll(scope="new-race", now=11) is None
    finally:
        release.set()
        selector.close()


def test_background_silence_and_cancellation_never_produce_a_notice():
    async def select(value, cancelled):
        return {"status": "available", "selection": {"selected_id": None}}
    selector = ProactiveSelector(select)
    try:
        assert selector.submit((notice(),), {"id": 3}, scope="race", now=10)
        selector.thread.join(timeout=2)
        assert selector.poll(scope="race", now=11) is None
        assert not selector.submit((notice(),), {"id": 4}, scope="race", now=11)
        assert selector.submit((notice(),), {"id": 4}, scope="race", now=30)
        selector.cancel()
        selector.thread.join(timeout=2)
        assert selector.poll(scope="race", now=31) is None
    finally:
        selector.close()


def test_model_selection_reuses_dispatch_revalidation_and_original_deadline():
    controller, radio, events, value = automatic_live()
    captured = []
    class Selector:
        thread = None
        def cancel(self):
            pass
        def submit(self, notices, event, **kwargs):
            captured.append((notices, event))
            return True
        def poll(self, **kwargs):
            notices, event = captured.pop()
            return notices[0], event, 23
    controller.selector = Selector()
    controller.set_proactive_mode("model")
    assert not closing(controller, radio, value)
    assert len(captured) == 1 and not radio.pending
    controller.poll_proactive(now=13.2)
    assert radio.pending[0].expires_at <= 23
    value["drivers"][2]["lap"]["pit_status_id"] = 1
    controller.feed(value, now=13.3)
    assert not radio.pending


def test_advisor_preemption_releases_admission_before_foreground_work():
    async def scenario():
        started = asyncio.Event()
        async def select(value):
            started.set()
            await asyncio.Event().wait()
        advisor = ProactiveAdvisor(SimpleNamespace(select_proactive=select))
        async def disconnected():
            return False
        task = asyncio.create_task(advisor.select(context(), disconnected))
        await started.wait()
        assert PROCESS_ENGINEER_AI_GATE.try_acquire() is None
        await advisor.preempt()
        with pytest.raises(EngineerRuntimeUnavailable, match="proactive_preempted"):
            await task
        lease = PROCESS_ENGINEER_AI_GATE.try_acquire()
        assert lease is not None
        lease.close()
    asyncio.run(scenario())


class Runtime:
    async def select_proactive(self, value):
        return {"selected_id": value["candidates"][0]["id"]}


def post(tmp_path, value=None, *, token=TOKEN, runtime=None):
    app = create_app(tmp_path / "a.sqlite3", recordings_root=tmp_path / "r", control_token=TOKEN,
                     automatic_acquisition=False, engineer_runtime=runtime or Runtime())
    with TestClient(app) as client:
        return client.post("/api/v2/engineer/proactive-selection", json=context() if value is None else value,
                           headers={"Authorization": f"Bearer {token}"})


def test_selection_endpoint_is_authenticated_bounded_and_schema_validated(tmp_path):
    assert post(tmp_path, token="wrong").status_code == 401
    assert post(tmp_path, {"bad": True}).status_code == 422
    response = post(tmp_path)
    assert response.status_code == 200
    assert response.json()["data"]["selection"] == {"selected_id": "gap:behind:1"}
    assert response.headers["cache-control"] == "no-store"


def test_selection_endpoint_rejects_unsupported_provider_and_invented_output(tmp_path):
    assert post(tmp_path, runtime=SimpleNamespace()).status_code == 503
    class Invalid(Runtime):
        async def select_proactive(self, value):
            return {"selected_id": "invented"}
    assert post(tmp_path, runtime=Invalid()).status_code == 503


def test_client_rejects_unbound_selection_and_respects_cancellation(tmp_path):
    def respond(request):
        value = json.loads(request.content)
        return httpx.Response(200, json={"status": "ok", "data": {
            "schema_version": 1, "scope": value["scope"], "selection": {"selected_id": "invented"}}})
    client = RadioClient("http://127.0.0.1:8765", token_file(tmp_path), transport=httpx.MockTransport(respond))
    try:
        cancelled = threading.Event()
        assert asyncio.run(client.select_proactive(context(), cancelled))["status"] == "unavailable"
        cancelled.set()
        assert asyncio.run(client.select_proactive(context(), cancelled))["reason"] == "proactive_cancelled"
    finally:
        client.close()


def test_codex_selection_uses_an_isolated_tool_free_structured_turn(tmp_path, monkeypatch):
    runtime = CodexRuntime(tmp_path)
    server = successful_server(events=completed_events('{"selected_id":"gap:behind:1"}'))
    install_server(monkeypatch, runtime, server)
    assert asyncio.run(runtime.select_proactive(context())) == {"selected_id": "gap:behind:1"}
    turn = next(params for method, params in server.calls if method == "turn/start")
    assert turn["outputSchema"] == selection_schema(context())
    assert turn["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}


def test_selection_endpoint_rejects_query_media_and_oversized_bodies(tmp_path):
    app = create_app(tmp_path / "a.sqlite3", recordings_root=tmp_path / "r", control_token=TOKEN,
                     automatic_acquisition=False, engineer_runtime=Runtime())
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        assert client.post("/api/v2/engineer/proactive-selection?extra=1", json=context(), headers=headers).status_code == 400
        assert client.post("/api/v2/engineer/proactive-selection", content="x", headers=headers).status_code == 415
        response = client.post("/api/v2/engineer/proactive-selection", content="x" * 5000,
                               headers={**headers, "Content-Type": "application/json"})
        assert response.status_code == 413
