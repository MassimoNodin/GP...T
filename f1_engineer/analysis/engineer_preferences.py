"""Engineer preferences: strict schema, atomic JSON store and answer-style helpers.

The Settings screen stores the same choices in the browser; this module is the
backend copy so the engineer can honour them. Everything here is deterministic
and local. Preferences shape wording and the amount of evidence shown; they
never change what is measured, and the diagnostic-only disclaimers are not
optional.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

PREFERENCES_SCHEMA_VERSION = 1
PREFERENCES_FILENAME = ".f1-engineer-preferences.json"
MAX_PREFERENCES_BYTES = 16 * 1024

# Stable keys in the order the Settings screen lists them. Value is the default.
TOPIC_DEFAULTS: dict[str, bool] = {
    "tyre_management": True,       # "Tyre management"
    "track_conditions": True,      # "Track conditions"
    "race_strategy": True,         # "Race strategy"
    "driving_technique": True,     # "Driving technique"
    "gap_information": True,       # "Gap information"
    "rule_reminders": True,        # "Rule reminders"
    "setup_suggestions": False,    # "Setup suggestions"
    "general_information": False,  # "General information"
}
FOCUS_AREA_DEFAULTS: dict[str, bool] = {
    "driving_consistency": True,   # "Driving consistency"
    "comparative_analysis": True,  # "Comparative analysis"
    "corner_analysis": True,       # "Corner analysis"
    "setup_impact": True,          # "Setup impact"
    "tyre_performance": True,      # "Tyre performance"
    "data_quality_notes": False,   # "Data quality notes"
}
VERBOSITY_VALUES = ("concise", "balanced", "detailed")        # Concise | Balanced | Detailed
INTERRUPTION_VALUES = ("allow", "queue", "never")             # Allow interruptions | Queue until finished | Never interrupt
DEPTH_VALUES = ("quick", "standard", "in_depth")              # Quick | Standard | In-depth

TOP_LEVEL_KEYS = frozenset(
    {"topics", "verbosity", "interruption", "depth", "interpretations", "focus_areas"}
)

# In-session call topics (B4) enabled by each UI assistance topic.
CALL_TOPICS = frozenset({"gap", "tyres", "lap_time", "track_limits", "pit", "telemetry", "general"})
TOPIC_TO_CALL_TOPICS: dict[str, frozenset[str]] = {
    "tyre_management": frozenset({"tyres"}),
    "track_conditions": frozenset({"track_limits"}),
    "race_strategy": frozenset({"pit"}),
    "driving_technique": frozenset({"lap_time"}),
    "gap_information": frozenset({"gap"}),
    "rule_reminders": frozenset({"track_limits"}),
    "setup_suggestions": frozenset(),
    "general_information": frozenset({"general"}),
}
# Telemetry loss/recovery notices are data-integrity notices, not coaching, so
# no preference can silence them.
ALWAYS_ENABLED_CALL_TOPICS = frozenset({"telemetry"})


class PreferencesInvalid(ValueError):
    """Raised with a stable reason code and the offending field path."""

    def __init__(self, reason: str, field_path: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.field = field_path


class PreferencesUnreadable(Exception):
    """The persisted file exists but cannot be trusted."""


class RevisionConflict(Exception):
    def __init__(self, current: int) -> None:
        super().__init__("preferences_revision_conflict")
        self.current = current


def _frozen_flags(values: Mapping[str, bool]) -> tuple[tuple[str, bool], ...]:
    return tuple(values.items())


@dataclass(frozen=True, slots=True)
class EngineerPreferences:
    topics: tuple[tuple[str, bool], ...] = field(default_factory=lambda: _frozen_flags(TOPIC_DEFAULTS))
    verbosity: str = "balanced"
    interruption: str = "allow"
    depth: str = "standard"
    interpretations: bool = True
    focus_areas: tuple[tuple[str, bool], ...] = field(
        default_factory=lambda: _frozen_flags(FOCUS_AREA_DEFAULTS)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "topics": dict(self.topics),
            "verbosity": self.verbosity,
            "interruption": self.interruption,
            "depth": self.depth,
            "interpretations": self.interpretations,
            "focus_areas": dict(self.focus_areas),
        }

    @classmethod
    def from_dict(cls, value: object) -> "EngineerPreferences":
        """Strictly validate a complete preferences document (all keys required)."""
        if not isinstance(value, Mapping):
            raise PreferencesInvalid("preferences_invalid_type")
        for key in value:
            if key not in TOP_LEVEL_KEYS:
                raise PreferencesInvalid("preferences_unknown_key", str(key)[:64])
        for key in sorted(TOP_LEVEL_KEYS):
            if key not in value:
                raise PreferencesInvalid("preferences_missing_key", key)
        return cls(
            topics=_flags("topics", value["topics"], TOPIC_DEFAULTS),
            verbosity=_choice("verbosity", value["verbosity"], VERBOSITY_VALUES),
            interruption=_choice("interruption", value["interruption"], INTERRUPTION_VALUES),
            depth=_choice("depth", value["depth"], DEPTH_VALUES),
            interpretations=_boolean("interpretations", value["interpretations"]),
            focus_areas=_flags("focus_areas", value["focus_areas"], FOCUS_AREA_DEFAULTS),
        )


def _boolean(path: str, value: object) -> bool:
    if type(value) is not bool:
        raise PreferencesInvalid("preferences_invalid_type", path)
    return value


def _choice(path: str, value: object, options: Sequence[str]) -> str:
    if not isinstance(value, str) or value not in options:
        raise PreferencesInvalid("preferences_invalid_value", path)
    return value


def _flags(path: str, value: object, schema: Mapping[str, bool]) -> tuple[tuple[str, bool], ...]:
    if not isinstance(value, Mapping):
        raise PreferencesInvalid("preferences_invalid_type", path)
    for key in value:
        if key not in schema:
            raise PreferencesInvalid("preferences_unknown_key", f"{path}.{str(key)[:64]}")
    for key in schema:
        if key not in value:
            raise PreferencesInvalid("preferences_missing_key", f"{path}.{key}")
    # Keep the schema order so equal documents compare and serialise identically.
    return tuple((key, _boolean(f"{path}.{key}", value[key])) for key in schema)


DEFAULT_PREFERENCES = EngineerPreferences()


@dataclass(frozen=True, slots=True)
class StoredPreferences:
    preferences: EngineerPreferences
    revision: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PREFERENCES_SCHEMA_VERSION,
            **self.preferences.to_dict(),
            "revision": self.revision,
        }


class EngineerPreferencesStore:
    """JSON file store with atomic replacement and a monotonic revision counter.

    Revision 0 means "defaults, never saved". An identical re-save does not bump
    the revision, so the revision doubles as a stable ETag.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    @classmethod
    def beside_database(cls, database_path: str | Path) -> "EngineerPreferencesStore":
        return cls(Path(database_path).expanduser().resolve().parent / PREFERENCES_FILENAME)

    def load(self) -> StoredPreferences:
        """Return stored preferences; raise PreferencesUnreadable for a bad file."""
        with self._lock:
            return self._read()

    def current(self) -> EngineerPreferences:
        """Preferences for answer shaping; an unreadable file never blocks an answer."""
        try:
            return self.load().preferences
        except PreferencesUnreadable:
            return DEFAULT_PREFERENCES

    def update(
        self, preferences: EngineerPreferences, *, expected_revision: int | None = None
    ) -> StoredPreferences:
        with self._lock:
            try:
                existing: StoredPreferences | None = self._read()
            except PreferencesUnreadable:
                # An explicit save repairs a damaged file, but a caller that named
                # a revision cannot be matched against it.
                existing = None
                if expected_revision is not None:
                    raise RevisionConflict(0) from None
            current_revision = existing.revision if existing else 0
            if expected_revision is not None and expected_revision != current_revision:
                raise RevisionConflict(current_revision)
            if existing is not None and existing.preferences == preferences and current_revision > 0:
                return existing
            stored = StoredPreferences(preferences, current_revision + 1)
            self._write(stored)
            return stored

    def _read(self) -> StoredPreferences:
        try:
            with self.path.open("rb") as handle:
                raw = handle.read(MAX_PREFERENCES_BYTES + 1)
        except FileNotFoundError:
            return StoredPreferences(DEFAULT_PREFERENCES, 0)
        except OSError as exc:
            raise PreferencesUnreadable("preferences_file_unreadable") from exc
        if len(raw) > MAX_PREFERENCES_BYTES:
            raise PreferencesUnreadable("preferences_file_too_large")
        try:
            document = json.loads(raw.decode("utf-8"))
            if not isinstance(document, dict):
                raise PreferencesInvalid("preferences_invalid_type")
            expected = {"schema_version", "revision", *TOP_LEVEL_KEYS}
            if set(document) != expected:
                raise PreferencesInvalid("preferences_unknown_key")
            revision = document["revision"]
            if (document["schema_version"] != PREFERENCES_SCHEMA_VERSION
                    or type(revision) is not int or revision < 1):
                raise PreferencesInvalid("preferences_invalid_value")
            preferences = EngineerPreferences.from_dict(
                {key: document[key] for key in TOP_LEVEL_KEYS}
            )
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise PreferencesUnreadable("preferences_file_invalid") from exc
        return StoredPreferences(preferences, revision)

    def _write(self, stored: StoredPreferences) -> None:
        payload = json.dumps(
            stored.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8") + b"\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


# ---------------------------------------------------------------- application

_STYLE_BY_VERBOSITY = {
    # max_units counts message parts (sentences or observations); max_chars is the
    # length budget for the optional parts. Balanced budgets are deliberately
    # generous: they reproduce the pre-preferences answers unchanged.
    "concise": {"max_sentences": 3, "max_chars": 420},
    "balanced": {"max_sentences": 8, "max_chars": 2000},
    "detailed": {"max_sentences": 12, "max_chars": 3200},
}
_STYLE_BY_DEPTH = {
    "quick": {"max_facts": 3, "max_observations": 1},
    "standard": {"max_facts": 6, "max_observations": 3},
    "in_depth": {"max_facts": 8, "max_observations": 3},
}


def answer_style(preferences: EngineerPreferences | None) -> dict[str, Any]:
    """Pure answer-shaping budget derived from verbosity, depth and interpretations.

    Keys: ``max_sentences``/``max_chars`` (budget for optional message parts),
    ``max_facts`` (facts kept per answer), ``max_observations`` (measured
    observations kept in a focused message), ``include_notes`` (secondary notes
    such as qualification lists), ``include_context_note`` (extra detail line) and
    ``include_interpretations`` (cautionary reading notes). Diagnostic-only
    disclaimers are always kept. ``None`` means the default preferences.
    """
    prefs = preferences or DEFAULT_PREFERENCES
    style: dict[str, Any] = {
        "verbosity": prefs.verbosity,
        "depth": prefs.depth,
        **_STYLE_BY_VERBOSITY[prefs.verbosity],
        **_STYLE_BY_DEPTH[prefs.depth],
        "include_notes": prefs.verbosity != "concise",
        "include_context_note": prefs.verbosity == "detailed",
        "include_interpretations": prefs.interpretations,
    }
    if prefs.depth == "in_depth":
        style["include_notes"] = True
    return style


def enabled_topics(preferences: EngineerPreferences | None) -> set[str]:
    """In-session call topics (gap, tyres, lap_time, track_limits, pit, telemetry,
    general) allowed by the assistance-topic switches.

    Mapping: tyre_management->tyres, track_conditions->track_limits,
    race_strategy->pit, driving_technique->lap_time, gap_information->gap,
    rule_reminders->track_limits, general_information->general,
    setup_suggestions->(none). ``telemetry`` is always enabled.
    """
    prefs = preferences or DEFAULT_PREFERENCES
    enabled = set(ALWAYS_ENABLED_CALL_TOPICS)
    for key, on in prefs.topics:
        if on:
            enabled |= TOPIC_TO_CALL_TOPICS[key]
    return enabled


def compose_message(parts: Sequence[tuple[str, bool]], style: Mapping[str, Any] | None) -> str:
    """Join ``(text, required)`` parts; optional parts are dropped, in order, once
    the style's part-count or character budget is spent. Required parts (the
    measurement and the diagnostic disclaimer) are never dropped. ``style=None``
    keeps everything."""
    if style is None:
        return " ".join(text for text, _required in parts)
    required_parts = [text for text, required in parts if required]
    units = len(required_parts)
    chars = sum(len(text) for text in required_parts) + max(0, units - 1)
    kept: list[str] = []
    for text, required in parts:
        if required:
            kept.append(text)
            continue
        if units + 1 > style["max_sentences"] or chars + len(text) + 1 > style["max_chars"]:
            continue
        units += 1
        chars += len(text) + 1
        kept.append(text)
    return " ".join(kept)


def with_topic(preferences: EngineerPreferences, key: str, enabled: bool) -> EngineerPreferences:
    """Convenience for tests and tooling: return a copy with one topic changed."""
    if key not in TOPIC_DEFAULTS:
        raise PreferencesInvalid("preferences_unknown_key", f"topics.{key}")
    return replace(
        preferences,
        topics=tuple((name, enabled if name == key else value) for name, value in preferences.topics),
    )
