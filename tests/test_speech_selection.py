from __future__ import annotations

import asyncio
import hashlib
import json
import os

import httpx
import pytest

from f1_engineer.analysis.api_keys import OpenAIKeyStore, valid_openai_key
from f1_engineer.analysis.engineer_preferences import RevisionConflict
from f1_engineer.analysis.local_speech import LocalSpeechUnavailable, LocalTranscription, _runtime_identity
from f1_engineer.analysis.speech_router import SpeechRouter
from f1_engineer.analysis.speech_settings import SpeechSettingsStore, validate_settings
from f1_engineer.api.app import create_app
from tests.test_local_speech import canonical_wav, write_pin


KEY = "sk-test-0123456789abcdefghijklmnop"
LOCAL = {"provider": "local", "local": {"model": "tiny.en", "device": "cpu"}}
TOKEN = "test-speech-control-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def make_app(tmp_path, **kwargs):
    return create_app(tmp_path / "test.sqlite3", recordings_root=tmp_path / "captures",
                      automatic_acquisition=False, control_token=TOKEN, **kwargs)


def install_pin(directory, *, model="tiny.en", device="cpu", legacy=False):
    write_pin(directory)
    source = directory / "pin.json"
    document = json.loads(source.read_text())
    document["model_name"] = model
    document["device"] = device
    document["runtime_id"] = _runtime_identity(
        document["runtime_version"], document["executable_sha256"], document["model_sha256"],
        tuple((item["name"], item["sha256"]) for item in document["dependencies"]),
        model_name=model, device=device,
    )
    target = directory / (".f1-engineer-whisper-pin.json" if legacy else
                          f".f1-engineer-whisper-pin.{model}.{device}.json")
    target.write_text(json.dumps(document))
    return target


def test_settings_revision_seed_noop_conflict_and_restart(tmp_path):
    path = tmp_path / "settings.json"
    store = SpeechSettingsStore(path, LOCAL)
    assert store.load() == {"settings": LOCAL, "revision": 0}
    assert store.update(LOCAL, expected_revision=0)["revision"] == 1
    assert store.update(LOCAL, expected_revision=1)["revision"] == 1
    cloud = {**LOCAL, "provider": "openai"}
    assert store.update(cloud, expected_revision=1)["revision"] == 2
    with pytest.raises(RevisionConflict) as conflict:
        store.update(LOCAL, expected_revision=1)
    assert conflict.value.current == 2
    assert SpeechSettingsStore(path, LOCAL).load() == {"settings": cloud, "revision": 2}
    assert list(tmp_path.glob("settings.json.*")) == []


@pytest.mark.parametrize("value", [None, [], {}, {"provider": "openai"},
    {**LOCAL, "other": True}, {"provider": "azure", "local": LOCAL["local"]},
    {"provider": "local", "local": {"model": "other", "device": "cpu"}},
    {"provider": "local", "local": {"model": "tiny.en", "device": "gpu"}}])
def test_settings_schema_is_strict(value):
    with pytest.raises(ValueError, match="speech_settings_invalid"):
        validate_settings(value)


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"x" * 4097,
    b'{"settings": {}, "revision": -1}'])
def test_unreadable_settings_fail_closed(tmp_path, raw):
    path = tmp_path / "settings.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="speech_settings_unreadable"):
        SpeechSettingsStore(path, LOCAL).load()
    store = SpeechSettingsStore(path, LOCAL)
    with pytest.raises(RevisionConflict):
        store.update(LOCAL, expected_revision=0)
    assert store.update(LOCAL)["revision"] == 1


@pytest.mark.parametrize("model", ["tiny.en", "large-v3"])
@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_legacy_discovery_and_default_follow_declared_variant(tmp_path, model, device):
    install_pin(tmp_path, model=model, device=device, legacy=True)
    router = SpeechRouter(tmp_path)
    assert router.store.load()["settings"]["local"] == {"model": model, "device": device}
    assert router.resolve() is router.legacy
    options = router.options()
    assert len(options) == 5
    assert len([option for option in options if option["status"] == "ready"]) == 1
    assert router.status()["placement"] == device.upper()


