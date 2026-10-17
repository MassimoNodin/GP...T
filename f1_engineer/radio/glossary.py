from __future__ import annotations

import tomllib
import unicodedata
from pathlib import Path


# Sent to the interpreter with every request so it can repair speech-to-text mistakes.
# `terms` explain radio vocabulary; `mishearings` map phrases the transcriber tends to produce to what was meant.
DEFAULT_GLOSSARY = {
    "terms": {
        "lap time": "time of a completed lap",
        "gap": "time between two cars",
        "delta": "time difference to another car or lap",
        "sector": "one third of a lap",
        "P3": "race position 3",
        "box": "pit stop",
        "DRS": "drag reduction system",
        "tyre": "tire",
        "braking point": "where the driver starts braking",
        "minimum speed": "slowest speed in a corner",
        "region": "named corner or distance window on the track",
        "consistency": "how steady lap times are",
    },
    "mishearings": {
        "slap": "lap",
        "lab": "lap",
        "laptop": "lap time",
        "lab time": "lap time",
        "latest time": "last lap time",
        "last time": "last lap time",
        "sector won": "sector 1",
        "sector to": "sector 2",
        "sector tree": "sector 3",
        "tire": "tyre",
        "brake point": "braking point",
        "mid speed": "minimum speed",
    },
}
MAX_ENTRIES = 64


def speech_hint(context, limit: int = 300) -> str | None:
    """Plain-ASCII vocabulary for the transcriber: this session's drivers and corner names."""
    if not isinstance(context, dict):
        return None

    def ascii_text(value):
        folded = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
        return " ".join("".join(c for c in folded if c.isalnum() or c in " '-.").split())

    names = [ascii_text(driver["name"]) for driver in context.get("drivers", []) if isinstance(driver, dict)]
    corners = [ascii_text(label) for label in context.get("regions", [])]
    hint = "Formula 1 race radio. Lap time, sector, gap."
    for label, entries in (("Drivers", names), ("Corners", corners)):
        entries = [entry for entry in entries if entry]
        if not entries:
            continue
        line = f" {label}:"
        for entry in entries:
            candidate = f"{line} {entry},"
            if len((hint + candidate).encode()) > limit - 1:
                break
            line = candidate
        if line != f" {label}:":
            hint += line.rstrip(",") + "."
    return hint


def _clean(entries, limit):
    clean = {}
    if isinstance(entries, dict):
        for heard, meant in entries.items():
            if (isinstance(heard, str) and isinstance(meant, str) and 0 < len(heard.encode()) <= 64
                    and 0 < len(meant.encode()) <= 120 and len(clean) < limit):
                clean[heard] = meant
    return clean


def load_glossary(path: str | Path | None = None) -> tuple[dict, str | None]:
    """Default glossary plus optional local TOML overrides; returns (glossary, problem)."""
    glossary = {key: dict(value) for key, value in DEFAULT_GLOSSARY.items()}
    if path is None:
        return glossary, None
    try:
        with open(path, "rb") as handle:
            custom = tomllib.load(handle)
    except FileNotFoundError:
        return glossary, None
    except (OSError, tomllib.TOMLDecodeError):
        return glossary, "glossary_file_invalid"
    for key in glossary:
        glossary[key].update(_clean(custom.get(key), MAX_ENTRIES))
        while len(glossary[key]) > MAX_ENTRIES:
            glossary[key].pop(next(iter(glossary[key])))
    return glossary, None
