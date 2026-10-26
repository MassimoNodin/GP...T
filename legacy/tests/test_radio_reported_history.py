from copy import deepcopy

import pytest

from f1_engineer.processing.engineer_facts import LiveEngineerFacts
from f1_engineer.radio.controller import RadioController
from tests.test_reported_history import live, history
from tests.test_standalone_radio import scheduler


def connected(*, flags=15, source_lag=0):
    facts = LiveEngineerFacts()
    live(facts, cars=2, identity="a" * 64)
    live(facts, ordinal=6, number=2, cars=2, identity="a" * 64)
    history(facts, flags=flags)
    history(facts, car=1, flags=flags)
    if source_lag:
        live(facts, ordinal=6 + source_lag, number=2, cars=2, identity="a" * 64)
    value = facts.snapshot()
    value.update(generation="1" * 32, session_id="2" * 32, age_s=0.1)
    value["drivers"][0]["lap"]["car_position"] = 2
    value["drivers"][1]["lap"]["car_position"] = 1
    radio, playback, events = scheduler()
    controller = RadioController(radio, notify=events.append)
    controller.feed(value, now=10)
    return controller, radio, value


def test_pre_radio_history_answers_numbered_lap_for_multiple_drivers():
    controller, radio, value = connected()
    controller.answer_question("my and leader lap 1", now=10)
    text = radio.pending[-1].text
    assert text.count("game-reported valid lap 1 100.000 seconds") == 2
    assert "not a fully observed coaching reference" in text
    assert all(not controller.lap_history.get(row).laps for row in value["drivers"])


def test_complete_current_game_selector_supports_session_best():
    controller, radio, value = connected()
    controller.answer_question("my and leader best lap", now=10)
    assert radio.pending[-1].text.count("game-reported session-best valid lap 1 100.000 seconds") == 2
    assert "not a coaching reference" in radio.pending[-1].text


def test_invalid_numbered_lap_is_disclosed_and_not_promoted_to_best():
    controller, radio, value = connected(flags=14)
    controller.answer_question("my lap 1 and best lap", now=10)
    assert "game-reported invalid lap 1 100.000 seconds" in radio.pending[-1].text
    assert "session-best lap unavailable" in radio.pending[-1].text


def test_unknown_validity_is_not_reported_as_a_valid_time():
    controller, radio, value = connected(flags=31)
    controller.answer_question("my lap 1", now=10)
    assert "unknown validity flags" in radio.pending[-1].text
    assert "100.000 seconds" not in radio.pending[-1].text


def test_old_historical_lap_remains_queryable_but_best_requires_current_source():
    controller, radio, value = connected(source_lag=3)
    controller.answer_question("my lap 1 and best lap", now=10)
    assert "100.000 seconds" in radio.pending[-1].text
    assert "game-reported history is stale" in radio.pending[-1].text


def test_best_latency_includes_history_age_and_transport_and_dispatch_delay():
    controller, radio, value = connected(source_lag=2)
    controller.feed(value, now=10, transport_age_s=0.2)
    controller.answer_question("my best lap", now=10.2)
    message = radio.pending[-1]
    assert message.observation_age_s == pytest.approx(2.5)
    assert message.dispatch_before == pytest.approx(10.7)
    radio.tick(now=10.71)
    assert not radio.playback.calls


@pytest.mark.parametrize("fault", ["identity", "scope", "future_frame", "future_time", "future_receipt",
                                  "rows", "duplicate", "incomplete", "best_time", "best_number",
                                  "best_conflict", "coaching", "partial", "source", "first_lap"])
def test_unqualified_or_conflicting_sources_cannot_speak_a_session_best(fault):
    controller, radio, value = connected()
    reported = value["drivers"][0]["reported_history"]
    if fault == "identity":
        reported["source"]["identity"]["tenure_ordinal"] += 1
    elif fault == "scope":
        reported["source"]["scope"]["epoch"] += 1
    elif fault == "future_frame":
        reported["source"]["frame_ordinal"] += 1
    elif fault == "future_time":
        reported["source"]["session_time_s"] += 1
    elif fault == "future_receipt":
        reported["source"]["received_monotonic_ns"] += 1
    elif fault == "rows":
        reported["laps"] = None
    elif fault == "duplicate":
        reported["laps"].append(deepcopy(reported["laps"][0]))
    elif fault == "incomplete":
        reported["reported_lap_count"] = 0
    elif fault == "best_time":
        reported["session_best"]["lap_time_ms"] += 1
    elif fault == "best_number":
        reported["session_best"]["lap_number"] = True
    elif fault == "best_conflict":
        reported["session_best_reason"] = "reported_best_selector_conflicted"
    elif fault == "coaching":
        reported["coaching_eligible"] = True
    elif fault == "partial":
        reported["laps"][0]["lap_number"] = 2
    elif fault == "first_lap":
        reported["first_attributable_lap"] = 2
    else:
        reported["source"] = None
    controller.feed(value, now=10)
    controller.answer_question("my best lap", now=10)
    assert "session-best lap unavailable" in radio.pending[-1].text
    assert "100.000 seconds" not in radio.pending[-1].text


def test_session_best_does_not_implicitly_enable_recurring_or_target_pace_reference():
    controller, radio, value = connected()
    controller.handle_request("tell me my best lap at the end of the lap", now=10)
    assert controller.tasks.active() == ()
    assert not controller.pace.best


def test_incomplete_current_lap_and_pre_backend_laps_still_refuse_numbered_lookup():
    controller, radio, value = connected()
    controller.answer_question("my lap 2", now=10)
    assert "lap 2 unavailable" in radio.pending[-1].text
