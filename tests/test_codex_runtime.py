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


def tracked_radio_server(monkeypatch, runtime, server):
    lifecycle = []

    @asynccontextmanager
    async def open_server():
        lifecycle.append("open")
        try:
            yield server
        finally:
            lifecycle.append("close")

    monkeypatch.setattr(runtime, "_open", open_server)
    return lifecycle


def test_radio_start_warms_once_and_refreshes_login_every_sixty_seconds(monkeypatch, tmp_path):
    runtime = codex_runtime.CodexRuntime(tmp_path)
    server = successful_server()
    lifecycle = tracked_radio_server(monkeypatch, runtime, server)
    tick = asyncio.Event()
    sleeping = asyncio.Event()
    delays = []

    async def sleep(delay):
        delays.append(delay)
        sleeping.set()
        await tick.wait()
        tick.clear()

    monkeypatch.setattr(codex_runtime.asyncio, "sleep", sleep)

    async def run():
        await runtime.start()
        task = runtime._radio_refresh_task
        try:
            await runtime.start()
            assert runtime._radio_refresh_task is task
            assert [method for method, _ in server.calls] == ["account/read", "model/list"]
            await asyncio.wait_for(sleeping.wait(), 1)
            sleeping.clear()
            tick.set()
            await asyncio.wait_for(sleeping.wait(), 1)
            assert [method for method, _ in server.calls] == ["account/read", "model/list"] * 2
            assert delays == [60.0, 60.0]
            assert lifecycle == ["open"]
        finally:
            await runtime.close()
        assert task.cancelled()
        assert lifecycle == ["open", "close"]
        await runtime.close()
        assert lifecycle == ["open", "close"]
        with pytest.raises(EngineerRuntimeUnavailable, match="codex_runtime_closed"):
            await runtime._radio_server()

    asyncio.run(run())


def test_radio_warmup_failure_can_retry_after_login(monkeypatch, tmp_path):
    runtime = codex_runtime.CodexRuntime(tmp_path)
    server = FakeServer()
    lifecycle = tracked_radio_server(monkeypatch, runtime, server)

    async def run():
        await runtime.start()
        try:
            assert runtime._radio_current is None
            assert lifecycle == ["open", "close"]
            server.account = {"type": "chatgpt"}
            async with runtime._radio_lock():
                await runtime._warm_radio_server(refresh=True)
            assert runtime._radio_current is server
        finally:
            await runtime.close()
        assert lifecycle == ["open", "close", "open", "close"]

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["preflight", "turn"])
@pytest.mark.parametrize("failure", ["deadline", "timeout", "protocol", "cancel"])
def test_radio_reuses_server_after_timeout_but_closes_on_protocol_failure(
    monkeypatch, tmp_path, phase, failure,
):
    runtime = codex_runtime.CodexRuntime(tmp_path)
    server = successful_server()
    lifecycle = tracked_radio_server(monkeypatch, runtime, server)
    entered = asyncio.Event()
    monkeypatch.setattr(codex_runtime, "RADIO_TIMEOUT_S", 0.02)
    monkeypatch.setattr(codex_runtime, "parse_intent", lambda content, **kwargs: content)
    context = {"drivers": [], "tasks": [], "track": None, "regions": [], "recent": [],
               "pending_region": None, "glossary": {"terms": {}, "mishearings": {}}}

    async def fail(*args, **kwargs):
        entered.set()
        if failure in ("deadline", "cancel"):
            await asyncio.Event().wait()
        if failure == "timeout":
            raise TimeoutError()
        raise EngineerRuntimeUnavailable("codex_protocol_invalid")

    async def run():
        await runtime._radio_server()
        original_preflight = runtime._preflight
        original_turn = runtime._turn
        if phase == "preflight":
            runtime._radio_checked = -codex_runtime.RADIO_PREFLIGHT_TTL_S
            monkeypatch.setattr(runtime, "_preflight", fail)
        else:
            monkeypatch.setattr(runtime, "_turn", fail)
        try:
            task = asyncio.create_task(runtime.interpret_radio("hello", context))
            await asyncio.wait_for(entered.wait(), 1)
            if failure == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                reason = "codex_protocol_invalid" if failure == "protocol" else "codex_request_timeout"
                with pytest.raises(EngineerRuntimeUnavailable, match=reason):
                    await task
            retained = failure in ("deadline", "timeout")
            assert lifecycle == (["open"] if retained else ["open", "close"])
            assert (runtime._radio_current is server) is retained
            monkeypatch.setattr(runtime, "_preflight", original_preflight)
            monkeypatch.setattr(runtime, "_turn", original_turn)
            server.events.extend(completed_events())
            result, _ = await runtime.interpret_radio("hello", context)
            assert result == ROUTER_JSON
            assert lifecycle.count("open") == (1 if retained else 2)
        finally:
            await runtime.close()

    asyncio.run(run())


