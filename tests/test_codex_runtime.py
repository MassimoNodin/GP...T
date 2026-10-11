from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from f1_engineer.analysis import codex_runtime
from f1_engineer.analysis.engineer_runtime import EngineerRuntimeUnavailable
from f1_engineer.cli import build_parser


MODEL = "gpt-6-luna"
ROUTES = ("attempt_summary",)
ROUTER_JSON = '{"route":"attempt_summary","focus":"overview"}'


class FakeServer:
    def __init__(self, *, account=None, models=None, events=(), responses=None):
        self.account = account
        self.models = [{"model": MODEL}] if models is None else models
        self.events = list(events)
        self.responses = responses or {}
        self.calls = []

    async def rpc(self, method, params):
        self.calls.append((method, params))
        if method == "account/read":
            return {"account": self.account}
        if method == "model/list":
            return {"data": self.models}
        response = self.responses.get(method)
        if isinstance(response, BaseException):
            raise response
        return response or {}

    async def notification(self):
        event = self.events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event


def install_server(monkeypatch, runtime, server):
    @asynccontextmanager
    async def open_server():
        yield server

    monkeypatch.setattr(runtime, "_open", open_server)


def successful_server(events=(), responses=None):
    return FakeServer(
        account={"type": "chatgpt"},
        events=events,
        responses=responses or {
            "thread/start": {
                "model": MODEL,
                "modelProvider": "openai",
                "thread": {"id": "thread-1"},
            },
            "turn/start": {"turn": {"id": "turn-1"}},
        },
    )


def event(method, **params):
    return {"method": method, "params": params}


def completed_events(text=ROUTER_JSON):
    return [
        event("item/started", threadId="thread-1", turnId="turn-1",
              item={"type": "userMessage"}),
        event("item/completed", threadId="thread-1", turnId="turn-1",
              item={"type": "agentMessage", "text": text}),
        event("turn/completed", threadId="thread-1", turnId="turn-1",
              turn={"id": "turn-1", "status": "completed"}),
    ]


@pytest.mark.parametrize(
    ("account", "reason", "status"),
    [
        (None, "codex_subscription_login_required", "not_configured"),
        ({"type": "apiKey"}, "codex_subscription_auth_required", "unavailable"),
    ],
)
def test_status_requires_chatgpt_subscription(monkeypatch, tmp_path, account, reason, status):
    runtime = codex_runtime.CodexRuntime(tmp_path)
    server = FakeServer(account=account)
    install_server(monkeypatch, runtime, server)

    result = asyncio.run(runtime.status())

    assert result["reason"] == reason
    assert result["status"] == status
    assert result["fallback_enabled"] is False
    assert [method for method, _ in server.calls] == ["account/read"]


def test_status_requires_exact_luna_model(monkeypatch, tmp_path):
    runtime = codex_runtime.CodexRuntime(tmp_path)
    server = FakeServer(account={"type": "chatgpt"}, models=[{"model": "gpt-6"}])
    install_server(monkeypatch, runtime, server)

    result = asyncio.run(runtime.status())

    assert result["reason"] == "codex_model_unavailable"
    assert result["status"] == "unavailable"
    assert [method for method, _ in server.calls] == ["account/read", "model/list"]


def test_route_uses_exact_model_provider_and_existing_router(monkeypatch, tmp_path):
    runtime = codex_runtime.CodexRuntime(tmp_path)
    server = successful_server(completed_events())
    install_server(monkeypatch, runtime, server)

    route, model = asyncio.run(runtime.route_question(
        "Summarize this attempt", selection_kind="attempt", allowed_routes=ROUTES
    ))

    assert (route.route, route.focus) == ("attempt_summary", "overview")
    assert model["model_name"] == MODEL
    assert model["billing"] == "codex_subscription"
    assert model["fallback_enabled"] is False
    calls = dict((method, params) for method, params in server.calls)
    assert calls["thread/start"]["model"] == MODEL
    assert calls["thread/start"]["modelProvider"] == "openai"
    assert calls["thread/start"]["ephemeral"] is True
    assert calls["turn/start"]["threadId"] == "thread-1"
    assert calls["turn/start"]["model"] == MODEL
    assert calls["turn/start"]["sandboxPolicy"] == {
        "type": "readOnly", "networkAccess": False
    }
    assert [method for method, _ in server.calls[:2]] == ["account/read", "model/list"]


