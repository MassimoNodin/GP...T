import json
from copy import deepcopy

import pytest

from f1_engineer.radio.questions import plan_question
from f1_engineer.radio.spoken_numbers import regional_reply
from f1_engineer.radio.tasks import parse_task_request
from tests.test_radio_focus_coach import baseline
from tests.test_radio_opponent_controller import connected
from tests.test_radio_questions import live
from tests.test_radio_tasks import finish_lap
from tests.test_radio_track_geometry import reviewed, setup_named


PAIRED = "keep track of braking points and minimum speed for Leclerc in comparison to me for the next three laps"
FOCUS = "help me improve my worst corner for three laps"


def pending_pair():
    manager, client, request, controller, radio, value = connected()
    controller.handle_request("cancel all tracking", now=10)
    controller.handle_request(PAIRED, now=10)
    assert not controller.tasks.active()
    assert controller.status()["pending_region_clarification"]["requested_laps"] == 3
    radio.cancel()
    return controller, radio, value


@pytest.mark.parametrize("word,count", [("one", 1), ("three", 3), ("twelve", 12), ("twenty", 20)])
def test_spoken_laps_and_report_durations(word, count):
    assert plan_question(f"my and leader lap {word} time").metrics == (f"lap_number:{count}",)
    action = parse_task_request(f"tell me at the end of lap my last lap for the next {word} laps")
    assert action.laps == count
    paired = parse_task_request(PAIRED.replace("three", word))
    assert paired.laps == count and paired.region_opponent == "leclerc"


@pytest.mark.parametrize("text", [
    "tell me if Leclerc starts closing for next three laps",
    "track my consistency for next three laps",
    "compare my braking and minimum speed from 10 to 50 metres with Leclerc for next three laps",
])
def test_spoken_duration_preserves_existing_task_intent(text):
    action = parse_task_request(text)
    assert action is not None and action.laps == 3


@pytest.mark.parametrize("text", ["my lap twenty one", "my lap 256", "my lap three in rain"])
def test_unknown_number_qualifiers_are_not_silently_dropped(text):
    with pytest.raises(ValueError):
        plan_question(text)


def test_nearby_report_confirms_and_answers_all_targets():
    controller, radio, events, value = live()
    controller.handle_request("tell me at the end of lap a comparison between my car and cars around me", now=10)
    task, = controller.tasks.active()
    assert task.plan.targets == ("self", "ahead", "behind")
    assert set(task.plan.metrics) == {"last_lap", "lap_comparison"}
    assert task.until_lap is None
    radio.cancel()
    finish_lap(controller, value)
    text = radio.pending[-1].text
    assert "player comparison reference" in text
    assert "ahead:" in text and "behind:" in text
    assert text.count("last lap comparison unavailable") == 2
    assert "101.000 seconds" in text and "102.000 seconds" in text


def test_nearby_aligned_comparisons_are_lap_differences_not_race_gaps():
    controller, radio, events, value = live()
    controller.handle_request("tell me at the end of lap compare my car with the cars around me", now=10)
    radio.cancel()
    for row in value["drivers"][1:]:
        row["lap"]["current_lap_number"] = 4
    finish_lap(controller, value)
    text = radio.pending[-1].text
    assert "ahead:" in text and "behind:" in text
    assert "unavailable" not in text
    assert "reported lap 3 2.000 seconds slower than you" in text
    assert "reported lap 3 3.000 seconds slower than you" in text
    assert "gap to" not in text


def test_paired_clarification_preserves_independent_original_laps_and_identity():
    controller, radio, value = pending_pair()
    pending = deepcopy(controller.status()["pending_region_clarification"])
    json.dumps(pending)
    assert "action" not in pending
    controller.handle_request("from 10 to 50 metres", now=10)
    task, = controller.tasks.active()
    assert task.first_lap == pending["player_first_lap"]
    assert task.opponent_first_lap == pending["opponent_first_lap"]
    assert task.until_lap == task.first_lap + 2
    assert json.loads(task.fixed_bindings[1]) == pending["opponent_identity"]
    assert controller.status()["pending_region_clarification"] is None


def test_focus_reply_retains_requested_horizon():
    controller, radio, events, value, clock, store, client, captures = baseline()
    controller.handle_request(FOCUS, now=10)
    assert controller.status()["pending_region_clarification"]["focus_coach"]
    controller.handle_request("10 to 50 meters", now=10)
    task, = controller.tasks.active()
    assert task.focus_coach and task.until_lap == task.first_lap + 2


def test_reviewed_spoken_turn_reply_resolves_only_catalog_geometry(reviewed):
    manager, client, controller, radio, value, catalog = setup_named(reviewed)
    controller.handle_request(PAIRED, now=10)
    controller.handle_request("Turn One", now=10)
    task, = controller.tasks.active()
    assert task.region_window.start_m == 10 and task.region_window.end_m == 50
    assert task.region_geometry_binding is not None


def test_old_captured_voice_reply_cannot_consume_newer_intent():
    controller, radio, value = pending_pair()
    old_context = controller.capture_task_context()
    controller.handle_request(PAIRED.replace("three", "two"), now=10)
    pending = deepcopy(controller.status()["pending_region_clarification"])
    controller.handle_request("10 to 50 metres", now=10, task_context=old_context)
    assert not controller.tasks.active()
    assert controller.status()["pending_region_clarification"] == pending
    assert "No matching current" in radio.pending[-1].text
    controller.handle_request("10 to 50 metres", now=10)
    task, = controller.tasks.active()
    assert task.until_lap == task.first_lap + 1


