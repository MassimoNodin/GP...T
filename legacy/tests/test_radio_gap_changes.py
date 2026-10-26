from copy import deepcopy

import pytest

from f1_engineer.radio.gaps import LapGapTracker
from f1_engineer.radio.questions import plan_question
from tests.test_live_driver_names import named_live
from tests.test_radio_tasks import finish_lap, install


def gap_live():
    controller, radio, events, value = named_live()
    for row in value["drivers"]:
        row["lap"].update(pit_status_id=0, result_status_id=2)
    controller.feed(value, now=10)
    return controller, radio, events, value


def boundary(controller, radio, value, *, cursor=1, number=4, now=11, gap=2500, front=None):
    radio.cancel()
    value["drivers"][0]["lap"].update(delta_to_race_leader_ms=gap,
                                    delta_to_car_in_front_ms=gap if front is None else front)
    for row in value["drivers"][1:]:
        row["lap"]["current_lap_number"] = number
    finish_lap(controller, value, cursor=cursor, number=number, now=now)


@pytest.mark.parametrize("question,metric", [
    ("my gap change to the leader", "leader_gap_change"),
    ("my time to leader change that lap", "leader_gap_change"),
    ("my delta that lap", "leader_gap_change"),
    ("my gap change to the car ahead", "front_gap_change"),
    ("my gap to car in front change that lap", "front_gap_change"),
])
def test_gap_change_plans(question, metric):
    assert plan_question(question).metrics == (metric,)
    assert plan_question(question).targets == ("self",)


def test_requested_leader_time_and_delta_plan():
    plan = plan_question("my time to the leader and delta that lap")
    assert set(plan.metrics) == {"leader_gap", "leader_gap_change"}


@pytest.mark.parametrize("gap,expected", [
    (2000, "gained 0.500 seconds"), (3000, "lost 0.500 seconds"), (2500, "unchanged"),
])
def test_gap_arithmetic_uses_consecutive_player_boundaries(gap, expected):
    controller, radio, events, value = gap_live()
    install(controller, radio, "report at end of each lap my time to leader and delta that lap")
    boundary(controller, radio, value)
    assert "two consecutive comparable" in radio.pending[-1].text
    boundary(controller, radio, value, cursor=2, number=5, now=12, gap=gap)
    text = radio.pending[-1].text
    assert "lap 4 gap to leader change" in text and expected in text
    assert f"gap to leader {gap / 1000:.3f} seconds" in text
    controller.feed(value, now=12.1)
    assert len(radio.pending) == 1


def test_front_and_leader_changes_are_separate():
    controller, radio, events, value = gap_live()
    boundary(controller, radio, value, gap=4000, front=2000)
    boundary(controller, radio, value, cursor=2, number=5, now=12, gap=3500, front=3000)
    radio.cancel()
    controller.handle_request("my gap change to leader and gap change to car ahead", now=12)
    text = radio.pending[-1].text
    assert "gap to leader change: you gained 0.500" in text
    assert "gap to car ahead change: you lost 1.000" in text
    assert radio.pending[-1].owner == "gap_changes"


@pytest.mark.parametrize("field,value", [
    ("delta_to_race_leader_ms", 0), ("delta_to_race_leader_ms", None),
    ("delta_to_race_leader_ms", True), ("delta_to_race_leader_ms", 2**32),
    ("car_position", 1), ("car_position", None), ("car_position", True),
    ("pit_status_id", 1), ("pit_status_id", None), ("pit_status_id", False),
    ("result_status_id", 3), ("result_status_id", None),
])
def test_unknown_or_ineligible_player_data_breaks_interval(field, value):
    controller, radio, events, snapshot = gap_live()
    boundary(controller, radio, snapshot)
    original = deepcopy(snapshot["drivers"][0]["lap"])
    snapshot["drivers"][0]["lap"][field] = value
    controller.feed(snapshot, now=11.2)
    snapshot["drivers"][0]["lap"] = original
    controller.feed(snapshot, now=11.3)
    boundary(controller, radio, snapshot, cursor=2, number=5, now=12, gap=2000)
    controller.handle_request("my delta that lap", now=12)
    assert "unavailable" in radio.pending[-1].text


