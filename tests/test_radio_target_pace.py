from copy import deepcopy

import pytest

from f1_engineer.radio.pace import PaceReference
from f1_engineer.radio.questions import QuestionPlan
from f1_engineer.radio.tasks import LapReportRegistry, parse_task_request
from tests.test_radio_gap_changes import gap_live
from tests.test_radio_tasks import finish_lap


REQUEST = "help me stay within three tenths of my best valid lap"


def pace_live():
    controller, radio, events, value = gap_live()
    value["drivers"][0]["lap"]["current_lap_invalid_id"] = 0
    value["drivers"][0]["tyre"] = {"status": "available", "value": {"actual_compound": 16}}
    controller.feed(value, now=10)
    return controller, radio, events, value


def finish(controller, radio, value, number, elapsed=100000, *, invalid=0, now=None, cursor=None, event_elapsed=None):
    radio.cancel()
    original = controller.feed
    def tagged(snapshot, **kwargs):
        snapshot["events"][0]["previous_observed_invalid"] = invalid
        original(snapshot, **kwargs)
    controller.feed = tagged
    try:
        finish_lap(controller, value, cursor=number - 3 if cursor is None else cursor, number=number,
                   now=number + 7 if now is None else now, elapsed=elapsed,
                   event_elapsed=elapsed if event_elapsed is None else event_elapsed)
    finally:
        controller.feed = original


def ready():
    controller, radio, events, value = pace_live()
    finish(controller, radio, value, 4)
    finish(controller, radio, value, 5)
    radio.cancel()
    return controller, radio, events, value


@pytest.mark.parametrize("amount,expected", [
    ("one tenth", 100), ("three tenths", 300), ("nine tenths", 900),
    ("3 tenths", 300), ("0.3 seconds", 300), ("0.001 seconds", 1), ("5 seconds", 5000),
])
def test_bounded_pace_intents(amount, expected):
    action = parse_task_request(f"help me stay within {amount} of my best valid lap")
    assert action.question == "my target pace" and action.tolerance_ms == expected and action.laps is None


def test_update_and_finite_pace_intents():
    action = parse_task_request("update task 2 " + REQUEST + " for next 4 laps")
    assert action.kind == "update" and action.task_id == 2 and action.laps == 4 and action.tolerance_ms == 300


@pytest.mark.parametrize("request_text", [
    "help me stay within 0 seconds of my best valid lap",
    "help me stay within 5.001 seconds of my best valid lap",
    "help me stay within 9 seconds of my best valid lap",
    REQUEST + " for next 21 laps",
])
def test_out_of_range_tolerance_and_duration_refused(request_text):
    with pytest.raises(ValueError):
        parse_task_request(request_text)


@pytest.mark.parametrize("request_text", [
    "help me stay within three tenths of Leclerc best lap", REQUEST + " and quit",
    "help me stay within three tenths of my best wet lap",
    "help me stay within 0.0001 seconds of my best valid lap",
])
def test_unsupported_pace_requests_do_not_create_tasks(request_text):
    controller, radio, events, value = ready()
    controller.handle_request(request_text, now=12)
    assert not controller.tasks.active()


def test_partial_first_lap_is_not_a_valid_reference():
    controller, radio, events, value = pace_live()
    finish(controller, radio, value, 4, elapsed=90000)
    controller.handle_request(REQUEST, now=11)
    assert not controller.tasks.active() and "No fully observed valid radio lap" in radio.pending[-1].text
    finish(controller, radio, value, 5, elapsed=100000)
    controller.handle_request(REQUEST, now=12)
    task = controller.tasks.active()[0]
    assert task.pace_reference == PaceReference(4, 100000, 16)
    assert "Fixed reference" in radio.pending[-1].text and "does not update automatically" in radio.pending[-1].text


@pytest.mark.parametrize("elapsed,expected", [
    (100000, "0.000 seconds slower"), (100300, "within the 0.300"),
    (100301, "0.001 seconds outside"), (99500, "0.500 seconds faster"),
])
def test_deterministic_target_boundaries_and_faster_laps(elapsed, expected):
    controller, radio, events, value = ready()
    controller.handle_request(REQUEST, now=12)
    finish(controller, radio, value, 6, elapsed=elapsed)
    assert expected in radio.pending[-1].text
    controller.feed(value, now=13.1)
    assert len(radio.pending) == 1


