from copy import deepcopy

import pytest

from f1_engineer.radio.questions import plan_question, timing_fact
from tests.test_radio_questions import live
from tests.test_radio_sector_pace import sector_ready
from tests.test_radio_target_pace import ready


@pytest.mark.parametrize("phrase,metric", [
    ("my current lap sector one time", "current_sector1"),
    ("my current lap sector 2 time", "current_sector2"),
    ("my last completed lap sector one time", "last_sector1"),
    ("my previous lap sector two time", "last_sector2"),
    ("my latest lap sector 3 time", "last_sector3"),
    ("my best observed valid lap time", "observed_best"),
])
def test_explicit_sector_and_observed_history_question_plans(phrase, metric):
    plan = plan_question(phrase)
    assert plan.targets == ("self",) and plan.metrics == (metric,)


@pytest.mark.parametrize("phrase", [
    "my sector one", "my current lap sector three time", "my best lap",
    "my best observed lap in the rain", "my last lap sector one in traffic",
    "my lap 3 sector one time", "my best observed lap on soft tyres",
])
def test_ambiguous_or_unavailable_historical_filters_are_not_silently_removed(phrase):
    with pytest.raises(ValueError):
        plan_question(phrase)


def lap():
    return {"current_lap_number": 5, "sector_id": 2, "current_lap_time_ms": 68000,
            "sector1_time_ms": 30000, "sector2_time_ms": 35000, "current_lap_invalid_id": 0}


@pytest.mark.parametrize("metric,expected", [("current_sector1", "30.000"), ("current_sector2", "35.000")])
def test_current_sector_time_is_completed_measurement_not_elapsed_or_prediction(metric, expected):
    text = timing_fact(metric, lap())
    assert f"{expected} seconds" in text and "lap 5 measured sector" in text


@pytest.mark.parametrize("field,value", [
    ("current_lap_number", None), ("current_lap_number", True), ("current_lap_number", 0),
    ("sector_id", 0), ("sector_id", True), ("sector_id", 3),
    ("sector1_time_ms", None), ("sector1_time_ms", True), ("sector1_time_ms", 0),
    ("sector1_time_ms", 2**32), ("current_lap_time_ms", None),
    ("current_lap_time_ms", 29999), ("current_lap_time_ms", True),
])
def test_current_sector_one_rejects_unmeasured_or_misaligned_snapshot_values(field, value):
    sample = lap()
    sample[field] = value
    assert "unavailable" in timing_fact("current_sector1", sample)


@pytest.mark.parametrize("field,value", [
    ("sector_id", 1), ("sector2_time_ms", 0), ("sector2_time_ms", None),
    ("sector2_time_ms", 35000.0), ("sector1_time_ms", 0), ("current_lap_time_ms", 64999),
])
def test_current_sector_two_requires_both_measured_sectors_and_matching_elapsed_time(field, value):
    sample = lap()
    sample[field] = value
    assert "unavailable" in timing_fact("current_sector2", sample)


@pytest.mark.parametrize("invalid,expected", [(1, "observed invalid"), (None, "validity unknown"),
                                            (False, "validity unknown")])
def test_current_sector_reports_known_or_unknown_validity(invalid, expected):
    sample = lap()
    sample["current_lap_invalid_id"] = invalid
    assert expected in timing_fact("current_sector1", sample)


def test_multi_driver_multi_metric_question_keeps_available_parts():
    controller, radio, events, value = live()
    for row in value["drivers"]:
        row["lap"].update(lap())
    value["drivers"][1]["lap"]["sector2_time_ms"] = 0
    controller.feed(value, now=10.1)
    controller.answer_question("my and leader current lap sector one time and current lap sector two time", now=10.1)
    text = radio.pending[-1].text
    assert "30.000 seconds" in text and "35.000 seconds" in text
    assert "sector 2 unavailable" in text
    assert len(events[-1]["results"]) == 2


