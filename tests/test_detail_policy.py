from dataclasses import replace

import pytest

from f1_engineer.processing.detail_policy import (
    DetailDemandTarget, DetailPolicyFrame, select_detail, TRAFFIC_TYPES,
)
from f1_engineer.sessions.car_lap_inventory import CarSlotTenure
from f1_engineer.sessions.context import GameMode, RuleSet, SessionContext, SessionType
from f1_engineer.udp.lap_data import CarLapData
from f1_engineer.udp.models import PacketFormat


def lap(*, position=1, distance=0.0, result=2, pit=0, driver=1):
    return CarLapData(0, 0, 0, 0, 0, 0, distance, distance, 0.0, position, 1,
                      pit, 0, 0, 0, 0, 0, 0, 0, 0, position, driver, result,
                      False, 0, 0, False, 0.0, 0)


def context(kind=SessionType.RACE, length=1000):
    return SessionContext(99, PacketFormat.F1_25, 1, 0, None, 20, 20, 10,
                          length, 0, kind, 0, None, 0, 0, 0, GameMode.GRAND_PRIX_23,
                          0, RuleSet.RACE if kind in (SessionType.RACE, SessionType.RACE_2,
                          SessionType.RACE_3) else RuleSet.PRACTICE_QUALIFYING,
                          0, 0, 0, 0)


def tenure(car, *, session=99, epoch=2, fmt=2025, ordinal=1):
    return CarSlotTenure(session, fmt, epoch, car, 1, 1, 100, 0, "wire", f"identity-{car}-{ordinal}", "active")


def frame(data, *, kind=SessionType.RACE, positions=None, distances=None, valid=None,
          player=0, scope=True, conflicted=False, length=1000, tasks=(), format=2025):
    count = len(data)
    positions = positions or list(range(1, count + 1))
    distances = distances or [row.lap_distance_m for row in data]
    data = tuple(replace(row, car_position=positions[index], lap_distance_m=distances[index])
                 for index, row in enumerate(data))
    valid = set(range(count)) if valid is None else set(valid)
    tenures = tuple(tenure(index, fmt=format) for index in sorted(valid))
    return DetailPolicyFrame(99, 5, format, 2, player, data, context(kind, length), scope,
                             conflicted, tenures, tuple(tasks))


def test_P01_race_uses_position_order_not_physical_distance():
    source = frame([lap() for _ in range(5)], positions=[3, 4, 2, 1, 5],
                   distances=[500, 501, 1, 2, 3])
    selected = select_detail(source)
    assert selected.cars == (0, 1, 2)
    assert selected.reasons_for(1) == ("race_behind",)
    assert selected.reasons_for(2) == ("race_ahead",)


@pytest.mark.parametrize("positions,expected", [([1, 2, 3], (0, 1)), ([3, 2, 1], (0, 1)), ([1], (0,))])
def test_P02_race_field_edges_and_single_car(positions, expected):
    result = select_detail(frame([lap() for _ in positions], positions=positions))
    assert result.cars == expected


def test_P03_lapped_and_pitting_race_neighbor_remains_but_inactive_does_not():
    source = frame([lap() for _ in range(4)], positions=[2, 1, 3, 4], valid={0, 1, 2})
    source = replace(source, lap_data=(source.lap_data[0], replace(source.lap_data[1], pit_status_id=2),
                                       source.lap_data[2], source.lap_data[3]))
    assert select_detail(source).cars == (0, 1, 2)


@pytest.mark.parametrize("invalid_position", [0, -1])
def test_P04_invalid_duplicate_conflicted_and_unassessable_suppress_auto(invalid_position):
    base = frame([lap() for _ in range(4)], positions=[2, 1, 3, 4])
    assert select_detail(replace(base, lap_data=(replace(base.lap_data[0], car_position=invalid_position),
                                                  *base.lap_data[1:]))).cars == (0,)
    duplicate = replace(base, lap_data=(base.lap_data[0], base.lap_data[1],
                                         replace(base.lap_data[2], car_position=1), base.lap_data[3]))
    assert select_detail(duplicate).cars == (0,)
    assert select_detail(replace(base, lap_data_conflicted=True)).cars == (0,)
    assert select_detail(replace(base, association_scope_assessable=False)).cars == (0,)


