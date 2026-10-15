from __future__ import annotations

import asyncio
import hashlib
import io
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from f1_engineer import cli
from f1_engineer.analysis.local_speech import SPEECH_MAX_SAMPLES, validate_canonical_wav
from f1_engineer.radio import audio, command
from f1_engineer.radio.audio import NativeRadioAudio, RadioAudioUnavailable, cue_pcm
from f1_engineer.radio.client import RadioClient
from f1_engineer.radio.listening import VoiceInput, canonical_wav, capture_levels
from f1_engineer.radio.scheduler import RadioMessage, RadioScheduler


REQUEST_ID = "12345678-1234-4123-8123-123456789abc"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Signal:
    def __init__(self, clock):
        self.clock = clock
        self.value = False

    def set(self):
        self.value = True

    def is_set(self):
        return self.value

    def wait(self, timeout):
        self.clock.now += timeout
        return self.value


class Audio:
    def __init__(self, clock, finish, *, stop_after=3, fail=None, on_read=None):
        self.clock = clock
        self.finish = finish
        self.stop_after = stop_after
        self.fail = fail
        self.on_read = on_read
        self.calls = []
        self.active = False
        self.frames = 0

    def cue(self, kind):
        assert not self.active, "cues must never play into an active microphone"
        self.calls.append(kind)
        if self.fail == "cue":
            raise RadioAudioUnavailable("secret driver details")
        self.clock.now += 0.1

    def start(self):
        self.calls.append("open")
        if self.fail == "open":
            raise RadioAudioUnavailable("secret device details")
        self.active = True

    def read(self):
        assert self.active
        self.calls.append("read")
        self.frames += 1
        self.clock.now += 0.02
        if self.fail == "read":
            raise RadioAudioUnavailable("secret capture content")
        if self.on_read:
            self.on_read()
        if self.frames == self.stop_after:
            self.finish.set()
        return b"\x01\x00" * 320

    def pause(self):
        self.calls.append("pause")
        self.active = False

    def close(self):
        self.calls.append("close")
        self.active = False


def listening(*, transcribe=None, stop_after=3, fail=None, maximum=12):
    clock = Clock()
    finish, cancelled = Signal(clock), Signal(clock)
    hardware = Audio(clock, finish, stop_after=stop_after, fail=fail)
    events = []
    voice = VoiceInput(transcribe=transcribe, notify=events.append, clock=clock, maximum_seconds=maximum,
                       audio_factory=lambda **kwargs: hardware)
    voice._capture_id = REQUEST_ID
    voice._finish, voice._cancelled = finish, cancelled
    voice._scope = "session"
    return voice, hardware, events, clock


def run_capture(voice):
    voice._run(REQUEST_ID, 0, voice._finish, voice._cancelled)
    voice.tick(scope="session")


def test_wav_is_canonical_and_bounded():
    validate_canonical_wav(canonical_wav(b"\x00\x00"))
    validate_canonical_wav(canonical_wav(b"\x00\x00" * SPEECH_MAX_SAMPLES))


@pytest.mark.parametrize("pcm", [b"", b"x", b"x" * (SPEECH_MAX_SAMPLES * 2 + 2)],
                         ids=["empty", "odd", "oversized"])
def test_invalid_pcm_is_rejected(pcm):
    with pytest.raises(ValueError):
        canonical_wav(pcm)


@pytest.mark.parametrize("pcm", [b"", b"\x00\x00" * 320])
def test_capture_levels_identifies_silence_without_retaining_audio(pcm):
    assert capture_levels(pcm) == {"audio_rms_dbfs": None, "audio_peak": 0.0,
                                   "audio_clipped_fraction": 0.0}


def test_capture_levels_reports_signal_and_clipping():
    levels = capture_levels(b"\xff\x7f\x00\x80\x00\x00\x00\x00")
    assert levels["audio_peak"] == 1
    assert levels["audio_clipped_fraction"] == 0.5
    assert levels["audio_rms_dbfs"] == pytest.approx(-3.0104, abs=0.001)


