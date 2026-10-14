import pytest

from f1_engineer.radio.tasks import parse_task_request
from tests.test_radio_opponent_controller import connected


@pytest.mark.parametrize("question,laps", [
    ("Help me improve my worst corner for three laps.", 3),
    ("coach my worst corner for the next 1 lap", 1),
    ("help me improve my worst corner for next twenty laps", 20),
    ("help me improve my worst corner", None),
])
def test_focus_intent_retains_requested_horizon_without_inventing_a_region(question, laps):
    action = parse_task_request(question)
    assert action.kind == "focus_corner" and action.laps == laps
    assert action.region_bounds_m is None and action.region_opponent is None


def test_unbounded_focus_horizon_is_rejected():
    with pytest.raises(ValueError, match="one to twenty"):
        parse_task_request("help me improve my worst corner for 21 laps")


def test_missing_corner_evidence_clarifies_and_preserves_existing_tasks_and_history():
    manager, client, request, controller, radio, value = connected()
    before = controller.tasks.views()
    question = "help me improve my worst corner for three laps"
    controller.handle_request(question, now=10)
    assert controller.tasks.views() == before
    turn = controller.conversation.context()[-1]
    assert turn["kind"] == "instruction" and turn["request"] == question and turn["tasks"] == []
    for phrase in ("verified corner geometry", "eligible reference", "explicit start and end", "requested 3 laps",
                   "numeric diagnostics", "No new tracking started"):
        assert phrase in turn["response"]
    controller.handle_request("what was my last request", now=10)
    assert question in radio.pending[-1].text


def test_focus_clarification_requires_live_telemetry_and_does_not_create_or_revive_tasks():
    manager, client, request, controller, radio, value = connected()
    controller.feed({"status": "unavailable", "reason": "no_telemetry"}, now=10)
    controller.handle_request("help me improve my worst corner for three laps", now=10)
    assert controller.tasks.views() == [] and controller.opponent_capture_requests() == []
    assert "Fresh player telemetry" in controller.conversation.context()[-1]["response"]
