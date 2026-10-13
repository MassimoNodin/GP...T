from dataclasses import replace
from types import SimpleNamespace

import pytest

from f1_engineer.radio import command
from f1_engineer.radio.scheduler import RadioMessage
from f1_engineer.radio.listening import VoiceInput
from tests.test_radio_listening import Audio, Clock
from tests.test_radio_voice_submission import WHEEL, prepared


@pytest.mark.parametrize("phrase", ["quiet", "quiet radio", "pause coaching", "  QUIET   RADIO  ", "Quiet radio."])
def test_quiet_voice_control_mutes_coaching_without_cancelling_tracking(phrase, capsys):
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared(phrase)
    controller.handle_request("tell me at the end of the lap my last lap time", now=10)
    assert command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert radio.muted
    assert len(controller.tasks.active()) == 1
    assert "Tracking continues" in radio.pending[-1].text
    assert '"action":"quiet"' in capsys.readouterr().out
    assert not command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)


@pytest.mark.parametrize("phrase", ["resume coaching", "resume radio"])
def test_resume_voice_control_unmutes_and_acknowledges(phrase):
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared(phrase)
    radio.quiet()
    assert command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert not radio.muted
    assert radio.pending[-1].text == "Proactive coaching resumed."


@pytest.mark.parametrize("phrase", ["cancel speech", "stop speaking"])
def test_cancel_speech_clears_speech_not_tasks_or_mute_state(phrase):
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared(phrase)
    controller.handle_request("tell me at the end of the lap my last lap time", now=10)
    radio.tick(now=10)
    radio.muted = True
    assert command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert radio.active is None and not radio.pending
    assert len(controller.tasks.active()) == 1 and radio.muted


@pytest.mark.parametrize("phrase", ["cancel", "cancel that", "stop", "resume", "repeat"])
def test_ambiguous_controls_clarify_without_changing_tasks_or_quiet_policy(phrase):
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared(phrase)
    controller.handle_request("tell me at the end of the lap my last lap time", now=10)
    assert command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert len(controller.tasks.active()) == 1 and not radio.muted
    assert "cancel task followed by its number" in radio.pending[-1].text


@pytest.mark.parametrize("phrase", ["repeat that", "repeat message", "Repeat that?"])
def test_repeat_uses_frozen_interrupted_message_not_newer_speech(phrase):
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared(phrase)
    original = RadioMessage("original", radio.scope, "Original report.", "proactive", 20)
    radio.submit(original, now=10)
    radio.tick(now=10)
    voice.repeat_reference = radio.capture_repeat_reference()
    radio.cancel()
    assert radio.last_completed is None
    radio.submit(replace(original, key="newer", text="Newer report."), now=10)
    radio.tick(now=10)
    assert command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert radio.pending[-1].text == original.text
    assert radio.pending[-1].kind == "answer"
    assert radio.last_completed is None


@pytest.mark.parametrize("invalidate", ["owner", "scope", "scope_round_trip", "expiry", "freshness", "missing"])
def test_repeat_abstains_when_frozen_reference_is_invalid(invalidate):
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared("repeat that")
    original = RadioMessage("original", radio.scope, "Old report.", "proactive", 20,
                            dispatch_before=12, owner="lap_reports")
    radio.last_completed = original
    voice.repeat_reference = radio.capture_repeat_reference()
    if invalidate == "owner":
        radio.cancel_owner("lap_reports")
    elif invalidate in ("scope", "scope_round_trip"):
        scope = radio.scope
        radio.set_scope("other")
        if invalidate == "scope_round_trip":
            radio.set_scope(scope)
    elif invalidate == "missing":
        voice.repeat_reference = None
    now = 21 if invalidate == "expiry" else 12 if invalidate == "freshness" else 10
    command.dispatch_voice_request("repeat that", controller, voice, now=now)
    assert not any(message.text == "Old report." for message in radio.pending)


def test_request_listening_freezes_active_message_before_cancelling_and_toggle_preserves_it():
    controller, radio, events, value, unused, hardware, voice_events, unused_clock = prepared()
    clock = Clock()
    voice = VoiceInput(clock=clock, audio_factory=lambda **settings: Audio(clock, voice._finish, stop_after=1))
    original = RadioMessage("original", radio.scope, "Original report.", "answer", 20)
    radio.submit(original, now=10)
    radio.tick(now=10)
    command.request_listening(radio, voice, source="wheel", binding=WHEEL["binding"],
                              connection=WHEEL["connection_instance"])
    voice._worker.join(timeout=3)
    assert not voice._worker.is_alive()
    frozen = voice.repeat_reference
    assert frozen.message.text == original.text
    assert radio.active is None and radio.last_completed is None
    voice._worker = SimpleNamespace(is_alive=lambda: True)
    voice._state = "listening"
    voice._finish.clear()
    command.request_listening(radio, voice, source="wheel", binding=WHEEL["binding"],
                              connection=WHEEL["connection_instance"])
    assert voice.repeat_reference is frozen
    voice._worker = None
    voice.close()


def test_manual_transcript_submission_uses_same_control_policy():
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared("quiet radio")
    command.submit_question("submit", controller, voice, now=10)
    assert radio.muted


@pytest.mark.parametrize("deadline,now", [(None, 20), (12, 12), (12, 13)])
def test_frozen_repeat_keeps_original_expiry_and_dispatch_freshness_gates(deadline, now):
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared()
    radio.last_completed = RadioMessage("old", radio.scope, "Old report.", "answer", 20,
                                        dispatch_before=deadline)
    reference = radio.capture_repeat_reference()
    assert not radio.repeat_captured(reference, now=now)
    assert not radio.pending


def test_frozen_repeat_survives_natural_completion_but_not_task_update():
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared()
    radio.last_completed = RadioMessage("old", radio.scope, "Old report.", "answer", 20,
                                        owner="lap_reports")
    reference = radio.capture_repeat_reference()
    assert radio.repeat_captured(reference, now=10)
    radio.cancel_owner("lap_reports")
    assert not radio.repeat_captured(reference, now=10)
    assert not radio.pending and radio.last_completed is None


def test_explicit_task_cancellation_still_uses_typed_task_dispatcher():
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared("cancel all tracking")
    controller.handle_request("tell me at the end of the lap my last lap time", now=10)
    assert command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert not controller.tasks.active()
