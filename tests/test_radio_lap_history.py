from copy import deepcopy

import pytest

from f1_engineer.radio.questions import plan_question
from f1_engineer.radio.controller import RadioController
from tests.test_standalone_radio import scheduler
from tests.test_live_driver_names import named_live


def history_live():
    controller, radio, events, value = named_live()
    for row in value["drivers"]:
        row["lap"].update(current_lap_invalid_id=0, pit_status_id=0, result_status_id=2,
                          sector_id=0, sector1_time_ms=0, sector2_time_ms=0)
        row["tyre"] = {"status": "available", "value": {"actual_compound": 16, "age_laps": 2}}
    controller.feed(value, now=10.1)
    return controller, radio, events, value


def publish(controller, value, *, car=1, kind="lap_completed", number=4, elapsed=100000,
            sector=None, now=11, invalid=0, edit=None, transport_age_s=0):
    row = value["drivers"][car]
    value["event_cursor"] += 1
    value["received_monotonic_ns"] += 100_000_000
    event = {"id": value["event_cursor"], "scope": deepcopy(value["scope"]),
             "driver": deepcopy(row["identity"]), "kind": kind,
             "received_monotonic_ns": value["received_monotonic_ns"], "lap_number": number}
    if kind == "lap_completed":
        row["lap"].update(current_lap_number=number + 1, last_lap_time_ms=elapsed,
                          sector_id=0, sector1_time_ms=0, sector2_time_ms=0)
        event.update(qualification="observed_lap_transition", lap_time_ms=elapsed,
                     previous_observed_invalid=invalid)
    elif kind == "sector_completed":
        row["lap"].update(sector_id=sector, current_lap_time_ms=70000)
        row["lap"][f"sector{sector}_time_ms"] = elapsed
        event.update(sector=sector, sector_time_ms=elapsed)
    if edit:
        edit(event)
    value["events"] = [event]
    controller.feed(value, now=now, transport_age_s=transport_age_s)
    return event


def opponent_ready(*, sectors=False):
    controller, radio, events, value = history_live()
    publish(controller, value, number=3, now=11)
    if sectors:
        publish(controller, value, kind="sector_completed", number=4, sector=1, elapsed=30000, now=11.1)
        publish(controller, value, kind="sector_completed", number=4, sector=2, elapsed=35000, now=11.2)
    publish(controller, value, number=4, now=12)
    radio.cancel()
    return controller, radio, events, value


def records(controller, value, car=1):
    state = controller.lap_history.get(value["drivers"][car])
    return list(state.laps) if state else []


def test_connection_does_not_import_initial_retained_events_or_first_partial_lap():
    controller, radio, events, value = history_live()
    publish(controller, value, number=3, now=11)
    assert not records(controller, value)
    publish(controller, value, number=4, now=12)
    assert [record.lap_number for record in records(controller, value)] == [4]
    assert not any(event["event"] == "radio_started" for event in events)


def test_opponent_sector_history_and_best_are_answered_without_player_substitution():
    controller, radio, events, value = opponent_ready(sectors=True)
    controller.answer_question("Leclerc last lap sector one time and last lap sector three time", now=12)
    assert "lap 4 sector 1 30.000 seconds" in radio.pending[-1].text
    assert "lap 4 sector 3 35.000 seconds (derived" in radio.pending[-1].text
    radio.cancel()
    controller.answer_question("Leclerc best observed valid lap", now=12)
    assert "100.000 seconds" in radio.pending[-1].text and "not the complete session best" in radio.pending[-1].text


def test_numbered_laps_and_multi_driver_partial_missing_history():
    controller, radio, events, value = opponent_ready()
    controller.answer_question("my and Leclerc lap 4 time and lap 3 time", now=12)
    text = radio.pending[-1].text
    assert "fully observed valid lap 4 100.000 seconds" in text
    assert "lap 3 unavailable in retained eligible radio history" in text
    assert len(controller._history[-1]["plan"].metrics) == 2


@pytest.mark.parametrize("phrase,metrics", [
    ("my lap 1 time", ("lap_number:1",)), ("Leclerc lap 255 time", ("lap_number:255",)),
    ("my lap 4 and lap 4 time", ("lap_number:4",)),
])
def test_numbered_lap_plans_are_bounded_and_deduplicated(phrase, metrics):
    assert plan_question(phrase, names=("Leclerc",)).metrics == metrics


@pytest.mark.parametrize("phrase", ["my lap 0 time", "my lap 256 time", "my lap -1 time",
    "my lap 4 in rain", "my lap 3 sector one time", "my lap 1 and lap 2 and lap 3 and lap 4"])
def test_unsupported_numbered_lap_filters_and_bounds_abstain(phrase):
    with pytest.raises(ValueError):
        plan_question(phrase)


@pytest.mark.parametrize("field,value", [
    ("current_lap_invalid_id", 1), ("current_lap_invalid_id", None), ("current_lap_invalid_id", False),
    ("pit_status_id", 1), ("pit_status_id", None), ("result_status_id", 0),
])
def test_known_or_unknown_ineligible_opponent_lap_is_not_retained(field, value):
    controller, radio, events, snapshot = history_live()
    publish(controller, snapshot, number=3, now=11)
    snapshot["drivers"][1]["lap"][field] = value
    controller.feed(snapshot, now=11.1)
    snapshot["drivers"][1]["lap"][field] = 0 if field != "result_status_id" else 2
    publish(controller, snapshot, number=4, now=12)
    assert not records(controller, snapshot)


@pytest.mark.parametrize("fault", ["missing_tyre", "compound_change", "invalid_completion", "unknown_validity",
                                  "unqualified", "lap_time_mismatch", "malformed_identity"])
