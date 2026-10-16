from __future__ import annotations

import json

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool

from ..analysis.api_keys import OpenAIKeyStore, valid_openai_key
from ..analysis.engineer_preferences import RevisionConflict
from ..analysis.local_speech import LocalSpeechUnavailable
from ..analysis.speech_router import SpeechRouter
from ..analysis.speech_settings import validate_settings
from .engineer_preferences_routes import _IF_MATCH, _envelope as _preferences_envelope


def _envelope(*args):
    response = _preferences_envelope(*args)
    response.headers["Cache-Control"] = "no-store"
    return response


def register_speech_routes(app: FastAPI, *, router: SpeechRouter, control_token: str | None):
    from . import app as app_module

    keys = OpenAIKeyStore(router.cloud.key_file)
    app.state.speech_router = router
    app.state.openai_key_store = keys

    async def read_document(request):
        try:
            body = await app_module._read_limited_body(request, 2048)
            return json.loads(body)
        except app_module._EngineerRequestBodyLimitExceeded:
            return app_module._api_error(413, "speech_request_too_large")
        except (ValueError, UnicodeError, RecursionError):
            return app_module._api_error(422, "speech_body_invalid")

    def authorized(request):
        return app_module._authorized(request.headers.get("authorization"), control_token)

    @app.get("/api/v1/engineer/speech")
    async def get_speech():
        try:
            document = await run_in_threadpool(router.document)
            return _envelope(200, "ok", document, None, document["revision"])
        except (OSError, ValueError):
            return app_module._api_error(503, "speech_settings_unreadable")

    @app.put("/api/v1/engineer/speech")
    async def put_speech(request: Request):
        if not authorized(request):
            return app_module._api_error(403, "speech_control_not_authorized")
        header = request.headers.get("if-match")
        expected = None
        if header is not None and header.strip() != "*":
            match = _IF_MATCH.fullmatch(header.strip())
            if match is None:
                return app_module._api_error(422, "speech_if_match_invalid")
            expected = int(match.group(1))
        document = await read_document(request)
        if not isinstance(document, dict):
            return document if hasattr(document, "status_code") else app_module._api_error(422, "speech_settings_invalid")
        try:
            settings = validate_settings(document)
        except ValueError:
            return app_module._api_error(422, "speech_settings_invalid")
        try:
            try:
                current = router.store.load()
            except ValueError:
                if expected is not None:
                    raise RevisionConflict(0) from None
                current = {"revision": 0}
            if expected is not None and expected != current["revision"]:
                raise RevisionConflict(current["revision"])
            options = await run_in_threadpool(router.options)
            selected = next(option for option in options if option["provider"] == settings["provider"]
                            and (settings["provider"] == "openai" or
                                 (option["model"] == settings["local"]["model"] and
                                  option["device"] == settings["local"]["device"])))
            if selected["status"] != "ready":
                return app_module._api_error(422, selected["reason"] or "speech_selection_not_ready")
            router.store.update(settings, expected_revision=expected)
            await router.sync_selection()
            return await get_speech()
        except RevisionConflict as exc:
            return _envelope(409, "unavailable", {"revision": exc.current}, "speech_revision_conflict", exc.current)
        except (OSError, ValueError):
            return app_module._api_error(503, "speech_store_unavailable")

    @app.get("/api/v1/secrets/openai-api-key")
    async def get_key():
        try:
            return _envelope(200, "ok", keys.status(), None)
        except OSError:
            return app_module._api_error(503, "secret_store_unavailable")

    @app.put("/api/v1/secrets/openai-api-key")
    async def put_key(request: Request):
        if not authorized(request):
            return app_module._api_error(403, "secret_control_not_authorized")
        document = await read_document(request)
        if not isinstance(document, dict):
            return document if hasattr(document, "status_code") else app_module._api_error(422, "openai_key_invalid")
        if (set(document) not in ({"key", "verify"}, {"verify"})
                or type(document.get("verify")) is not bool
                or ("key" in document and not valid_openai_key(document["key"]))
                or ("key" not in document and document["verify"] is not True)):
            return app_module._api_error(422, "openai_key_invalid")
        try:
            result = await keys.save(document.get("key"), verify=document["verify"])
            await router.sync_selection()
            return _envelope(200, "ok", result, None)
        except (ValueError, LocalSpeechUnavailable):
            return app_module._api_error(422, "openai_key_invalid")
        except OSError:
            return app_module._api_error(503, "secret_store_unavailable")

    @app.delete("/api/v1/secrets/openai-api-key")
    async def delete_key(request: Request):
        if not authorized(request):
            return app_module._api_error(403, "secret_control_not_authorized")
        try:
            result = await keys.remove()
            await router.sync_selection()
            return _envelope(200, "ok", result, None)
        except OSError:
            return app_module._api_error(503, "secret_store_unavailable")
