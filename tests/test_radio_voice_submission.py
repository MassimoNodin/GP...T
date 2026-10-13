import io
import json
from types import SimpleNamespace

import pytest

from f1_engineer import cli
from f1_engineer.radio import command
from f1_engineer.radio.listening import VoiceInput
from tests.test_radio_listening import Audio, Clock, REQUEST_ID, listening
from tests.test_radio_questions import live
from tests.test_standalone_radio import Client


WHEEL = {"status": "ready", "binding": {"button": 3}, "connection_instance": "wheel-connection-1"}


def prepared(question="my last lap", *, source="wheel"):
    controller, radio, events, value = live()
    async def transcribe(wav, request_id, cancelled):
        return {"status": "available", "transcript": question}
    voice, hardware, voice_events, clock = listening(transcribe=transcribe)
    voice._source = source
    voice._scope = radio.scope
    voice._binding = WHEEL["binding"].copy()
    voice._connection = WHEEL["connection_instance"]
    voice._worker = SimpleNamespace(is_alive=lambda: False)
    voice._run(REQUEST_ID, 0, voice._finish, voice._cancelled)
    voice.tick(scope=radio.scope, input_status=WHEEL)
    return controller, radio, events, value, voice, hardware, voice_events, clock


def test_completed_wheel_capture_submits_once_without_console_command(capsys):
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared()
    clock.now += 0.1
    assert command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert "103.500 seconds" in radio.pending[-1].text
    assert not hardware.active
    assert not voice.status()["draft_available"]
    assert not command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    submitted = json.loads(capsys.readouterr().out)
    assert submitted["event"] == "radio_voice_submitted" and submitted["capture_id"] == REQUEST_ID
    assert submitted["source"] == "wheel" and submitted["verified"] is False and submitted["submitted"] is True
    assert submitted["submission_wait_s"] == pytest.approx(0.1)
    assert len([event for event in events if event["event"] == "radio_question_answer"]) == 1


@pytest.mark.parametrize("question", ["tell me at the end of the lap my last lap time",
                                    "tell me if the car ahead starts closing",
                                    "coach my consistency for the next 3 laps"])
def test_supported_task_requests_execute_from_completed_voice_capture(question):
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared(question)
    assert command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert len(controller.tasks.active()) == 1
    assert "Task 1 active" in radio.pending[-1].text
    assert not command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert len(controller.tasks.active()) == 1


def test_console_initiated_capture_also_submits_without_a_wheel():
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared(source="console")
    assert command.submit_ready_question(controller, voice, now=10, input_status={"status": "unavailable"})
    assert "103.500 seconds" in radio.pending[-1].text


@pytest.mark.parametrize("change", ["disconnected", "binding", "connection", "missing_status",
                                   "unbound_binding", "unknown_connection"])
def test_changed_wheel_origin_cannot_submit_even_if_transcript_is_already_ready(change):
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared()
    status = {**WHEEL}
    if change == "disconnected":
        status["status"] = "unavailable"
    elif change == "binding":
        status["binding"] = {"button": 4}
    elif change == "connection":
        status["connection_instance"] = "wheel-connection-2"
    elif change == "unbound_binding":
        voice._binding = status["binding"] = None
    elif change == "unknown_connection":
        voice._connection = status["connection_instance"] = None
    else:
        status = None
    assert not command.submit_ready_question(controller, voice, now=10, input_status=status)
    assert not voice.status()["draft_available"]
    assert not any(event["event"] == "radio_question_answer" for event in events)
    assert not command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)


@pytest.mark.parametrize("change", ["session", "stale", "cancelled", "unknown_origin"])
def test_stale_cancelled_or_unbound_capture_cannot_execute(change):
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared()
    now = 10
    if change == "session":
        value["scope"]["epoch"] += 1
        controller.feed(value, now=10)
    elif change == "stale":
        now = 14
    elif change == "cancelled":
        voice.cancel()
    else:
        voice._source = "remote"
    assert not command.submit_ready_question(controller, voice, now=now, input_status=WHEEL)
    assert not voice.status()["draft_available"]
    assert not controller.tasks.active()


@pytest.mark.parametrize("state", ["starting", "listening", "stopping", "transcribing", "unavailable"])
def test_auto_submission_waits_for_idle_state(state):
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared()
    voice._state = state
    assert not command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert voice.status()["draft_available"]


def test_auto_submission_waits_for_worker_cleanup_before_using_draft():
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared()
    alive = [True]
    voice._worker = SimpleNamespace(is_alive=lambda: alive[0])
    assert not command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert voice.status()["draft_available"]
    alive[0] = False
    assert command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)


def test_failed_microphone_cleanup_prevents_automatic_submission():
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared()
    voice._cleanup_failed = True
    assert voice.blocks_playback
    assert not command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert not any(event["event"] == "radio_question_answer" for event in events)


def test_duplicate_transcript_cannot_restore_a_consumed_capture():
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared()
    assert command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    voice._put(REQUEST_ID, "radio_transcript", state="idle", transcript="cancel all tracking")
    voice.tick(scope=radio.scope, input_status=WHEEL)
    assert not voice.status()["draft_available"]
    assert not command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert len([event for event in voice_events if event["event"] == "radio_transcript"]) == 1