def test_start_stop_cues_and_capture_metadata_without_upload_or_persistence():
    voice, hardware, events, clock = listening()
    run_capture(voice)
    assert hardware.calls[0:2] == ["start", "open"]
    assert hardware.calls.index("close") < hardware.calls.index("end")
    completed = next(event for event in events if event["event"] == "radio_capture_complete")
    assert completed["capture_audio_s"] == pytest.approx(0.06)
    assert completed["audio_peak"] == pytest.approx(1 / 32768)
    assert completed["audio_rms_dbfs"] < -80
    assert completed["audio_clipped_fraction"] == 0
    assert not completed["audio_persisted"] and not completed["automatic_stop"]
    assert voice.status()["state"] == "idle"
    assert not any("transcript" in event for event in events)


def test_ongoing_cue_pauses_microphone_and_excludes_tones_from_samples():
    voice, hardware, events, clock = listening(stop_after=110, maximum=5)
    run_capture(voice)
    pulse = hardware.calls.index("pulse")
    assert hardware.calls[pulse - 1:pulse + 2] == ["pause", "pulse", "open"]
    assert hardware.frames == 110
    assert next(event for event in events if event["event"] == "radio_capture_complete")["capture_audio_s"] == pytest.approx(2.2)


def test_duration_limit_automatically_stops_and_never_exceeds_backend_samples():
    voice, hardware, events, clock = listening(stop_after=None, maximum=1)
    run_capture(voice)
    completed = next(event for event in events if event["event"] == "radio_capture_complete")
    assert completed["automatic_stop"]
    assert completed["capture_audio_s"] <= 1.02
    assert not hardware.active


def test_transcript_is_an_unverified_draft_not_an_executed_command():
    uploads = []
    async def transcribe(wav, request_id, cancelled):
        validate_canonical_wav(wav)
        uploads.append((request_id, len(wav)))
        return {"status": "available", "transcript": "cancel all tracking"}
    voice, hardware, events, clock = listening(transcribe=transcribe)
    run_capture(voice)
    transcript = next(event for event in events if event["event"] == "radio_transcript")
    assert transcript["transcript"] == "cancel all tracking"
    assert transcript["verified"] is False and transcript["submitted"] is False
    assert transcript["end_to_transcript_s"] == pytest.approx(0.1)
    assert uploads == [(REQUEST_ID, 1964)]
    assert voice.status()["draft_available"]


@pytest.mark.parametrize("fail", ["cue", "open", "read"])
def test_audio_errors_fail_closed_without_upload_or_sensitive_diagnostics(fail):
    async def transcribe(*args):
        pytest.fail("failed capture must not upload")
    voice, hardware, events, clock = listening(transcribe=transcribe, fail=fail)
    run_capture(voice)
    assert not hardware.active
    assert not voice.status()["draft_available"]
    assert any(event["event"] == "radio_listen_unavailable" for event in events)
    assert "secret" not in json.dumps(events)


def test_cancelled_capture_closes_microphone_and_never_uploads():
    async def transcribe(*args):
        pytest.fail("cancelled capture must not upload")
    voice, hardware, events, clock = listening(transcribe=transcribe)
    hardware.on_read = voice.cancel
    run_capture(voice)
    assert not hardware.active
    assert hardware.calls.count("end") == 1
    assert not any(event["event"] == "radio_transcript" for event in events)


def test_end_cue_still_plays_if_capture_ends_during_starting():
    voice, hardware, events, clock = listening()
    original = hardware.cue
    def cue(kind):
        original(kind)
        if kind == "start":
            voice._finish.set()
    hardware.cue = cue
    run_capture(voice)
    assert "open" not in hardware.calls
    assert hardware.calls.count("end") == 1


def test_session_change_discards_already_completed_but_not_delivered_transcript():
    async def transcribe(*args):
        return {"status": "available", "transcript": "old session"}
    voice, hardware, events, clock = listening(transcribe=transcribe)
    voice._run(REQUEST_ID, 0, voice._finish, voice._cancelled)
    voice.tick(scope="other session")
    assert not any(event["event"] == "radio_transcript" for event in events)
    assert not voice.status()["draft_available"]


