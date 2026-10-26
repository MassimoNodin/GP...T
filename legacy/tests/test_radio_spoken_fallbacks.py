from types import SimpleNamespace

import pytest

from f1_engineer.radio.command import dispatch_submission
from tests.test_radio_questions import live


@pytest.mark.parametrize("fresh", [True, False])
def test_interpreter_failure_says_again_instead_of_running_raw_request(fresh):
    controller, radio, events, value = live()
    if not fresh:
        controller.check_freshness(now=40)
    dispatch_submission({"question": "tell me at the end of each lap my position",
                         "interpretation": {"status": "unavailable", "reason": "intent_timeout"}},
                        controller, SimpleNamespace(), now=10 if fresh else 40)
    assert not controller.tasks.active()
    assert radio.pending[-1].text == "Say again."
    assert (radio.pending[-1].scope is not None) == fresh


@pytest.mark.parametrize("text", ["Copy.", "Say again.", "That strategy is unavailable."])
def test_control_replies_are_spoken_without_a_session(text):
    controller, radio, events, value = live()
    controller.check_freshness(now=40)
    controller.answer_radio_control(text, now=40)
    radio.tick(now=40)
    assert radio.active is not None
    assert radio.active.text == text and radio.active.scope is None


def test_report_capacity_message_identifies_running_tasks_and_outcome():
    controller, radio, events, value = live()
    for fact in ("position", "last lap", "gap to leader", "current lap"):
        controller.handle_request("tell me at the end of each lap my " + fact, now=10)
    assert len(controller.tasks.active()) == 3
    assert "tasks 1, 2, 3" in radio.pending[-1].text
    assert "Cancel one to add this" in radio.pending[-1].text
