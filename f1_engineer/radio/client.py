from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..analysis.local_speech import (SPEECH_MAX_RESPONSE_BYTES, SPEECH_MAX_TRANSCRIPT_BYTES,
                                    LocalSpeechUnavailable, valid_speech_hint, validate_canonical_wav)
from .diagnostics import exception_trace, trace_enabled, trace_event
from .intents import MAX_CONTEXT_BYTES, parse_context, parse_intent

INTENT_TIMEOUT_S = 10
INTENT_MAX_RESPONSE_BYTES = 32 * 1024


def _trace_request(request):
    if trace_enabled():
        request.extensions["radio_trace_started"] = time.monotonic()
        request.extensions["radio_trace_id"] = uuid.uuid4().hex
        request.headers["X-F1-Radio-Request-ID"] = request.extensions["radio_trace_id"]
        trace_event({"event": "radio_http_request", "http_id": request.extensions["radio_trace_id"],
                     "capture_id": request.headers.get("X-Request-ID"),
                     "method": request.method, "path": request.url.path})


def _trace_response(response):
    started = response.request.extensions.get("radio_trace_started")
    if started is not None:
        trace_event({"event": "radio_http_response", "method": response.request.method,
                     "http_id": response.request.extensions.get("radio_trace_id"),
                     "backend_request_id": response.headers.get("X-F1-Backend-Request-ID"),
                     "path": response.request.url.path, "status_code": response.status_code,
                     "headers_elapsed_s": time.monotonic() - started})


async def _trace_async_request(request):
    _trace_request(request)


async def _trace_async_response(response):
    _trace_response(response)


def read_control_token(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("radio_token_file_unavailable")
    with path.open("rb") as stream:
        raw = stream.read(1025)
    if len(raw) > 1024:
        raise ValueError("radio_token_file_invalid")
    try:
        token = raw.decode("utf-8").strip()
    except UnicodeError as exception:
        raise ValueError("radio_token_file_invalid") from exception
    if re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token) is None:
        raise ValueError("radio_token_file_invalid")
    return token


