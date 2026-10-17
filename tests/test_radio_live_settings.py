from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import httpx
import pytest

from f1_engineer.analysis.engineer_preferences import EngineerPreferences, PreferencesInvalid, PreferencesUnreadable, RevisionConflict
from f1_engineer.api.app import create_app
from f1_engineer.api.radio_routes import RadioPresence
from f1_engineer.radio.live_settings import LivePlayback, RadioLiveSettings
from f1_engineer.radio.listening import VoiceInput
from f1_engineer.radio.scheduler import RadioMessage, RadioScheduler
from f1_engineer.radio.settings import CachedDocument, DEFAULT_RADIO_SETTINGS, RadioSettings, RadioSettingsStore


class Playback:
    def __init__(self):
        self.playing = False
        self.cancelled = False
        self.closed = False

    def start(self, text):
        self.playing = True

    def busy(self):
        return self.playing

    def cancel(self):
        self.cancelled = True
        self.playing = False

    def close(self):
        self.closed = True


def block(revision="1:1", **changes):
    return {"revision": revision, "radio_revision": int(revision.split(":")[1]),
            "radio": {**DEFAULT_RADIO_SETTINGS, **changes},
            "assistance": {key: value for key, value in EngineerPreferences().to_dict().items()
                           if key in ("topics", "verbosity", "interruption", "depth")}}


def test_store_revision_conflicts_cache_and_repair(tmp_path):
    store = RadioSettingsStore(tmp_path / "radio.json")
    assert store.load().revision == 0
    settings = RadioSettings.from_dict(DEFAULT_RADIO_SETTINGS)
    assert store.update(settings, expected_revision=0).revision == 1
    assert store.update(settings, expected_revision=1).revision == 1
    with pytest.raises(RevisionConflict):
        store.update(settings, expected_revision=0)
    cache = CachedDocument(store)
    first = cache.load()
    assert cache.load() is first
    store.update(RadioSettings.from_dict({**DEFAULT_RADIO_SETTINGS, "rate": 2}))
    assert cache.load().revision == 2
    store.path.write_text("broken")
    with pytest.raises(PreferencesUnreadable):
        store.load()
    assert store.update(settings).revision == 1


@pytest.mark.parametrize("changes", [{"rate": True}, {"volume": 101}, {"microphone": -1},
    {"cue_volume": 0}, {"capture_seconds": 13}, {"voice": "x" * 257},
    {"answer_mode": "grounded", "intent_mode": "parser"}, {"other": 1}, {"tts_engine": "unknown"}])
def test_invalid_settings(changes):
    with pytest.raises(PreferencesInvalid):
        RadioSettings.from_dict({**DEFAULT_RADIO_SETTINGS, **changes})


def test_live_settings_leave_active_speech_and_cli_defaults_unchanged():
    old, replacement = Playback(), Playback()
    events = []
    playback = LivePlayback(old, lambda args: replacement, {}, events.append)
    scheduler = RadioScheduler(playback)
    args = SimpleNamespace(**{**DEFAULT_RADIO_SETTINGS, "rate": 7}, dry_run=True)
    voice = VoiceInput(enabled=False)
    controller = SimpleNamespace(proactive_mode="rules")
    live = RadioLiveSettings(args, scheduler, controller, voice, None, events.append)
    live.apply(block("1:0", rate=1))
    assert args.rate == 7
    old.start("active")
    live.apply(block("1:1", rate=2))
    assert args.rate == 2 and old.playing and not old.cancelled and not old.closed
    old.playing = False
    playback.start("next")
    assert old.closed and replacement.playing
    invalid = block("1:2", rate=100)
    live.apply(invalid)
    assert args.rate == 2 and live.revision == "1:1"
    assert events[-1]["event"] == "radio_settings_invalid"


def test_capture_configuration_is_deferred_until_next_request():
    voice = VoiceInput(enabled=False, microphone=3, maximum_seconds=5)
    voice._state = "listening"
    voice.configure(microphone=8, output=2, cue_volume=0.1, maximum_seconds=10, interpret=None)
    assert voice._audio_settings["microphone"] == 3
    assert voice._maximum_seconds == 5
    assert voice._state == "listening"


