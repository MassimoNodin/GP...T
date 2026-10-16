"""Revisioned radio configuration. Revision zero preserves CLI values."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from ..analysis.engineer_preferences import EngineerPreferencesStore, PreferencesInvalid, PreferencesUnreadable, StoredPreferences

DEFAULT_RADIO_SETTINGS = {
    "proactive_mode": "rules", "answer_mode": "intent", "intent_mode": "luna",
    "tts_engine": "edge", "voice": None, "rate": 0, "volume": 100,
    "microphone": None, "cue_output": None, "cue_volume": 0.08, "capture_seconds": 8,
}


@dataclass(frozen=True)
class RadioSettings:
    values: tuple

    def to_dict(self):
        return dict(self.values)

    @classmethod
    def from_dict(cls, document):
        if not isinstance(document, dict) or set(document) != set(DEFAULT_RADIO_SETTINGS):
            raise PreferencesInvalid("radio_settings_fields_invalid")
        choices = {"proactive_mode": ("off", "rules", "model"), "answer_mode": ("intent", "grounded"),
                   "intent_mode": ("luna", "parser"), "tts_engine": ("edge", "windows")}
        for key, value in document.items():
            if key in choices:
                valid = value in choices[key]
            elif key == "voice":
                valid = value is None or (isinstance(value, str) and 0 < len(value) <= 256)
            elif key in ("microphone", "cue_output"):
                valid = value is None or type(value) is int and 0 <= value <= 65535
            elif key in ("rate", "volume"):
                low, high = (-10, 10) if key == "rate" else (0, 100)
                valid = type(value) is int and low <= value <= high
            else:
                low, high = (0, 0.3) if key == "cue_volume" else (1, 12)
                valid = type(value) in (int, float) and math.isfinite(value) and low <= value <= high
                if key == "cue_volume" and value == 0:
                    valid = False
            if not valid:
                raise PreferencesInvalid("radio_settings_value_invalid", key)
        if document["answer_mode"] == "grounded" and document["intent_mode"] != "luna":
            raise PreferencesInvalid("radio_grounded_requires_luna", "intent_mode")
        return cls(tuple(sorted(document.items())))


class RadioSettingsStore(EngineerPreferencesStore):
    @classmethod
    def beside_database(cls, database_path):
        return cls(Path(database_path).expanduser().resolve().parent / ".f1-radio-settings.json")

    def _read(self):
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(16385)
        except FileNotFoundError:
            return StoredPreferences(RadioSettings.from_dict(DEFAULT_RADIO_SETTINGS), 0)
        except OSError as exc:
            raise PreferencesUnreadable("radio_settings_unreadable") from exc
        try:
            if len(raw) > 16384:
                raise ValueError("too large")
            document = json.loads(raw)
            revision = document.pop("revision")
            schema = document.pop("schema_version")
            if type(revision) is not int or revision < 1 or schema != 1:
                raise ValueError("invalid revision")
            return StoredPreferences(RadioSettings.from_dict(document), revision)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
            raise PreferencesUnreadable("radio_settings_unreadable") from exc


class CachedDocument:
    def __init__(self, store):
        self.store = store
        self.signature = object()
        self.document = None

    def load(self):
        try:
            stat = self.store.path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            signature = None
        if signature != self.signature:
            self.document = self.store.load()
            self.signature = signature
        return self.document
