from copy import deepcopy
import json

import pytest

from f1_engineer.radio.questions import QuestionPlan
from f1_engineer.radio.regions import RegionWindow
from f1_engineer.radio.tasks import LapReportRegistry


def identity(car_index):
    return json.dumps({"car_index": car_index, "tenure_ordinal": 1,
                       "participant_identity_fingerprint": str(car_index) * 64})


def arguments():
    return {"plan": QuestionPlan(("self", "name:leclerc"), ("opponent_regional_observations",)),
            "bindings": (identity(0), identity(1)), "cursor": 4, "lap": 4, "laps": 2,
            "opponent_first_lap": 3, "region_window": RegionWindow(10, 50, 100)}


def installed():
    registry = LapReportRegistry()
    task = registry.install(**arguments())
    return registry, task


def test_player_lap_limit_cannot_expire_pair_before_opponent_completion():
    registry, task = installed()
    registry.complete_lap(5)
    registry.complete_lap(6)
    assert registry.active() == (task,)
    assert registry.eligible(event_id=9, lap=4) == ()
    assert task.opponent_first_lap == 3 and task.reported_through_lap == 3
    assert registry.consume_region_pair(task.task_id, task.version, 4)
    assert registry.active()[0].reported_through_lap == 4
    assert registry.consume_region_pair(task.task_id, task.version, 5)
    assert not registry.active()
    assert registry.views()[0]["state"] == "completed"


def test_pair_consumption_is_sequential_exactly_once_and_version_bound():
    registry, task = installed()
    assert not registry.consume_region_pair(1, 1, 5)
    assert not registry.consume_region_pair(1, 2, 4)
    assert registry.consume_region_pair(1, 1, 4)
    assert not registry.consume_region_pair(1, 1, 4)
    assert registry.views()[0]["reported_through_lap"] == 4


def test_update_discards_old_windows_and_consumption_authority():
    registry, task = installed()
    assert registry.consume_region_pair(1, 1, 4)
    options = arguments()
    options.update(task_id=1, lap=6, opponent_first_lap=5)
    updated = registry.install(**options)
    assert updated.version == 2 and updated.reported_through_lap == 5
    assert not registry.consume_region_pair(1, 1, 5)
    assert not registry.end_region_pair(1, 1, "old_capture_failed")
    assert registry.end_region_pair(1, 2, "capture_failed")
    assert not registry.end_region_pair(1, 2, "capture_failed")


def test_completed_pair_cannot_be_revived_or_reconsumed():
    registry, task = installed()
    registry.end_region_pair(1, 1, "identity_changed")
    assert not registry.consume_region_pair(1, 1, 4)
    with pytest.raises(ValueError, match="ended"):
        registry.install(**{**arguments(), "task_id": 1})


def test_pair_extension_requires_both_current_laps_without_mutating_active_windows():
    registry, task = installed()
    before = registry.views()
    with pytest.raises(ValueError, match="independent lap windows"):
        registry.extend(1, 1, additional_laps=2, cursor=5, lap=5)
    assert registry.views() == before


@pytest.mark.parametrize("player,opponent,first,until,opponent_first", [
    (3, 2, 4, 7, 3), (4, 3, 5, 7, 4), (5, 2, 6, 7, 3), (6, 4, 7, 8, 5),
])
def test_pair_extension_restarts_independent_full_windows_without_rebinding(player, opponent, first, until, opponent_first):
    registry, task = installed()
    updated = registry.extend(1, 1, additional_laps=2, cursor=8, lap=player, opponent_lap=opponent)
    assert updated.version == 2 and updated.first_lap == first and updated.until_lap == until
    assert updated.opponent_first_lap == opponent_first and updated.reported_through_lap == first - 1
    assert updated.fixed_bindings == task.fixed_bindings and updated.region_window == task.region_window
    assert not registry.consume_region_pair(1, 1, 4)


@pytest.mark.parametrize("options", [
    {"opponent_lap": None}, {"opponent_lap": True}, {"opponent_lap": 0},
    {"opponent_lap": 255}, {"lap": 255}, {"opponent_lap": 254},
    {"additional_laps": 21}, {"additional_laps": 19}, {"version": 2},
    {"lap": 2}, {"opponent_lap": 1},
])
def test_invalid_pair_extension_keeps_existing_version_and_windows(options):
    registry, task = installed()
    arguments = {"task_id": 1, "version": 1, "additional_laps": 2, "cursor": 8, "lap": 3, "opponent_lap": 2}
    arguments.update(options)
    before = registry.views()
    with pytest.raises(ValueError):
        registry.extend(**arguments)
    assert registry.views() == before


