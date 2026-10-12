from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


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