def test_radio_close_cancels_inflight_refresh_without_waiting_for_deadline(monkeypatch, tmp_path):
    runtime = codex_runtime.CodexRuntime(tmp_path)
    server = successful_server()
    lifecycle = tracked_radio_server(monkeypatch, runtime, server)
    entered = asyncio.Event()

    async def preflight(server):
        entered.set()
        await asyncio.Event().wait()

    async def run():
        await runtime._radio_server()
        monkeypatch.setattr(runtime, "_preflight", preflight)
        monkeypatch.setattr(codex_runtime, "RADIO_PREFLIGHT_TTL_S", 0)
        runtime._radio_refresh_task = asyncio.create_task(runtime._refresh_radio_login())
        task = runtime._radio_refresh_task
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(runtime.close(), 1)
        assert task.cancelled()
        assert runtime._radio_refresh_task is None
        assert lifecycle == ["open", "close"]

    asyncio.run(run())


def test_radio_shutdown_during_startup_does_not_leave_refresh_task(monkeypatch, tmp_path):
    runtime = codex_runtime.CodexRuntime(tmp_path)
    server = successful_server()
    lifecycle = tracked_radio_server(monkeypatch, runtime, server)
    entered = asyncio.Event()
    release = asyncio.Event()
    preflight = runtime._preflight

    async def delayed_preflight(server):
        entered.set()
        await release.wait()
        return await preflight(server)

    monkeypatch.setattr(runtime, "_preflight", delayed_preflight)

    async def run():
        start = asyncio.create_task(runtime.start())
        await asyncio.wait_for(entered.wait(), 1)
        closed = asyncio.create_task(runtime.close())
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(start, closed), 1)
        assert runtime._radio_refresh_task is None
        assert runtime._radio_current is None
        assert lifecycle == ["open", "close"]

    asyncio.run(run())


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["timeout", "protocol"])
def test_radio_warmup_deadline_preserves_only_an_existing_healthy_server(
    monkeypatch, tmp_path, existing, failure,
):
    runtime = codex_runtime.CodexRuntime(tmp_path)
    server = successful_server()
    lifecycle = tracked_radio_server(monkeypatch, runtime, server)
    monkeypatch.setattr(codex_runtime, "CODEX_TIMEOUT_S", 0.02)

    async def fail(server):
        if failure == "timeout":
            await asyncio.Event().wait()
        raise EngineerRuntimeUnavailable("codex_protocol_invalid")

    async def run():
        if existing:
            await runtime._radio_server()
        monkeypatch.setattr(runtime, "_preflight", fail)
        await runtime._warm_radio_server(refresh=True)
        retained = existing and failure == "timeout"
        assert (runtime._radio_current is server) is retained
        assert lifecycle == (["open"] if retained else ["open", "close"])
        await runtime.close()
        assert lifecycle == ["open", "close"]

    asyncio.run(run())


def test_app_server_handles_late_response_after_timeout_without_restarting():
    async def run():
        process = FakeProcess([])
        process.stdout = asyncio.StreamReader()
        server = codex_runtime._AppServer(process)
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.02):
                await server.rpc("account/read", {"refreshToken": False})
        process.stdout.feed_data(b'{"id":1,"result":{"account":{"type":"chatgpt"}}}\n')
        process.stdout.feed_data(b'{"method":"turn/completed","params":{"threadId":"old"}}\n')
        process.stdout.feed_data(b'{"id":2,"result":{"data":[]}}\n')
        assert await server.rpc("model/list", {}) == {"data": []}
        assert (await server.notification())["params"]["threadId"] == "old"
        assert [json.loads(value)["id"] for value in process.writes] == [1, 2]

    asyncio.run(run())
