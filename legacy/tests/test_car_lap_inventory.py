from __future__ import annotations

import struct
from types import SimpleNamespace

import pytest

from f1_engineer.sessions.context import GameMode, RuleSet, SessionContext, SessionType
from f1_engineer.sessions.car_lap_inventory import CarLapInventoryTracker
from f1_engineer.storage.database import Database
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.lap_data import CarLapData
from f1_engineer.udp.models import PacketFormat
from tests.helpers import make_datagram


_PARTICIPANT = struct.Struct("<7B32s2BH14B")
_PARTICIPANT_2026 = struct.Struct("<BHHHBBB32sBBHBB12B")
_SESSION_UID = 8181


def _participants_body(
    *,
    active_count: int = 22,
    driver_ids: dict[int, int] | None = None,
    slot_count: int = 22,
    packet_format: int = 2025,
) -> bytes:
    driver_ids = driver_ids or {}
    records = []
    for index in range(slot_count):
        name = f"Driver {index}".encode().ljust(32, b"\0")
        values = (
                0,
                driver_ids.get(index, index + 1),
                0,
                2,
                0,
                index + 1,
                1,
                name,
                0,
                1,
                50,
                0,
                3,
                *([0] * 12),
        )
        record = _PARTICIPANT_2026 if packet_format == 2026 else _PARTICIPANT
        records.append(record.pack(*values))
    return bytes((active_count,)) + b"".join(records)


def _participants_packet(
    frame: int,
    *,
    driver_ids: dict[int, int] | None = None,
    packet_format: int = 2025,
    active_count: int = 22,
    slot_count: int = 22,
):
    raw = make_datagram(
        packet_format=packet_format,
        packet_id=4,
        session_uid=_SESSION_UID,
        frame=frame,
        session_time=frame / 60,
        body=_participants_body(
            driver_ids=driver_ids,
            active_count=active_count,
            slot_count=slot_count,
            packet_format=packet_format,
        ),
        sequence=frame,
    )
    return PacketDecoder().decode(raw)


def _car_lap(
    *,
    lap_number: int = 1,
    distance: float = 10.0,
    last_lap_ms: int = 0,
    current_lap_ms: int = 1_000,
    driver_status: int = 1,
) -> CarLapData:
    return CarLapData(
        last_lap_time_ms=last_lap_ms,
        current_lap_time_ms=current_lap_ms,
        sector1_time_ms=0,
        sector2_time_ms=0,
        delta_to_car_in_front_ms=0,
        delta_to_race_leader_ms=0,
        lap_distance_m=distance,
        total_distance_m=distance,
        safety_car_delta_s=0.0,
        car_position=2,
        current_lap_number=lap_number,
        pit_status_id=0,
        pit_stop_count=0,
        sector_id=0,
        current_lap_invalid_id=0,
        penalties_s=0,
        total_warnings=0,
        corner_cutting_warnings=0,
        drive_through_penalties_remaining=0,
        stop_go_penalties_remaining=0,
        grid_position=2,
        driver_status_id=driver_status,
        result_status_id=2,
        pit_lane_timer_active=False,
        pit_lane_time_ms=0,
        pit_stop_timer_ms=0,
        pit_stop_should_serve_penalty=False,
        speed_trap_fastest_speed_kph=0.0,
        speed_trap_fastest_lap_number=0,
    )


def _lap_data(
    slot_data: CarLapData, *, slot_index: int = 1, slot_count: int = 22
) -> tuple[CarLapData, ...]:
    inactive = _car_lap(driver_status=0)
    cars = [inactive for _ in range(slot_count)]
    cars[slot_index] = slot_data
    return tuple(cars)


def _observe(
    tracker: CarLapInventoryTracker,
    frame: int,
    *,
    lap: CarLapData | None = None,
    participants=(),
    conflicted: bool = False,
    context: SessionContext | None = None,
    timeline=(),
    packet_format: int = 2025,
    lifecycle_epoch: int = 0,
    slot_index: int = 1,
    slot_count: int = 22,
) -> None:
    tracker.observe_frame(
        session_uid=_SESSION_UID,
        frame_ordinal=frame,
        packet_format=packet_format,
        lifecycle_epoch=lifecycle_epoch,
        player_car_index=0,
        session_time_s=frame / 60,
        frame_identifier=frame,
        packets=participants,
        lap_data=(
            _lap_data(lap, slot_index=slot_index, slot_count=slot_count)
            if lap is not None
            else None
        ),
        lap_data_conflicted=conflicted,
        association_scope_assessable=True,
        context=context,
        timeline_provider=lambda _start, _end: timeline,
    )


