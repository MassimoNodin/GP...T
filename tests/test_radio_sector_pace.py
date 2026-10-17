from copy import deepcopy

import pytest

from f1_engineer.radio.pace import PaceReference
from tests.test_radio_target_pace import REQUEST, finish, pace_live, ready


def sector(controller, radio, value, number, elapsed, *, cursor=None, now=12.1, reported=None, age=0):
    radio.cancel()
    player = value["drivers"][0]
    player["lap"].update(sector_id=number)
    player["lap"][f"sector{number}_time_ms"] = elapsed if reported is None else reported
    value["received_monotonic_ns"] += 100_000_000
    value["event_cursor"] = value["event_cursor"] + 1 if cursor is None else cursor
    value["events"] = [{"id": value["event_cursor"], "scope": deepcopy(value["scope"]),
        "driver": deepcopy(player["identity"]), "kind": "sector_completed",
        "lap_number": player["lap"]["current_lap_number"], "sector": number,
        "sector_time_ms": elapsed, "received_monotonic_ns": value["received_monotonic_ns"] - int(age * 1e9)}]
    controller.feed(value, now=now)


def sector_ready():
    controller, radio, events, value = pace_live()
    finish(controller, radio, value, 4)
    sector(controller, radio, value, 1, 30000, now=11.1)
    sector(controller, radio, value, 2, 35000, now=11.2)
    finish(controller, radio, value, 5, cursor=4)
    value["drivers"][0]["lap"].update(sector_id=0, sector1_time_ms=0, sector2_time_ms=0)
    controller.feed(value, now=12.05)
    controller.handle_request(REQUEST, now=12.05)
    assert "Observed sector feedback enabled" in radio.pending[-1].text
    radio.cancel()
    return controller, radio, events, value


def test_reference_sector_times_belong_to_one_valid_lap_and_are_immutable():
    controller, radio, events, value = sector_ready()
    task = controller.tasks.active()[0]
    assert task.pace_reference == PaceReference(4, 100000, 16, (30000, 35000))
    view = task.view()
    view["pace_reference"]["sector_times_ms"][0] = 1
    assert task.pace_reference.sector_times_ms == (30000, 35000)


@pytest.mark.parametrize("first,second,expected", [
    (30100, 35100, "cumulative 0.200 seconds behind"),
    (29800, 34800, "cumulative 0.400 seconds ahead of"),
    (30200, 35100, "within the 0.300 second lap allowance"),
    (30200, 35101, "0.001 seconds outside the lap allowance"),
])
def test_observed_cumulative_deltas_do_not_predict_lap_outcome(first, second, expected):
    controller, radio, events, value = sector_ready()
    sector(controller, radio, value, 1, first)
    assert "After sector 1" in radio.pending[-1].text
    sector(controller, radio, value, 2, second, now=12.2)
    text = radio.pending[-1].text
    assert "After sector 2" in text and expected in text and "not a predicted lap result" in text
    assert radio.pending[-1].owner == "pace_sectors"
    assert controller.tasks.active()[0].state == "active"


def test_sector_two_requires_observed_sector_one_not_a_snapshot_guess():
    controller, radio, events, value = sector_ready()
    value["drivers"][0]["lap"]["sector1_time_ms"] = 30000
    sector(controller, radio, value, 2, 35100)
    assert "valid aligned sector observations are missing" in radio.pending[-1].text
    assert controller.pace.sectors == {}


def test_missing_reference_sectors_does_not_borrow_another_lap():
    controller, radio, events, value = ready()
    controller.handle_request(REQUEST, now=12)
    assert "Sector feedback unavailable" in radio.pending[-1].text
    sector(controller, radio, value, 1, 30000)
    assert "fixed reference has no complete observed sector timings" in radio.pending[-1].text
    assert controller.tasks.active()[0].pace_reference.sector_times_ms is None