@pytest.mark.parametrize("field,value", [
    ("pit_status_id", 1), ("pit_status_id", None), ("result_status_id", 3),
    ("current_lap_number", 2), ("current_lap_number", 9),
])
def test_rival_ineligible_midlap_breaks_interval(field, value):
    controller, radio, events, snapshot = gap_live()
    boundary(controller, radio, snapshot)
    original = deepcopy(snapshot["drivers"][1]["lap"])
    snapshot["drivers"][1]["lap"][field] = value
    controller.feed(snapshot, now=11.2)
    snapshot["drivers"][1]["lap"] = original
    controller.feed(snapshot, now=11.3)
    boundary(controller, radio, snapshot, cursor=2, number=5, now=12, gap=2000)
    controller.handle_request("my delta that lap", now=12)
    assert "unavailable" in radio.pending[-1].text


def test_leader_change_and_return_breaks_interval():
    controller, radio, events, value = gap_live()
    boundary(controller, radio, value)
    value["drivers"][1]["lap"]["car_position"] = 3
    value["drivers"][2]["lap"]["car_position"] = 1
    controller.feed(value, now=11.2)
    value["drivers"][1]["lap"]["car_position"] = 1
    value["drivers"][2]["lap"]["car_position"] = 3
    controller.feed(value, now=11.3)
    boundary(controller, radio, value, cursor=2, number=5, now=12, gap=2000)
    controller.handle_request("my delta that lap", now=12)
    assert "unavailable" in radio.pending[-1].text


def test_rival_may_cross_line_before_player_without_resetting_interval():
    controller, radio, events, value = gap_live()
    boundary(controller, radio, value)
    value["drivers"][1]["lap"]["current_lap_number"] = 5
    controller.feed(value, now=11.5)
    boundary(controller, radio, value, cursor=2, number=5, now=12, gap=2000)
    controller.handle_request("my delta that lap", now=12)
    assert "gained 0.500" in radio.pending[-1].text


def test_lapped_reference_at_boundary_is_not_compared():
    controller, radio, events, value = gap_live()
    boundary(controller, radio, value)
    value["drivers"][1]["lap"]["current_lap_number"] = 6
    value["drivers"][0]["lap"]["delta_to_race_leader_ms"] = 2000
    finish_lap(controller, value, cursor=2, number=5, now=12)
    controller.handle_request("my delta that lap", now=12)
    assert "unavailable" in radio.pending[-1].text


@pytest.mark.parametrize("change", ["scope", "stale", "pause", "history_gap", "rewind", "missing_event", "invalid_time"])
def test_scope_and_observation_failures_do_not_produce_numeric_change(change):
    controller, radio, events, value = gap_live()
    boundary(controller, radio, value)
    if change == "scope":
        value["generation"] = "3" * 32
    elif change == "stale":
        value["age_s"] = 4
    elif change == "pause":
        controller.feed(value, now=15)
    elif change == "history_gap":
        value["event_floor"] = value["event_cursor"] = 2
    elif change == "rewind":
        value["drivers"][0]["lap"]["current_lap_number"] = 3
    elif change == "missing_event":
        value["drivers"][0]["lap"]["current_lap_number"] = 5
        value["events"] = []
    elif change == "invalid_time":
        finish_lap(controller, value, cursor=2, number=5, now=11.2, event_elapsed=100000)
    controller.feed(value, now=15.1 if change == "pause" else 11.3)
    value["age_s"] = 0.1
    boundary(controller, radio, value, cursor=3, number=6 if change in ("missing_event", "invalid_time") else 5,
             now=16 if change == "pause" else 12, gap=2000)
    controller.handle_request("my delta that lap", now=16 if change == "pause" else 12)
    assert "gained" not in radio.pending[-1].text and "lost" not in radio.pending[-1].text


def test_relation_change_invalidates_queued_active_and_repeatable_gap_answer():
    controller, radio, events, value = gap_live()
    boundary(controller, radio, value)
    boundary(controller, radio, value, cursor=2, number=5, now=12, gap=2000)
    radio.cancel()
    controller.handle_request("my delta that lap", now=12)
    radio.tick(now=12)
    assert radio.active.owner == "gap_changes"
    value["drivers"][1]["identity"]["tenure_ordinal"] += 1
    controller.feed(value, now=12.1)
    assert radio.active is None and not radio.repeat(now=12.1)