@pytest.mark.parametrize("sector,elapsed,label", [(1, "30.000", "observed"),
    (2, "35.000", "observed"), (3, "35.000", "derived from lap minus sectors 1 and 2")])
def test_last_lap_sectors_use_one_complete_eligible_observed_lap(sector, elapsed, label):
    controller, radio, events, value = sector_ready()
    controller.answer_question(f"my last lap sector {sector} time", now=12.05)
    text = radio.pending[-1].text
    assert f"lap 4 sector {sector} {elapsed} seconds ({label})" in text


def test_last_lap_sector_does_not_borrow_incomplete_reference_sectors():
    controller, radio, events, value = ready()
    controller.answer_question("my last lap sector one time and last lap", now=12)
    assert "complete observed player sector history missing" in radio.pending[-1].text
    assert "lap 4 100.000 seconds" in radio.pending[-1].text


def test_observed_best_explicitly_excludes_unseen_session_history():
    controller, radio, events, value = ready()
    controller.answer_question("my best observed valid lap", now=12)
    text = radio.pending[-1].text
    assert "lap 4 100.000 seconds" in text
    assert "current compound 16" in text and "not the complete session best" in text


def test_opponent_observed_history_unavailable_without_losing_player_answer():
    controller, radio, events, value = ready()
    controller.answer_question("my and leader best observed lap", now=12)
    assert "lap 4 100.000 seconds" in radio.pending[-1].text
    assert "observed lap history unavailable for this opponent" in radio.pending[-1].text


def test_changed_compound_does_not_substitute_previous_compound_best():
    controller, radio, events, value = ready()
    value["drivers"][0]["tyre"]["value"]["actual_compound"] = 18
    controller.feed(value, now=12.1)
    controller.answer_question("my best observed lap", now=12.1)
    assert "No fully observed valid radio lap on your current tyre compound" in radio.pending[-1].text


@pytest.mark.parametrize("invalidate", ["stale", "history_gap", "session", "identity"])
def test_observed_retrieval_does_not_cross_invalidated_context(invalidate):
    controller, radio, events, value = sector_ready()
    if invalidate == "stale":
        controller.check_freshness(now=16)
    else:
        value = deepcopy(value)
        if invalidate == "history_gap":
            value["event_cursor"] += 2
            value["event_floor"] = value["event_cursor"] - 1
        elif invalidate == "session":
            value["session_id"] = "3" * 32
        else:
            value["drivers"][0]["identity"]["tenure_ordinal"] += 1
        controller.feed(value, now=12.1)
    assert not controller.pace.best and controller.pace.last_completed is None


def test_sector_followup_preserves_driver_bindings():
    controller, radio, events, value = live()
    for row in value["drivers"]:
        row["lap"].update(lap())
    controller.feed(value, now=10.1)
    controller.answer_question("my and leader current lap sector one time", now=10.1)
    radio.cancel()
    value["drivers"][1]["lap"]["car_position"] = 3
    value["drivers"][2]["lap"]["car_position"] = 1
    controller.feed(value, now=10.2)
    controller.answer_question("and current lap sector two time?", now=10.2)
    assert controller._history[-1]["bindings"][1]["car_index"] == 1
    assert "Previously selected Leader" in radio.pending[-1].text


def test_number_word_driver_alias_does_not_consume_sector_word_and_can_be_quoted():
    plan = plan_question("my last lap sector one time and last lap", names=("Player One", "One"))
    assert plan.targets == ("self",) and plan.metrics == ("last_sector1", "last_lap")
    named = plan_question('"One" current lap sector one time', names=("One",))
    assert named.targets == ("name:one",) and named.metrics == ("current_sector1",)


def test_last_lap_sector_rejects_changed_reported_lap_time_without_borrowing_old_sectors():
    controller, radio, events, value = sector_ready()
    value["drivers"][0]["lap"]["last_lap_time_ms"] = 99000
    controller.feed(value, now=12.1)
    controller.answer_question("my last lap sector three time", now=12.1)
    assert "complete observed player sector history missing" in radio.pending[-1].text
