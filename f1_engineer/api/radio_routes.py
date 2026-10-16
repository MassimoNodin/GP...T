"""Radio configuration delivery and bounded, process-local presence leases."""
from __future__ import annotations

import json
import threading
import time

from fastapi import Request
from fastapi.responses import JSONResponse

from ..analysis.engineer_preferences import PreferencesInvalid, PreferencesUnreadable, RevisionConflict
from ..radio.settings import CachedDocument, RadioSettings, RadioSettingsStore


class RadioPresence:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._lock = threading.Lock()
        self._entries = {}

    def update(self, body):
        if not isinstance(body, dict) or set(body) != {
            "instance_id", "version", "applied_revision", "tts_engine", "voices", "devices",
            "wheel_status", "speaking", "listening",
        }:
            raise ValueError("radio_presence_invalid")
        for key in ("instance_id", "version", "applied_revision", "wheel_status"):
            if not isinstance(body[key], str) or not 0 < len(body[key]) <= 128:
                raise ValueError("radio_presence_invalid")
        if body["tts_engine"] not in ("edge", "windows") or any(
            type(body[key]) is not bool for key in ("speaking", "listening")
        ):
            raise ValueError("radio_presence_invalid")
        if not isinstance(body["voices"], list) or len(body["voices"]) > 64 or any(
            not isinstance(voice, str) or not 0 < len(voice) <= 256 for voice in body["voices"]
        ):
            raise ValueError("radio_presence_invalid")
        if not isinstance(body["devices"], list) or len(body["devices"]) > 64:
            raise ValueError("radio_presence_invalid")
        for device in body["devices"]:
            if (not isinstance(device, dict) or set(device) != {"index", "name", "input", "output"}
                    or type(device["index"]) is not int or not 0 <= device["index"] <= 65535
                    or not isinstance(device["name"], str) or not 0 < len(device["name"]) <= 256
                    or type(device["input"]) is not bool or type(device["output"]) is not bool):
                raise ValueError("radio_presence_invalid")
        with self._lock:
            now = self.clock()
            self._entries = {key: entry for key, entry in self._entries.items() if now - entry[0] < 6}
            self._entries[body["instance_id"]] = (now, body.copy())
            if len(self._entries) > 16:
                del self._entries[min(self._entries, key=lambda key: self._entries[key][0])]

    def snapshot(self):
        with self._lock:
            if not self._entries:
                return {"connected": False, "age_s": None}
            received, body = max(self._entries.values(), key=lambda entry: entry[0])
            age = max(0, self.clock() - received)
            return {**body, "connected": age < 6, "age_s": age}


def register_radio_routes(app, *, database_path, preferences_store, control_token):
    from . import app as app_module
    from .engineer_preferences_routes import _IF_MATCH

    store = RadioSettingsStore.beside_database(database_path)
    radio = CachedDocument(store)
    assistance = CachedDocument(preferences_store)
    presence = RadioPresence()
    discovery = {"revision": 0}

    def settings_block():
        try:
            saved, prefs = radio.load(), assistance.load()
            return {"revision": f"{prefs.revision}:{saved.revision}", "radio_revision": saved.revision,
                    "assistance": {key: value for key, value in prefs.preferences.to_dict().items()
                                   if key in ("topics", "verbosity", "interruption", "depth")},
                    "radio": saved.preferences.to_dict(), "radio_connected": presence.snapshot()["connected"],
                    "devices_revision": discovery["revision"]}
        except (PreferencesUnreadable, OSError):
            return None

    app.state.radio_settings_block = settings_block

    def response(stored):
        return JSONResponse({"api_version": "v2", "status": "ok", "reason": None,
                             "data": stored.to_dict()}, headers={"ETag": f'"{stored.revision}"'})

    @app.get("/api/v2/engineer/radio/settings")
    def get_settings():
        try:
            return response(store.load())
        except PreferencesUnreadable:
            return app_module._api_error(503, "radio_settings_unreadable")

    @app.put("/api/v2/engineer/radio/settings")
    async def put_settings(request: Request):
        if not app_module._authorized(request.headers.get("authorization"), control_token):
            return app_module._api_error(403, "radio_control_not_authorized")
        try:
            raw = await app_module._read_limited_body(request, 8192)
            settings = RadioSettings.from_dict(json.loads(raw))
            header = request.headers.get("if-match")
            match = _IF_MATCH.fullmatch(header.strip()) if header and header.strip() != "*" else None
            if header and header.strip() != "*" and match is None:
                raise ValueError("radio_if_match_invalid")
            return response(store.update(settings, expected_revision=int(match[1]) if match else None))
        except RevisionConflict as exc:
            return JSONResponse({"status": "unavailable", "reason": "radio_revision_conflict",
                                 "data": {"revision": exc.current}}, status_code=409,
                                headers={"ETag": f'"{exc.current}"'})
        except app_module._EngineerRequestBodyLimitExceeded:
            return app_module._api_error(413, "radio_request_too_large")
        except (ValueError, UnicodeError, RecursionError) as exc:
            return app_module._api_error(422, str(exc) if isinstance(exc, PreferencesInvalid) else "radio_settings_invalid")
        except OSError:
            return app_module._api_error(503, "radio_settings_unavailable")

    @app.get("/api/v2/engineer/radio/presence")
    def get_presence():
        return {"data": presence.snapshot()}

    @app.put("/api/v2/engineer/radio/presence/refresh")
    def refresh_devices(request: Request):
        if not app_module._authorized(request.headers.get("authorization"), control_token):
            return app_module._api_error(403, "radio_control_not_authorized")
        discovery["revision"] += 1
        return {"data": {"devices_revision": discovery["revision"]}}

    @app.put("/api/v2/engineer/radio/presence")
    async def put_presence(request: Request):
        if not app_module._authorized(request.headers.get("authorization"), control_token):
            return app_module._api_error(403, "radio_control_not_authorized")
        try:
            presence.update(json.loads(await app_module._read_limited_body(request, 32768)))
            return {"data": presence.snapshot()}
        except app_module._EngineerRequestBodyLimitExceeded:
            return app_module._api_error(413, "radio_request_too_large")
        except (ValueError, UnicodeError, RecursionError):
            return app_module._api_error(422, "radio_presence_invalid")
