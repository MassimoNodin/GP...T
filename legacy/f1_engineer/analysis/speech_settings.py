from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from .engineer_preferences import RevisionConflict


MODELS = ("tiny.en", "large-v3")
DEVICES = ("cpu", "cuda")


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_settings(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"provider", "local"}:
        raise ValueError("speech_settings_invalid")
    local = value["local"]
    if (value["provider"] not in ("local", "openai") or not isinstance(local, dict)
            or set(local) != {"model", "device"} or local["model"] not in MODELS
            or local["device"] not in DEVICES):
        raise ValueError("speech_settings_invalid")
    return {"provider": value["provider"], "local": dict(local)}


class SpeechSettingsStore:
    def __init__(self, path: Path, default: dict[str, Any]) -> None:
        self.path = path
        self.default = validate_settings(default)
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        try:
            with self.path.open("rb") as handle:
                raw = handle.read(4097)
        except FileNotFoundError:
            return {"settings": validate_settings(self.default), "revision": 0}
        try:
            if len(raw) > 4096:
                raise ValueError()
            document = json.loads(raw)
            if (not isinstance(document, dict) or set(document) != {"settings", "revision"}
                    or type(document["revision"]) is not int or document["revision"] < 1):
                raise ValueError()
            return {"settings": validate_settings(document["settings"]), "revision": document["revision"]}
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise ValueError("speech_settings_unreadable") from exc

    def load(self) -> dict[str, Any]:
        with self._lock:
            return self._read()

    def update(self, settings: dict[str, Any], *, expected_revision: int | None = None) -> dict[str, Any]:
        settings = validate_settings(settings)
        with self._lock:
            try:
                current = self._read()
            except ValueError:
                if expected_revision is not None:
                    raise RevisionConflict(0) from None
                current = {"settings": self.default, "revision": 0}
            if expected_revision is not None and expected_revision != current["revision"]:
                raise RevisionConflict(current["revision"])
            if current["revision"] and current["settings"] == settings:
                return current
            stored = {"settings": settings, "revision": current["revision"] + 1}
            atomic_write(self.path, json.dumps(stored, sort_keys=True).encode() + b"\n")
            return stored
