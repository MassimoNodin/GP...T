from copy import deepcopy

import pytest

from f1_engineer.radio.questions import QuestionPlan
from f1_engineer.radio.tasks import LapReportRegistry, parse_task_request
from tests.test_radio_gap_changes import boundary, gap_live
from tests.test_radio_tasks import finish_lap


def watch_live(question="tell me if Norris starts closing"):
    controller, radio, events, value = gap_live()
    value["drivers"][2]["lap"]["delta_to_car_in_front_ms"] = 2500
    controller.feed(value, now=10)
    controller.handle_request(question, now=10)
    radio.cancel()
    return controller, radio, events, value


def step(controller, radio, value, number, gap, *, behind=True):
    if behind:
        value["drivers"][2]["lap"]["delta_to_car_in_front_ms"] = gap
    boundary(controller, radio, value, number=number, cursor=number - 3, now=number + 7, gap=gap)


@pytest.mark.parametrize("question,target,laps", [
    ("Tell me if Norris starts closing", "norris", None),
    ("watch the car ahead", "the car ahead", None),
    ("watch Norris for next 4 laps", "norris", 4),
    ("tell me if Norris starts closing for the next 4 laps", "norris", 4),
    ("update task 2 watch Leclerc", "leclerc", None),
])
def test_closed_watch_intents(question, target, laps):
    action = parse_task_request(question)
    assert action.question == target + " catch or defend watch" and action.laps == laps
    assert action.kind == ("update" if question.startswith("update") else "create")


@pytest.mark.parametrize("question", ["watch Norris for next 21 laps", "tell me if Norris starts closing for next 99 laps"])
def test_invalid_watch_duration_refused(question):
    with pytest.raises(ValueError):
        parse_task_request(question)


@pytest.mark.parametrize("question", ["watch me", "watch Norris and Leclerc", "watch Norris position", "watch P2",
                                      "watch Unknown Driver", "watch Norris and quit"])
def test_invalid_watch_does_not_install(question):
    controller, radio, events, value = gap_live()
    controller.handle_request(question, now=10)
    assert not controller.tasks.active()


@pytest.mark.parametrize("behind,question,phrase", [
    (True, "tell me if Norris starts closing", "driver behind is closing"),
    (False, "watch Leclerc", "You are catching"),
])
def test_three_boundaries_support_catch_and_defend(behind, question, phrase):
    controller, radio, events, value = watch_live(question)
    step(controller, radio, value, 4, 3000, behind=behind)
    assert "collecting three" in radio.pending[-1].text
    step(controller, radio, value, 5, 2700, behind=behind)
    assert not radio.pending
    step(controller, radio, value, 6, 2400, behind=behind)
    text = radio.pending[-1].text
    assert phrase in text and "0.600 seconds over two observed laps" in text
    assert "now 2.400 seconds" in text
    controller.feed(value, now=13.1)
    assert len(radio.pending) == 1
    step(controller, radio, value, 7, 2000, behind=behind)
    assert not radio.pending


@pytest.mark.parametrize("gaps,phrase", [
    ((3000, 3200, 3500), "pulling away"),
    ((3000, 2900, 2800), "no consistent"),
    ((3000, 2500, 2700), "no consistent"),
    ((3000, 3000, 3000), "no consistent"),
])
def test_opening_noise_and_reversals_are_not_closing_claims(gaps, phrase):
    controller, radio, events, value = watch_live()
    for number, gap in enumerate(gaps, start=4):
        step(controller, radio, value, number, gap)
    assert phrase in radio.pending[-1].text
    assert "is closing" not in radio.pending[-1].text


def test_trend_transition_is_spoken_once():
    controller, radio, events, value = watch_live()
    for number, gap in enumerate((3000, 2700, 2400, 2600, 2900), start=4):
        step(controller, radio, value, number, gap)
    assert "You are pulling away" in radio.pending[-1].text
    notices = [event["text"] for event in events if event["event"] == "radio_watch_notice"]
    assert sum("is closing" in text for text in notices) == 1
    assert sum("You are pulling away" in text for text in notices) == 1