def test_nonplayer_change_is_partial_unavailable():
    controller, radio, events, value = gap_live()
    controller.handle_request("Leclerc gap change to leader and position", now=10)
    text = radio.pending[-1].text
    assert "supported only for you" in text and "position 1" in text


def test_quiet_still_observes_boundaries_without_backlog():
    controller, radio, events, value = gap_live()
    install(controller, radio, "report at end of each lap my delta that lap")
    radio.quiet()
    boundary(controller, radio, value)
    boundary(controller, radio, value, cursor=2, number=5, now=12, gap=2000)
    assert not radio.pending
    radio.muted = False
    controller.handle_request("my delta that lap", now=12)
    assert "gained 0.500" in radio.pending[-1].text


@pytest.mark.parametrize("number", [None, "3", True, -1, 256])
def test_malformed_lap_number_does_not_poison_tracker(number):
    controller, radio, events, value = gap_live()
    player = deepcopy(value["drivers"][0])
    player["lap"]["current_lap_number"] = number
    tracker = LapGapTracker()
    tracker.observe(player, controller._resolve_target, now=10)
    tracker.observe(value["drivers"][0], controller._resolve_target, now=11)
    assert not tracker.results


@pytest.mark.parametrize("discontinuity_first", [False, True])
def test_discontinuity_event_invalidates_interval_regardless_of_event_order(discontinuity_first):
    controller, radio, events, value = gap_live()
    install(controller, radio, "report at end of each lap my delta that lap")
    boundary(controller, radio, value)
    original_feed = controller.feed
    def with_discontinuity(snapshot, **kwargs):
        discontinuity = {"id": 3, "scope": deepcopy(snapshot["scope"]),
                         "driver": deepcopy(snapshot["drivers"][1]["identity"]), "kind": "driver_discontinuity"}
        snapshot["events"].insert(0 if discontinuity_first else len(snapshot["events"]), discontinuity)
        snapshot["event_cursor"] = 3
        original_feed(snapshot, **kwargs)
    controller.feed = with_discontinuity
    boundary(controller, radio, value, cursor=2, number=5, now=12, gap=2000)
    assert "unavailable" in radio.pending[-1].text
    assert "gained" not in radio.pending[-1].text


def test_bad_completion_cancels_active_and_repeatable_gap_answer():
    controller, radio, events, value = gap_live()
    boundary(controller, radio, value)
    boundary(controller, radio, value, cursor=2, number=5, now=12, gap=2000)
    radio.cancel()
    controller.handle_request("my delta that lap", now=12)
    radio.tick(now=12)
    assert radio.active is not None
    finish_lap(controller, value, cursor=3, number=5, now=12.1, event_elapsed=100000)
    assert radio.active is None and not radio.repeat(now=12.1)


def test_reference_reset_preserves_unrelated_question_output():
    controller, radio, events, value = gap_live()
    boundary(controller, radio, value)
    boundary(controller, radio, value, cursor=2, number=5, now=12, gap=2000)
    radio.cancel()
    controller.handle_request("my position", now=12)
    radio.tick(now=12)
    value["drivers"][1]["identity"]["tenure_ordinal"] += 1
    controller.feed(value, now=12.1)
    assert radio.active is not None and "position 2" in radio.active.text


def test_missing_own_lap_time_does_not_block_gap_measurement():
    controller, radio, events, value = gap_live()
    boundary(controller, radio, value)
    for row in value["drivers"][1:]:
        row["lap"]["current_lap_number"] = 5
    value["drivers"][0]["lap"]["delta_to_race_leader_ms"] = 2000
    finish_lap(controller, value, cursor=2, number=5, now=12, elapsed=None, event_elapsed=None)
    controller.handle_request("my delta that lap", now=12)
    assert "gained 0.500" in radio.pending[-1].text
    observations = [event for event in events if event["event"] == "radio_gap_change_observed"]
    assert observations[-1]["changes_ms"]["leader_gap_change"] == -500


def test_gap_change_followup_retains_metric():
    controller, radio, events, value = gap_live()
    boundary(controller, radio, value)
    boundary(controller, radio, value, cursor=2, number=5, now=12, gap=2000)
    radio.cancel()
    controller.handle_request("my gap change to leader", now=12)
    radio.cancel()
    controller.handle_request("and me?", now=12)
    assert "gained 0.500" in radio.pending[-1].text
