from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_INPUT_SETTINGS = "data/radio-input.json"


@dataclass(frozen=True)
class ButtonBinding:
    guid: str
    name: str
    button: int

    def __post_init__(self) -> None:
        if (not isinstance(self.guid, str) or re.fullmatch(r"[a-f0-9]{32}", self.guid) is None
                or not isinstance(self.name, str) or not 1 <= len(self.name) <= 256
                or any(ord(character) < 32 for character in self.name)
                or type(self.button) is not int or not 0 <= self.button < 512):
            raise ValueError("radio_input_binding_invalid")


def load_binding(settings: Path) -> ButtonBinding | None:
    if settings.is_symlink():
        raise ValueError("radio_input_settings_invalid")
    try:
        with settings.open("rb") as stream:
            raw = stream.read(4097)
    except FileNotFoundError:
        return None
    if len(raw) > 4096:
        raise ValueError("radio_input_settings_invalid")
    try:
        document = json.loads(raw)
        if (not isinstance(document, dict) or set(document) != {"schema_version", "listen"}
                or type(document["schema_version"]) is not int or document["schema_version"] != 1):
            raise ValueError
        value = document["listen"]
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != {"guid", "name", "button"}:
            raise ValueError
        return ButtonBinding(**value)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exception:
        raise ValueError("radio_input_settings_invalid") from exception


def save_binding(settings: Path, binding: ButtonBinding | None) -> None:
    if settings.is_symlink():
        raise ValueError("radio_input_settings_invalid")
    settings.parent.mkdir(parents=True, exist_ok=True)
    document = {"schema_version": 1, "listen": asdict(binding) if binding else None}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=settings.parent,
                                         prefix=".radio-input-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(document, stream, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, settings)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
