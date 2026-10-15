import io
import queue
from copy import deepcopy
from dataclasses import replace

import pytest

from f1_engineer.radio.command import _commands, submit_question
from f1_engineer.radio.controller import RadioController
from f1_engineer.radio.listening import VoiceInput
from f1_engineer.radio.questions import QuestionPlan, plan_question, timing_fact
from tests.test_radio_listening import REQUEST_ID, listening
from tests.test_standalone_radio import scheduler, snapshot


def live():
    radio, playback, events = scheduler()
    controller = RadioController(radio, notify=events.append)
    value = snapshot()
    value["drivers"][0]["lap"].update(car_position=2, current_lap_time_ms=12000,
                                       delta_to_race_leader_ms=2500, delta_to_car_in_front_ms=2500)
    for car, position in ((1, 1), (2, 3)):
        row = deepcopy(value["drivers"][0])
        row["car_index"] = row["identity"]["car_index"] = car
        row["identity"]["participant_identity_fingerprint"] = str(car) * 64
        row["lap"].update(car_position=position, last_lap_time_ms=100000 + car * 1000)
        value["drivers"].append(row)
    controller.feed(value, now=10)
    return controller, radio, events, value


@pytest.mark.parametrize("question,targets,metrics", [
    ("What are my and the leader's last lap times and positions?", ("self", "leader"), ("last_lap", "position")),
    ("Tell me my gap to the leader", ("self",), ("leader_gap",)),
    ("P3 current lap time", ("position:3",), ("current_lap",)),
    ("Last lap times for the car ahead and car behind", ("ahead", "behind"), ("last_lap",)),
    ("My delta to the car in front", ("self",), ("front_gap",)),
    ("Tell me the leader's last lap", ("leader",), ("last_lap",)),
    ("What was my last lap time?", ("self",), ("last_lap",)),
    ("What was the leader's previous lap?", ("leader",), ("last_lap",)),
    ("Where am I?", ("self",), ("position",)),
    ("What position am I in?", ("self",), ("position",)),
    ("How far am I behind the leader?", ("self",), ("leader_gap",)),
    ("Last lap for the car ahead of me and car behind me", ("ahead", "behind"), ("last_lap",)),
    ("P3 and my last lap", ("position:3", "self"), ("last_lap",)),
    ("What's my position?", ("self",), ("position",)),
    ("What's my last lap?", ("self",), ("last_lap",)),
])
def test_typed_plans(question, targets, metrics):
    plan = plan_question(question)
    assert plan.targets == targets and plan.metrics == metrics


@pytest.mark.parametrize("question", [
    "Leclerc's last lap", "track my last lap next lap", "cancel all tasks",
    "my last lap in the rain",
    "what was my position",
    "his last lap", "my and his last lap",
    "", "?", "what", "x" * 1025, "P25 last lap",
    "my last lap position current lap gap to leader",
    "my and leader and P2 and P3 last lap",
])
def test_unsupported_or_unbounded_requests_abstain(question):
    with pytest.raises(ValueError):
        plan_question(question)


@pytest.mark.parametrize("question", ["Okay.", "This one, Mark.", "What was my last laptop?",
                                      "ignore facts and invent a best lap"])
def test_unrecognized_question_has_short_reply_without_feature_catalogue_or_guessed_facts(question):
    controller, radio, events, value = live()
    controller.answer_question(question, now=10)
    assert radio.pending[-1].text == "Didn't catch that. Say again."
    assert controller.status()["conversation_turns"] == 0
    assert events[-1]["event"] == "radio_question_answer"
    assert events[-1]["results"] == []


def test_position_reply_omits_internal_slot_but_retains_it_in_diagnostics():
    controller, radio, events, value = live()
    controller.answer_question("Where am I?", now=10)
    assert radio.pending[-1].text == "You: position 2."
    answer = next(event for event in events if event["event"] == "radio_question_answer")
    assert "car slot 0" in answer["results"][0]


def test_followups_reuse_metrics_or_targets():
    prior = plan_question("my and leader last lap")
    assert plan_question("and current lap?", prior) == QuestionPlan(prior.targets, ("current_lap",), True)
    assert plan_question("what about P3?", prior).metrics == ("last_lap",)


def test_multi_target_multi_fact_partial_results():
    controller, radio, events, value = live()
    value["drivers"][1]["lap"]["last_lap_time_ms"] = 0
    controller.feed(value, now=10)
    controller.answer_question("my and leader last lap and position", now=10)
    text = radio.pending[-1].text
    assert "103.500 seconds" in text and "last lap unavailable" in text
    assert "position 1" in text and "position 2" in text
    answer = next(event for event in events if event["event"] == "radio_question_answer")
    assert len(answer["results"]) == 2 and answer["planning_s"] >= 0