def _time_trial_context(air_temperature_c: int) -> SessionContext:
    return SessionContext(
        session_uid=_SESSION_UID,
        packet_format=PacketFormat.F1_25,
        packet_version=1,
        weather_id=0,
        weather_name=None,
        track_temperature_c=25,
        air_temperature_c=air_temperature_c,
        total_laps=0,
        track_length_m=5_000,
        session_type_id=19,
        session_type=SessionType.TIME_TRIAL,
        track_id=1,
        track_name="Bahrain",
        formula_id=0,
        network_game_id=0,
        game_mode_id=2,
        game_mode=GameMode.TIME_TRIAL,
        rule_set_id=2,
        rule_set=RuleSet.TIME_TRIAL,
        steering_assist_id=0,
        braking_assist_id=0,
        gearbox_assist_id=0,
        equal_car_performance_id=0,
    )


def test_active_tenure_snapshots_are_cached_bounded_and_invalidated_by_observation():
    tracker = CarLapInventoryTracker()
    _observe(tracker, 1, participants=(_participants_packet(1),), lap=_car_lap())
    first = tracker.active_tenures()
    assert first and tracker.active_tenures() is first
    for frame in range(2, 10):
        snapshot = tracker.active_tenures({_SESSION_UID: frame})
        assert all(tenure.end_frame_ordinal_exclusive == frame + 1 for tenure in snapshot)
        assert len(tracker._active_tenure_cache) <= 2
    _observe(tracker, 2, participants=(_participants_packet(2, driver_ids={1: 88}),), lap=_car_lap())
    changed = tracker.active_tenures()
    assert changed is not first
    assert changed[1].participant_identity_fingerprint != first[1].participant_identity_fingerprint
    assert first[1].end_frame_ordinal_exclusive == 2
    _observe(tracker, 3, participants=(_participants_packet(3),), lap=_car_lap(), lifecycle_epoch=1)
    assert all(tenure.lifecycle_epoch == 1 for tenure in tracker.active_tenures())


@pytest.mark.parametrize('boundary', ['interrupt', 'finish', 'end_session', 'start_session'])
def test_active_tenure_cache_cannot_survive_scope_closure(boundary):
    tracker = CarLapInventoryTracker()
    _observe(tracker, 1, participants=(_participants_packet(1),), lap=_car_lap())
    assert tracker.active_tenures()
    if boundary == 'interrupt':
        tracker.interrupt(2, 'test_gap')
    elif boundary == 'finish':
        tracker.finish()
    elif boundary == 'end_session':
        tracker.end_session(_SESSION_UID)
    else:
        tracker.start_session(_SESSION_UID + 1)
    assert tracker.active_tenures() == ()


def test_non_player_lap_is_bound_to_admitted_slot_tenure() -> None:
    tracker = CarLapInventoryTracker()
    _observe(tracker, 1, lap=_car_lap(), participants=(_participants_packet(1),))
    _observe(tracker, 2, lap=_car_lap(distance=500.0))
    _observe(
        tracker,
        3,
        lap=_car_lap(lap_number=2, distance=10.0, last_lap_ms=90_500),
    )

    tracker.finish()
    tenures, attempts = tracker.drain()
    slot_one_tenures = [row for row in tenures if row.car_index == 1]
    slot_one_attempts = [row for row in attempts if row.car_index == 1]
    completed = next(row for row in slot_one_attempts if row.attempt.disposition.value == "completed")

    assert len(slot_one_tenures) == 1
    assert (slot_one_tenures[0].start_frame_ordinal, slot_one_tenures[0].end_frame_ordinal_exclusive) == (1, 4)
    assert completed.tenure_ordinal == slot_one_tenures[0].tenure_ordinal
    assert completed.attempt.lap_time_ms == 90_500
    assert completed.attempt.completion_frame_ordinal == 3
    assert completed.attempt.reference_eligible is False
    assert slot_one_tenures[0].participant_identity_fingerprint != ""


def test_unrelated_roster_change_does_not_reset_slot_tenure() -> None:
    tracker = CarLapInventoryTracker()
    _observe(tracker, 1, participants=(_participants_packet(1),))
    _observe(tracker, 2, lap=_car_lap())
    _observe(
        tracker,
        3,
        participants=(_participants_packet(3, driver_ids={2: 90}),),
    )
    tracker.finish()
    tenures, _ = tracker.drain()

    assert len([row for row in tenures if row.car_index == 1]) == 1