def test_reference_stays_fixed_and_explicit_update_uses_new_best():
    controller, radio, events, value = ready()
    controller.handle_request(REQUEST, now=12)
    finish(controller, radio, value, 6, elapsed=99000)
    assert controller.tasks.active()[0].pace_reference.elapsed_ms == 100000
    assert controller.status()["pace_bests"][0]["elapsed_ms"] == 99000
    controller.handle_request("update task 1 " + REQUEST, now=13)
    task = controller.tasks.active()[0]
    assert task.version == 2 and task.pace_reference == PaceReference(5, 99000, 16)
    radio.cancel()
    controller.handle_request("what are you tracking", now=13)
    assert "fixed observed lap 5 99.000" in radio.pending[-1].text


@pytest.mark.parametrize("field,broken", [
    ("current_lap_invalid_id", 1), ("current_lap_invalid_id", None), ("current_lap_invalid_id", False),
    ("pit_status_id", 1), ("pit_status_id", None), ("pit_status_id", False),
    ("result_status_id", 3), ("result_status_id", None), ("result_status_id", True),
])
def test_ineligible_midlap_samples_poison_the_reference_and_feedback(field, broken):
    controller, radio, events, value = ready()
    controller.handle_request(REQUEST, now=12)
    original = deepcopy(value["drivers"][0]["lap"])
    value["drivers"][0]["lap"][field] = broken
    controller.feed(value, now=12.2)
    value["drivers"][0]["lap"] = original
    controller.feed(value, now=12.3)
    finish(controller, radio, value, 6, elapsed=99000)
    assert "Target pace unavailable" in radio.pending[-1].text
    assert controller.status()["pace_bests"][0]["elapsed_ms"] == 100000


@pytest.mark.parametrize("invalid", [1, None, False, "0"])
def test_completion_requires_explicit_game_validity(invalid):
    controller, radio, events, value = ready()
    controller.handle_request(REQUEST, now=12)
    finish(controller, radio, value, 6, elapsed=99000, invalid=invalid)
    assert "Target pace unavailable" in radio.pending[-1].text
    assert controller.status()["pace_bests"][0]["elapsed_ms"] == 100000


@pytest.mark.parametrize("tyre", [
    None, {"status": "unavailable", "value": None},
    {"status": "available", "value": {"actual_compound": 0}},
    {"status": "available", "value": {"actual_compound": True}},
])
def test_unknown_tyre_context_breaks_lap_evidence(tyre):
    controller, radio, events, value = ready()
    controller.handle_request(REQUEST, now=12)
    original = deepcopy(value["drivers"][0]["tyre"])
    value["drivers"][0]["tyre"] = tyre
    controller.feed(value, now=12.2)
    value["drivers"][0]["tyre"] = original
    controller.feed(value, now=12.3)
    finish(controller, radio, value, 6, elapsed=99000)
    assert "unavailable" in radio.pending[-1].text


def test_changed_compound_does_not_retarget_existing_task():
    controller, radio, events, value = ready()
    controller.handle_request(REQUEST, now=12)
    value["drivers"][0]["tyre"]["value"]["actual_compound"] = 18
    controller.feed(value, now=12.2)
    finish(controller, radio, value, 6)
    assert "unavailable" in radio.pending[-1].text
    finish(controller, radio, value, 7)
    assert "tyre compound differs" in radio.pending[-1].text
    assert controller.tasks.active()[0].pace_reference.compound == 16
    controller.handle_request("update task 1 " + REQUEST, now=14)
    assert controller.tasks.active()[0].pace_reference.compound == 18