@pytest.mark.parametrize("elapsed,reported", [
    (0, 0), (None, None), (True, True), (30000.0, 30000.0),
    (30000, 30001), (30000, 30000.0), (2**32, 2**32),
])
def test_malformed_or_mismatched_sector_time_abstains(elapsed, reported):
    controller, radio, events, value = sector_ready()
    sector(controller, radio, value, 1, elapsed, reported=reported)
    assert "unavailable" in radio.pending[-1].text
    assert not controller.pace.sectors


@pytest.mark.parametrize("field,broken", [
    ("current_lap_invalid_id", 1), ("current_lap_invalid_id", None), ("current_lap_invalid_id", False),
    ("pit_status_id", 1), ("pit_status_id", None), ("result_status_id", 3),
])
def test_ineligible_current_lap_cannot_produce_sector_pace(field, broken):
    controller, radio, events, value = sector_ready()
    value["drivers"][0]["lap"][field] = broken
    sector(controller, radio, value, 1, 30100)
    assert "unavailable" in radio.pending[-1].text


def test_different_compound_keeps_fixed_sector_reference_and_pauses_feedback():
    controller, radio, events, value = sector_ready()
    value["drivers"][0]["tyre"]["value"]["actual_compound"] = 18
    sector(controller, radio, value, 1, 30100)
    assert "tyre compound differs" in radio.pending[-1].text
    assert controller.tasks.active()[0].pace_reference.compound == 16


def test_changed_first_sector_value_rejects_cumulative_sector_two():
    controller, radio, events, value = sector_ready()
    sector(controller, radio, value, 1, 30100)
    value["drivers"][0]["lap"]["sector1_time_ms"] = 30200
    sector(controller, radio, value, 2, 35100, now=12.2)
    assert "unavailable" in radio.pending[-1].text and not controller.pace.sectors


@pytest.mark.parametrize("age", [3, 5, -1])
def test_old_or_future_sector_receipts_are_not_spoken_or_stored(age):
    controller, radio, events, value = sector_ready()
    sector(controller, radio, value, 1, 30100, age=age)
    assert not radio.pending and not controller.pace.sectors


def test_same_snapshot_and_new_id_duplicate_sector_are_not_spoken_twice():
    controller, radio, events, value = sector_ready()
    sector(controller, radio, value, 1, 30100)
    controller.feed(value, now=12.11)
    assert len(radio.pending) == 1
    radio.cancel()
    sector(controller, radio, value, 1, 30100, now=12.12)
    assert not radio.pending
    sector(controller, radio, value, 2, 35100, now=12.2)
    assert "cumulative 0.200" in radio.pending[-1].text


def test_new_sector_replaces_active_older_sector_and_lap_report_replaces_it():
    controller, radio, events, value = sector_ready()
    sector(controller, radio, value, 1, 30100)
    radio.tick(now=12.1)
    original_cancel = radio.cancel
    radio.cancel = lambda: None
    try:
        sector(controller, radio, value, 2, 35100, now=12.2)
        assert radio.active is None and len(radio.pending) == 1 and "After sector 2" in radio.pending[0].text
        radio.tick(now=12.2)
        finish(controller, radio, value, 6, elapsed=100400, cursor=7)
        assert radio.active is None and len(radio.pending) == 1 and radio.pending[0].owner == "lap_reports"
    finally:
        radio.cancel = original_cancel


@pytest.mark.parametrize("action", ["cancel task 1", "cancel all tracking", "update task 1 " + REQUEST])
def test_task_controls_invalidate_active_and_repeatable_sector_output(action):
    controller, radio, events, value = sector_ready()
    sector(controller, radio, value, 1, 30100)
    radio.tick(now=12.1)
    controller.handle_request(action, now=12.11)
    assert radio.active is None and not radio.repeat(now=12.11)
    assert not any(message.owner == "pace_sectors" for message in radio.pending)


def test_invalidating_current_lap_cancels_owned_sector_output_only():
    controller, radio, events, value = sector_ready()
    sector(controller, radio, value, 1, 30100)
    radio.tick(now=12.1)
    value["drivers"][0]["lap"]["current_lap_invalid_id"] = 1
    controller.feed(value, now=12.11)
    assert radio.active is None and not radio.repeat(now=12.11)


