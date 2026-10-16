import asyncio
import json
import logging
import traceback

import httpx
import pytest

from f1_engineer.api.app import create_app
from f1_engineer.api.request_trace import RequestTraceMiddleware


TOKEN = "trace-secret-" + "x" * 32
REQUEST_ID = "a" * 32


def request(app, *, headers=None):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/v2/session-evidence/status?private=not-logged", headers=headers)
    return asyncio.run(run())


def logged(caplog):
    return [json.loads(record.message) for record in caplog.records if record.message.startswith('{"event":"engineer_http_trace"')]


def test_trace_opt_in_correlates_without_credentials_query_or_body(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("F1_ENGINEER_TRACE_REQUESTS", "1")
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    app = create_app(tmp_path / "data.sqlite3", control_token=TOKEN, require_auth=True, automatic_acquisition=False)
    response = request(app, headers={"Authorization": "Bearer " + TOKEN, "X-F1-Radio-Request-ID": REQUEST_ID})
    assert response.status_code == 200
    assert response.headers["X-F1-Backend-Request-ID"] == REQUEST_ID
    event, = logged(caplog)
    assert event["request_id"] == REQUEST_ID and event["status_code"] == 200 and event["elapsed_s"] >= 0
    assert event["path"] == "/api/v2/session-evidence/status"
    assert TOKEN not in caplog.text and "not-logged" not in caplog.text


def test_disabled_trace_does_not_change_response(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("F1_ENGINEER_TRACE_REQUESTS", raising=False)
    app = create_app(tmp_path / "data.sqlite3", control_token=TOKEN, require_auth=True, automatic_acquisition=False)
    response = request(app, headers={"Authorization": "Bearer " + TOKEN})
    assert response.status_code == 200 and "X-F1-Backend-Request-ID" not in response.headers
    assert not logged(caplog)


def test_unauthorized_and_invalid_trace_ids_are_safe(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("F1_ENGINEER_TRACE_REQUESTS", "1")
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    app = create_app(tmp_path / "data.sqlite3", control_token=TOKEN, require_auth=True, automatic_acquisition=False)
    response = request(app, headers={"X-F1-Radio-Request-ID": "secret-non-id", "Authorization": "Bearer denied"})
    assert response.status_code == 403
    event, = logged(caplog)
    assert len(event["request_id"]) == 32 and event["status_code"] == 403
    assert "secret-non-id" not in caplog.text and "Bearer denied" not in caplog.text


def test_failure_frame_chain_is_logged_and_exception_preserved(caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    async def broken(scope, receive, send):
        try:
            raise ValueError("private-body")
        except ValueError as exception:
            raise RuntimeError("Bearer private-token") from exception
    async def receive():
        return {"type": "http.disconnect"}
    async def send(message):
        pytest.fail("unexpected output")
    with pytest.raises(RuntimeError):
        asyncio.run(RequestTraceMiddleware(broken)({"type": "http", "path": "/api/test", "method": "GET"}, receive, send))
    event, = logged(caplog)
    assert [item["type"] for item in event["exception_chain"]] == ["RuntimeError", "ValueError"]
    assert event["status_code"] is None
    assert "private-body" not in caplog.text and "private-token" not in caplog.text


def test_non_http_scope_passes_through(caplog):
    received = []
    async def app(scope, receive, send):
        received.append(scope)
    asyncio.run(RequestTraceMiddleware(app)({"type": "lifespan"}, None, None))
    assert received == [{"type": "lifespan"}] and not logged(caplog)


def test_secret_failures_never_include_request_body_or_exception_details(caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    key = "sk-" + "private-key-contents" * 2

    async def broken(scope, receive, send):
        await receive()
        raise ValueError(key)

    async def receive():
        return {"type": "http.request", "body": json.dumps({"key": key}).encode()}

    async def send(message):
        pytest.fail("unexpected output")

    with pytest.raises(RuntimeError, match="secret_request_failed") as captured:
        asyncio.run(RequestTraceMiddleware(broken)({
            "type": "http", "path": "/api/v1/secrets/openai-api-key", "method": "PUT",
        }, receive, send))
    event, = logged(caplog)
    assert event["exception_chain"] == [{"type": "ValueError"}]
    assert key not in caplog.text
    assert key not in "".join(traceback.format_exception(captured.value))
