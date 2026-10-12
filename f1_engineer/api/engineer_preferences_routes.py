"""GET/PUT /api/v1/engineer/preferences (control token required to write)."""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..analysis.engineer_preferences import (
    EngineerPreferences,
    EngineerPreferencesStore,
    PreferencesInvalid,
    PreferencesUnreadable,
    RevisionConflict,
    StoredPreferences,
)

MAX_PREFERENCES_REQUEST_BYTES = 8 * 1024
_IF_MATCH = re.compile(r'^(?:W/)?"?([0-9]{1,9})"?$')


def _envelope(status_code: int, status: str, data: Any, reason: str | None,
              revision: int | None = None) -> JSONResponse:
    response = JSONResponse(
        status_code=status_code,
        content={"api_version": "v1", "status": status, "data": data, "reason": reason},
    )
    if revision is not None:
        response.headers["ETag"] = f'"{revision}"'
    return response


def _ok(stored: StoredPreferences) -> JSONResponse:
    return _envelope(200, "ok", stored.to_dict(), None, stored.revision)


def register_engineer_preferences_routes(
    app: FastAPI,
    *,
    store: EngineerPreferencesStore,
    control_token: str | None,
    max_request_bytes: int = MAX_PREFERENCES_REQUEST_BYTES,
) -> None:
    # Imported at call time: app.py imports this module, so a top-level import
    # would be circular. Reusing app.py keeps one authorization implementation.
    from . import app as app_module

    @app.get("/api/v1/engineer/preferences")
    def get_engineer_preferences() -> JSONResponse:
        try:
            return _ok(store.load())
        except PreferencesUnreadable as exc:
            return _envelope(503, "unavailable", None, str(exc) or "preferences_file_unreadable")

    @app.put("/api/v1/engineer/preferences")
    async def put_engineer_preferences(request: Request) -> JSONResponse:
        if not app_module._authorized(request.headers.get("authorization"), control_token):
            return app_module._api_error(403, "preferences_control_not_authorized")
        expected: int | None = None
        header = request.headers.get("if-match")
        if header is not None and header.strip() != "*":
            match = _IF_MATCH.fullmatch(header.strip())
            if match is None:
                return app_module._api_error(422, "preferences_if_match_invalid")
            expected = int(match.group(1))
        try:
            body = await app_module._read_limited_body(request, max_request_bytes)
        except app_module._EngineerRequestBodyLimitExceeded:
            return app_module._api_error(413, "preferences_request_too_large")
        except ValueError:
            return app_module._api_error(400, "preferences_content_length_invalid")
        try:
            document = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            return app_module._api_error(422, "preferences_body_malformed")
        try:
            preferences = EngineerPreferences.from_dict(document)
        except PreferencesInvalid as exc:
            return _envelope(422, "unavailable", {"field": exc.field} if exc.field else None,
                             exc.reason)
        try:
            return _ok(store.update(preferences, expected_revision=expected))
        except RevisionConflict as exc:
            return _envelope(409, "unavailable", {"revision": exc.current},
                             "preferences_revision_conflict", exc.current)
        except OSError:
            return app_module._api_error(503, "preferences_store_unavailable")