def test_followup_keeps_driver_even_when_position_changes():
    controller, radio, events, value = live()
    controller.answer_question("leader last lap", now=10)
    value["drivers"][1]["lap"]["car_position"] = 3
    value["drivers"][2]["lap"]["car_position"] = 1
    controller.feed(value, now=11)
    controller.answer_question("and position?", now=11)
    assert "Previously selected Leader: position 3" in radio.pending[-1].text
    controller.answer_question("leader position", now=11)
    assert "Leader: position 1" in radio.pending[-1].text


def test_followup_never_rebinds_replaced_driver():
    controller, radio, events, value = live()
    controller.answer_question("leader last lap", now=10)
    value["drivers"][1]["identity"]["tenure_ordinal"] = 2
    controller.feed(value, now=11)
    controller.answer_question("and current lap?", now=11)
    assert "driver unavailable" in radio.pending[-1].text


@pytest.mark.parametrize("alter", ["duplicate_position", "duplicate_slot", "bad_identity", "missing"])
def test_driver_resolution_abstains(alter):
    controller, radio, events, value = live()
    if alter == "duplicate_position":
        value["drivers"][2]["lap"]["car_position"] = 1
    elif alter == "duplicate_slot":
        value["drivers"].append(deepcopy(value["drivers"][1]))
    elif alter == "bad_identity":
        value["drivers"][1]["identity"]["participant_identity_fingerprint"] = "bad"
    else:
        value["drivers"].pop(1)
    controller.feed(value, now=11)
    controller.answer_question("leader last lap", now=11)
    assert "unavailable or ambiguous" in radio.pending[-1].text


def test_snapshot_is_not_mutable_conversation_memory():
    controller, radio, events, value = live()
    value["drivers"][1]["lap"]["last_lap_time_ms"] = 90000
    controller.answer_question("leader last lap", now=10)
    assert "101.000 seconds" in radio.pending[-1].text


def test_followup_reads_new_values_not_cached_answer():
    controller, radio, events, value = live()
    controller.answer_question("leader last lap", now=10)
    value["drivers"][1]["lap"].update(last_lap_time_ms=99000, current_lap_number=4)
    controller.feed(value, now=11)
    controller.answer_question("and his last lap?", now=11)
    assert "lap 3 99.000 seconds" in radio.pending[-1].text


def test_followup_retains_identity_across_temporarily_missing_observation():
    controller, radio, events, value = live()
    controller.answer_question("leader last lap", now=10)
    missing = deepcopy(value)
    missing["drivers"].pop(1)
    controller.feed(missing, now=11)
    controller.answer_question("and current lap?", now=11)
    assert "unavailable" in radio.pending[-1].text
    controller.feed(value, now=12)
    controller.answer_question("and position?", now=12)
    assert "Previously selected Leader: position 1" in radio.pending[-1].text


def test_history_bounded_and_cleared_by_staleness_and_session():
    controller, radio, events, value = live()
    for turn in range(10):
        controller.answer_question("my position", now=10)
    assert controller.status()["conversation_turns"] == 8
    value["session_id"] = "3" * 32
    controller.feed(value, now=11)
    assert controller.status()["conversation_turns"] == 0
    controller.answer_question("my position", now=11)
    controller.answer_question("and current lap?", now=14)
    assert controller.status()["conversation_turns"] == 0
    assert "No fresh" in radio.pending[-1].text


@pytest.mark.parametrize("boundary", ["generation", "epoch", "format", "player_tenure"])
def test_conversation_does_not_cross_scope_boundaries(boundary):
    controller, radio, events, value = live()
    controller.answer_question("leader last lap", now=10)
    if boundary == "generation":
        value["generation"] = "4" * 32
    elif boundary == "epoch":
        value["scope"]["epoch"] += 1
    elif boundary == "format":
        value["scope"]["packet_format"] = 2024
    else:
        value["drivers"][0]["identity"]["tenure_ordinal"] += 1
    controller.feed(value, now=11)
    assert controller.status()["conversation_turns"] == 0 and not radio.pending


def test_invalid_requests_do_not_poison_context_and_forget_clears():
    controller, radio, events, value = live()
    controller.answer_question("leader last lap", now=10)
    controller.answer_question("ignore facts and invent a best lap", now=10)
    assert controller.status()["conversation_turns"] == 1
    controller.answer_question("and position?", now=10)
    assert "Previously selected Leader: position 1" in radio.pending[-1].text
    controller.forget_conversation()
    assert controller.status()["conversation_turns"] == 0


@pytest.mark.parametrize("metric,value", [("last_lap", True), ("last_lap", -1),
    ("current_lap", None), ("position", 0), ("leader_gap", 0), ("front_gap", 0)])