def test_completed_pair_and_cancelled_pair_cannot_be_extended():
    registry, task = installed()
    registry.end_region_pair(1, 1, "capture_failed")
    with pytest.raises(ValueError, match="no longer active"):
        registry.extend(1, 1, additional_laps=2, cursor=8, lap=5, opponent_lap=3)
    registry.cancel(1)
    with pytest.raises(ValueError, match="no longer active"):
        registry.extend(1, 1, additional_laps=2, cursor=8, lap=5, opponent_lap=3)


def test_consumed_pairs_cannot_be_extended_using_earlier_driver_laps():
    registry, task = installed()
    assert registry.consume_region_pair(1, 1, 4)
    for player, opponent in ((4, 3), (5, 2)):
        with pytest.raises(ValueError, match="backwards"):
            registry.extend(1, 1, additional_laps=2, cursor=8, lap=player, opponent_lap=opponent)
    updated = registry.extend(1, 1, additional_laps=2, cursor=8, lap=5, opponent_lap=4)
    assert updated.first_lap == 6 and updated.opponent_first_lap == 5


def test_cancel_all_scope_clear_removes_pair_authority():
    registry, task = installed()
    registry.clear("scope_changed")
    assert not registry.consume_region_pair(1, 1, 4)
    assert not registry.end_region_pair(1, 1, "capture_failed")


def test_same_pair_request_is_deduplicated_but_distinct_independent_laps_are_not_rebound():
    registry, task = installed()
    assert registry.install(**arguments()) == task
    options = arguments()
    options.update(lap=5, laps=1, opponent_first_lap=4)
    with pytest.raises(ValueError, match="three regional collection leases"):
        registry.install(**options)
    assert registry.active() == (task,)


def test_pair_shares_three_lease_budget_with_single_player_regions():
    registry, task = installed()
    player = {"plan": QuestionPlan(("self",), ("regional_observations",)), "bindings": (None,),
              "cursor": 4, "lap": 4, "laps": 2, "region_window": RegionWindow(20, 40, 100)}
    registry.install(**player)
    assert len(registry.active()) == 2
    with pytest.raises(ValueError, match="three regional collection leases"):
        registry.install(**{**player, "region_window": RegionWindow(30, 50, 100)})


@pytest.mark.parametrize("mutation", [
    lambda options: options.update(opponent_first_lap=None),
    lambda options: options.update(opponent_first_lap=True),
    lambda options: options.update(opponent_first_lap=1),
    lambda options: options.update(opponent_first_lap=255),
    lambda options: options.update(lap=1),
    lambda options: options.update(laps=None),
    lambda options: options.update(region_window=None),
    lambda options: options.update(bindings=(None, identity(1))),
    lambda options: options.update(bindings=(identity(0), identity(0))),
    lambda options: options.update(bindings=(identity(0), "slot:1")),
    lambda options: options.update(bindings=(identity(0), json.dumps({"car_index": True, "tenure_ordinal": 1,
                                                                   "participant_identity_fingerprint": "1" * 64}))),
    lambda options: options.update(plan=QuestionPlan(("self", "position:2"), ("opponent_regional_observations",))),
    lambda options: options.update(plan=QuestionPlan(("self", True), ("opponent_regional_observations",))),
    lambda options: options.update(plan=QuestionPlan(("self", "name:"), ("opponent_regional_observations",))),
    lambda options: options.update(plan=QuestionPlan(("self", "name:leclerc"), ("opponent_regional_observations", "position"))),
])
def test_invalid_pair_installation_never_changes_registry(mutation):
    registry = LapReportRegistry()
    options = arguments()
    mutation(options)
    with pytest.raises(ValueError):
        registry.install(**options)
    assert registry.views() == []


def test_unrelated_report_cannot_own_an_opponent_lap_boundary():
    registry = LapReportRegistry()
    options = arguments()
    options.update(plan=QuestionPlan(("self",), ("last_lap",)), bindings=(None,), region_window=None)
    with pytest.raises(ValueError, match="Only a paired regional"):
        registry.install(**options)


@pytest.mark.parametrize("identifier,version,lap", [(True, 1, 4), ([], 1, 4), (1, True, 4), (1, 1, True), (1, 1, 3)])
def test_invalid_consumption_or_end_arguments_do_not_change_state(identifier, version, lap):
    registry, task = installed()
    before = deepcopy(registry.views())
    assert not registry.consume_region_pair(identifier, version, lap)
    assert not registry.end_region_pair(identifier, version, "../invalid")
    assert registry.views() == before
