from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..analysis.local_speech import (SPEECH_MAX_RESPONSE_BYTES, SPEECH_MAX_TRANSCRIPT_BYTES,
                                    LocalSpeechUnavailable, validate_canonical_wav)


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
        except (httpx.RequestError, ValueError, RecursionError, RuntimeError):
            return {"status": "unavailable", "reason": "backend_response_unavailable"}

    def close(self) -> None:
        self._client.close()

    async def transcribe(self, wav: bytes, request_id: str, cancelled) -> dict:
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

        request = asyncio.create_task(self._transcribe(wav, request_id))
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

    async def _transcribe(self, wav: bytes, request_id: str) -> dict:
        digest = hashlib.sha256(wav).hexdigest()
        headers = {"Content-Type": "audio/wav", "X-Request-ID": request_id, "X-Audio-SHA256": digest}
        try:
            async with httpx.AsyncClient(base_url=self._base_url, headers=self._headers,
                                         timeout=httpx.Timeout(62, connect=2, write=5, pool=2),
                                         follow_redirects=False, trust_env=False, transport=self._transport) as client:
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
        except (httpx.RequestError, ValueError, RecursionError, RuntimeError, AttributeError):
            return {"status": "unavailable", "reason": "transcription_response_invalid_or_unavailable"}