@pytest.mark.parametrize("failure", ["stale", "scope", "history_gap", "rewind", "pause", "missing_event", "bad_time"])
def test_observation_failures_cannot_supply_new_valid_best(failure):
    controller, radio, events, value = ready()
    if failure == "stale":
        value["age_s"] = 4
    elif failure == "scope":
        value["generation"] = "3" * 32
    elif failure == "history_gap":
        value["event_floor"] = value["event_cursor"] = 3
    elif failure == "rewind":
        value["drivers"][0]["lap"]["current_lap_number"] = 3
    elif failure == "missing_event":
        value["drivers"][0]["lap"]["current_lap_number"] = 6
        value["events"] = []
    elif failure == "bad_time":
        finish(controller, radio, value, 6, elapsed=99000, event_elapsed=90000)
    controller.feed(value, now=16 if failure == "pause" else 13.1)
    value["age_s"] = 0.1
    finish(controller, radio, value, 7, elapsed=98000, now=17 if failure == "pause" else 14)
    assert all(record.elapsed_ms >= 100000 for record in controller.pace.best.values())


def test_capacity_idempotence_tolerance_update_and_failed_update_preserve_reference():
    controller, radio, events, value = ready()
    controller.handle_request(REQUEST, now=12)
    controller.handle_request(REQUEST, now=12)
    assert len(controller.tasks.active()) == 1
    controller.handle_request("help me stay within four tenths of my best valid lap", now=12)
    assert len(controller.tasks.active()) == 2
    original = controller.tasks.active()[0]
    controller.handle_request("update task 1 help me stay within 0 seconds of my best valid lap", now=12)
    assert controller.tasks.active()[0] == original


def test_finite_pace_and_normal_report_share_output_and_cancel_owned_speech():
    controller, radio, events, value = ready()
    controller.handle_request(REQUEST + " for next 1 lap", now=12)
    controller.handle_request("report at end of each lap my position", now=12)
    finish(controller, radio, value, 6, elapsed=100400)
    assert len(radio.pending) == 1 and "Task 1" in radio.pending[-1].text and "Task 2" in radio.pending[-1].text
    assert controller.status()["tasks"][0]["state"] == "completed"
    radio.tick(now=13)
    controller.handle_request("cancel task 1", now=13)
    assert radio.active is None and not radio.repeat(now=13)


def test_quiet_consumes_finite_pace_without_replay():
    controller, radio, events, value = ready()
    controller.handle_request(REQUEST + " for next 1 lap", now=12)
    radio.quiet()
    finish(controller, radio, value, 6)
    assert not radio.pending and controller.status()["tasks"][0]["state"] == "completed"


def test_registry_requires_reference_and_tolerance():
    registry = LapReportRegistry()
    with pytest.raises(ValueError, match="fixed player-lap"):
        registry.install(QuestionPlan(("self",), ("target_pace",)), (None,), cursor=0, lap=3, laps=None)
    with pytest.raises(ValueError, match="Only target-pace"):
        registry.install(QuestionPlan(("self",), ("position",)), (None,), cursor=0, lap=3, laps=None,
                         pace_reference=PaceReference(2, 99000, 16), pace_tolerance_ms=300)
    assert registry.views() == []


@pytest.mark.parametrize("lap_number,elapsed,compound", [
    (0, 99000, 16), (True, 99000, 16), (256, 99000, 16),
    (4, 0, 16), (4, 99000.0, 16), (4, 2**32, 16),
    (4, 99000, None), (4, 99000, 0), (4, 99000, True),
])
def test_malformed_reference_cannot_be_frozen(lap_number, elapsed, compound):
    with pytest.raises(ValueError):
        PaceReference(lap_number, elapsed, compound)


def test_failed_compound_update_preserves_active_reference():
    controller, radio, events, value = ready()
    controller.handle_request(REQUEST, now=12)
    original = controller.tasks.active()[0]
    value["drivers"][0]["tyre"]["value"]["actual_compound"] = 18
    controller.feed(value, now=12.2)
    controller.handle_request("update task 1 " + REQUEST, now=12.2)
    assert controller.tasks.active()[0] == original
    assert "No fully observed valid radio lap" in radio.pending[-1].text


def test_unavailable_scope_and_cancel_all_remove_best_inventory():
    controller, radio, events, value = ready()
    assert controller.status()["pace_bests"]
    controller.handle_request("cancel all tracking", now=12)
    assert controller.status()["pace_bests"] == []
    controller, radio, events, value = ready()
    controller.feed({"status": "unavailable", "reason": "telemetry_silence"}, now=12.1)
    assert controller.status()["pace_bests"] == []