@pytest.mark.parametrize(
    ("started", "reason"),
    [
        ({"model": "other", "modelProvider": "openai", "thread": {"id": "t"}},
         "codex_model_changed"),
        ({"model": MODEL, "modelProvider": "other", "thread": {"id": "t"}},
         "codex_model_changed"),
        ({"model": MODEL, "modelProvider": "openai", "thread": {"id": "t"},
          "instructionSources": ["/untrusted/AGENTS.md"]},
         "codex_engineer_profile_not_isolated"),
    ],
)
def test_route_rejects_changed_model_or_instructions_without_fallback(monkeypatch, tmp_path, started, reason):
    runtime = codex_runtime.CodexRuntime(tmp_path)
    server = successful_server(responses={"thread/start": started})
    install_server(monkeypatch, runtime, server)

    with pytest.raises(EngineerRuntimeUnavailable) as error:
        asyncio.run(runtime.route_question(
            "Summarize this attempt", selection_kind="attempt", allowed_routes=ROUTES
        ))

    assert error.value.reason == reason
    assert "turn/start" not in [method for method, _ in server.calls]


@pytest.mark.parametrize(
    ("events", "reason"),
    [
        ([event("item/started", threadId="thread-1", turnId="turn-1",
                item={"type": "functionCall"})], "codex_tool_use_rejected"),
        ([event("turn/completed", threadId="thread-1", turnId="turn-1",
                turn={"id": "turn-1", "status": "failed"})], "codex_turn_failed"),
    ],
)
def test_route_rejects_tool_requests_and_failed_turns(monkeypatch, tmp_path, events, reason):
    runtime = codex_runtime.CodexRuntime(tmp_path)
    server = successful_server(events)
    install_server(monkeypatch, runtime, server)

    with pytest.raises(EngineerRuntimeUnavailable) as error:
        asyncio.run(runtime.route_question(
            "Summarize this attempt", selection_kind="attempt", allowed_routes=ROUTES
        ))

    assert error.value.reason == reason


def test_route_timeout_closes_open_server(monkeypatch, tmp_path):
    runtime = codex_runtime.CodexRuntime(tmp_path)
    entered = asyncio.Event()
    exited = asyncio.Event()

    class WaitingServer(FakeServer):
        async def notification(self):
            entered.set()
            await asyncio.Event().wait()

    server = WaitingServer(account={"type": "chatgpt"}, responses={
        "thread/start": {
            "model": MODEL, "modelProvider": "openai", "thread": {"id": "t"}
        },
        "turn/start": {"turn": {"id": "u"}},
    })

    @asynccontextmanager
    async def open_server():
        try:
            yield server
        finally:
            exited.set()

    monkeypatch.setattr(runtime, "_open", open_server)
    monkeypatch.setattr(codex_runtime, "CODEX_TIMEOUT_S", 0.01)

    async def run():
        with pytest.raises(EngineerRuntimeUnavailable) as error:
            await runtime.route_question(
                "Summarize this attempt", selection_kind="attempt", allowed_routes=ROUTES
            )
        assert error.value.reason == "codex_request_timeout"
        assert entered.is_set()
        assert exited.is_set()

    asyncio.run(run())


def test_route_cancellation_closes_open_server(monkeypatch, tmp_path):
    runtime = codex_runtime.CodexRuntime(tmp_path)
    entered = asyncio.Event()
    exited = asyncio.Event()

    class WaitingServer(FakeServer):
        async def notification(self):
            entered.set()
            await asyncio.Event().wait()

    server = WaitingServer(account={"type": "chatgpt"}, responses={
        "thread/start": {
            "model": MODEL, "modelProvider": "openai", "thread": {"id": "t"}
        },
        "turn/start": {"turn": {"id": "u"}},
    })

    @asynccontextmanager
    async def open_server():
        try:
            yield server
        finally:
            exited.set()

    monkeypatch.setattr(runtime, "_open", open_server)

    async def run():
        task = asyncio.create_task(runtime.route_question(
            "Summarize this attempt", selection_kind="attempt", allowed_routes=ROUTES
        ))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set()

    asyncio.run(run())


