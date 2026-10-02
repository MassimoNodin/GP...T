from __future__ import annotations

import os
import secrets
from pathlib import Path


def load_or_create_control_token(path: str | Path) -> str:
    token_path = Path(path).expanduser().resolve()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = secrets.token_urlsafe(32)
        try:
            with token_path.open("x", encoding="utf-8") as stream:
                stream.write(token + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                token_path.chmod(0o600)
            except OSError:
                pass
        except FileExistsError:
            token = token_path.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise ValueError("local API control token must contain at least 32 characters")
    return token