@pytest.mark.parametrize("field,broken", [
    ("pit_status_id", 1), ("pit_status_id", None), ("pit_status_id", False),
    ("result_status_id", 3), ("result_status_id", None),
    ("delta_to_car_in_front_ms", 0), ("delta_to_car_in_front_ms", True),
    ("delta_to_car_in_front_ms", 2**32), ("current_lap_number", 1),
    ("current_lap_number", None), ("current_lap_number", True),
])
def test_missing_or_ineligible_midlap_data_resets_evidence(field, broken):
    controller, radio, events, value = watch_live()
    step(controller, radio, value, 4, 3000)
    step(controller, radio, value, 5, 2700)
    original = deepcopy(value["drivers"][2]["lap"])
    value["drivers"][2]["lap"][field] = broken
    controller.feed(value, now=12.2)
    value["drivers"][2]["lap"] = original
    controller.feed(value, now=12.3)
    step(controller, radio, value, 6, 2400)
    assert "collecting three" in radio.pending[-1].text
    assert "is closing" not in radio.pending[-1].text


def test_role_reference_is_frozen_not_rebound_to_new_adjacent_driver():
    controller, radio, events, value = watch_live("watch car behind")
    step(controller, radio, value, 4, 3000)
    value["drivers"][2]["lap"]["car_position"] = 4
    row = deepcopy(value["drivers"][2])
    row["identity"]["car_index"] = row["car_index"] = 3
    row["identity"]["participant_identity_fingerprint"] = "4" * 64
    row["lap"]["car_position"] = 3
    row.pop("participant")
    value["drivers"].append(row)
    step(controller, radio, value, 5, 2700)
    assert "Lando Norris" in radio.pending[-1].text and "no longer adjacent" in radio.pending[-1].text
    step(controller, radio, value, 6, 2400)
    assert not radio.pending
    assert "identity fixed" in controller.status()["tasks"][0]["reference_policy"]


def test_replaced_driver_is_paused_not_rebound_by_name():
    controller, radio, events, value = watch_live()
    step(controller, radio, value, 4, 3000)
    value["drivers"][2]["identity"]["tenure_ordinal"] += 1
    step(controller, radio, value, 5, 2700)
    assert "fixed driver unavailable or replaced" in radio.pending[-1].text


def test_overtake_resets_the_side_and_warms_up_again():
    controller, radio, events, value = watch_live()
    step(controller, radio, value, 4, 3000)
    step(controller, radio, value, 5, 2700)
    value["drivers"][1]["lap"]["car_position"] = 3
    value["drivers"][2]["lap"]["car_position"] = 1
    step(controller, radio, value, 6, 2400, behind=False)
    assert "collecting three" in radio.pending[-1].text


def test_behind_driver_crosses_line_after_player_without_being_treated_as_lapped():
    controller, radio, events, value = watch_live()
    for number, gap in enumerate((3000, 2700, 2400), start=4):
        radio.cancel()
        for row in value["drivers"][1:]:
            row["lap"]["current_lap_number"] = number
        value["drivers"][2]["lap"].update(current_lap_number=number - 1, delta_to_car_in_front_ms=gap)
        finish_lap(controller, value, number=number, cursor=number - 3, now=number + 7)
    assert "driver behind is closing" in radio.pending[-1].text


def test_lapped_behind_driver_is_paused():
    controller, radio, events, value = watch_live()
    step(controller, radio, value, 4, 3000)
    value["drivers"][2]["lap"]["current_lap_number"] = 2
    controller.feed(value, now=11.2)
    assert not controller.watches.states[(1, 1)].history


def test_watch_and_normal_report_share_one_utterance():
    controller, radio, events, value = watch_live()
    controller.handle_request("report at end of each lap my position", now=10)
    step(controller, radio, value, 4, 3000)
    assert len(radio.pending) == 1
    assert "Task 1" in radio.pending[-1].text and "Task 2" in radio.pending[-1].text
    step(controller, radio, value, 5, 2700)
    assert len(radio.pending) == 1 and "position 2" in radio.pending[-1].text


def test_update_cancel_and_expiry_use_existing_task_controls():
    controller, radio, events, value = watch_live("watch Norris for next 3 laps")
    for number, gap in enumerate((3000, 2700, 2400), start=4):
        step(controller, radio, value, number, gap)
    assert controller.status()["tasks"][0]["state"] == "completed"
    radio.tick(now=13)
    controller.feed(value, now=13.1)
    assert radio.active is not None
    controller.handle_request("update task 1 watch Leclerc", now=13.1)
    assert radio.active is None and controller.status()["tasks"][0]["version"] == 2
    radio.cancel()
    controller.handle_request("cancel task 1", now=13.2)
    assert not controller.tasks.active() and not radio.repeat(now=13.2)
    assert controller.status()["watches"] == []


