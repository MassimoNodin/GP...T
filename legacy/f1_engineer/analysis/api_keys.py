from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .local_speech import LocalSpeechUnavailable
from .openai_speech import OPENAI_MODELS_URL, OpenAISpeechRuntime, valid_openai_key
from .speech_settings import atomic_write


class OpenAIKeyStore:
    """Key files are owner-only on POSIX; Windows relies on the data folder ACL."""

    def __init__(self, path: Path, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.path = path
        self.metadata_path = path.with_name(path.name + ".metadata.json")
        self.transport = transport
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        try:
            key = OpenAISpeechRuntime(self.path)._key()
        except LocalSpeechUnavailable:
            return {"configured": False, "last4": None, "updated_at_utc": None, "verified": None}
        modified = datetime.fromtimestamp(self.path.stat().st_mtime, timezone.utc).isoformat()
        verified = None
        try:
            metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            if metadata.get("sha256") == hashlib.sha256(key.encode()).hexdigest():
                candidate = metadata.get("verified")
                if candidate in ("ok", "auth_failed", "unreachable"):
                    verified = candidate
        except (OSError, ValueError, AttributeError):
            pass
        return {"configured": True, "last4": key[-4:], "updated_at_utc": modified, "verified": verified}

    async def save(self, key: str | None, *, verify: bool) -> dict[str, Any]:
        async with self._lock:
            if key is None:
                key = OpenAISpeechRuntime(self.path)._key()
            if not valid_openai_key(key):
                raise ValueError("openai_key_invalid")
            result = None
            if verify:
                try:
                    async with httpx.AsyncClient(timeout=5, transport=self.transport,
                                                 trust_env=False, follow_redirects=False) as client:
                        async with client.stream("GET", OPENAI_MODELS_URL,
                                                 headers={"Authorization": f"Bearer {key}"}) as response:
                            result = ("ok" if response.is_success else
                                      "auth_failed" if response.status_code in (401, 403) else "unreachable")
                except httpx.HTTPError:
                    result = "unreachable"
            atomic_write(self.path, key.encode() + b"\n")
            atomic_write(self.metadata_path, json.dumps({
                "sha256": hashlib.sha256(key.encode()).hexdigest(), "verified": result,
            }).encode())
            return self.status()

    async def remove(self) -> dict[str, Any]:
        async with self._lock:
            self.path.unlink(missing_ok=True)
            self.metadata_path.unlink(missing_ok=True)
            return self.status()