def test_duplicate_transcript_cannot_replace_an_unconsumed_request_with_a_task_cancellation():
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared("my position")
    controller.handle_request("tell me at the end of the lap my last lap time", now=10)
    voice._put(REQUEST_ID, "radio_transcript", state="idle", transcript="cancel all tracking")
    voice.tick(scope=radio.scope, input_status=WHEEL)
    assert command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert len(controller.tasks.active()) == 1
    assert "position 2" in radio.pending[-1].text


@pytest.mark.parametrize("transcript", ["", "   ", None, 3, "x" * 4097, chr(0xD800)],
                         ids=["empty", "whitespace", "missing", "non-text", "oversized", "surrogate"])
def test_invalid_transcripts_never_become_automatic_requests(transcript):
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared(transcript)
    assert not voice.status()["draft_available"]
    assert not command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert any(event["event"] == "radio_transcription_unavailable" for event in voice_events)


@pytest.mark.parametrize("question", ["quit", "status", "run powershell", "change the wheel button to three"])
def test_voice_submission_does_not_execute_arbitrary_console_or_configuration_controls(question):
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared(question)
    assert command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert not radio.muted and not controller.tasks.active()
    assert "not supported" in radio.pending[-1].text


def test_real_capture_worker_finishes_and_closes_mock_microphone_before_submission():
    controller, radio, events, value = live()
    clock = Clock()
    hardware = []
    async def transcribe(wav, request_id, cancelled):
        return {"status": "available", "transcript": "my position"}
    def factory(**settings):
        audio = Audio(clock, voice._finish, stop_after=1)
        hardware.append(audio)
        return audio
    voice = VoiceInput(transcribe=transcribe, audio_factory=factory, clock=clock)
    voice.request(source="wheel", scope=radio.scope, binding=WHEEL["binding"],
                  connection=WHEEL["connection_instance"])
    assert not command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    voice._worker.join(timeout=3)
    assert not voice._worker.is_alive()
    voice.tick(scope=radio.scope, input_status=WHEEL)
    assert not hardware[0].active and hardware[0].calls.count("close") >= 1
    assert command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert "position 2" in radio.pending[-1].text
    voice.close()


def test_each_new_deliberate_capture_can_submit_once_after_previous_capture_is_consumed():
    controller, radio, events, value = live()
    clock = Clock()
    async def transcribe(wav, request_id, cancelled):
        return {"status": "available", "transcript": "my position"}
    voice = VoiceInput(transcribe=transcribe, clock=clock,
                       audio_factory=lambda **settings: Audio(clock, voice._finish, stop_after=1))
    captures = []
    for index in range(2):
        voice.request(source="wheel", scope=radio.scope, binding=WHEEL["binding"],
                      connection=WHEEL["connection_instance"])
        voice._worker.join(timeout=3)
        assert not voice._worker.is_alive()
        captures.append(voice.status()["capture_id"])
        voice.tick(scope=radio.scope, input_status=WHEEL)
        assert command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
        assert not command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert captures[0] != captures[1]
    assert len([event for event in events if event["event"] == "radio_question_answer"]) == 2
    voice._put(captures[0], "radio_transcript", state="idle", transcript="cancel all tracking")
    voice.tick(scope=radio.scope, input_status=WHEEL)
    assert not voice.status()["draft_available"]
    voice.close()


@pytest.mark.parametrize("options,console,expected", [([], "status\nquit\n", 1),
    (["--manual-submit"], "status\nquit\n", 0),
    (["--manual-submit"], "submit\nquit\n", 1),
    (["--capture-test"], "status\nquit\n", 0)])
def test_radio_loop_automatic_manual_and_capture_test_modes(tmp_path, monkeypatch, capsys, options, console, expected):
    class Wheel:
        def __init__(self, *arguments, **settings):
            pass
        def status(self):
            return WHEEL
        def poll(self, *, now):
            return False
        def close(self):
            pass
    class Input(VoiceInput):
        def __init__(self, **settings):
            super().__init__(enabled=False)
            self.delivered = False
        def tick(self, *, scope, input_status=None):
            if not self.delivered:
                self.delivered = True
                self._capture_id = REQUEST_ID
                self._scope = scope
                self._source = "wheel"
                self._binding = WHEEL["binding"]
                self._connection = WHEEL["connection_instance"]
                self._worker = SimpleNamespace(is_alive=lambda: False, join=lambda **arguments: None)
                self._put(REQUEST_ID, "radio_transcript", state="idle", transcript="my position")
            super().tick(scope=scope, input_status=input_status)
    monkeypatch.setattr(command, "RadioClient", Client)
    monkeypatch.setattr(command, "WheelButtonMonitor", Wheel)
    monkeypatch.setattr(command, "VoiceInput", Input)
    monkeypatch.setattr(command.sys, "stdin", io.StringIO(console))
    arguments = ["radio", *options]
    if "--capture-test" not in options:
        arguments.append("--dry-run")
    assert command.run_radio(cli.build_parser().parse_args(arguments)) == 0
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len([event for event in output if event["event"] == "radio_question_answer"]) == expected
    ready = next(event for event in output if event["event"] == "radio_ready")
    assert ready["voice_submission"] == ("disabled" if "--capture-test" in options else
                                         "manual" if "--manual-submit" in options else "automatic")