def test_quiet_consumes_notices_without_replay():
    controller, radio, events, value = watch_live()
    radio.quiet()
    for number, gap in enumerate((3000, 2700, 2400), start=4):
        step(controller, radio, value, number, gap)
    assert not radio.pending
    radio.muted = False
    step(controller, radio, value, 7, 2000)
    assert not radio.pending


@pytest.mark.parametrize("failure", ["scope", "stale", "history_gap", "rewind", "pause"])
def test_session_and_observation_failures_do_not_reuse_trend(failure):
    controller, radio, events, value = watch_live()
    step(controller, radio, value, 4, 3000)
    step(controller, radio, value, 5, 2700)
    if failure == "scope":
        value["generation"] = "3" * 32
    elif failure == "stale":
        value["age_s"] = 4
    elif failure == "history_gap":
        value["event_floor"] = value["event_cursor"] = 3
    elif failure == "rewind":
        value["drivers"][0]["lap"]["current_lap_number"] = 3
    controller.feed(value, now=16 if failure == "pause" else 12.2)
    assert all(not state.history for state in controller.watches.states.values())


def test_reference_change_cancels_active_and_repeatable_watch_notice():
    controller, radio, events, value = watch_live()
    for number, gap in enumerate((3000, 2700, 2400), start=4):
        step(controller, radio, value, number, gap)
    radio.tick(now=13)
    assert radio.active is not None
    value["drivers"][2]["lap"]["pit_status_id"] = 1
    controller.feed(value, now=13.1)
    assert radio.active is None and not radio.repeat(now=13.1)


@pytest.mark.parametrize("failure", ["ambiguous_positions", "missing_event", "discontinuity", "bad_time"])
def test_gaps_in_evidence_do_not_complete_a_trend(failure):
    controller, radio, events, value = watch_live()
    step(controller, radio, value, 4, 3000)
    step(controller, radio, value, 5, 2700)
    if failure == "ambiguous_positions":
        value["drivers"][1]["lap"]["car_position"] = 3
        controller.feed(value, now=12.2)
        value["drivers"][1]["lap"]["car_position"] = 1
    elif failure == "missing_event":
        for row in value["drivers"]:
            row["lap"]["current_lap_number"] = 6
        value["events"] = []
        controller.feed(value, now=12.2)
        step(controller, radio, value, 7, 2400)
        assert "collecting three" in radio.pending[-1].text
        return
    elif failure == "discontinuity":
        value["event_cursor"] = 3
        value["events"] = [{"id": 3, "scope": deepcopy(value["scope"]),
                            "driver": deepcopy(value["drivers"][2]["identity"]), "kind": "driver_discontinuity"}]
        controller.feed(value, now=12.2)
        value["drivers"][2]["lap"]["delta_to_car_in_front_ms"] = 2400
        boundary(controller, radio, value, number=6, cursor=4, now=13, gap=2400)
        assert "is closing" not in radio.pending[-1].text
        return
    elif failure == "bad_time":
        finish_lap(controller, value, cursor=3, number=6, now=12.2, event_elapsed=100000)
        assert all(not state.history for state in controller.watches.states.values())
        return
    step(controller, radio, value, 6, 2400)
    assert "is closing" not in radio.pending[-1].text


def test_watch_acknowledgement_and_status_confirm_the_driver_and_evidence():
    controller, radio, events, value = gap_live()
    controller.handle_request("watch car behind", now=10)
    assert "Watching Lando Norris" in radio.pending[-1].text
    step(controller, radio, value, 4, 3000)
    status = controller.status()["watches"][0]
    assert status["task_id"] == 1 and status["comparable_boundaries"] == 1 and status["role"] == "behind"
    controller.handle_request("what are you tracking", now=11)
    assert "fixed driver identity" in radio.pending[-1].text


def test_registry_rejects_unbound_watch_plans():
    registry = LapReportRegistry()
    with pytest.raises(ValueError, match="fixed identity"):
        registry.install(QuestionPlan(("behind",), ("race_watch",)), (None,), cursor=0, lap=3, laps=None)
    assert registry.views() == []