def test_capture_configuration_applies_only_after_previous_worker_finishes(monkeypatch):
    worker = SimpleNamespace(is_alive=lambda: True)
    voice = VoiceInput(enabled=True, microphone=3, maximum_seconds=5)
    voice._worker = worker
    voice._state = "listening"
    voice.configure(microphone=8, output=2, cue_volume=0.1, maximum_seconds=10, interpret=None)
    voice.request(source="console", scope=None)
    assert voice._audio_settings["microphone"] == 3
    worker.is_alive = lambda: False
    monkeypatch.setattr(threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    voice.request(source="console", scope=None)
    assert voice._audio_settings == {"microphone": 8, "output": 2, "cue_volume": 0.1}
    assert voice._maximum_seconds == 10


def test_proactive_mode_waits_for_idle_without_touching_tracking_tasks():
    playback = LivePlayback(Playback(), lambda args: Playback(), {}, lambda event: None)
    scheduler = RadioScheduler(playback)
    args = SimpleNamespace(**DEFAULT_RADIO_SETTINGS, dry_run=True)
    voice = VoiceInput(enabled=False)
    applied = []
    controller = SimpleNamespace(proactive_mode="rules", set_proactive_mode=applied.append)
    live = RadioLiveSettings(args, scheduler, controller, voice, None, lambda event: None)
    scheduler.active = RadioMessage("active", None, "answer", "answer", 60)
    live.apply(block("0:1", proactive_mode="off"))
    live.tick()
    assert applied == []
    scheduler.active = None
    voice._state = "listening"
    live.tick()
    assert applied == []
    voice._state = "idle"
    live.tick()
    assert applied == ["off"]


@pytest.mark.parametrize("policy,accepted", [("allow", True), ("queue", True), ("never", False)])
def test_interruption_collision(policy, accepted):
    scheduler = RadioScheduler(Playback())
    scheduler.interruption = policy
    scheduler.active = RadioMessage("active", None, "answer", "answer", 60)
    assert scheduler.submit(RadioMessage("next", None, "gap", "proactive", 60, owner="automatic_gaps"), now=0) is accepted
    scheduler.suspended = True
    result = scheduler.submit(RadioMessage("capture", None, "gap", "proactive", 60), now=0)
    assert result is (policy == "queue")


def test_topic_filter_never_filters_answers():
    scheduler = RadioScheduler(Playback())
    scheduler.proactive_topics = {"gap_information": False}
    assert not scheduler.submit(RadioMessage("gap", None, "gap", "proactive", 60, owner="gap_changes"), now=0)
    assert scheduler.submit(RadioMessage("answer", None, "gap", "answer", 60, owner="gap_changes"), now=0)


def presence_body():
    return {"instance_id": "test", "version": "test", "applied_revision": "1:1", "tts_engine": "edge",
            "voices": [], "devices": [], "wheel_status": "ready", "speaking": False, "listening": False}


def test_presence_freshness_validation_and_multiple_instances():
    now = [0]
    presence = RadioPresence(lambda: now[0])
    assert not presence.snapshot()["connected"]
    presence.update(presence_body())
    assert presence.snapshot()["connected"]
    now[0] = 6
    assert not presence.snapshot()["connected"]
    with pytest.raises(ValueError):
        presence.update({**presence_body(), "voices": ["voice"] * 65})
    presence.update({**presence_body(), "instance_id": "second"})
    assert presence.snapshot()["instance_id"] == "second"


def test_api_settings_live_delivery_and_presence(tmp_path):
    async def run():
        app = create_app(tmp_path / "telemetry.sqlite", recordings_root=tmp_path / "recordings",
                         automatic_acquisition=False, control_token="test")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            endpoint = "/api/v2/engineer/radio/settings"
            assert (await client.get(endpoint)).headers["etag"] == '"0"'
            assert (await client.put(endpoint, json=DEFAULT_RADIO_SETTINGS)).status_code == 403
            headers = {"Authorization": "Bearer test", "If-Match": '"0"'}
            saved = await client.put(endpoint, json=DEFAULT_RADIO_SETTINGS, headers=headers)
            assert saved.status_code == 200 and saved.json()["data"]["revision"] == 1
            assert (await client.put(endpoint, json=DEFAULT_RADIO_SETTINGS, headers=headers)).status_code == 409
            live = await client.get("/api/v2/engineer/live", headers=headers)
            assert live.json()["data"]["settings"]["revision"] == "0:1"
            endpoint = "/api/v2/engineer/radio/presence"
            assert not (await client.get(endpoint)).json()["data"]["connected"]
            assert (await client.put(endpoint, json=presence_body(), headers=headers)).status_code == 200
            assert (await client.get(endpoint)).json()["data"]["connected"]
            assert (await client.put(endpoint, json={"bad": True}, headers=headers)).status_code == 422
    asyncio.run(run())