class FakeProcess:
    def __init__(self, lines):
        self.writes = []
        self.stdin = SimpleNamespace(write=self.writes.append, drain=self.drain)
        self.stdout = SimpleNamespace(readline=self.readline)
        self.lines = iter(lines)

    async def drain(self):
        return None

    async def readline(self):
        return next(self.lines)


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        (b"not-json\n", "codex_protocol_invalid"),
        (b"[]\n", "codex_protocol_invalid"),
        (b'{"method":"invalid","params":[]}\n', "codex_protocol_invalid"),
        (b"{" + b" " * codex_runtime.MAX_LINE_BYTES + b"}\n",
         "codex_protocol_limit_exceeded"),
        (b'{"id":7,"method":"item/tool/call","params":{}}\n',
         "codex_tool_request_rejected"),
    ],
    ids=("invalid-json", "non-object", "invalid-params", "oversized-line", "incoming-request"),
)
def test_app_server_rejects_malformed_oversized_and_request_lines(line, reason):
    server = codex_runtime._AppServer(FakeProcess([line]))

    with pytest.raises(EngineerRuntimeUnavailable) as error:
        asyncio.run(server.read())

    assert error.value.reason == reason


def test_app_server_rpc_matches_response_and_queues_notifications():
    process = FakeProcess([
        b'{"method":"item/started","params":{"item":{"type":"reasoning"}}}\n',
        b'{"id":1,"result":{"ready":true}}\n',
    ])
    server = codex_runtime._AppServer(process)

    result = asyncio.run(server.rpc("initialize", {}))

    assert result == {"ready": True}
    assert json.loads(process.writes[0]) == {
        "id": 1, "method": "initialize", "params": {}
    }
    assert asyncio.run(server.notification())["method"] == "item/started"


def test_open_isolates_environment_and_terminates_process_on_cancellation(monkeypatch, tmp_path):
    captured = {}

    class FakeStdin:
        def write(self, value):
            captured.setdefault("writes", []).append(json.loads(value))

        async def drain(self):
            return None

    class FakeStdout:
        async def readline(self):
            return b'{"id":1,"result":{}}\n'

    class FakeProcess:
        stdin = FakeStdin()
        stdout = FakeStdout()
        stderr = None
        returncode = None

        def terminate(self):
            captured["terminated"] = True

        async def wait(self):
            self.returncode = 0
            captured["waited"] = True
            return 0

    async def create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(codex_runtime.asyncio, "create_subprocess_exec", create_subprocess_exec)
    for name in (
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID",
        "OPENAI_ORGANIZATION", "OPENAI_PROJECT_ID", "CODEX_HOME",
    ):
        monkeypatch.setenv(name, "inherited-secret")
    home = tmp_path / "codex-home"
    runtime = codex_runtime.CodexRuntime(home, executable="codex-test")

    async def run():
        entered = asyncio.Event()

        async def use_server():
            async with runtime._open():
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(use_server())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())

    args = captured["args"]
    kwargs = captured["kwargs"]
    assert args[:3] == ("codex-test", "app-server", "-c")
    assert 'forced_login_method="chatgpt"' in args
    assert 'model_provider="openai"' in args
    assert kwargs["env"]["CODEX_HOME"] == str(home.resolve())
    assert all(name not in kwargs["env"] for name in (
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID",
        "OPENAI_ORGANIZATION", "OPENAI_PROJECT_ID",
    ))
    assert kwargs["cwd"] != str(home)
    assert kwargs["stdin"] is asyncio.subprocess.PIPE
    assert kwargs["stdout"] is asyncio.subprocess.PIPE
    assert captured["terminated"] is True
    assert captured["waited"] is True


def test_cli_defaults_engineer_provider_to_codex():
    args = build_parser().parse_args(["api"])

    assert args.engineer_ai_provider == "codex"
