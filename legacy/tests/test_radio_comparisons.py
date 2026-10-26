from copy import deepcopy

import pytest

from f1_engineer.radio.questions import lap_comparison, plan_question, timing_fact
from tests.test_live_driver_names import named_live
from tests.test_radio_tasks import finish_lap, install


@pytest.mark.parametrize("question,targets", [
    ("Compare my last lap with Leclerc", ("name:leclerc",)),
    ("Compare my previous completed lap time with Leclerc and Norris?", ("name:leclerc", "name:norris")),
    ("Compare my latest lap with the leader", ("leader",)),
    ("P3 last lap comparison", ("position:3",)),
    ("Leclerc last lap comparison and position", ("name:leclerc",)),
])
def test_comparison_plans(question, targets):
    plan = plan_question(question, names=("leclerc", "norris"))
    assert plan.targets == targets
    assert plan.metrics[0] == "lap_comparison"


@pytest.mark.parametrize("question", [
    "compare my current lap with Leclerc", "compare my best lap with Leclerc",
    "compare my last lap with Leclerc in the rain", "compare my last lap with Leclerc and quit",
    "compare my last lap with ?", "compare my last lap with and",
])
def test_comparison_requests_do_not_broaden_other_facts(question):
    with pytest.raises(ValueError):
        plan_question(question, names=("leclerc",))


@pytest.mark.parametrize("elapsed,expected", [
    (99500, "0.500 seconds faster than you"),
    (100500, "0.500 seconds slower than you"),
    (100000, "matched your time"),
])
def test_integer_delta_direction(elapsed, expected):
    player = {"current_lap_number": 3, "last_lap_time_ms": 100000}
    opponent = {"current_lap_number": 3, "last_lap_time_ms": elapsed}
    assert timing_fact("lap_comparison", opponent, player) == "reported lap 2 " + expected
    assert player["last_lap_time_ms"] == 100000


@pytest.mark.parametrize("field,value", [
    ("last_lap_time_ms", None), ("last_lap_time_ms", 0), ("last_lap_time_ms", -1),
    ("last_lap_time_ms", True), ("last_lap_time_ms", 100000.0),
    ("last_lap_time_ms", 2**32), ("current_lap_number", None),
    ("current_lap_number", 1), ("current_lap_number", True),
    ("current_lap_number", 256), ("current_lap_number", 3.0),
])
@pytest.mark.parametrize("reference", [False, True])
def test_malformed_target_or_reference_abstains(field, value, reference):
    player = {"current_lap_number": 3, "last_lap_time_ms": 100000}
    opponent = deepcopy(player)
    (player if reference else opponent)[field] = value
    assert "unavailable" in lap_comparison(opponent, player)


def test_missing_reference_and_different_laps_abstain():
    opponent = {"current_lap_number": 3, "last_lap_time_ms": 100000}
    assert "reference missing" in lap_comparison(opponent, None)
    answer = lap_comparison(opponent, {"current_lap_number": 4, "last_lap_time_ms": 99000})
    assert "unavailable" in answer and "driver last completed lap 2" in answer
    assert "your last completed lap 3" in answer
    assert "faster" not in answer and "slower" not in answer


def test_multi_driver_comparisons_and_followup_keep_identity():
    controller, radio, events, value = named_live()
    controller.handle_request("compare my last lap with Leclerc and Norris", now=10)
    answer = radio.pending[-1].text
    assert "Charles Leclerc" in answer and "2.500 seconds faster than you" in answer
    assert "Lando Norris" in answer and "1.500 seconds faster than you" in answer
    radio.cancel()
    value["drivers"][1]["identity"]["tenure_ordinal"] += 1
    controller.feed(value, now=10.1)
    controller.handle_request("and last lap comparison?", now=10.1)
    assert "driver unavailable" in radio.pending[-1].text
    assert "Previously selected Lando Norris" in radio.pending[-1].text


def test_partial_missing_reference_does_not_hide_other_metrics():
    controller, radio, events, value = named_live()
    value["drivers"][0]["lap"]["last_lap_time_ms"] = 0
    controller.feed(value, now=10)
    controller.handle_request("Leclerc last lap comparison and position", now=10)
    assert "comparison unavailable" in radio.pending[-1].text
    assert "position 1" in radio.pending[-1].text


def test_recurring_comparison_waits_for_opponent_same_lap_and_uses_fresh_values():
    controller, radio, events, value = named_live()
    install(controller, radio, "report at end of each lap compare my last lap with Leclerc")
    finish_lap(controller, value)
    assert "comparison unavailable" in radio.pending[-1].text
    assert "driver last completed lap 2" in radio.pending[-1].text
    radio.cancel()
    value["drivers"][1]["lap"].update(current_lap_number=5, last_lap_time_ms=98500)
    finish_lap(controller, value, cursor=2, number=5, now=12)
    assert "reported lap 4 0.500 seconds faster than you" in radio.pending[-1].text


def test_dynamic_comparison_role_resolves_again_each_lap():
    controller, radio, events, value = named_live()
    install(controller, radio, "report at end of each lap compare my last lap with the leader")
    value["drivers"][1]["lap"].update(current_lap_number=4, last_lap_time_ms=100000)
    finish_lap(controller, value)
    assert "Charles Leclerc" in radio.pending[-1].text and "slower than you" in radio.pending[-1].text
    radio.cancel()
    value["drivers"][1]["lap"]["car_position"] = 3
    value["drivers"][2]["lap"].update(car_position=1, current_lap_number=5, last_lap_time_ms=98000)
    finish_lap(controller, value, cursor=2, number=5, now=12)
    assert "Lando Norris" in radio.pending[-1].text and "1.000 seconds faster than you" in radio.pending[-1].text


@pytest.mark.parametrize("pause", ["stale", "unavailable"])
def test_paused_comparisons_retain_tracking_but_require_fresh_values_after_resume(pause):
    controller, radio, events, value = named_live()
    install(controller, radio, "report at end of each lap compare my last lap with Leclerc")
    controller.handle_request("compare my last lap with Leclerc", now=10)
    tasks = controller.tasks.active()
    history = deepcopy(controller._history)
    if pause == "stale":
        controller.check_freshness(now=14)
    else:
        controller.feed({"status": "unavailable", "reason": "replay_paused"}, now=14)
    assert controller.tasks.active() == tasks and controller._history == history
    assert radio.scope is None and not radio.pending
    controller.handle_request("compare my last lap with Leclerc", now=14)
    assert "No fresh player telemetry" in radio.pending[-1].text
    assert "faster than you" not in radio.pending[-1].text
    controller.handle_request("report at end of each lap my position", now=14)
    assert controller.tasks.active() == tasks
    assert controller.status()["tasks"] == controller.tasks.views()
    value["drivers"][1]["lap"]["last_lap_time_ms"] = 100000
    value["events"] = []
    controller.feed(value, now=15)
    assert controller.tasks.active() == tasks
    controller.handle_request("compare my last lap with Leclerc", now=15)
    assert "3.500 seconds faster than you" in radio.pending[-1].text
    assert "2.500 seconds faster than you" not in radio.pending[-1].text