def test_slot_replacement_closes_attempt_and_starts_a_new_tenure() -> None:
    tracker = CarLapInventoryTracker()
    _observe(tracker, 1, lap=_car_lap(), participants=(_participants_packet(1),))
    _observe(tracker, 2, lap=_car_lap(distance=400.0))
    _observe(
        tracker,
        3,
        participants=(_participants_packet(3, driver_ids={1: 91}),),
    )
    tracker.finish()
    tenures, attempts = tracker.drain()
    slot_one_tenures = sorted(
        (row for row in tenures if row.car_index == 1),
        key=lambda row: row.tenure_ordinal,
    )
    slot_one_attempts = [row for row in attempts if row.car_index == 1]

    assert len(slot_one_tenures) == 2
    assert slot_one_tenures[0].close_reason == "participants_identity_changed"
    assert slot_one_tenures[0].end_frame_ordinal_exclusive == 3
    assert slot_one_tenures[1].start_frame_ordinal == 3
    assert slot_one_attempts[0].tenure_ordinal == slot_one_tenures[0].tenure_ordinal
    assert slot_one_attempts[0].attempt.disposition.value == "abandoned"


def test_conflicting_roster_fences_only_the_disputed_slot() -> None:
    tracker = CarLapInventoryTracker()
    _observe(tracker, 1, participants=(_participants_packet(1),))
    _observe(tracker, 2, lap=_car_lap())
    _observe(
        tracker,
        3,
        participants=(
            _participants_packet(3),
            _participants_packet(3, driver_ids={1: 88}),
        ),
    )
    tracker.finish()
    tenures, attempts = tracker.drain()

    assert any(
        row.car_index == 1 and row.close_reason == "participants_slot_conflict"
        for row in tenures
    )
    assert all(
        row.attempt.disposition.value != "completed"
        for row in attempts
        if row.car_index == 1
    )
    assert any(
        row.car_index == 2 and row.close_reason == "capture_ended"
        for row in tenures
    )


def test_car_lap_context_history_is_bounded() -> None:
    tracker = CarLapInventoryTracker()
    context = _time_trial_context(20)
    _observe(
        tracker,
        1,
        lap=_car_lap(distance=10.0),
        participants=(_participants_packet(1),),
        context=context,
    )
    timeline = tuple((frame, _time_trial_context(frame % 40)) for frame in range(1, 100))
    _observe(tracker, 2, lap=_car_lap(distance=20.0), context=context, timeline=timeline)
    _observe(
        tracker,
        3,
        lap=_car_lap(lap_number=2, distance=10.0, last_lap_ms=90_500),
        context=context,
        timeline=timeline,
    )

    _, attempts = tracker.drain()
    completed = next(row.attempt for row in attempts if row.attempt.disposition.value == "completed")

    assert len(completed.context_segments) <= 64
    assert any(segment.context is None for segment in completed.context_segments)


def test_writer_drops_attempts_whose_tenure_was_not_retained(monkeypatch) -> None:
    from f1_engineer.storage import importer as importer_module

    monkeypatch.setattr(importer_module, "MAX_STORED_CAR_LAP_INVENTORY_ROWS", 1)
    writer = importer_module._CarLapInventoryWriter()
    retained_tenure = SimpleNamespace(
        session_uid=1,
        packet_format=2025,
        lifecycle_epoch=0,
        car_index=1,
        tenure_ordinal=1,
    )
    omitted_tenure_attempt = SimpleNamespace(
        session_uid=1,
        packet_format=2025,
        lifecycle_epoch=0,
        car_index=1,
        tenure_ordinal=2,
    )
    writer.consume((retained_tenure,), (omitted_tenure_attempt,))

    writer.retain_referentially_complete_attempts()

    assert writer.tenures_dropped == 0
    assert writer.attempts == []
    assert writer.attempts_dropped == 1


def test_2026_slot_23_lap_is_bound_to_its_roster_tenure() -> None:
    tracker = CarLapInventoryTracker()
    _observe(
        tracker,
        1,
        lap=_car_lap(distance=10.0),
        participants=(
            _participants_packet(1, packet_format=2026, active_count=24, slot_count=24),
        ),
        packet_format=2026,
        slot_index=23,
        slot_count=24,
    )
    _observe(
        tracker,
        2,
        lap=_car_lap(lap_number=2, distance=10.0, last_lap_ms=91_250),
        packet_format=2026,
        slot_index=23,
        slot_count=24,
    )

    tracker.finish()
    tenures, attempts = tracker.drain()

    assert any(row.car_index == 23 for row in tenures)
    assert any(
        row.car_index == 23 and row.attempt.lap_time_ms == 91_250
        for row in attempts
    )


