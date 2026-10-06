from __future__ import annotations

import json
import re
import stat
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .engineer_ask import (
    ENGINEER_ASK_MAX_ROUTER_OUTPUT_BYTES,
    EngineerAskRoute,
    build_router_messages,
    parse_router_output,
    route_schema,
)


OLLAMA_BASE_URL = "http://127.0.0.1:11435"
OLLAMA_MODEL_NAME = "qwen3:4b"
OLLAMA_PROFILE_NAME = "gp-dot-t-private-v1"
OLLAMA_STATUS_MAX_BYTES = 8 * 1024
OLLAMA_RESULT_MAX_BYTES = 32 * 1024
OLLAMA_TIMEOUT_SECONDS = 58.0
_DIGEST_PATTERN = re.compile(r"^(?:sha256:)?([a-f0-9]{64})$")
_OLLAMA_VERSION_PATTERN = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")


class OllamaUnavailable(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ModelPin:
    model: str
    digest: str
    profile: str
    endpoint: str
    no_cloud_requested: bool


class OllamaRuntime:
    """Small, fixed-loopback client for the app-managed Ollama profile."""

    def __init__(self, pin_path: str | Path) -> None:
        self._pin_path = Path(pin_path)
        self._last_placement: str | None = None
        self._last_placement_at: str | None = None

    async def status(self) -> dict[str, object]:
        pin = self._load_pin()
        if pin is None:
            return self._unavailable("model_pin_missing", status="not_configured")
        async with self._client(timeout=4.0) as client:
            try:
                version = await self._get_json(client, "/api/version", OLLAMA_STATUS_MAX_BYTES)
                runtime_version = version.get("version")
                if not isinstance(runtime_version, str) or not _is_supported_ollama_version(
                    runtime_version
                ):
                    return self._unavailable(
                        "runtime_version_unsupported",
                        status="unavailable",
                        pin=pin,
                        runtime_version=runtime_version,
                    )
                tags = await self._get_json(client, "/api/tags", OLLAMA_STATUS_MAX_BYTES)
                model = self._find_model(tags, pin.model)
                if model is None:
                    return self._unavailable("model_not_installed", status="model_unavailable", pin=pin)
                if _is_remote_model(model):
                    return self._unavailable("remote_model_rejected", status="model_rejected", pin=pin)
                if _normalized_digest(model.get("digest")) != pin.digest:
                    return self._unavailable("model_digest_changed", status="model_changed", pin=pin)
            except OllamaUnavailable as exc:
                return self._unavailable(exc.reason, status="unavailable", pin=pin)
            except httpx.HTTPError:
                return self._unavailable("runtime_unavailable", status="unavailable", pin=pin)
        if (
            pin.profile != OLLAMA_PROFILE_NAME
            or pin.endpoint != OLLAMA_BASE_URL
            or pin.no_cloud_requested is not True
        ):
            return self._unavailable("private_runtime_profile_invalid", status="model_rejected", pin=pin)
        runtime_version = version.get("version")
        if not isinstance(runtime_version, str) or not runtime_version or len(runtime_version) > 64:
            return self._unavailable("runtime_version_unavailable", status="unavailable", pin=pin)
        quantization = _bounded_text(_mapping(model.get("details")), "quantization_level", 32)
        parameters = _bounded_text(_mapping(model.get("details")), "parameter_size", 32)
        return {
            "status": "ready",
            "reason": None,
            "runtime": "Ollama",
            "runtime_version": runtime_version,
            "endpoint": "127.0.0.1:11435",
            "model_name": pin.model,
            "model_digest": pin.digest,
            "model_quantization": quantization,
            "model_parameter_size": parameters,
            "cloud_routing": "unverified",
            "requested_placement": "CPU",
            "last_inference_placement": self._last_placement,
            "last_inference_at_utc": self._last_placement_at,
        }

    async def route_question(
        self,
        question: str,
        *,
        selection_kind: str,
        allowed_routes: tuple[str, ...],
    ) -> tuple[EngineerAskRoute, dict[str, object]]:
        status = await self.status()
        if status.get("status") != "ready":
            raise OllamaUnavailable(str(status.get("reason") or "runtime_unavailable"))
        pin = self._load_pin()
        if (
            pin is None
            or status.get("model_name") != pin.model
            or status.get("model_digest") != pin.digest
        ):
            raise OllamaUnavailable("model_pin_changed_during_request")
        messages = build_router_messages(
            question,
            selection_kind=selection_kind,  # type: ignore[arg-type]
            allowed_routes=allowed_routes,
        )
        schema = route_schema(allowed_routes)
        request_payload = {
            "model": pin.model,
            "messages": messages,
            "format": schema,
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0,
                "num_ctx": 4_096,
                "num_predict": 128,
                "num_gpu": 0,
                "num_thread": 2,
            },
            "keep_alive": "10s",
        }
        payload_bytes = json.dumps(
            request_payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if len(payload_bytes) > 12 * 1024:
            raise OllamaUnavailable("router_prompt_limit_exceeded")

        started = time.perf_counter()
        try:
            async with self._client(timeout=OLLAMA_TIMEOUT_SECONDS) as client:
                response = await self._post_json(
                    client, "/api/chat", request_payload, OLLAMA_RESULT_MAX_BYTES
                )
                if response.get("done") is not True or response.get("done_reason") == "length":
                    raise OllamaUnavailable("router_output_truncated")
                model_name = response.get("model")
                if model_name != pin.model:
                    raise OllamaUnavailable("runtime_returned_unpinned_model")
                if _is_remote_model(response):
                    raise OllamaUnavailable("remote_model_rejected")
                message = _mapping(response.get("message"))
                if (
                    message is None
                    or message.get("role") != "assistant"
                    or message.get("tool_calls") not in (None, [])
                ):
                    raise OllamaUnavailable("router_response_shape_invalid")
                try:
                    route = parse_router_output(
                        message.get("content"), allowed_routes=allowed_routes
                    )
                except ValueError as exc:
                    raise OllamaUnavailable("router_output_invalid") from exc
                tags = await self._get_json(client, "/api/tags", OLLAMA_STATUS_MAX_BYTES)
                model = self._find_model(tags, pin.model)
                if (
                    model is None
                    or _is_remote_model(model)
                    or _normalized_digest(model.get("digest")) != pin.digest
                ):
                    raise OllamaUnavailable("model_digest_changed")
                placement = await self._cpu_placement(client, pin)
        except httpx.TimeoutException as exc:
            raise OllamaUnavailable("inference_timeout") from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailable("runtime_request_failed") from exc

        self._last_placement = placement
        self._last_placement_at = _utc_now()
        elapsed_ms = max(0, round((time.perf_counter() - started) * 1_000))
        runtime_version = _bounded_text(_mapping(status), "runtime_version", 64)
        return route, {
            "model_name": pin.model,
            "model_digest": pin.digest,
            "runtime_version": runtime_version,
            "inference_placement": placement,
            "latency_ms": elapsed_ms,
        }

    async def _cpu_placement(self, client: httpx.AsyncClient, pin: ModelPin) -> str:
        process = await self._get_json(client, "/api/ps", OLLAMA_STATUS_MAX_BYTES)
        raw_models = process.get("models")
        if not isinstance(raw_models, list) or len(raw_models) != 1:
            raise OllamaUnavailable("inference_placement_unavailable")
        active = _mapping(raw_models[0])
        if (
            active is None
            or _normalized_digest(active.get("digest")) != pin.digest
            or active.get("model", active.get("name")) != pin.model
            or _is_remote_model(active)
        ):
            raise OllamaUnavailable("inference_placement_model_mismatch")
        size_vram = active.get("size_vram")
        if isinstance(size_vram, bool) or not isinstance(size_vram, int) or size_vram != 0:
            raise OllamaUnavailable("cpu_only_inference_not_confirmed")
        return "CPU (0 VRAM)"

    def _load_pin(self) -> ModelPin | None:
        try:
            info = self._pin_path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or self._pin_path.is_symlink()
                or info.st_size > 2_048
            ):
                return None
            with self._pin_path.open("rb") as pin_file:
                raw_bytes = pin_file.read(2_049)
            if len(raw_bytes) > 2_048:
                return None
            raw = raw_bytes.decode("utf-8", errors="strict")
            value = json.loads(raw)
        except (OSError, UnicodeError, ValueError, RecursionError, OverflowError):
            return None
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "profile",
            "endpoint",
            "no_cloud_requested",
            "model",
            "digest",
        }:
            return None
        digest = value.get("digest")
        if (
            value.get("schema_version") != 1
            or value.get("profile") != OLLAMA_PROFILE_NAME
            or value.get("endpoint") != OLLAMA_BASE_URL
            or value.get("no_cloud_requested") is not True
            or value.get("model") != OLLAMA_MODEL_NAME
            or not isinstance(digest, str)
            or _normalized_digest(digest) != digest
        ):
            return None
        return ModelPin(
            model=OLLAMA_MODEL_NAME,
            digest=digest,
            profile=OLLAMA_PROFILE_NAME,
            endpoint=OLLAMA_BASE_URL,
            no_cloud_requested=True,
        )

    def _client(self, *, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=OLLAMA_BASE_URL,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 2.0)),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        )

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        maximum: int,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        if method == "GET":
            request = client.stream("GET", path)
        elif method == "POST":
            request = client.stream("POST", path, json=payload)
        else:
            raise OllamaUnavailable("runtime_method_unsupported")
        async with request as response:
            if response.is_redirect:
                raise OllamaUnavailable("runtime_redirect_rejected")
            if response.status_code < 200 or response.status_code >= 300:
                raise OllamaUnavailable("runtime_request_failed")
            raw = await _read_bounded(response, maximum)
        return _decode_object(raw)

    async def _post_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        payload: dict[str, object],
        maximum: int,
    ) -> dict[str, Any]:
        async with client.stream("POST", path, json=payload) as response:
            if response.is_redirect:
                raise OllamaUnavailable("runtime_redirect_rejected")
            if response.status_code < 200 or response.status_code >= 300:
                raise OllamaUnavailable("runtime_request_failed")
            raw = await _read_bounded(response, maximum)
        return _decode_object(raw)

    @staticmethod
    def _find_model(tags: Mapping[str, object], name: str) -> Mapping[str, object] | None:
        models = tags.get("models")
        if not isinstance(models, list) or len(models) > 256:
            raise OllamaUnavailable("runtime_model_catalog_invalid")
        for item in models:
            model = _mapping(item)
            if model and model.get("name") == name:
                return model
        return None

    @staticmethod
    def _unavailable(
        reason: str,
        *,
        status: str = "unavailable",
        pin: ModelPin | None = None,
        runtime_version: object = None,
    ) -> dict[str, object]:
        return {
            "status": status,
            "reason": reason,
            "runtime": "Ollama",
            "runtime_version": _bounded_runtime_version(runtime_version),
            "endpoint": "127.0.0.1:11435",
            "model_name": pin.model if pin else OLLAMA_MODEL_NAME,
            "model_digest": pin.digest if pin else None,
            "model_quantization": None,
            "model_parameter_size": None,
            "cloud_routing": "unverified",
            "requested_placement": "CPU",
            "last_inference_placement": None,
            "last_inference_at_utc": None,
        }