@pytest.mark.parametrize("changes", [
    {"status": "unavailable"}, {"binding": {"button": 3}}, {"connection_instance": 9},
], ids=["disconnected", "rebound", "reconnected-between-polls"])
def test_changed_wheel_binding_or_instance_discards_transcript(changes):
    async def transcribe(*args):
        return {"status": "available", "transcript": "old input"}
    voice, hardware, events, clock = listening(transcribe=transcribe)
    voice._source, voice._binding, voice._connection = "wheel", {"button": 32}, 7
    voice._run(REQUEST_ID, 0, voice._finish, voice._cancelled)
    status = {"status": "ready", "binding": {"button": 32}, "connection_instance": 7, **changes}
    voice.tick(scope="session", input_status=status)
    assert not any(event["event"] == "radio_transcript" for event in events)


def test_dry_run_never_initializes_audio():
    events = []
    voice = VoiceInput(enabled=False, notify=events.append, audio_factory=lambda **kwargs: pytest.fail("no audio"))
    voice.request(source="console", scope=None)
    assert voice.status()["state"] == "idle"
    assert events[-1]["reason"] == "dry_run"


def test_second_press_finishes_capture_but_transcribing_press_is_busy():
    voice, hardware, events, clock = listening()
    voice._worker = SimpleNamespace(is_alive=lambda: True)
    voice._state = "listening"
    voice.request(source="console", scope="session")
    assert voice._finish.is_set() and voice.status()["state"] == "stopping"
    voice._state = "transcribing"
    voice.request(source="console", scope="session")
    assert events[-1]["reason"] == "capture_busy"


def test_slow_cancellation_remains_busy_and_reports_pending_cleanup():
    voice, hardware, events, clock = listening()
    voice._worker = SimpleNamespace(is_alive=lambda: True)
    voice._state = "listening"
    voice.cancel()
    clock.now = 3
    voice.tick(scope="session")
    assert events[-1]["event"] == "radio_capture_stop_pending"
    assert voice.blocks_playback
    voice.request(source="console", scope="session")
    assert events[-1]["reason"] == "capture_busy"


@pytest.mark.parametrize("kind,size", [("start", 11520), ("end", 9600), ("pulse", 2400)])
def test_tonal_cues_are_short_bounded_pcm(kind, size):
    pcm = cue_pcm(kind, 0.08)
    assert len(pcm) == size
    assert pcm != b"\x00" * size


@pytest.mark.parametrize("volume", [0, -0.1, 0.31, float("nan"), float("inf")])
def test_invalid_cue_volume_is_rejected(volume):
    with pytest.raises(ValueError):
        cue_pcm("start", volume)


def client(tmp_path, responder):
    token = tmp_path / "token"
    token.write_text("a" * 32, encoding="utf-8")
    return RadioClient("http://127.0.0.1:8765", token, transport=httpx.MockTransport(responder))


def response_data(wav):
    return {"status": "ok", "data": {"schema_version": 1, "request_id": REQUEST_ID,
            "audio_sha256": hashlib.sha256(wav).hexdigest(), "transcript": "How was my lap?",
            "language": "en", "verified": False}}


def test_transcription_posts_only_authenticated_correlated_canonical_wav(tmp_path):
    wav = canonical_wav(b"\x00\x00" * 320)
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=response_data(wav))
    backend = client(tmp_path, respond)
    try:
        assert asyncio.run(backend.transcribe(wav, REQUEST_ID, threading.Event())) == {
            "status": "available", "transcript": "How was my lap?"}
        request = requests[0]
        assert request.url.path == "/api/v1/engineer/transcribe"
        assert request.headers["Authorization"] == "Bearer " + "a" * 32
        assert request.headers["X-Request-ID"] == REQUEST_ID
        assert request.headers["X-Audio-SHA256"] == hashlib.sha256(wav).hexdigest()
        assert request.content == wav
    finally:
        backend.close()


