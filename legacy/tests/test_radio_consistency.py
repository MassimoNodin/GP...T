from copy import deepcopy

import pytest

from f1_engineer.radio.questions import QuestionPlan
from f1_engineer.radio.tasks import LapReportRegistry, parse_task_request
from tests.test_radio_sector_pace import sector
from tests.test_radio_target_pace import finish, pace_live


REQUEST = "coach my consistency"


def ready(request=REQUEST):
    controller, radio, events, value = pace_live()
    finish(controller, radio, value, 4)
    controller.handle_request(request, now=11)
    radio.cancel()
    return controller, radio, events, value


def lap(controller, radio, value, first=30000, second=35000, elapsed=100000, *, invalid=0):
    number = value["drivers"][0]["lap"]["current_lap_number"]
    sector(controller, radio, value, 1, first, now=number + 7.1)
    sector(controller, radio, value, 2, second, now=number + 7.2)
    finish(controller, radio, value, number + 1, elapsed=elapsed, invalid=invalid,
           cursor=value["event_cursor"] + 1)


@pytest.mark.parametrize("verb", ["coach", "track", "monitor"])
def test_consistency_intents_and_duration(verb):
    action = parse_task_request(verb + " my consistency")
    assert action.question == "my consistency" and action.kind == "create" and action.laps is None
    action = parse_task_request("update task 2 " + verb + " my consistency for the next 4 laps")
    assert action.task_id == 2 and action.kind == "update" and action.laps == 4


def test_out_of_range_schedule_is_refused():
    with pytest.raises(ValueError):
        parse_task_request(REQUEST + " for next 21 laps")


@pytest.mark.parametrize("plan", [QuestionPlan(("leader",), ("consistency",)),
                                  QuestionPlan(("self", "leader"), ("consistency",)),
                                  QuestionPlan(("self",), ("consistency", "last_lap"))])
def test_registry_rejects_non_player_or_mixed_consistency(plan):
    registry = LapReportRegistry()
    with pytest.raises(ValueError, match="Consistency"):
        registry.install(plan, (None,) * len(plan.targets), cursor=1, lap=4, laps=None)
    assert not registry.active()


def test_acknowledgement_and_task_list_explain_the_rolling_window():
    controller, radio, events, value = pace_live()
    controller.handle_request(REQUEST, now=10)
    assert "three" in radio.pending[-1].text and "short tasks may expire" in radio.pending[-1].text
    assert "rolling three" in controller.tasks.active()[0].view()["reference_policy"]
    controller.handle_request("what are you tracking", now=10)
    assert "consistency" in radio.pending[-1].text


def test_warmup_then_measured_sector_variation_not_slowness():
    controller, radio, events, value = ready()
    lap(controller, radio, value, 30000, 35000, 100000)
    assert "1 of 3" in radio.pending[-1].text
    lap(controller, radio, value, 30100, 35200, 100300)
    assert "2 of 3" in radio.pending[-1].text
    lap(controller, radio, value, 30200, 35600, 100800)
    text = radio.pending[-1].text
    assert "valid laps 4, 5, 6" in text and "0.800 seconds for the lap" in text
    assert "0.200 for sector 1" in text and "0.600 for sector 2" in text
    assert "Sector 2 has more observed variation" in text and "not a pace deficit" in text
    reports = [event for event in events if event["event"] == "radio_consistency_report"]
    assert [sample["lap_number"] for sample in reports[-1]["samples"]] == [4, 5, 6]
    assert len(controller.pace.recent) == 3


@pytest.mark.parametrize("first,second,focus", [(30000, 35000, "equal observed variation"),
                                               (30400, 35100, "Sector 1 has more")])
def test_equal_or_first_sector_variation(first, second, focus):
    controller, radio, events, value = ready()
    lap(controller, radio, value)
    lap(controller, radio, value, first, second)
    lap(controller, radio, value)
    assert focus in radio.pending[-1].text


def test_rolling_samples_drop_old_lap_without_retargeting_other_tasks():
    controller, radio, events, value = ready()
    lap(controller, radio, value, 31000, 36000, 102000)
    lap(controller, radio, value)
    lap(controller, radio, value)
    lap(controller, radio, value)
    assert "valid laps 5, 6, 7" in radio.pending[-1].text
    assert "0.000 seconds for the lap" in radio.pending[-1].text
    assert "equal observed variation" in radio.pending[-1].text


@pytest.mark.parametrize("invalid", [1, None, False, "0"])
def test_invalid_completion_restarts_warmup(invalid):
    controller, radio, events, value = ready()
    lap(controller, radio, value)
    lap(controller, radio, value, invalid=invalid)
    assert "0 of 3" in radio.pending[-1].text
    lap(controller, radio, value)
    assert "1 of 3" in radio.pending[-1].text


@pytest.mark.parametrize("field,broken", [("current_lap_invalid_id", 1), ("current_lap_invalid_id", None),
                                          ("pit_status_id", 1), ("result_status_id", 3)])