def test_per_variant_pin_wins_even_when_invalid(tmp_path):
    install_pin(tmp_path, legacy=True)
    pin = install_pin(tmp_path)
    router = SpeechRouter(tmp_path)
    assert router.resolve() is router.locals[("tiny.en", "cpu")]
    pin.write_text("not json")
    option = router.options()[0]
    assert option["status"] == "invalid"
    assert option["reason"] == "runtime_pin_invalid"
    assert router.status()["status"] == "unavailable"


def test_discovery_verifies_hashes_and_detects_mislabeled_variants(tmp_path):
    pin = install_pin(tmp_path)
    router = SpeechRouter(tmp_path)
    assert router.options()[0]["status"] == "ready"
    (tmp_path / "ggml-tiny.en.bin").write_bytes(b"tampered")
    assert router.options()[0]["reason"] == "runtime_model_changed"
    install_pin(tmp_path)
    wrong = tmp_path / ".f1-engineer-whisper-pin.large-v3.cuda.json"
    wrong.write_bytes(pin.read_bytes())
    assert router.options()[3]["reason"] == "runtime_variant_mismatch"
    router.store.update({"provider": "local", "local": {"model": "large-v3", "device": "cuda"}})
    with pytest.raises(LocalSpeechUnavailable, match="runtime_variant_mismatch"):
        router.resolve()


def test_cli_seed_is_ignored_after_first_saved_selection(tmp_path):
    first = SpeechRouter(tmp_path, provider="openai")
    assert first.store.load()["settings"]["provider"] == "openai"
    first.store.update(LOCAL)
    assert SpeechRouter(tmp_path, provider="openai").store.load()["settings"] == LOCAL


def test_switch_while_local_clip_is_running_keeps_original_runtime(tmp_path, monkeypatch):
    router = SpeechRouter(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = router.resolve()

    async def local_transcribe(wav, **kwargs):
        entered.set()
        await release.wait()
        return "original local clip"

    monkeypatch.setattr(original, "transcribe", local_transcribe)

    async def scenario():
        task = asyncio.create_task(router.transcribe(b"wav"))
        await entered.wait()
        router.store.update({**LOCAL, "provider": "openai"})
        assert router.resolve() is router.cloud
        release.set()
        assert await task == "original local clip"
        await router.close()

    asyncio.run(scenario())


def test_switch_away_stops_cloud_keepalive_but_drains_inflight_clip(tmp_path, monkeypatch):
    router = SpeechRouter(tmp_path, provider="openai")
    asyncio.run(OpenAIKeyStore(router.cloud.key_file).save(KEY, verify=False))
    router.cloud._transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": []}))
    entered, release = asyncio.Event(), asyncio.Event()

    async def cloud_transcribe(wav, **kwargs):
        client = router.cloud._client
        entered.set()
        await release.wait()
        assert client is not None and not client.is_closed
        return "original cloud clip"

    monkeypatch.setattr(router.cloud, "transcribe", cloud_transcribe)

    async def scenario():
        await router.start()
        task = asyncio.create_task(router.transcribe(b"wav"))
        await entered.wait()
        router.store.update(LOCAL)
        await router.sync_selection()
        assert router.cloud._keepalive is None
        assert router.cloud._client is not None
        router.store.update({**LOCAL, "provider": "openai"})
        await router.sync_selection()
        assert router.cloud._keepalive is not None
        router.store.update(LOCAL)
        await router.sync_selection()
        release.set()
        assert await task == "original cloud clip"
        assert router.cloud._client is None
        await router.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("key", ["", "sk-short", "other-" + "x" * 32, "sk-" + "x" * 510,
    "sk-" + "x" * 20 + "\n", "sk-" + "x" * 20 + " "])
def test_key_format_rejected_without_writing(tmp_path, key):
    store = OpenAIKeyStore(tmp_path / "key")
    assert not valid_openai_key(key)
    with pytest.raises(ValueError, match="openai_key_invalid"):
        asyncio.run(store.save(key, verify=False))
    assert not store.path.exists()


@pytest.mark.parametrize("status,verified", [(200, "ok"), (401, "auth_failed"), (403, "auth_failed"), (500, "unreachable")])
def test_key_store_verification_only_returns_safe_metadata(tmp_path, status, verified):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json={"error": KEY})

    store = OpenAIKeyStore(tmp_path / "key", transport=httpx.MockTransport(handler))
    result = asyncio.run(store.save(KEY, verify=True))
    assert result["configured"] and result["last4"] == KEY[-4:]
    assert result["verified"] == verified and result["updated_at_utc"]
    assert KEY not in json.dumps(result)
    assert str(seen[0].url) == "https://api.openai.com/v1/models"
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert store.path.read_text().strip() == KEY
    if os.name == "posix":
        assert store.path.stat().st_mode & 0o777 == 0o600
    result = asyncio.run(store.save(None, verify=True))
    assert result["verified"] == verified
    asyncio.run(store.remove())
    assert store.status() == {"configured": False, "last4": None, "updated_at_utc": None, "verified": None}
    assert not store.metadata_path.exists()


