import sys
import threading
import time
import types

import pytest

from f1_engineer.radio import command
from f1_engineer.radio.edge_speech import DEFAULT_VOICE, EdgeSpeech, EdgeSpeechUnavailable


class FakeMusic:
    def __init__(self):
        self.playing = False

    def load(self, path): pass
    def set_volume(self, value): pass
    def play(self): self.playing = True
    def stop(self): self.playing = False
    def unload(self): pass
    def get_busy(self): return self.playing


class FakeFallback:
    def __init__(self):
        self.spoken = []

    def start(self, text): self.spoken.append(text)
    def busy(self): return False
    def cancel(self): pass
    def close(self): pass


@pytest.fixture
def engines(monkeypatch):
    calls = []
    music = FakeMusic()

    class Communicate:
        def __init__(self, text, voice, rate):
            calls.append((text, voice, rate))

        async def save(self, path):
            if calls[-1][1] == "bad":
                raise OSError("offline")
            open(path, "wb").close()

    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=Communicate))
    mixer = types.SimpleNamespace(init=lambda: None, quit=lambda: None, music=music)
    monkeypatch.setitem(sys.modules, "pygame", types.SimpleNamespace(mixer=mixer, error=RuntimeError))
    return calls, music


def wait(condition):
    deadline = time.monotonic() + 3
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert condition()


def test_default_voice_rate_and_playback(engines):
    calls, music = engines
    speech = EdgeSpeech(rate=4)
    speech.start("Box this lap")
    wait(lambda: music.playing)
    assert calls == [("Box this lap", DEFAULT_VOICE, "+55%")]
    assert speech.busy()
    music.playing = False
    wait(lambda: not speech.busy())
    speech.close()


def test_cancel_stops_playback(engines):
    calls, music = engines
    speech = EdgeSpeech()
    speech.start("Hello")
    wait(lambda: music.playing)
    speech.cancel()
    assert not music.playing and not speech.busy()
    speech.close()


def test_synthesis_failure_uses_fallback(engines):
    fallback = FakeFallback()
    speech = EdgeSpeech(voice="bad", fallback=fallback)
    speech.start("Box now")
    wait(lambda: fallback.spoken == ["Box now"])
    speech.close()


def test_cancel_during_synthesis_does_not_start_fallback(engines, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class DelayedSynthesis:
        def __init__(self, *args, **kwargs):
            pass

        async def save(self, path):
            started.set()
            release.wait(timeout=3)
            finished.set()
            raise OSError("offline")

    monkeypatch.setattr(sys.modules["edge_tts"], "Communicate", DelayedSynthesis)
    fallback = FakeFallback()
    speech = EdgeSpeech(fallback=fallback)
    speech.start("Old answer")
    assert started.wait(timeout=3)
    speech.cancel()
    release.set()
    assert finished.wait(timeout=3)
    speech.close()
    assert not fallback.spoken
    assert not engines[1].playing


@pytest.mark.parametrize("engine", ["edge", "windows"])
def test_radio_selects_requested_engine(engines, monkeypatch, engine):
    fallback = FakeFallback()
    monkeypatch.setattr(command, "WindowsSpeech", lambda **kwargs: fallback)
    args = types.SimpleNamespace(tts_engine=engine, voice=None, rate=4, volume=75)
    speech = command._speech(args)
    try:
        assert isinstance(speech, EdgeSpeech) if engine == "edge" else speech is fallback
    finally:
        speech.close()


def test_radio_falls_back_when_neural_engine_is_unavailable(monkeypatch):
    fallback = FakeFallback()
    monkeypatch.setattr(command, "WindowsSpeech", lambda **kwargs: fallback)

    def unavailable(**kwargs):
        raise EdgeSpeechUnavailable("offline")

    monkeypatch.setattr(command, "EdgeSpeech", unavailable)
    args = types.SimpleNamespace(tts_engine="edge", voice=None, rate=4, volume=75)
    assert command._speech(args) is fallback