def test_inactive_roster_slot_closes_tenure_and_blocks_later_lap_data() -> None:
    tracker = CarLapInventoryTracker()
    _observe(
        tracker,
        1,
        lap=_car_lap(distance=10.0),
        participants=(_participants_packet(1),),
    )
    _observe(
        tracker,
        2,
        participants=(_participants_packet(2, active_count=1),),
    )
    _observe(tracker, 3, lap=_car_lap(lap_number=2, last_lap_ms=90_000))

    tracker.finish()
    tenures, attempts = tracker.drain()

    assert any(
        row.car_index == 1 and row.close_reason == "participants_slot_inactive"
        for row in tenures
    )
    assert all(
        row.attempt.disposition.value != "completed"
        for row in attempts
        if row.car_index == 1
    )
    assert tracker.coverage_counts[(_SESSION_UID, 1, "participants_slot_inactive")] == 1


def test_lifecycle_epoch_change_closes_and_reopens_slot_tenure() -> None:
    tracker = CarLapInventoryTracker()
    _observe(
        tracker,
        1,
        lap=_car_lap(distance=10.0),
        participants=(_participants_packet(1),),
    )
    _observe(
        tracker,
        2,
        participants=(_participants_packet(2),),
        lifecycle_epoch=1,
    )
    _observe(
        tracker,
        3,
        lap=_car_lap(lap_number=1),
        lifecycle_epoch=1,
    )
    _observe(
        tracker,
        4,
        lap=_car_lap(lap_number=2, last_lap_ms=90_000),
        lifecycle_epoch=1,
    )

    tracker.finish()
    tenures, attempts = tracker.drain()
    slot_tenures = [row for row in tenures if row.car_index == 1]

    assert len(slot_tenures) == 2
    assert slot_tenures[0].close_reason == "association_scope_changed"
    assert slot_tenures[0].lifecycle_epoch == 0
    assert slot_tenures[1].lifecycle_epoch == 1
    assert any(
        row.lifecycle_epoch == 1
        for row in attempts
        if row.car_index == 1 and row.attempt.completion_frame_ordinal == 4
    )


def test_schema_13_migrates_diagnostic_car_lap_tables(tmp_path) -> None:
    database_path = tmp_path / "migration.sqlite3"
    with Database(database_path) as db:
        db.connection.execute("DROP TABLE observed_car_lap_attempts")
        db.connection.execute("DROP TABLE car_slot_tenures")
        db.connection.execute(
            "UPDATE schema_info SET version = 13 WHERE singleton = 1"
        )
        db.connection.commit()

    with Database(database_path) as db:
        version = db.connection.execute(
            "SELECT version FROM schema_info WHERE singleton = 1"
        ).fetchone()[0]
        table_names = {
            row[0]
            for row in db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert version == 15
    assert "car_slot_tenures" in table_names
    assert "observed_car_lap_attempts" in table_names


def test_packet_format_change_fences_previous_slot_provenance() -> None:
    tracker = CarLapInventoryTracker()
    _observe(
        tracker,
        1,
        lap=_car_lap(distance=10.0),
        participants=(_participants_packet(1),),
    )
    _observe(
        tracker,
        2,
        participants=(
            _participants_packet(2, packet_format=2026, active_count=24, slot_count=24),
        ),
        packet_format=2026,
        slot_count=24,
    )
    _observe(
        tracker,
        3,
        lap=_car_lap(lap_number=1),
        packet_format=2026,
        slot_count=24,
    )
    _observe(
        tracker,
        4,
        lap=_car_lap(lap_number=2, last_lap_ms=90_000),
        packet_format=2026,
        slot_count=24,
    )

    tracker.finish()
    tenures, attempts = tracker.drain()
    slot_tenures = sorted(
        (row for row in tenures if row.car_index == 1),
        key=lambda row: row.tenure_ordinal,
    )

    assert [row.packet_format for row in slot_tenures] == [2025, 2026]
    assert slot_tenures[0].close_reason == "association_scope_changed"
    assert any(
        row.packet_format == 2026 and row.attempt.completion_frame_ordinal == 4
        for row in attempts
        if row.car_index == 1
    )