def test_key_store_network_failure_and_external_rotation(tmp_path):
    def handler(request):
        raise httpx.ConnectError("unreachable")

    store = OpenAIKeyStore(tmp_path / "key", transport=httpx.MockTransport(handler))
    assert asyncio.run(store.save(KEY, verify=True))["verified"] == "unreachable"
    store.path.write_text(KEY + "new")
    assert store.status()["verified"] is None


def test_speech_endpoints_authorization_validation_etags_and_conflict(tmp_path):
    install_pin(tmp_path)
    app = make_app(tmp_path)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = await client.get("/api/v1/engineer/speech")
            assert first.status_code == 200 and first.headers["etag"] == '"0"'
            assert first.json()["data"]["selected_status"]["status"] == "ready"
            assert (await client.put("/api/v1/engineer/speech", json=LOCAL)).status_code == 403
            assert (await client.put("/api/v1/engineer/speech", json={}, headers=HEADERS)).status_code == 422
            assert (await client.put("/api/v1/engineer/speech", json=LOCAL,
                headers={**HEADERS, "if-match": "wrong"})).status_code == 422
            saved = await client.put("/api/v1/engineer/speech", json=LOCAL, headers={**HEADERS, "if-match": '"0"'})
            assert saved.status_code == 200 and saved.headers["etag"] == '"1"'
            conflict = await client.put("/api/v1/engineer/speech", json=LOCAL, headers={**HEADERS, "if-match": '"0"'})
            assert conflict.status_code == 409 and conflict.json()["data"] == {"revision": 1}
            for settings in ({**LOCAL, "provider": "openai"},
                {"provider": "local", "local": {"model": "large-v3", "device": "cuda"}}):
                assert (await client.put("/api/v1/engineer/speech", json=settings, headers=HEADERS)).status_code == 422
            assert (await client.put("/api/v1/engineer/speech", content=b"x" * 2049, headers=HEADERS)).status_code == 413
            assert (await client.put("/api/v1/engineer/speech", content=b"{", headers=HEADERS)).status_code == 422
    asyncio.run(scenario())


