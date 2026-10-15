from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from ..radio.intent_prompt import build_intent_messages
from ..radio.intents import intent_schema, parse_intent
from .engineer_ask import build_router_messages, parse_router_output, route_schema
from .engineer_runtime import EngineerRuntimeUnavailable


CODEX_MODEL = "gpt-6-luna"
CODEX_TIMEOUT_S = 25.0
MAX_PROTOCOL_BYTES = 2 * 1024 * 1024
MAX_LINE_BYTES = 256 * 1024
RADIO_TIMEOUT_S = 8.0
RADIO_PREFLIGHT_TTL_S = 300.0
RADIO_RECYCLE_REQUESTS = 100
RADIO_EFFORT = "low"


class _AppServer:
    def __init__(self, process):
        self.process = process
        self.sequence = 0
        self.bytes_read = 0
        self.pending = []

    async def send(self, value):
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        await self.process.stdin.drain()

    async def read(self):
        try:
            line = await self.process.stdout.readline()
        except ValueError as exc:
            raise EngineerRuntimeUnavailable("codex_protocol_limit_exceeded") from exc
        self.bytes_read += len(line)
        if len(line) > MAX_LINE_BYTES or self.bytes_read > MAX_PROTOCOL_BYTES:
            raise EngineerRuntimeUnavailable("codex_protocol_limit_exceeded")
        if not line:
            raise EngineerRuntimeUnavailable("codex_process_closed")
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise EngineerRuntimeUnavailable("codex_protocol_invalid") from exc
        if not isinstance(value, dict):
            raise EngineerRuntimeUnavailable("codex_protocol_invalid")
        if "method" in value and not isinstance(value.get("params", {}), dict):
            raise EngineerRuntimeUnavailable("codex_protocol_invalid")
        if "method" in value and "id" in value:
            await self.send({"id": value["id"], "error": {
                "code": -32601, "message": "Engineer tools and interactive requests are disabled"
            }})
            raise EngineerRuntimeUnavailable("codex_tool_request_rejected")
        return value

    async def rpc(self, method, params):
        self.sequence += 1
        identifier = self.sequence
        await self.send({"id": identifier, "method": method, "params": params})
        while True:
            value = await self.read()
            if value.get("id") == identifier:
                if "error" in value:
                    raise EngineerRuntimeUnavailable("codex_request_failed")
                result = value.get("result")
                if not isinstance(result, dict):
                    raise EngineerRuntimeUnavailable("codex_protocol_invalid")
                return result
            if "method" in value:
                self.pending.append(value)

    async def notification(self):
        return self.pending.pop(0) if self.pending else await self.read()


