from __future__ import annotations

import asyncio
import hashlib
import os
import stat
from pathlib import Path

import httpx

from .ai_admission import AIGateLease
from .local_speech import (SPEECH_MAX_TRANSCRIPT_BYTES, LocalSpeechUnavailable, LocalTranscription,
                           valid_speech_hint, validate_canonical_wav)


OPENAI_TRANSCRIPTION_MODEL = "gpt-transcribe"
OPENAI_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_TRANSCRIPTION_TIMEOUT_S = 20.0
OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
OPENAI_CONNECT_TIMEOUT_S = 4.0
OPENAI_KEEPALIVE_INTERVAL_S = 45.0
_MAX_RESPONSE_BYTES = 64 * 1024


class OpenAISpeechRuntime:
    """Cloud transcription with OpenAI gpt-transcribe; the API key is read from an owner-only file."""

    def __init__(self, key_file: str | Path, *, transport=None, model: str = OPENAI_TRANSCRIPTION_MODEL):
        self.key_file = Path(key_file)
        self.model = model
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._keepalive: asyncio.Task | None = None

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(OPENAI_TRANSCRIPTION_TIMEOUT_S, connect=OPENAI_CONNECT_TIMEOUT_S),
                                 limits=httpx.Limits(max_keepalive_connections=2, keepalive_expiry=600),
                                 follow_redirects=False, trust_env=False, transport=self._transport)

    async def start(self) -> None:
        """Open a shared connection and keep it warm; new connections to the API can be slow to establish."""
        if self._client is None:
            self._client = self._new_client()
            self._keepalive = asyncio.create_task(self._keep_warm())

    async def close(self) -> None:
        if self._keepalive is not None:
            self._keepalive.cancel()
            await asyncio.gather(self._keepalive, return_exceptions=True)
            self._keepalive = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _keep_warm(self) -> None:
        while True:
            try:
                await self._client.get(OPENAI_MODELS_URL, headers={"Authorization": f"Bearer {self._key()}"})
            except (httpx.HTTPError, LocalSpeechUnavailable):
                pass
            await asyncio.sleep(OPENAI_KEEPALIVE_INTERVAL_S)

    async def _post(self, client, key, data, wav):
        for attempt in range(2):
            try:
                return await client.post(OPENAI_TRANSCRIPTION_URL, data=data,
                                         files={"file": ("clip.wav", wav, "audio/wav")},
                                         headers={"Authorization": f"Bearer {key}"})
            except (httpx.ConnectTimeout, httpx.ConnectError):
                if attempt:
                    raise

    def _key(self) -> str:
        try:
            info = self.key_file.lstat()
            if not stat.S_ISREG(info.st_mode) or self.key_file.is_symlink():
                raise LocalSpeechUnavailable("openai_key_file_invalid")
            if os.name == "posix" and info.st_mode & 0o077:
                raise LocalSpeechUnavailable("openai_key_file_permissions_too_open")
            key = self.key_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise LocalSpeechUnavailable("openai_key_file_missing") from exc
        except (OSError, UnicodeError) as exc:
            raise LocalSpeechUnavailable("openai_key_file_invalid") from exc
        if not key.startswith("sk-") or not 20 <= len(key) <= 512 or any(c.isspace() for c in key):
            raise LocalSpeechUnavailable("openai_key_file_invalid")
        return key

    def status(self) -> dict[str, object]:
        base = {"runtime": "OpenAI", "runtime_version": None, "model_name": self.model, "model_sha256": None,
                "runtime_id": None, "language": "en", "placement": "cloud", "maximum_clip_seconds": None,
                "audio_persisted": False, "billing": "openai_api"}
        try:
            self._key()
        except LocalSpeechUnavailable as exc:
            return {**base, "status": "not_configured", "reason": exc.reason}
        return {**base, "status": "ready", "reason": None}

    def cleanup_stale_temporary_files(self) -> None:
        """Nothing is written to disk; present for interface parity with the local runtime."""

    async def transcribe(self, wav: bytes, *, gate_lease: AIGateLease | None = None,
                         hint: str | None = None) -> LocalTranscription:
        validate_canonical_wav(wav)
        if hint is not None and not valid_speech_hint(hint):
            raise LocalSpeechUnavailable("speech_hint_invalid")
        key = self._key()
        data = {"model": self.model, "language": "en", "response_format": "json"}
        if hint:
            data["prompt"] = hint
        try:
            if self._client is not None:
                response = await self._post(self._client, key, data, wav)
            else:
                async with self._new_client() as client:
                    response = await self._post(client, key, data, wav)
        except httpx.TimeoutException as exc:
            raise LocalSpeechUnavailable("openai_transcription_timeout") from exc
        except httpx.RequestError as exc:
            raise LocalSpeechUnavailable("openai_transcription_unreachable") from exc
        if response.status_code in (401, 403):
            raise LocalSpeechUnavailable("openai_authentication_failed")
        if response.status_code == 429:
            raise LocalSpeechUnavailable("openai_rate_limited")
        if response.status_code != 200 or len(response.content) > _MAX_RESPONSE_BYTES:
            raise LocalSpeechUnavailable("openai_transcription_failed")
        try:
            transcript = response.json().get("text")
        except (ValueError, AttributeError) as exc:
            raise LocalSpeechUnavailable("openai_transcription_failed") from exc
        if not isinstance(transcript, str) or not transcript.strip():
            raise LocalSpeechUnavailable("transcript_empty")
        transcript = " ".join(transcript.split())
        if len(transcript.encode("utf-8")) > SPEECH_MAX_TRANSCRIPT_BYTES:
            raise LocalSpeechUnavailable("transcript_limit_exceeded")
        return LocalTranscription(transcript=transcript, audio_sha256=hashlib.sha256(wav).hexdigest(),
                                  runtime_id="openai", runtime_version="v1", model_name=self.model,
                                  model_sha256="not_applicable", placement="cloud")