def test_secret_endpoints_never_return_key_and_switch_without_restart(tmp_path):
    install_pin(tmp_path)
    app = make_app(tmp_path)
    app.state.speech_router.cloud._transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    app.state.openai_key_store.transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            path = "/api/v1/secrets/openai-api-key"
            assert (await client.get(path)).json()["data"]["configured"] is False
            for method in ("PUT", "DELETE"):
                assert (await client.request(method, path, json={"key": KEY, "verify": False})).status_code == 403
            for document in ({"key": "bad", "verify": False}, {"key": KEY, "verify": "yes"},
                             {"key": KEY, "verify": False, "other": True}, {"verify": False}):
                response = await client.put(path, json=document, headers=HEADERS)
                assert response.status_code == 422 and KEY not in response.text
            assert (await client.put(path, content=b"x" * 2049, headers=HEADERS)).status_code == 413
            saved = await client.put(path, json={"key": KEY, "verify": True}, headers=HEADERS)
            assert saved.status_code == 200 and KEY not in saved.text
            assert saved.json()["data"]["verified"] == "ok"
            status = await client.get(path)
            assert status.headers["cache-control"] == "no-store" and KEY not in status.text
            tested = await client.put(path, json={"verify": True}, headers=HEADERS)
            assert tested.json()["data"]["verified"] == "ok"
            cloud = await client.put("/api/v1/engineer/speech", json={**LOCAL, "provider": "openai"}, headers=HEADERS)
            assert cloud.status_code == 200
            assert cloud.json()["data"]["settings"]["local"] == LOCAL["local"]
            assert (await client.get("/api/v1/engineer/transcribe")).json()["data"]["runtime_id"] == "openai"
            assert app.state.speech_router.cloud._keepalive is not None
            removed = await client.delete(path, headers=HEADERS)
            assert not removed.json()["data"]["configured"] and KEY not in removed.text
            assert app.state.speech_router.cloud._keepalive is None
            assert (await client.get("/api/v1/engineer/transcribe")).json()["data"]["status"] == "not_configured"
        await app.state.speech_router.close()
    asyncio.run(scenario())


def test_cloud_transcription_does_not_take_process_ai_gate(tmp_path, monkeypatch):
    from f1_engineer.api import app as app_module

    app = make_app(tmp_path, speech_provider="openai")
    router = app.state.speech_router
    asyncio.run(app.state.openai_key_store.save(KEY, verify=False))
    router.cloud._transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))

    async def transcribe(wav, **kwargs):
        assert kwargs.get("gate_lease") is None
        return LocalTranscription("Copy", hashlib.sha256(wav).hexdigest(), "openai", "v1",
                                  "gpt-transcribe", "not_applicable", "cloud")

    monkeypatch.setattr(router.cloud, "transcribe", transcribe)
    lease = app_module.PROCESS_ENGINEER_AI_GATE.try_acquire()
    assert lease is not None

    async def scenario():
        wav = canonical_wav(16_000)
        headers = {**HEADERS, "content-type": "audio/wav",
                   "x-request-id": "12345678-1234-4abc-8abc-123456789abc",
                   "x-audio-sha256": hashlib.sha256(wav).hexdigest(), "x-speech-hint": "Drivers: Leclerc."}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/engineer/transcribe", content=wav, headers=headers)
            assert response.status_code == 200 and response.json()["data"]["runtime"]["runtime_id"] == "openai"
            router.store.update(LOCAL)
            await router.sync_selection()
            assert (await client.post("/api/v1/engineer/transcribe", content=wav, headers=headers)).status_code == 409
        await router.close()
    try:
        asyncio.run(scenario())
    finally:
        lease.close()


def test_cloud_disconnect_cancels_without_a_gate_lease():
    from f1_engineer.api.app import _EngineerClientDisconnected, _run_until_client_disconnect

    class Disconnected:
        async def is_disconnected(self):
            return True

    async def scenario():
        cancelled = asyncio.Event()

        async def clip():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with pytest.raises(_EngineerClientDisconnected):
            await _run_until_client_disconnect(clip(), Disconnected(), gate_lease=None)
        await asyncio.wait_for(cancelled.wait(), timeout=1)
    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="POSIX file mode checks require POSIX")
def test_api_key_permissions_are_owner_only_even_when_replacing_an_open_file(tmp_path):
    path = tmp_path / "key"
    path.write_text(KEY)
    os.chmod(path, 0o644)
    store = OpenAIKeyStore(path)
    assert not store.status()["configured"]
    asyncio.run(store.save(KEY, verify=False))
    assert path.stat().st_mode & 0o777 == 0o600