def test_quiet_collects_sector_reference_without_a_speech_backlog():
    controller, radio, events, value = sector_ready()
    radio.quiet()
    sector(controller, radio, value, 1, 29800)
    sector(controller, radio, value, 2, 34800, now=12.2)
    assert not radio.pending
    finish(controller, radio, value, 6, elapsed=99500, cursor=7)
    assert controller.pace.best[16].sector_times_ms == (29800, 34800)
    assert not radio.pending


def test_sampling_interruption_cancels_sector_output_even_when_current_snapshot_is_healthy():
    controller, radio, events, value = sector_ready()
    sector(controller, radio, value, 1, 30100)
    radio.tick(now=12.1)
    controller.feed(value, now=15.1)
    assert radio.active is None and not radio.repeat(now=15.1)
    assert not controller.pace.sectors and not controller.pace.started


@pytest.mark.parametrize("field,broken", [("lap_number", None), ("received_monotonic_ns", None),
                                          ("sector", 3), ("sector", True), ("lap_number", 4)])
def test_malformed_or_misaligned_sector_event_cannot_produce_a_delta(field, broken):
    controller, radio, events, value = sector_ready()
    sector(controller, radio, value, 1, 30100)
    radio.cancel()
    value["event_cursor"] += 1
    value["events"][0]["id"] = value["event_cursor"]
    value["events"][0][field] = broken
    controller.feed(value, now=12.2)
    assert not controller.pace.sectors
    assert not any("cumulative" in message.text for message in radio.pending)


def test_partial_sector_metadata_does_not_block_valid_lap_feedback():
    controller, radio, events, value = sector_ready()
    sector(controller, radio, value, 1, 30100)
    finish(controller, radio, value, 6, elapsed=100200, cursor=6)
    assert "within the 0.300" in radio.pending[-1].text
    assert controller.pace.last_completed.sector_times_ms is None


def test_equal_best_with_observed_sectors_can_be_explicitly_selected_without_changing_active_reference():
    controller, radio, events, value = ready()
    controller.handle_request(REQUEST, now=12)
    sector(controller, radio, value, 1, 30000)
    sector(controller, radio, value, 2, 35000, now=12.2)
    finish(controller, radio, value, 6, elapsed=100000, cursor=5)
    assert controller.pace.best[16].sector_times_ms == (30000, 35000)
    assert controller.tasks.active()[0].pace_reference.sector_times_ms is None
    controller.handle_request("update task 1 " + REQUEST, now=13)
    assert controller.tasks.active()[0].pace_reference.sector_times_ms == (30000, 35000)


@pytest.mark.parametrize("timings", [(0, 35000), (True, 35000), (30000.0, 35000), (30000,),
                                     (30000, 35000, 35000), [30000, 35000], (60000, 40000)])
def test_invalid_sector_reference_metadata_is_rejected(timings):
    with pytest.raises(ValueError):
        PaceReference(4, 100000, 16, timings)


def test_sector_times_do_not_create_a_reference_when_their_sum_is_not_below_lap_time():
    controller, radio, events, value = pace_live()
    finish(controller, radio, value, 4)
    sector(controller, radio, value, 1, 60000, now=11.1)
    sector(controller, radio, value, 2, 40000, now=11.2)
    finish(controller, radio, value, 5, cursor=4)
    assert controller.pace.best[16].sector_times_ms is None


def test_sector_progress_does_not_consume_a_finite_lap_schedule():
    controller, radio, events, value = sector_ready()
    controller.handle_request("update task 1 " + REQUEST + " for next 1 lap", now=12.05)
    sector(controller, radio, value, 1, 30100)
    sector(controller, radio, value, 2, 35100, now=12.2)
    assert controller.tasks.active()[0].until_lap == 5
    finish(controller, radio, value, 6, elapsed=100200, cursor=7)
    assert controller.status()["tasks"][0]["state"] == "completed"