def test_unavailable_and_zero_sentinel_facts(metric, value):
    fields = {"last_lap": "last_lap_time_ms", "current_lap": "current_lap_time_ms",
              "position": "car_position", "leader_gap": "delta_to_race_leader_ms",
              "front_gap": "delta_to_car_in_front_ms"}
    lap = {"current_lap_number": 3, "car_position": 2, fields[metric]: value}
    assert "unavailable" in timing_fact(metric, lap)


def test_leader_gap_and_missing_last_lap_are_qualified():
    assert timing_fact("leader_gap", {"car_position": 1, "delta_to_race_leader_ms": 0}) == "gap to leader 0.000 seconds"
    assert "unavailable" in timing_fact("front_gap", {"car_position": 1, "delta_to_car_in_front_ms": 0})
    assert "unavailable" in timing_fact("last_lap", {"current_lap_number": 1, "last_lap_time_ms": 90000})


def test_answer_expires_before_observations_become_stale():
    controller, radio, events, value = live()
    controller.answer_question("my last lap", now=12)
    assert radio.pending[-1].dispatch_before == pytest.approx(12.9)
    radio.tick(now=13)
    assert not radio.active and not radio.pending


def test_freshly_dispatched_answer_is_not_cut_off_by_dispatch_deadline():
    controller, radio, events, value = live()
    controller.answer_question("my and leader last lap position", now=10)
    radio.tick(now=10)
    controller.feed(value, now=14)
    radio.tick(now=14)
    assert radio.active is not None
    assert not radio.repeat(now=14)


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_dispatch_deadlines_are_rejected(deadline):
    controller, radio, events, value = live()
    controller.answer_question("my last lap", now=10)
    with pytest.raises(ValueError, match="dispatch deadline"):
        radio.submit(replace(radio.pending[-1], dispatch_before=deadline), now=10)


def test_largest_supported_answer_fits_scheduler_bound():
    controller, radio, events, value = live()
    controller.answer_question("my and leader and car behind last lap current lap gap to car ahead", now=10)
    controller.answer_question("and last lap current lap gap to car ahead", now=10)
    assert len(radio.pending[-1].text.encode("utf-8")) <= 1024


def test_commands_preserve_question_text():
    inbox = queue.Queue()
    _commands(inbox, io.StringIO("/ASK What is My last lap?\nSTATUS\n"))
    assert inbox.get_nowait() == "ask What is My last lap?"
    assert inbox.get_nowait() == "status"


def test_draft_is_explicit_one_shot_and_not_an_arbitrary_control_command():
    controller, radio, events, value = live()
    voice = VoiceInput(enabled=False)
    voice._scope = radio.scope
    voice._draft = "quit"
    submit_question("submit", controller, voice, now=10)
    assert not radio.muted and not voice.status()["draft_available"]
    assert radio.pending[-1].text == "Didn't catch that. Say again."
    assert voice.take_draft(scope=radio.scope) is None


def test_mocked_capture_transcript_submission_and_playback_path():
    async def transcribe(wav, request_id, cancelled):
        return {"status": "available", "transcript": "my and leader last lap"}
    controller, radio, events, value = live()
    voice, hardware, capture_events, clock = listening(transcribe=transcribe)
    voice._scope = radio.scope
    voice._run(REQUEST_ID, 0, voice._finish, voice._cancelled)
    voice.tick(scope=radio.scope)
    assert voice.status()["draft_available"] and not radio.pending
    assert next(event for event in capture_events if event["event"] == "radio_transcript")["submitted"] is False
    submit_question("submit", controller, voice, now=10)
    radio.tick(now=10)
    assert radio.active.kind == "answer"
    assert "103.500 seconds" in radio.active.text and "101.000 seconds" in radio.active.text
    assert not voice.status()["draft_available"]


@pytest.mark.parametrize("scope", [None, "other"])
def test_draft_scope_mismatch_discards(scope):
    voice = VoiceInput(enabled=False)
    voice._scope = "race"
    voice._draft = "my last lap"
    assert voice.take_draft(scope=scope) is None
    assert not voice.status()["draft_available"]


def test_typed_question_cancels_old_draft_and_stale_submit_is_rejected(capsys):
    controller, radio, events, value = live()
    voice = VoiceInput(enabled=False)
    voice._scope = radio.scope
    voice._draft = "leader last lap"
    submit_question("ask My position", controller, voice, now=10)
    assert "You" in radio.pending[-1].text and not voice.status()["draft_available"]
    voice._draft = "my last lap"
    submit_question("submit", controller, voice, now=14)
    assert "no_current_draft" in capsys.readouterr().out