def test_reply_without_captured_intent_cannot_create_tracking():
    controller, radio, value = pending_pair()
    controller.handle_request("10 to 50 metres", now=10, task_context=None)
    assert not controller.tasks.active()
    assert controller.status()["pending_region_clarification"] is not None


@pytest.mark.parametrize("change", ["player_identity", "stale", "unavailable"])
def test_lost_player_binding_or_freshness_clears_pending(change):
    controller, radio, value = pending_pair()
    if change == "player_identity":
        value["drivers"][0]["identity"]["tenure_ordinal"] += 1
        controller.feed(value, now=10)
    elif change == "stale":
        controller.check_freshness(now=14)
    else:
        controller.feed({"status": "unavailable", "reason": "test_loss"}, now=10)
    assert controller.status()["pending_region_clarification"] is None
    controller.handle_request("10 to 50 metres", now=14 if change == "stale" else 10)
    assert not controller.tasks.active()


@pytest.mark.parametrize("driver", [0, 1])
def test_original_next_lap_cannot_shift_during_clarification(driver):
    controller, radio, value = pending_pair()
    value["drivers"][driver]["lap"]["current_lap_number"] += 1
    controller.feed(value, now=10)
    controller.handle_request("10 to 50 metres", now=10)
    assert not controller.tasks.active()
    assert controller.status()["pending_region_clarification"] is None


def test_opponent_replacement_cannot_be_substituted():
    controller, radio, value = pending_pair()
    row = value["drivers"][1]
    row["identity"]["tenure_ordinal"] += 1
    row["participant"]["identity"] = deepcopy(row["identity"])
    controller.feed(value, now=10)
    controller.handle_request("10 to 50 metres", now=10)
    assert not controller.tasks.active()
    assert controller.status()["pending_region_clarification"] is None


@pytest.mark.parametrize("command", ["cancel that", "cancel all tracking", "my last lap"])
def test_cancel_or_unrelated_request_cannot_reactivate_pending_intent(command):
    controller, radio, value = pending_pair()
    controller.handle_request(command, now=10)
    assert controller.status()["pending_region_clarification"] is None
    controller.handle_request("10 to 50 metres", now=10)
    assert not controller.tasks.active()


def test_forget_and_list_pending_clarification():
    controller, radio, value = pending_pair()
    controller.handle_request("what are you tracking", now=10)
    assert "clarification" in radio.pending[-1].text.lower()
    assert not controller.tasks.active()
    controller.forget_conversation()
    controller.handle_request("10 to 50 metres", now=10)
    assert not controller.tasks.active()


def test_pending_clarification_does_not_modify_an_existing_task():
    manager, client, request, controller, radio, value = connected()
    original = controller.tasks.views()
    controller.handle_request(PAIRED.replace("three", "two"), now=10)
    controller.handle_request("list tasks", now=10)
    assert controller.tasks.views() == original
    controller.forget_conversation()
    assert controller.tasks.views() == original
    assert controller.status()["pending_region_clarification"] is None


def test_partial_update_cannot_replace_existing_tracking():
    manager, client, request, controller, radio, value = connected()
    original = controller.tasks.views()
    controller.handle_request("update task 1 " + PAIRED, now=10)
    assert controller.tasks.views() == original
    assert controller.status()["pending_region_clarification"] is None
    controller.forget_conversation()
    controller.handle_request("10 to 50 metres", now=10)
    assert controller.tasks.views() == original


@pytest.mark.parametrize("change", ["scope", "length", "circuit"])
def test_changed_context_prevents_regional_installation(change):
    controller, radio, value = pending_pair()
    if change == "scope":
        value["scope"]["epoch"] += 1
    elif change == "length":
        value["context"]["value"]["track_length_m"] += 1
    else:
        value["context"]["value"]["track_id"] = 9
    controller.feed(value, now=10)
    controller.handle_request("10 to 50 metres", now=10)
    assert not controller.tasks.active()


@pytest.mark.parametrize("text", ["tell me my lap", "10 to 50 metres in rain", "turn twenty one", "x" * 129])
def test_unmatched_region_replies_do_not_guess(text):
    assert regional_reply(text) is None


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


@pytest.mark.parametrize("pause", ["stale", "unavailable"])
def test_focus_clarification_preserves_existing_tasks_but_requires_live_telemetry(pause):
    manager, client, request, controller, radio, value = connected()
    tasks = controller.tasks.active()
    assert tasks and controller.opponent_capture_requests()
    if pause == "stale":
        controller.check_freshness(now=14)
    else:
        controller.feed({"status": "unavailable", "reason": "no_telemetry"}, now=14)
    controller.handle_request(FOCUS, now=14)
    assert controller.tasks.active() == tasks and controller.opponent_capture_requests() == []
    assert controller.status()["pending_region_clarification"] is None
    assert "Fresh player telemetry" in controller.conversation.context()[-1]["response"]
    value["events"] = []
    controller.feed(value, now=15)
    assert controller.tasks.active() == tasks
    assert controller.opponent_capture_requests() == [request]


def test_cancelled_task_during_pause_is_not_revived_on_resume():
    manager, client, request, controller, radio, value = connected()
    controller.feed({"status": "unavailable", "reason": "replay_paused"}, now=10)
    controller.handle_request("cancel all tracking", now=10)
    assert not controller.tasks.active()
    controller.feed(value, now=11)
    assert not controller.tasks.active() and not controller.opponent_capture_requests()