class CodexRuntime:
    """Subscription-only Codex app-server client; no API or provider fallback."""

    def __init__(self, home: str | Path, *, executable: str = "codex"):
        self.home = Path(home).resolve()
        self.executable = executable
        self._radio_guard = None
        self._radio_manager = None
        self._radio_current = None
        self._radio_checked = 0.0
        self._radio_uses = 0
        self._radio_effort = RADIO_EFFORT

    @asynccontextmanager
    async def _open(self):
        self.home.mkdir(parents=True, exist_ok=True)
        if any((self.home / name).exists() for name in ("config.toml", "AGENTS.md")):
            raise EngineerRuntimeUnavailable("codex_engineer_profile_not_isolated")
        environment = {
            key: value for key, value in os.environ.items()
            if key not in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID",
                           "OPENAI_ORGANIZATION", "OPENAI_PROJECT_ID", "CODEX_HOME")
        }
        environment["CODEX_HOME"] = str(self.home)
        process = None
        with tempfile.TemporaryDirectory(prefix="f1-engineer-codex-") as directory:
            try:
                process = await asyncio.create_subprocess_exec(
                    self.executable, "app-server", "-c", 'forced_login_method="chatgpt"',
                    "-c", 'model_provider="openai"', "-c", 'web_search="disabled"',
                    "-c", 'features.shell_tool=false', "-c", 'features.apps=false',
                    "-c", 'features.hooks=false', "-c", 'features.multi_agent=false',
                    "-c", 'features.multi_agent_v2=false', "-c", 'features.browser_use=false',
                    "-c", 'features.computer_use=false', "-c", 'features.image_generation=false',
                    "-c", 'features.view_image=false', "-c", 'features.code_mode_host=false',
                    "-c", 'features.memories=false',
                    cwd=directory, env=environment, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    limit=MAX_LINE_BYTES,
                )
                server = _AppServer(process)
                await server.rpc("initialize", {"clientInfo": {
                    "name": "f1_engineer", "version": "0.1.0"
                }})
                await server.send({"method": "initialized"})
                yield server
            except OSError as exc:
                raise EngineerRuntimeUnavailable("codex_executable_unavailable") from exc
            finally:
                if process is not None and process.returncode is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(process.wait(), timeout=2.0)
                    except TimeoutError:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        await process.wait()

    async def _preflight(self, server):
        result = await server.rpc("account/read", {"refreshToken": False})
        account = result.get("account")
        if not isinstance(account, dict):
            raise EngineerRuntimeUnavailable("codex_subscription_login_required")
        if account.get("type") != "chatgpt":
            raise EngineerRuntimeUnavailable("codex_subscription_auth_required")
        cursor = None
        for _page in range(4):
            result = await server.rpc("model/list", {"limit": 100, "cursor": cursor})
            models = result.get("data")
            if not isinstance(models, list):
                raise EngineerRuntimeUnavailable("codex_protocol_invalid")
            for model in models:
                if isinstance(model, dict) and model.get("model") == CODEX_MODEL:
                    return model
            cursor = result.get("nextCursor")
            if not cursor:
                break
        raise EngineerRuntimeUnavailable("codex_model_unavailable")

    async def status(self):
        status = {"runtime": "Codex", "model_name": CODEX_MODEL,
                  "billing": "codex_subscription", "fallback_enabled": False,
                  "cloud_routing": "subscription", "requested_placement": "cloud",
                  "endpoint": "codex:stdio", "runtime_version": None,
                  "model_digest": None, "model_quantization": None,
                  "model_parameter_size": None, "last_inference_placement": None,
                  "last_inference_at_utc": None}
        try:
            async with asyncio.timeout(CODEX_TIMEOUT_S), self._open() as server:
                await self._preflight(server)
            return {**status, "status": "ready", "reason": None}
        except (EngineerRuntimeUnavailable, TimeoutError) as exc:
            reason = getattr(exc, "reason", "codex_request_timeout")
            return {**status, "status": "not_configured" if reason in (
                "codex_subscription_login_required", "codex_executable_unavailable"
            ) else "unavailable", "reason": reason}

    async def _turn(self, server, *, instructions, prompt, schema, effort=None):
        """Run one ephemeral, tool-free structured turn and return the final agent message."""
        started = await server.rpc("thread/start", {
            "model": CODEX_MODEL, "modelProvider": "openai", "ephemeral": True,
            "sandbox": "read-only", "approvalPolicy": "untrusted",
            "baseInstructions": instructions,
            "developerInstructions": "Use no tools. Return only the required JSON.",
        })
        if started.get("model") != CODEX_MODEL or started.get("modelProvider") != "openai":
            raise EngineerRuntimeUnavailable("codex_model_changed")
        if started.get("instructionSources", []) != []:
            raise EngineerRuntimeUnavailable("codex_engineer_profile_not_isolated")
        thread = started.get("thread")
        if not isinstance(thread, dict):
            raise EngineerRuntimeUnavailable("codex_protocol_invalid")
        thread_id = thread.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise EngineerRuntimeUnavailable("codex_protocol_invalid")
        turn = {
            "threadId": thread_id, "model": CODEX_MODEL,
            "input": [{"type": "text", "text": prompt}],
            "outputSchema": schema,
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
        }
        try:
            turn = await server.rpc("turn/start", {**turn, **({"effort": effort} if effort else {})})
        except EngineerRuntimeUnavailable as exc:
            if effort is None or exc.reason != "codex_request_failed":
                raise
            self._radio_effort = None  # this Codex build rejects the effort option; stop sending it
            turn = await server.rpc("turn/start", turn)
        turn_data = turn.get("turn")
        if not isinstance(turn_data, dict):
            raise EngineerRuntimeUnavailable("codex_protocol_invalid")
        turn_id = turn_data.get("id")
        if not isinstance(turn_id, str) or not turn_id:
            raise EngineerRuntimeUnavailable("codex_protocol_invalid")
        content = None
        while True:
            event = await server.notification()
            params = event.get("params") or {}
            if not isinstance(params, dict):
                raise EngineerRuntimeUnavailable("codex_protocol_invalid")
            if params.get("threadId") != thread_id or params.get("turnId", turn_id) != turn_id:
                continue
            method = event.get("method")
            if method in ("item/started", "item/completed"):
                item = params.get("item") or {}
                if not isinstance(item, dict):
                    raise EngineerRuntimeUnavailable("codex_protocol_invalid")
                if item.get("type") not in ("userMessage", "agentMessage", "reasoning"):
                    raise EngineerRuntimeUnavailable("codex_tool_use_rejected")
                if method == "item/completed" and item.get("type") == "agentMessage":
                    content = item.get("text")
            if method == "turn/completed":
                completed = params.get("turn") or {}
                if not isinstance(completed, dict):
                    raise EngineerRuntimeUnavailable("codex_protocol_invalid")
                if completed.get("id") != turn_id:
                    continue
                if completed.get("status") != "completed":
                    raise EngineerRuntimeUnavailable("codex_turn_failed")
                return content

    def _model_report(self, started_at):
        return {"runtime": "Codex", "model_name": CODEX_MODEL,
                "billing": "codex_subscription", "fallback_enabled": False,
                "model_digest": "not_applicable", "runtime_version": None,
                "inference_placement": "cloud",
                "latency_ms": round((time.monotonic() - started_at) * 1000)}

    async def route_question(self, question, *, selection_kind, allowed_routes):
        started_at = time.monotonic()
        messages = build_router_messages(
            question, selection_kind=selection_kind, allowed_routes=allowed_routes
        )
        try:
            async with asyncio.timeout(CODEX_TIMEOUT_S), self._open() as server:
                await self._preflight(server)
                content = await self._turn(
                    server, instructions=messages[0]["content"], prompt=messages[1]["content"],
                    schema=route_schema(allowed_routes))
                try:
                    route = parse_router_output(content, allowed_routes=allowed_routes)
                except ValueError as exc:
                    raise EngineerRuntimeUnavailable(str(exc)) from exc
                return route, self._model_report(started_at)
        except TimeoutError as exc:
            raise EngineerRuntimeUnavailable("codex_request_timeout") from exc

    async def _radio_server(self):
        """Reuse one app-server for the radio so each request skips process start and login checks."""
        server = self._radio_current
        if server is not None:
            process = getattr(server, "process", None)
            if (process is not None and process.returncode is not None) or self._radio_uses >= RADIO_RECYCLE_REQUESTS:
                await self._close_radio_server()
                server = None
            elif time.monotonic() - self._radio_checked >= RADIO_PREFLIGHT_TTL_S:
                await self._preflight(server)
                self._radio_checked = time.monotonic()
        if server is None:
            manager = self._open()
            server = await manager.__aenter__()
            try:
                await self._preflight(server)
            except BaseException:
                await manager.__aexit__(None, None, None)
                raise
            self._radio_manager, self._radio_current = manager, server
            self._radio_checked, self._radio_uses = time.monotonic(), 0
        server.bytes_read = 0
        return server

    async def _close_radio_server(self):
        manager, self._radio_manager, self._radio_current = self._radio_manager, None, None
        if manager is not None:
            try:
                await manager.__aexit__(None, None, None)
            except Exception:
                pass

    async def close(self):
        async with self._radio_lock():
            await self._close_radio_server()

    def _radio_lock(self):
        if self._radio_guard is None:
            self._radio_guard = asyncio.Lock()
        return self._radio_guard

    async def interpret_radio(self, transcript, context):
        """Interpret one radio transcript into a validated intent using the persistent server."""
        started_at = time.monotonic()
        messages = build_intent_messages(transcript, context)
        try:
            async with asyncio.timeout(RADIO_TIMEOUT_S), self._radio_lock():
                try:
                    server = await self._radio_server()
                    self._radio_uses += 1
                    content = await self._turn(
                        server, instructions=messages[0]["content"], prompt=messages[1]["content"],
                        schema=intent_schema(context), effort=self._radio_effort)
                except BaseException:
                    await self._close_radio_server()  # protocol state is unknown after any failure
                    raise
            try:
                intent = parse_intent(content, context=context)
            except ValueError as exc:
                raise EngineerRuntimeUnavailable(str(exc)) from exc
            return intent, self._model_report(started_at)
        except TimeoutError as exc:
            raise EngineerRuntimeUnavailable("codex_request_timeout") from exc