@pytest.mark.parametrize("kind", sorted(TRAFFIC_TYPES, key=lambda item: item.value))
def test_P05_every_practice_qualifying_shootout_uses_circular_traffic(kind):
    source = frame([lap() for _ in range(4)], kind=kind, positions=[4, 1, 2, 3],
                   distances=[5, 990, 200, 800])
    selection = select_detail(source)
    assert selection.cars == (0, 1, 2)
    assert selection.reasons_for(1) == ("traffic_behind",)
    assert selection.reasons_for(2) == ("traffic_ahead",)


def test_P06_traffic_exclusions_dedup_coincidence_and_equal_nearest():
    base = frame([lap() for _ in range(5)], kind=SessionType.PRACTICE_1,
                 distances=[10, 100, 900, 10, 100])
    excluded = replace(base, lap_data=(base.lap_data[0], replace(base.lap_data[1], pit_status_id=1),
                                       replace(base.lap_data[2], driver_status_id=2),
                                       replace(base.lap_data[3], result_status_id=1), base.lap_data[4]))
    excluded_result = select_detail(excluded)
    assert excluded_result.cars == (0, 4)
    assert excluded_result.reasons_for(4) == ("traffic_ahead", "traffic_behind")
    single = frame([lap(), lap()], kind=SessionType.PRACTICE_1, distances=[10, 400])
    result = select_detail(single)
    assert result.cars == (0, 1)
    assert result.reasons_for(1) == ("traffic_ahead", "traffic_behind")
    coincident = frame([lap(), lap()], kind=SessionType.PRACTICE_1, distances=[10, 1010])
    assert select_detail(coincident).cars == (0,)
    tied = frame([lap() for _ in range(4)], kind=SessionType.PRACTICE_1,
                 distances=[0, 100, 900, 100])
    assert select_detail(tied).cars == (0, 2)
    assert select_detail(tied).reasons_for(2) == ("traffic_behind",)


@pytest.mark.parametrize("kind", [SessionType.TIME_TRIAL, SessionType.UNKNOWN, None])
def test_P07_tt_and_unknown_have_no_automatic_selection(kind):
    assert select_detail(frame([lap() for _ in range(4)], kind=kind)).cars == (0,)


def test_P08_invalid_geometry_suppresses_traffic_but_keeps_task():
    base = frame([lap() for _ in range(3)], kind=SessionType.PRACTICE_1, distances=[0, 100, 900])
    current = base.verified_tenures[1]
    task = DetailDemandTarget("task-a", current)
    broken = replace(base, context=context(SessionType.PRACTICE_1, 0), demands=(task,))
    assert select_detail(broken).cars == (0, 1)
    nan_player = replace(base, lap_data=(replace(base.lap_data[0], lap_distance_m=float("nan")),
                                         *base.lap_data[1:]), demands=(task,))
    assert select_detail(nan_player).cars == (0, 1)


def test_P09_task_overlap_deduplicates_and_invalid_tenure_is_never_selected():
    base = frame([lap() for _ in range(4)], positions=[2, 1, 3, 4])
    task = DetailDemandTarget("task-a", base.verified_tenures[1])
    assert select_detail(replace(base, demands=(task,))).cars == (0, 1, 2)
    invalid = replace(task, tenure=replace(task.tenure, participant_identity_fingerprint="stale"))
    assert select_detail(replace(base, demands=(invalid,))).cars == (0, 1, 2)


@pytest.mark.parametrize("count", [22, 24])
def test_P10_selection_order_and_reason_order_are_stable(count):
    base = frame([lap() for _ in range(count)], positions=[1] + list(range(count, 1, -1)))
    tasks = tuple(DetailDemandTarget(f"t-{index}", base.verified_tenures[index]) for index in (0, 1, 2))
    selected = select_detail(replace(base, demands=tasks))
    assert selected.cars == tuple(sorted(selected.cars))
    assert all(tuple(sorted(selected.reasons_for(car))) == selected.reasons_for(car) for car in selected.cars)