@pytest.mark.parametrize("change", [
    {"request_id": "other"}, {"audio_sha256": "b" * 64}, {"schema_version": True},
    {"verified": True}, {"language": "fr"}, {"transcript": ""}, {"transcript": "x" * 4097},
], ids=["wrong-request", "wrong-audio", "boolean-schema", "claimed-verification", "wrong-language", "empty", "long"])
def test_uncorrelated_or_invalid_transcripts_are_rejected(tmp_path, change):
    wav = canonical_wav(b"\x00\x00")
    document = response_data(wav)
    document["data"].update(change)
    backend = client(tmp_path, lambda request: httpx.Response(200, json=document))
    try:
        assert asyncio.run(backend.transcribe(wav, REQUEST_ID, threading.Event()))["status"] == "unavailable"
    finally:
        backend.close()


@pytest.mark.parametrize("code,body,reason", [
    (401, b"secret", "backend_authentication_failed"), (409, b"secret", "transcription_busy"),
    (302, b"secret", "transcription_unavailable"), (200, b"x" * 16385, "transcription_response_invalid_or_unavailable"),
    (200, b"[]", "transcription_response_invalid_or_unavailable"),
], ids=["auth", "busy", "redirect", "oversized", "invalid-shape"])
def test_transport_errors_are_bounded_without_body_leaks_or_redirects(tmp_path, code, body, reason):
    backend = client(tmp_path, lambda request: httpx.Response(code, content=body, headers={"Location": "http://example.com"}))
    try:
        result = asyncio.run(backend.transcribe(canonical_wav(b"\x00\x00"), REQUEST_ID, threading.Event()))
        assert result["reason"] == reason
        assert "secret" not in json.dumps(result)
    finally:
        backend.close()


def test_cancelled_transcription_aborts_inflight_http_request(tmp_path):
    cancelled = threading.Event()
    finished = threading.Event()
    async def respond(request):
        cancelled.set()
        try:
            await asyncio.sleep(60)
        finally:
            finished.set()
    backend = client(tmp_path, respond)
    try:
        result = asyncio.run(backend.transcribe(canonical_wav(b"\x00\x00"), REQUEST_ID, cancelled))
        assert result["reason"] == "transcription_cancelled"
        assert finished.is_set()
    finally:
        backend.close()


