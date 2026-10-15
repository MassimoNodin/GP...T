from __future__ import annotations

import asyncio
import os

import httpx
import pytest

from f1_engineer.analysis.local_speech import LocalSpeechUnavailable
from f1_engineer.analysis.openai_speech import OPENAI_TRANSCRIPTION_URL, OpenAISpeechRuntime
from f1_engineer.api.app import create_app
from tests.test_local_speech import canonical_wav

KEY = "sk-test-0123456789abcdefghijklmnop"


def key_file(tmp_path, value=KEY):
    path = tmp_path / ".openai-api-key"
    path.write_text(value + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def runtime(tmp_path, handler, **kwargs):
    return OpenAISpeechRuntime(key_file(tmp_path, **kwargs), transport=httpx.MockTransport(handler))


def test_sends_gpt_transcribe_request_with_hint_and_returns_cloud_transcript(tmp_path):
    seen = {}

    def handler(request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], request.content
        return httpx.Response(200, json={"text": "  What was my\nlast lap time? "})

    result = asyncio.run(runtime(tmp_path, handler).transcribe(canonical_wav(16_000), hint="Drivers: Leclerc."))
    assert result.transcript == "What was my last lap time?" and result.placement == "cloud"
    assert result.model_name == "gpt-transcribe" and len(result.audio_sha256) == 64
    assert seen["url"] == OPENAI_TRANSCRIPTION_URL and seen["auth"] == f"Bearer {KEY}"
    for part in (b'name="model"', b"gpt-transcribe", b'name="language"', b'name="prompt"', b"Drivers: Leclerc.",
                 b'filename="clip.wav"'):
        assert part in seen["body"]


def test_no_prompt_field_without_a_hint(tmp_path):
    bodies = []

    def handler(request):
        bodies.append(request.content)
        return httpx.Response(200, json={"text": "Hello"})

    asyncio.run(runtime(tmp_path, handler).transcribe(canonical_wav(16_000)))
    assert b'name="prompt"' not in bodies[0]


@pytest.mark.parametrize("status,reason", [(401, "openai_authentication_failed"), (429, "openai_rate_limited"),
                                           (500, "openai_transcription_failed")])
def test_api_errors_map_to_safe_reasons(tmp_path, status, reason):
    with pytest.raises(LocalSpeechUnavailable, match=reason):
        asyncio.run(runtime(tmp_path, lambda request: httpx.Response(status, json={"error": "x"}))
                    .transcribe(canonical_wav(16_000)))


def test_empty_and_unreachable_responses(tmp_path):
    with pytest.raises(LocalSpeechUnavailable, match="transcript_empty"):
        asyncio.run(runtime(tmp_path, lambda request: httpx.Response(200, json={"text": "  "}))
                    .transcribe(canonical_wav(16_000)))

    def unreachable(request):
        raise httpx.ConnectError("down")

    with pytest.raises(LocalSpeechUnavailable, match="openai_transcription_unreachable"):
        asyncio.run(runtime(tmp_path, unreachable).transcribe(canonical_wav(16_000)))


def test_key_file_is_required_and_validated(tmp_path):
    missing = OpenAISpeechRuntime(tmp_path / "absent")
    assert missing.status()["status"] == "not_configured"
    assert missing.status()["reason"] == "openai_key_file_missing"
    assert OpenAISpeechRuntime(key_file(tmp_path, value="not-a-key")).status()["reason"] == "openai_key_file_invalid"
    assert OpenAISpeechRuntime(key_file(tmp_path)).status()["status"] == "ready"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_group_readable_key_file_is_refused(tmp_path):
    path = key_file(tmp_path)
    os.chmod(path, 0o640)
    assert OpenAISpeechRuntime(path).status()["reason"] == "openai_key_file_permissions_too_open"


def test_unsafe_hint_is_rejected_before_any_request(tmp_path):
    def handler(request):
        raise AssertionError("must not call the API")

    with pytest.raises(LocalSpeechUnavailable, match="speech_hint_invalid"):
        asyncio.run(runtime(tmp_path, handler).transcribe(canonical_wav(16_000), hint="-m evil"))


def test_app_selects_the_openai_runtime_only_when_asked(tmp_path):
    from fastapi.testclient import TestClient

    def make(**kwargs):
        return create_app(tmp_path / "a.sqlite3", recordings_root=tmp_path / "r", control_token="t" * 40,
                          automatic_acquisition=False, **kwargs)

    app = make(speech_provider="openai", openai_api_key_file=key_file(tmp_path))
    with TestClient(app) as client:
        assert isinstance(app.state.local_speech_runtime, OpenAISpeechRuntime)
        assert client.get("/api/v1/engineer/transcribe").json()["data"]["status"] == "ready"
    default = make()
    with TestClient(default):
        assert not isinstance(default.state.local_speech_runtime, OpenAISpeechRuntime)
    with pytest.raises(ValueError):
        make(speech_provider="azure")


def test_shared_connection_is_reused_kept_warm_and_retried_once(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/transcriptions") and calls.count("/v1/audio/transcriptions") == 1:
            raise httpx.ConnectTimeout("slow connect")
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"text": "Copy"})

    speech = runtime(tmp_path, handler)

    async def scenario():
        await speech.start()
        await asyncio.sleep(0)
        result = await speech.transcribe(canonical_wav(16_000))
        await speech.close()
        return result

    assert asyncio.run(scenario()).transcript == "Copy"
    assert calls[0] == "/v1/models" and calls.count("/v1/audio/transcriptions") == 2
    assert speech._client is None and speech._keepalive is None
