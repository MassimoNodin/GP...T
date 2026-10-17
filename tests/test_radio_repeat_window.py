import queue

import pytest

from f1_engineer.radio import command
from f1_engineer.radio.scheduler import RadioMessage
from tests.test_radio_questions import live


def test_repeat_survives_dispatch_deadline_and_unrelated_owner_cancellation():
    controller, radio, events, value = live()
    radio.submit(RadioMessage("original", radio.scope, "Original report.", "answer", 15,
                              dispatch_before=12), now=10)
    radio.tick(now=10)
    radio.playback.playing = False
    radio.tick(now=11)
    reference = radio.capture_repeat_reference()
    radio.cancel_owner("pace_sectors")
    assert radio.repeat_captured(reference, now=21)
    assert radio.pending[-1].text == "Original report."
    assert radio.pending[-1].dispatch_before is None
    radio.cancel()
    assert not radio.repeat_captured(reference, now=26)


@pytest.mark.parametrize("phrase", ["Stop speaking", "stop talking", "shut up", "enough"])
def test_typed_stop_speaking_bypasses_interpreter_without_muting(phrase):
    controller, radio, events, value = live()
    controller.handle_request("tell me at the end of each lap my position", now=10)
    radio.tick(now=10)
    class Client:
        async def interpret(self, *args):
            raise AssertionError("local controls must not call the interpreter")
    results = queue.Queue()
    command.start_typed_request(phrase, Client(), controller, {}, results, interpret=True)
    submission, voice_like = results.get(timeout=1)
    command.dispatch_submission(submission, controller, voice_like, now=10)
    assert radio.active is None and not radio.pending
    assert not radio.muted and controller.tasks.active()