def test_unhealthy_midlap_snapshot_clears_evidence_and_owned_speech(field, broken):
    controller, radio, events, value = ready()
    lap(controller, radio, value)
    radio.tick(now=12)
    original = deepcopy(value["drivers"][0]["lap"])
    value["drivers"][0]["lap"][field] = broken
    controller.feed(value, now=12.1)
    assert not controller.pace.recent and radio.active is None and not radio.repeat(now=12.1)
    value["drivers"][0]["lap"] = original
    controller.feed(value, now=12.2)
    lap(controller, radio, value)
    assert "0 of 3" in radio.pending[-1].text


def test_missing_sectors_breaks_the_consecutive_window_without_poisoning_lap_best():
    controller, radio, events, value = ready()
    lap(controller, radio, value)
    finish(controller, radio, value, 6, elapsed=99000, cursor=value["event_cursor"] + 1)
    assert "0 of 3" in radio.pending[-1].text
    assert controller.pace.best[16].elapsed_ms == 99000
    lap(controller, radio, value)
    assert "1 of 3" in radio.pending[-1].text


def test_compound_change_does_not_mix_windows():
    controller, radio, events, value = ready()
    lap(controller, radio, value)
    value["drivers"][0]["tyre"]["value"]["actual_compound"] = 18
    controller.feed(value, now=12.1)
    lap(controller, radio, value)
    assert "0 of 3" in radio.pending[-1].text
    lap(controller, radio, value)
    assert "1 of 3" in radio.pending[-1].text
    lap(controller, radio, value)
    lap(controller, radio, value)
    assert "on compound 18" in radio.pending[-1].text


def test_update_excludes_samples_from_before_the_new_request():
    controller, radio, events, value = ready()
    lap(controller, radio, value)
    lap(controller, radio, value)
    controller.handle_request("update task 1 " + REQUEST, now=13)
    lap(controller, radio, value)
    assert "1 of 3" in radio.pending[-1].text
    assert controller.tasks.active()[0].version == 2


def test_new_task_does_not_preload_laps_observed_before_the_request():
    controller, radio, events, value = pace_live()
    finish(controller, radio, value, 4)
    lap(controller, radio, value)
    lap(controller, radio, value)
    lap(controller, radio, value)
    controller.handle_request(REQUEST, now=14)
    lap(controller, radio, value)
    assert "1 of 3" in radio.pending[-1].text
    report = [event for event in events if event["event"] == "radio_consistency_report"][-1]
    assert [sample["lap_number"] for sample in report["samples"]] == [7]


def test_repeated_request_and_snapshot_do_not_duplicate_tasks_samples_or_reports():
    controller, radio, events, value = ready()
    controller.handle_request(REQUEST, now=11)
    assert len(controller.tasks.active()) == 1
    lap(controller, radio, value)
    count = len(events)
    controller.feed(value, now=12.1)
    assert len(controller.pace.recent) == 1 and len(events) == count
    assert len(radio.pending) == 1


def test_failed_update_preserves_active_task_and_evidence():
    controller, radio, events, value = ready()
    lap(controller, radio, value)
    original = controller.tasks.active()[0]
    controller.handle_request("update task 1 " + REQUEST + " for next 21 laps", now=12)
    assert controller.tasks.active()[0] == original
    lap(controller, radio, value)
    assert "2 of 3" in radio.pending[-1].text


def test_finite_quiet_task_collects_evidence_but_does_not_replay():
    controller, radio, events, value = ready(REQUEST + " for next 2 laps")
    radio.quiet()
    lap(controller, radio, value)
    lap(controller, radio, value)
    assert not radio.pending and controller.status()["tasks"][0]["state"] == "completed"
    assert len([event for event in events if event["event"] == "radio_consistency_report"]) == 2


def test_cancel_invalidates_active_consistency_speech():
    controller, radio, events, value = ready()
    lap(controller, radio, value)
    radio.tick(now=12)
    controller.handle_request("cancel task 1", now=12.1)
    assert radio.active is None and not radio.repeat(now=12.1)
    lap(controller, radio, value)
    assert not any("Consistency" in message.text for message in radio.pending)


@pytest.mark.parametrize("failure", ["stale", "scope", "history_gap", "rewind", "pause"])
def test_discontinuities_clear_recent_evidence(failure):
    controller, radio, events, value = ready()
    lap(controller, radio, value)
    if failure == "stale":
        value["age_s"] = 4
    elif failure == "scope":
        value["generation"] = "3" * 32
    elif failure == "history_gap":
        value["event_floor"] = value["event_cursor"] + 1
        value["event_cursor"] += 1
    elif failure == "rewind":
        value["drivers"][0]["lap"]["current_lap_number"] = 3
    controller.feed(value, now=16 if failure == "pause" else 12.1)
    assert not controller.pace.recent


def test_consistency_and_ordinary_report_share_one_utterance():
    controller, radio, events, value = ready()
    controller.handle_request("report at end of each lap my position", now=11)
    lap(controller, radio, value)
    assert len(radio.pending) == 1
    assert "Task 1" in radio.pending[-1].text and "Task 2" in radio.pending[-1].text
    assert radio.pending[-1].owner == "lap_reports"
