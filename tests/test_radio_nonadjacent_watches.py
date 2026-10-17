from copy import deepcopy

import pytest

from tests.test_radio_watches import watch_live
from tests.test_radio_tasks import finish_lap


def connected(*, ahead=False):
    controller, radio, events, value = watch_live()
    value["context"] = {"status": "available", "value": {"session_uid": 99, "packet_format": 2025,
                                                          "track_id": 0, "track_length_m": 1000}}
    for row, position, offset in zip(value["drivers"], (4, 2, 1) if ahead else (2, 1, 4),
                                     (0, 100, 300) if ahead else (0, 500, -200)):
        row["lap"].update(car_position=position, current_lap_number=3,
                          total_distance_m=3000 + offset, delta_to_race_leader_ms=0)
    controller.feed(value, now=10)
    return controller, radio, events, value


def step(controller, radio, value, number, gap, *, ahead=False, rival_number=None, edit=None):
    radio.cancel()
    for index, row in enumerate(value["drivers"]):
        row["lap"]["current_lap_number"] = number
        row["lap"]["total_distance_m"] = number * 1000 + ((0, 100, 300) if ahead else (0, 500, -200))[index]
    value["drivers"][0]["lap"]["delta_to_race_leader_ms"] = gap if ahead else 5000
    value["drivers"][1]["lap"]["delta_to_race_leader_ms"] = gap // 2 if ahead else 0
    value["drivers"][2]["lap"]["delta_to_race_leader_ms"] = 0 if ahead else 5000 + gap
    if rival_number is not None:
        value["drivers"][2]["lap"]["current_lap_number"] = rival_number
    if edit is not None:
        edit(value)
    finish_lap(controller, value, number=number, cursor=number - 3, now=number + 7)


@pytest.mark.parametrize("ahead,phrase", [(False, "driver behind is closing"), (True, "You are catching")])
def test_named_nonadjacent_driver_supports_three_boundary_trend_with_explicit_gap_provenance(ahead, phrase):
    controller, radio, events, value = connected(ahead=ahead)
    for number, gap in enumerate((3000, 2700, 2400), start=4):
        step(controller, radio, value, number, gap, ahead=ahead)
    text = radio.pending[-1].text
    assert phrase in text and "0.600 seconds over two observed laps" in text
    assert "now 2.400 seconds" in text and "Derived from same-frame gaps to the same race leader" in text
    assert controller.watches.views()[0]["gap_basis"] == "same-frame leader-gap difference"
    assert controller.tasks.active()[0].fixed_bindings[0] is not None


@pytest.mark.parametrize("fault,reason", [
    ("length", "lap-distance alignment unavailable"), ("distance", "lap-distance alignment unavailable"),
    ("nan", "lap-distance alignment unavailable"), ("order", "position and distance disagree"),
    ("lapped", "lapped"), ("leader_gap", "leader-gap timing unavailable"),
    ("zero_gap", "leader-gap timing unavailable"), ("gap_order", "gaps disagree"),
    ("leader_pit", "leader reference unavailable"), ("leader_status", "leader reference unavailable"),
    ("leader_number", "leader reference unavailable"), ("leader_missing", "leader reference unavailable"),
    ("duplicate_leader", "leader reference unavailable"), ("pit", "pit or racing status"),
])
def test_missing_or_inconsistent_shared_reference_pauses_instead_of_claiming_closing(fault, reason):
    controller, radio, events, value = connected()
    step(controller, radio, value, 4, 3000)
    step(controller, radio, value, 5, 2700)
    def edit(current):
        player, leader, rival = current["drivers"]
        if fault == "length":
            current["context"]["status"] = "unavailable"
        elif fault == "distance":
            rival["lap"].pop("total_distance_m")
        elif fault == "nan":
            rival["lap"]["total_distance_m"] = float("nan")
        elif fault == "order":
            rival["lap"]["total_distance_m"] = player["lap"]["total_distance_m"] + 200
        elif fault == "lapped":
            rival["lap"]["total_distance_m"] = player["lap"]["total_distance_m"] - 1000
        elif fault == "leader_gap":
            rival["lap"]["delta_to_race_leader_ms"] = None
        elif fault == "zero_gap":
            rival["lap"]["delta_to_race_leader_ms"] = 0
        elif fault == "gap_order":
            rival["lap"]["delta_to_race_leader_ms"] = 4000
        elif fault == "leader_pit":
            leader["lap"]["pit_status_id"] = 1
        elif fault == "leader_status":
            leader["lap"]["result_status_id"] = 3
        elif fault == "leader_number":
            leader["lap"]["current_lap_number"] = None
        elif fault == "leader_missing":
            leader["lap"]["car_position"] = 3
        elif fault == "duplicate_leader":
            duplicate = deepcopy(leader)
            duplicate["car_index"] = duplicate["identity"]["car_index"] = 3
            duplicate["identity"]["participant_identity_fingerprint"] = "d" * 64
            current["drivers"].append(duplicate)
        else:
            rival["lap"]["pit_status_id"] = 1
    step(controller, radio, value, 6, 2400, edit=edit)
    assert "Watch paused" in radio.pending[-1].text and reason in radio.pending[-1].text
    assert "is closing" not in radio.pending[-1].text
    assert controller.watches.views()[0]["gap_basis"] is None


def test_nonadjacent_behind_finish_line_phase_is_not_mistaken_for_a_lapped_driver():
    controller, radio, events, value = connected()
    for number, gap in enumerate((3000, 2700, 2400), start=4):
        step(controller, radio, value, number, gap, rival_number=number - 1)
    assert "driver behind is closing" in radio.pending[-1].text


def test_physically_lapped_adjacent_driver_is_not_admitted_by_one_lap_number_difference():
    controller, radio, events, value = connected()
    value["drivers"][2]["lap"]["car_position"] = 3
    step(controller, radio, value, 4, 3000, rival_number=3,
         edit=lambda current: current["drivers"][2]["lap"].update(total_distance_m=3000))
    assert "lapped" in radio.pending[-1].text and "is closing" not in radio.pending[-1].text


def test_gap_source_change_from_shared_leader_to_adjacent_restarts_warmup():
    controller, radio, events, value = connected()
    step(controller, radio, value, 4, 3000)
    step(controller, radio, value, 5, 2700)
    value["drivers"][2]["lap"].update(car_position=3, delta_to_car_in_front_ms=2400)
    step(controller, radio, value, 6, 2400)
    assert "collecting three" in radio.pending[-1].text
    assert controller.watches.views()[0]["gap_basis"] == "direct adjacent gap"


def test_changed_shared_leader_identity_resets_trend_without_rebinding_watched_driver():
    controller, radio, events, value = connected()
    step(controller, radio, value, 4, 3000)
    step(controller, radio, value, 5, 2700)
    watched = controller.tasks.active()[0].fixed_bindings[0]
    value["drivers"][1]["identity"]["tenure_ordinal"] += 1
    step(controller, radio, value, 6, 2400)
    assert "collecting three" in radio.pending[-1].text
    assert controller.tasks.active()[0].fixed_bindings[0] == watched
