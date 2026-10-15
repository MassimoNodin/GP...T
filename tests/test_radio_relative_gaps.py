import pytest

from f1_engineer.radio.gaps import relative_gap_ms
from f1_engineer.radio.questions import plan_question
from tests.test_radio_gap_changes import gap_live


@pytest.mark.parametrize("phrase,target,metric", [
    ("my gap to the car ahead", "self", "front_gap"),
    ("my gap to the leader", "self", "leader_gap"),
    ("what's the gap to Leclerc?", "name:leclerc", "relative_gap"),
    ("how far is Leclerc behind me?", "name:leclerc", "relative_gap"),
    ("who's behind Leclerc?", "name:leclerc", "car_behind"),
    ("who's ahead of Leclerc?", "name:leclerc", "car_ahead"),
    ("Leclerc gap to me", "name:leclerc", "relative_gap"),
    ("Leclerc car ahead", "name:leclerc", "car_ahead"),
])
def test_gap_target_semantics(phrase, target, metric):
    plan = plan_question(phrase, names=("Leclerc",))
    assert plan.targets == (target,)
    assert plan.metrics == (metric,)


@pytest.mark.parametrize("gap,expected", [(5000, 3000), (1000, -1000), (2000, 0)])
def test_relative_gap_subtracts_player_leader_gap(gap, expected):
    controller, radio, events, value = gap_live()
    player, target = value["drivers"][:2]
    player["lap"].update(current_lap_number=4, delta_to_race_leader_ms=2000)
    target["lap"].update(current_lap_number=4, delta_to_race_leader_ms=gap)
    assert relative_gap_ms(player, target) == expected


@pytest.mark.parametrize("field,value", [("pit_status_id", 1), ("result_status_id", 7),
    ("current_lap_number", 3), ("delta_to_race_leader_ms", None)])
def test_incomparable_gaps_are_unavailable(field, value):
    controller, radio, events, snapshot = gap_live()
    player, target = snapshot["drivers"][:2]
    player["lap"].update(current_lap_number=4, delta_to_race_leader_ms=2000)
    target["lap"].update(current_lap_number=4, delta_to_race_leader_ms=5000)
    target["lap"][field] = value
    assert relative_gap_ms(player, target) is None


def test_relative_and_neighbour_answers_use_the_requested_pair():
    controller, radio, events, value = gap_live()
    for index, row in enumerate(value["drivers"]):
        row["lap"].update(current_lap_number=4, delta_to_race_leader_ms=index * 2000)
    controller.feed(value, now=10)
    target = value["drivers"][1]
    gap = relative_gap_ms(value["drivers"][0], target)
    text = controller._timing_fact("relative_gap", target, now=10)
    assert f"{abs(gap) / 1000:.3f} seconds" in text
    assert "behind you" in text
    position = target["lap"]["car_position"]
    neighbour = next((row for row in value["drivers"] if row["lap"]["car_position"] == position + 1), None)
    text = controller._timing_fact("car_behind", target, now=10)
    if neighbour is not None:
        assert controller._participant_name(neighbour).title() in text
        assert "2.000 seconds" in text
    else:
        assert "unavailable" in text


def test_gap_reports_track_the_player_not_the_leader():
    controller, radio, events, value = gap_live()
    controller.handle_request("tell me at the end of each lap my gap to the leader", now=10)
    assert controller.tasks.active()[0].plan.targets == ("self",)
    assert controller.tasks.active()[0].plan.metrics == ("leader_gap",)