class RadioClient:
    def __init__(self, base_url: str, token_file: Path, *, transport=None) -> None:
        parsed = urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in ("", "/")
                or parsed.port == 0):
            raise ValueError("radio_requires_loopback_http")
        token = read_control_token(token_file)
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._transport = transport
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), headers={"Authorization": f"Bearer {token}"},
            timeout=2, follow_redirects=False, trust_env=False, transport=transport,
            event_hooks={"request": [_trace_request], "response": [_trace_response]},
        )

    def fetch(self) -> dict:
        started = time.monotonic()
        try:
            with self._client.stream("GET", "/api/v2/engineer/live") as response:
                if response.status_code in (401, 403):
                    return {"status": "unavailable", "reason": "backend_authentication_failed"}
                if response.status_code != 200:
                    return {"status": "unavailable", "reason": "backend_response_unavailable"}
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 1_048_576 or time.monotonic() - started >= 5:
                        raise ValueError("oversized response")
                decoded = json.loads(body)
                if not isinstance(decoded, dict) or not isinstance(decoded.get("data"), dict):
                    raise ValueError("invalid response")
                return decoded["data"]
        except (httpx.RequestError, ValueError, RecursionError, RuntimeError) as exception:
            trace_event({"event": "radio_http_failure", "operation": "live_snapshot", "exception_chain": exception_trace(exception)})
            return {"status": "unavailable", "reason": "backend_response_unavailable"}

    def region_capture(self, method: str, lease_id: str, body=None) -> dict:
        if (method not in ("POST", "GET", "DELETE") or not isinstance(lease_id, str)
                or re.fullmatch(r"[a-f0-9]{32}", lease_id) is None):
            return {"status": "unavailable", "reason": "region_capture_request_invalid"}
        started = time.monotonic()
        try:
            options = {"json": body} if method == "POST" else {}
            with self._client.stream(method, f"/api/v2/engineer/region-captures/{lease_id}", **options) as response:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 1_048_576 or time.monotonic() - started >= 5:
                        raise ValueError("oversized response")
                decoded = json.loads(raw)
                if not isinstance(decoded, dict):
                    raise ValueError("invalid response")
                if response.status_code != (202 if method == "POST" else 200):
                    return {"status": "unavailable", "reason": "region_capture_rejected",
                            "status_code": response.status_code}
                if not isinstance(decoded.get("data"), dict):
                    raise ValueError("invalid response")
                return {"status": "available", "data": decoded["data"]}
        except (httpx.RequestError, ValueError, RecursionError, RuntimeError) as exception:
            trace_event({"event": "radio_http_failure", "operation": "region_capture", "exception_chain": exception_trace(exception)})
            return {"status": "unavailable", "reason": "region_capture_response_unavailable"}

    def detail_targets(self, session_id: str) -> dict:
        if (not isinstance(session_id, str)
                or re.fullmatch(r"[a-f0-9]{32}", session_id) is None):
            return {"status": "unavailable", "reason": "detail_request_invalid"}
        return self._detail_request("GET", "/api/v2/session-evidence/detail-targets",
                                    params={"session_id": session_id}, targets=True)

    def detail_task(self, method: str, task_id: str, body=None) -> dict:
        if (method not in ("POST", "GET", "DELETE") or not isinstance(task_id, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", task_id) is None):
            return {"status": "unavailable", "reason": "detail_request_invalid"}
        if method == "POST":
            if (not isinstance(body, dict) or set(body) != {"task_id", "session_id", "binding_id"}
                    or body.get("task_id") != task_id
                    or not isinstance(body.get("session_id"), str)
                    or re.fullmatch(r"[a-f0-9]{32}", body["session_id"]) is None
                    or not isinstance(body.get("binding_id"), str)
                    or re.fullmatch(r"[a-f0-9]{64}", body["binding_id"]) is None):
                return {"status": "unavailable", "reason": "detail_request_invalid"}
            return self._detail_request(method, "/api/v2/session-evidence/detail-tasks", json=body)
        if body is not None:
            return {"status": "unavailable", "reason": "detail_request_invalid"}
        return self._detail_request(method, f"/api/v2/session-evidence/detail-tasks/{task_id}")

    def track_models(self, session_id: str) -> dict:
        if not isinstance(session_id, str) or re.fullmatch(r"[a-f0-9]{32}", session_id) is None:
            return {"status": "unavailable", "reason": "track_model_request_invalid"}
        return self._detail_request("GET", f"/api/v2/session-evidence/sessions/{session_id}/track-models")

    def _detail_request(self, method: str, endpoint: str, *, targets=False, **options) -> dict:
        started = time.monotonic()
        try:
            with self._client.stream(method, endpoint, **options) as response:
                if response.status_code != (200 if method == "GET" else 202):
                    return {"status": "unavailable", "reason": "detail_request_rejected",
                            "status_code": response.status_code}
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 65_536 or time.monotonic() - started >= 5:
                        raise ValueError("oversized response")
                decoded = json.loads(raw)
                if not isinstance(decoded, dict):
                    raise ValueError("invalid response")
                data = decoded.get("data")
                if targets:
                    if (not isinstance(data, list) or len(data) > 24
                            or any(not isinstance(item, dict) for item in data)):
                        raise ValueError("invalid targets")
                elif not isinstance(data, dict):
                    raise ValueError("invalid task")
                return {"status": "available", "data": data}
        except (httpx.RequestError, ValueError, RecursionError, RuntimeError) as exception:
            trace_event({"event": "radio_http_failure", "operation": "detail_demand", "exception_chain": exception_trace(exception)})
            return {"status": "unavailable", "reason": "detail_response_unavailable"}

    def close(self) -> None:
        self._client.close()

    async def transcribe(self, wav: bytes, request_id: str, cancelled, hint: str | None = None) -> dict:
        try:
            validate_canonical_wav(wav)
        except LocalSpeechUnavailable:
            return {"status": "unavailable", "reason": "transcription_audio_invalid"}
        if (not isinstance(request_id, str)
                or re.fullmatch(r"[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}", request_id) is None):
            return {"status": "unavailable", "reason": "transcription_request_invalid"}
        if cancelled.is_set():
            return {"status": "unavailable", "reason": "transcription_cancelled"}
        async def watch_cancel():
            while not cancelled.is_set():
                await asyncio.sleep(0.05)

        request = asyncio.create_task(self._transcribe(wav, request_id, hint))
        cancellation = asyncio.create_task(watch_cancel())
        try:
            done, pending = await asyncio.wait((request, cancellation), timeout=65, return_when=asyncio.FIRST_COMPLETED)
            if request in done and not cancelled.is_set():
                return request.result()
            return {"status": "unavailable", "reason": ("transcription_cancelled" if cancelled.is_set()
                                                                     else "transcription_timeout")}
        finally:
            request.cancel()
            cancellation.cancel()
            await asyncio.gather(request, cancellation, return_exceptions=True)

    async def interpret(self, transcript: str, context: dict, request_id: str, cancelled) -> dict:
        """Ask the backend to interpret one transcript; failures are reported, never raised."""
        try:
            body = json.dumps({"transcript": transcript, "context": parse_context(context)},
                              ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(body) > MAX_CONTEXT_BYTES + 2048:
                raise ValueError
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return {"status": "unavailable", "reason": "intent_context_invalid"}
        if cancelled.is_set():
            return {"status": "unavailable", "reason": "intent_cancelled"}

        async def watch_cancel():
            while not cancelled.is_set():
                await asyncio.sleep(0.05)

        request = asyncio.create_task(self._interpret(body, request_id))
        cancellation = asyncio.create_task(watch_cancel())
        try:
            done, _pending = await asyncio.wait((request, cancellation), timeout=INTENT_TIMEOUT_S,
                                                return_when=asyncio.FIRST_COMPLETED)
            if request in done and not cancelled.is_set():
                return request.result()
            return {"status": "unavailable", "reason": "intent_cancelled" if cancelled.is_set() else "intent_timeout"}
        finally:
            request.cancel()
            cancellation.cancel()
            await asyncio.gather(request, cancellation, return_exceptions=True)

    async def _interpret(self, body: bytes, request_id: str) -> dict:
        headers = {"Content-Type": "application/json", "X-Request-ID": request_id}
        try:
            async with httpx.AsyncClient(base_url=self._base_url, headers=self._headers,
                                         timeout=httpx.Timeout(INTENT_TIMEOUT_S, connect=2, write=5, pool=2),
                                         follow_redirects=False, trust_env=False, transport=self._transport,
                                         event_hooks={"request": [_trace_async_request], "response": [_trace_async_response]}) as client:
                async with client.stream("POST", "/api/v2/engineer/radio-intent", headers=headers, content=body) as response:
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        payload.extend(chunk)
                        if len(payload) > INTENT_MAX_RESPONSE_BYTES:
                            raise ValueError
                    document = json.loads(payload) if payload else {}
                    reason = document.get("reason") if isinstance(document, dict) else None
                    reason = reason if isinstance(reason, str) and re.fullmatch(r"[a-z0-9_]{1,96}", reason) else None
                    if response.status_code in (401, 403):
                        return {"status": "unavailable", "reason": "backend_authentication_failed"}
                    if response.status_code != 200:
                        return {"status": "unavailable", "reason": reason or f"intent_http_{response.status_code}"}
                    data = document.get("data") if isinstance(document, dict) else None
                    if (document.get("status") != "ok" or not isinstance(data, dict)
                            or type(data.get("schema_version")) is not int or data["schema_version"] != 1):
                        raise ValueError
                    model = data.get("model") if isinstance(data.get("model"), dict) else {}
                    latency = model.get("latency_ms")
                    return {"status": "available", "intent": parse_intent(data.get("intent")),
                            "model_latency_ms": latency if type(latency) is int else None}
        except (httpx.RequestError, ValueError, RecursionError, RuntimeError, AttributeError) as exception:
            trace_event({"event": "radio_http_failure", "operation": "intent", "capture_id": request_id,
                         "exception_chain": exception_trace(exception)})
            return {"status": "unavailable", "reason": "intent_response_invalid_or_unavailable"}

    async def _transcribe(self, wav: bytes, request_id: str, hint: str | None = None) -> dict:
        digest = hashlib.sha256(wav).hexdigest()
        headers = {"Content-Type": "audio/wav", "X-Request-ID": request_id, "X-Audio-SHA256": digest}
        if hint and valid_speech_hint(hint):
            headers["X-Speech-Hint"] = hint
        try:
            async with httpx.AsyncClient(base_url=self._base_url, headers=self._headers,
                                         timeout=httpx.Timeout(62, connect=2, write=5, pool=2),
                                         follow_redirects=False, trust_env=False, transport=self._transport,
                                         event_hooks={"request": [_trace_async_request], "response": [_trace_async_response]}) as client:
                async with client.stream("POST", "/api/v1/engineer/transcribe", headers=headers, content=wav) as response:
                    if response.status_code in (401, 403):
                        return {"status": "unavailable", "reason": "backend_authentication_failed"}
                    if response.status_code == 409:
                        return {"status": "unavailable", "reason": "transcription_busy"}
                    if response.status_code != 200:
                        return {"status": "unavailable", "reason": "transcription_unavailable"}
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > SPEECH_MAX_RESPONSE_BYTES:
                            raise ValueError
                    document = json.loads(body)
                    data = document.get("data") if isinstance(document, dict) else None
                    if (document.get("status") != "ok" or not isinstance(data, dict)
                            or type(data.get("schema_version")) is not int or data["schema_version"] != 1
                            or data.get("request_id") != request_id or data.get("audio_sha256") != digest
                            or data.get("language") != "en" or data.get("verified") is not False
                            or not isinstance(data.get("transcript"), str) or not data["transcript"].strip()
                            or len(data["transcript"].encode("utf-8")) > SPEECH_MAX_TRANSCRIPT_BYTES):
                        raise ValueError
                    return {"status": "available", "transcript": data["transcript"].strip()}
        except (httpx.RequestError, ValueError, RecursionError, RuntimeError, AttributeError) as exception:
            trace_event({"event": "radio_http_failure", "operation": "transcription", "capture_id": request_id,
                         "exception_chain": exception_trace(exception)})
            return {"status": "unavailable", "reason": "transcription_response_invalid_or_unavailable"}
