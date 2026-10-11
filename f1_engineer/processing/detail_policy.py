from __future__ import annotations

import math
from dataclasses import dataclass

from ..sessions.car_lap_inventory import CarSlotTenure
from ..sessions.context import SessionContext, SessionType
from ..udp.lap_data import CarLapData


@dataclass(frozen=True, slots=True)
class DetailDemandTarget:
    task_id: str
    tenure: CarSlotTenure


@dataclass(frozen=True, slots=True)
class DetailPolicyFrame:
    session_uid: int
    frame_ordinal: int
    packet_format: int
    lifecycle_epoch: int
    player_car_index: int | None
    lap_data: tuple[CarLapData, ...]
    context: SessionContext | None
    association_scope_assessable: bool
    lap_data_conflicted: bool
    verified_tenures: tuple[CarSlotTenure, ...]
    demands: tuple[DetailDemandTarget, ...] = ()
    frame_conflicted: bool = False


@dataclass(frozen=True, slots=True)
class DetailSelection:
    cars: tuple[int, ...]
    reasons: tuple[tuple[int, tuple[str, ...]], ...]

    def reasons_for(self, car_index: int) -> tuple[str, ...]:
        return next((reasons for car, reasons in self.reasons if car == car_index), ())


RACE_TYPES = frozenset((SessionType.RACE, SessionType.RACE_2, SessionType.RACE_3))
TRAFFIC_TYPES = frozenset((
    SessionType.PRACTICE_1, SessionType.PRACTICE_2, SessionType.PRACTICE_3,
    SessionType.SHORT_PRACTICE, SessionType.QUALIFYING_1, SessionType.QUALIFYING_2,
    SessionType.QUALIFYING_3, SessionType.SHORT_QUALIFYING,
    SessionType.ONE_SHOT_QUALIFYING, SessionType.SPRINT_SHOOTOUT_1,
    SessionType.SPRINT_SHOOTOUT_2, SessionType.SPRINT_SHOOTOUT_3,
    SessionType.SHORT_SPRINT_SHOOTOUT, SessionType.ONE_SHOT_SPRINT_SHOOTOUT,
))


def _tenure_is_current(tenure: CarSlotTenure, frame: DetailPolicyFrame) -> bool:
    return (
        tenure.session_uid == frame.session_uid
        and tenure.packet_format == frame.packet_format
        and tenure.lifecycle_epoch == frame.lifecycle_epoch
        and tenure.close_reason == "active"
        and tenure.start_frame_ordinal <= frame.frame_ordinal < tenure.end_frame_ordinal_exclusive
        and 0 <= tenure.car_index < len(frame.lap_data)
    )


def select_detail(frame: DetailPolicyFrame) -> DetailSelection:
    """Select detailed observations from one internally consistent live frame."""
    reasons: dict[int, set[str]] = {}

    def add(car_index: int, reason: str) -> None:
        reasons.setdefault(car_index, set()).add(reason)

    player = frame.player_car_index
    if player is not None and 0 <= player < len(frame.lap_data):
        add(player, "player")

    by_car: dict[int, list[CarSlotTenure]] = {}
    for tenure in frame.verified_tenures:
        if _tenure_is_current(tenure, frame):
            by_car.setdefault(tenure.car_index, []).append(tenure)
    tenures = {car: rows[0] for car, rows in by_car.items() if len(rows) == 1}
    context = frame.context
    session_type = context.session_type if context is not None else None
    if (
        frame.association_scope_assessable
        and not frame.lap_data_conflicted
        and player in tenures
        and player is not None
        and 0 <= player < len(frame.lap_data)
    ):
        if session_type in RACE_TYPES:
            player_position = frame.lap_data[player].car_position
            eligible = [
                (index, data.car_position)
                for index, data in enumerate(frame.lap_data)
                if index != player and index in tenures and data.result_status_id == 2
                and data.car_position > 0
            ]
            positions = [position for _, position in eligible]
            if (player_position > 0 and frame.lap_data[player].result_status_id == 2
                    and len(set((*positions, player_position))) == len(positions) + 1):
                ahead = [(position, index) for index, position in eligible if position < player_position]
                behind = [(position, index) for index, position in eligible if position > player_position]
                if ahead:
                    add(max(ahead)[1], "race_ahead")
                if behind:
                    add(min(behind)[1], "race_behind")
        elif (session_type in TRAFFIC_TYPES and not frame.frame_conflicted
              and context.track_length_m > 0):
            length = float(context.track_length_m)
            player_distance = frame.lap_data[player].lap_distance_m
            if math.isfinite(length) and math.isfinite(player_distance):
                player_distance %= length
                ahead: list[tuple[float, int]] = []
                behind: list[tuple[float, int]] = []
                for index, data in enumerate(frame.lap_data):
                    if index == player or index not in tenures:
                        continue
                    if (data.result_status_id != 2 or data.pit_status_id != 0
                            or data.driver_status_id not in (1, 4)
                            or not math.isfinite(data.lap_distance_m)):
                        continue
                    distance = data.lap_distance_m % length
                    forward = (distance - player_distance) % length
                    reverse = (player_distance - distance) % length
                    if forward == 0 or reverse == 0:
                        continue
                    ahead.append((forward, index))
                    behind.append((reverse, index))
                for candidates, reason in ((ahead, "traffic_ahead"), (behind, "traffic_behind")):
                    if not candidates:
                        continue
                    nearest = min(distance for distance, _ in candidates)
                    matches = [index for distance, index in candidates if distance == nearest]
                    if len(matches) == 1:
                        add(matches[0], reason)

    for demand in frame.demands:
        if not frame.association_scope_assessable:
            continue
        tenure = demand.tenure
        matches = [current for current in tenures.values()
                   if current.session_uid == tenure.session_uid
                   and current.packet_format == tenure.packet_format
                   and current.lifecycle_epoch == tenure.lifecycle_epoch
                   and current.car_index == tenure.car_index
                   and current.tenure_ordinal == tenure.tenure_ordinal
                   and current.participant_identity_fingerprint == tenure.participant_identity_fingerprint]
        if len(matches) != 1:
            continue
        add(matches[0].car_index, "task")

    ordered = tuple(sorted(reasons))
    return DetailSelection(
        cars=ordered,
        reasons=tuple((car, tuple(sorted(reasons[car]))) for car in ordered),
    )