def test_capture_test_requires_no_backend_token_or_tts_and_quit_does_not_record(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(command, "RadioClient", lambda *args: pytest.fail("no backend"))
    monkeypatch.setattr(command, "WindowsSpeech", lambda **kwargs: pytest.fail("no TTS"))
    monkeypatch.setattr(command.sys, "stdin", io.StringIO("quit\n"))
    args = cli.build_parser().parse_args(["radio", "--capture-test", "--input-settings", str(tmp_path / "unbound")])
    assert command.run_radio(args) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert not any(event["event"] == "radio_capture_starting" for event in events)


def test_suspended_scheduler_drops_coaching_and_never_starts_speech():
    playback = SimpleNamespace(start=lambda text: pytest.fail("no playback during capture"))
    scheduler = RadioScheduler(playback)
    scheduler.set_scope(None)
    scheduler.suspended = True
    assert not scheduler.submit(RadioMessage("lap", None, "Lap", "proactive", 10), now=0)
    assert scheduler.submit(RadioMessage("answer", None, "Answer", "answer", 10), now=0)
    scheduler.tick(now=1)
    assert scheduler.active is None


def test_native_audio_adapter_uses_explicit_pcm_devices_and_closes_capture(monkeypatch):
    calls = []
    class Stream:
        def __init__(self, **settings):
            calls.append(settings)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append("output-close")

        def start(self):
            calls.append("start")

        def read(self, frames):
            return b"\x00\x00" * frames, False

        def write(self, pcm):
            return False

        def abort(self):
            calls.append("abort")

        def close(self):
            calls.append("input-close")
    sdk = SimpleNamespace(PortAudioError=RuntimeError, RawInputStream=Stream, RawOutputStream=Stream)
    monkeypatch.setattr(audio, "_sounddevice", lambda: sdk)
    hardware = NativeRadioAudio(microphone=1, output=3)
    assert not calls
    hardware.cue("start")
    hardware.start()
    assert len(hardware.read()) == 640
    hardware.pause()
    hardware.start()
    hardware.close()
    hardware.close()
    assert calls[0] == {"device": 3, "samplerate": 48000, "channels": 1, "dtype": "int16"}
    assert calls[2] == {"device": 1, "samplerate": 16000, "channels": 1, "dtype": "int16", "blocksize": 320}
    assert calls.count("input-close") == 1


def test_request_starts_worker_then_delivers_completion_without_blocking_main_loop():
    clock = Clock()
    events = []
    voice = VoiceInput(notify=events.append, clock=clock,
                       audio_factory=lambda **kwargs: Audio(clock, voice._finish))
    voice.request(source="console", scope=None)
    assert voice.status()["state"] == "starting"
    voice._worker.join(timeout=2)
    assert not voice._worker.is_alive()
    voice.tick(scope=None)
    assert voice.status()["state"] == "idle"
    assert any(event["event"] == "radio_capture_complete" for event in events)
    voice.close()


def test_list_audio_devices_does_not_open_input_or_output_streams(monkeypatch, capsys):
    sdk = SimpleNamespace(PortAudioError=RuntimeError, default=SimpleNamespace(device=(1, 2)),
                          query_devices=lambda: [{"name": "Microphone", "max_input_channels": 1,
                                                  "max_output_channels": 0, "hostapi": 0}])
    monkeypatch.setattr(audio, "_sounddevice", lambda: sdk)
    args = cli.build_parser().parse_args(["radio", "--list-audio-devices"])
    assert command.run_radio(args) == 0
    assert json.loads(capsys.readouterr().out)["devices"][0]["name"] == "Microphone"


@pytest.mark.parametrize("args", [["--capture-seconds", "0"], ["--capture-seconds", "13"],
    ["--cue-volume", "nan"], ["--microphone", "-1"], ["--cue-output", "-1"],
    ["--capture-test", "--once"]])
def test_invalid_capture_options_never_open_audio_or_backend(args, monkeypatch):
    monkeypatch.setattr(command, "RadioClient", lambda *args: pytest.fail("no backend"))
    parsed = cli.build_parser().parse_args(["radio", *args])
    with pytest.raises(ValueError):
        command.run_radio(parsed)


@pytest.mark.parametrize("state,started,requested,stop,now,reason", [
    ("starting", None, 0, None, 6, "audio_start_timeout"),
    ("listening", 0, 0, None, 14, "capture_deadline_exceeded"),
    ("stopping", 0, 0, 1, 4, "capture_stop_timeout"),
])
def test_native_audio_hangs_are_cancelled_without_blocking_or_starting_new_capture(state, started, requested, stop, now, reason):
    voice, hardware, events, clock = listening()
    voice._worker = SimpleNamespace(is_alive=lambda: True)
    voice._state = state
    voice._capture_started_at, voice._requested_at, voice._stop_at = started, requested, stop
    clock.now = now
    voice.tick(scope="session")
    assert events[-1]["reason"] == reason
    assert voice._cancelled.is_set() and voice.blocks_playback
    voice.request(source="console", scope="session")
    assert events[-1]["reason"] == "capture_busy"


def test_failed_native_cleanup_blocks_new_capture_and_playback_until_restart():
    voice, hardware, events, clock = listening()
    def cannot_close():
        raise RadioAudioUnavailable("device cleanup failed")
    hardware.close = cannot_close
    run_capture(voice)
    assert voice.blocks_playback
    voice.request(source="console", scope="session")
    assert events[-1]["reason"] == "audio_cleanup_failed_restart_required"
    assert "end" not in hardware.calls