async def _read_bounded(response: httpx.Response, maximum: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            size = int(declared)
        except ValueError as exc:
            raise OllamaUnavailable("runtime_content_length_invalid") from exc
        if size < 0 or size > maximum:
            raise OllamaUnavailable("runtime_response_limit_exceeded")
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > maximum:
            raise OllamaUnavailable("runtime_response_limit_exceeded")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeError, ValueError, RecursionError, OverflowError) as exc:
        raise OllamaUnavailable("runtime_response_malformed") from exc
    if not isinstance(value, dict):
        raise OllamaUnavailable("runtime_response_malformed")
    return value


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _normalized_digest(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = _DIGEST_PATTERN.fullmatch(value)
    return f"sha256:{match.group(1)}" if match else None


def _is_supported_ollama_version(value: str) -> bool:
    if len(value) > 64 or not value.isascii():
        return False
    match = _OLLAMA_VERSION_PATTERN.fullmatch(value)
    if match is None:
        return False
    return tuple(int(part) for part in match.groups()) >= (0, 9, 0)


def _bounded_runtime_version(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or len(value) > 64
        or not value.isascii()
        or not value.isprintable()
    ):
        return None
    return value


def _is_remote_model(value: Mapping[str, object]) -> bool:
    return any(
        isinstance(value.get(field), str) and bool(value[field].strip())
        for field in ("remote_host", "remote_model")
    )


def _bounded_text(value: Mapping[str, object] | None, key: str, maximum: int) -> str | None:
    if value is None:
        return None
    item = value.get(key)
    return item[:maximum] if isinstance(item, str) and len(item) <= maximum else None


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
