from __future__ import annotations

import unicodedata


def display_driver_name(value: object) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return None
    if any(unicodedata.category(character).startswith("C") for character in value):
        return None
    name = " ".join(value.split())
    return name if name and len(name.encode("utf-8")) <= 64 and any(character.isalnum() for character in name) else None


def driver_name_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()).replace("\u2019", "'")


def driver_name_aliases(value: str) -> tuple[str, ...]:
    key = driver_name_key(value)
    surname = key.split()[-1]
    return tuple(dict.fromkeys((key, surname))) if any(character.isalpha() for character in surname) else (key,)