def test_missing_or_conflicting_completion_context_is_not_retained(fault):
    controller, radio, events, value = history_live()
    publish(controller, value, number=3, now=11)
    if fault in ("missing_tyre", "compound_change"):
        value["drivers"][1]["tyre"] = ({"status": "unavailable"} if fault == "missing_tyre" else
                                        {"status": "available", "value": {"actual_compound": 18}})
        controller.feed(value, now=11.1)
    def edit(event):
        if fault == "unqualified":
            event["qualification"] = "unknown"
        elif fault == "lap_time_mismatch":
            event["lap_time_ms"] = 99000
        elif fault == "malformed_identity":
            event["driver"]["tenure_ordinal"] = True
    publish(controller, value, number=4, now=12,
            invalid=1 if fault == "invalid_completion" else None if fault == "unknown_validity" else 0, edit=edit)
    assert not records(controller, value)


@pytest.mark.parametrize("fault", ["old", "future", "transport_age"])
def test_event_receipt_freshness_includes_transport_time(fault):
    controller, radio, events, value = opponent_ready()
    def edit(event):
        event["received_monotonic_ns"] += (1 if fault == "future" else -3_000_000_000 if fault == "old" else -400_000_000)
    publish(controller, value, number=5, now=12.5, edit=edit, transport_age_s=2.6 if fault == "transport_age" else 0)
    assert not records(controller, value)


@pytest.mark.parametrize("fault", ["identity", "rewind", "skip", "missing", "duplicate", "malformed_duplicate", "discontinuity"])
def test_opponent_history_is_fenced_per_driver(fault):
    controller, radio, events, value = opponent_ready()
    other_identity = deepcopy(value["drivers"][0]["identity"])
    if fault == "identity":
        value["drivers"][1]["identity"]["tenure_ordinal"] += 1
    elif fault in ("rewind", "skip"):
        value["drivers"][1]["lap"]["current_lap_number"] = 3 if fault == "rewind" else 7
    elif fault == "missing":
        value["drivers"] = [value["drivers"][0], value["drivers"][2]]
    elif fault in ("duplicate", "malformed_duplicate"):
        duplicate = deepcopy(value["drivers"][1])
        if fault == "malformed_duplicate":
            duplicate["identity"] = None
        value["drivers"].append(duplicate)
    elif fault == "discontinuity":
        publish(controller, value, kind="driver_discontinuity", now=12.1)
    controller.feed(value, now=12.2)
    assert 1 not in controller.lap_history.drivers or not controller.lap_history.drivers[1].laps
    assert controller.lap_history.get(value["drivers"][0]).identity
    assert value["drivers"][0]["identity"] == other_identity


@pytest.mark.parametrize("fault", ["session", "event_gap", "cursor_reset"])
def test_all_history_is_cleared_on_global_context_loss(fault):
    controller, radio, events, value = opponent_ready()
    if fault == "stale":
        controller.check_freshness(now=16)
    else:
        if fault == "session":
            value["session_id"] = "3" * 32
        elif fault == "event_gap":
            value["event_cursor"] += 2
            value["event_floor"] = value["event_cursor"] - 1
        else:
            value["event_cursor"] = 0
            value["events"] = []
        controller.feed(value, now=12.1)
    assert not any(state.laps for state in controller.lap_history.drivers.values())


def test_duplicate_snapshot_and_events_do_not_duplicate_records():
    controller, radio, events, value = opponent_ready()
    value["events"].append(deepcopy(value["events"][0]))
    controller.feed(value, now=12.1)
    assert len(records(controller, value)) == 1


def test_numbered_lap_followup_keeps_bound_driver_when_positions_change():
    controller, radio, events, value = opponent_ready()
    controller.answer_question("leader lap 4 time", now=12)
    value["drivers"][1]["lap"]["car_position"] = 3
    value["drivers"][2]["lap"]["car_position"] = 1
    controller.feed(value, now=12.1)
    controller.answer_question("and lap 3 time?", now=12.1)
    assert controller._history[-1]["bindings"][0]["car_index"] == 1


def test_history_is_bounded_to_64_laps_per_driver():
    controller, radio, events, value = history_live()
    for number in range(3, 71):
        publish(controller, value, number=number, now=11 + number / 100)
    retained = records(controller, value)
    assert len(retained) == 64
    assert retained[0].lap_number == 7 and retained[-1].lap_number == 70
    assert len(controller.lap_history.drivers) <= 24


def test_new_connection_does_not_reconstruct_history_from_retained_completion_events():
    controller, radio, events, value = opponent_ready()
    fresh_radio, playback, notifications = scheduler()
    fresh = RadioController(fresh_radio)
    fresh.feed(value, now=12)
    assert not records(fresh, value)
    assert fresh.lap_history.get(value["drivers"][1]).tracker.number == 5


def test_conflicting_duplicate_event_id_does_not_create_an_arbitrary_record():
    controller, radio, events, value = history_live()
    publish(controller, value, number=3, now=11)
    original_feed = controller.feed
    def conflicting(snapshot, **settings):
        duplicate = deepcopy(snapshot["events"][0])
        duplicate["lap_time_ms"] += 1
        snapshot["events"].append(duplicate)
        original_feed(snapshot, **settings)
    controller.feed = conflicting
    publish(controller, value, number=4, now=12)
    assert not records(controller, value)


def test_cancel_all_tracking_does_not_erase_observed_player_question_history():
    controller, radio, events, value = history_live()
    publish(controller, value, car=0, number=3, now=11)
    publish(controller, value, car=0, number=4, now=12)
    controller.handle_request("cancel all tracking", now=12)
    radio.cancel()
    controller.answer_question("my best observed valid lap", now=12)
    assert "lap 4 100.000 seconds" in radio.pending[-1].text
