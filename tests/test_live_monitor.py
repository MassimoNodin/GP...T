from __future__ import annotations

import asyncio
import socket
import struct
import time
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from pydantic import ValidationError
httpx = pytest.importorskip("httpx")

from f1_engineer.api.app import (
    LiveCarDamageRecord,
    LiveCarSetupRecord,
    LiveMotionRecord,
    LiveSessionConditionsRecord,
    LiveTelemetryRecord,
    create_app,
)
from f1_engineer.recording.service import _AcquisitionObserver
from f1_engineer.recording.capture import CaptureReader
from tests.helpers import make_datagram
from tests.test_car_status import _status_body
from tests.test_lap_tracking import SESSION_UID, _lap_packet, _session_packet


_TELEMETRY_CAR = struct.Struct("<HfffBbHBBH4H4B4BH4f4B")
_LAP_DATA_CAR = struct.Struct("<IIHBHBHBHBfff15BHHBfB")
_CAR_DAMAGE_CAR = struct.Struct("<4f30B")
_CAR_SETUP_CAR = struct.Struct("<4B4f9B4fBf")
_MOTION_CAR_F1_25 = struct.Struct("<6f6h6f")
_MOTION_CAR_2026 = struct.Struct("<6f9h3f")
_CONTROL_TOKEN = "live-monitor-test-token"


def _telemetry_packet(
    *,
    frame: int,
    sequence: int,
    player_car_index: int = 0,
    packet_format: int = 2025,
    packet_version: int = 1,
    throttle: float = 0.75,
    brake: float = 0.2,
    engine_temperature_c: int = 95,
    brake_temperature_c: tuple[int, int, int, int] = (0, 0, 0, 0),
    tyre_surface_temperature_c: tuple[int, int, int, int] = (0, 0, 0, 0),
    tyre_inner_temperature_c: tuple[int, int, int, int] = (0, 0, 0, 0),
):
    records = []
    for car_index in range(22):
        fields = (
            (100 + car_index, throttle, 0.0, brake, 0, 0, 11_000, 0, 0, 0)
            + brake_temperature_c
            + tyre_surface_temperature_c
            + tyre_inner_temperature_c
            + (engine_temperature_c,)
            + (1.0,) * 4
            + (0,) * 4
        )
        records.append(
            _TELEMETRY_CAR.pack(*fields)
        )
    return make_datagram(
        packet_format=packet_format,
        packet_id=6,
        packet_version=packet_version,
        session_uid=SESSION_UID,
        frame=frame,
        player_car_index=player_car_index,
        body=b"".join(records) + bytes((0, 255, 0)),
        sequence=sequence,
    )


def _advance(
    frame: int,
    sequence: int,
    *,
    session_uid: int = SESSION_UID,
    player_car_index: int = 0,
    packet_format: int = 2025,
):
    return make_datagram(
        packet_format=packet_format,
        packet_id=255,
        session_uid=session_uid,
        frame=frame,
        player_car_index=player_car_index,
        body=b"advance-watermark",
        sequence=sequence,
    )


def _car_status_packet(
    *,
    frame: int,
    sequence: int,
    player_car_index: int = 0,
    packet_format: int = 2025,
    packet_version: int = 1,
    body: bytes | None = None,
):
    return make_datagram(
        packet_format=packet_format,
        packet_id=7,
        packet_version=packet_version,
        session_uid=SESSION_UID,
        frame=frame,
        player_car_index=player_car_index,
        body=body if body is not None else _status_body(packet_format=packet_format),
        sequence=sequence,
    )


def _car_damage_packet(
    *,
    frame: int,
    sequence: int,
    session_uid: int = SESSION_UID,
    player_car_index: int = 0,
    packet_format: int = 2025,
    packet_version: int = 1,
    session_time_s: float = 0.0,
    wear: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
    byte_values: tuple[int, ...] = (0,) * 30,
    body: bytes | None = None,
):
    car_count = 24 if packet_format == 2026 else 22
    if body is None:
        record = _CAR_DAMAGE_CAR.pack(*wear, *byte_values)
        body = record * car_count
    return make_datagram(
        packet_format=packet_format,
        packet_id=10,
        packet_version=packet_version,
        session_uid=session_uid,
        frame=frame,
        session_time=session_time_s,
        player_car_index=player_car_index,
        body=body,
        sequence=sequence,
    )


def _car_setup_packet(
    *,
    frame: int,
    sequence: int,
    session_uid: int = SESSION_UID,
    player_car_index: int = 0,
    packet_format: int = 2025,
    packet_version: int = 1,
    session_time_s: float = 0.0,
    front_wing: int = 20,
    next_front_wing_value: float = 45.5,
    front_camber: float = -3.5,
    other_car_front_wing: int | None = None,
    body: bytes | None = None,
):
    car_count = 24 if packet_format == 2026 else 22
    if body is None:
        records = []
        for car_index in range(car_count):
            current_front_wing = (
                front_wing
                if car_index == player_car_index or other_car_front_wing is None
                else other_car_front_wing
            )
            records.append(
                _CAR_SETUP_CAR.pack(
                    current_front_wing,
                    30,
                    50,
                    55,
                    front_camber,
                    -1.5,
                    0.1,
                    0.2,
                    5,
                    6,
                    7,
                    8,
                    9,
                    10,
                    100,
                    55,
                    20,
                    22.0,
                    22.1,
                    22.2,
                    22.3,
                    10,
                    30.5,
                )
            )
        body = b"".join(records) + struct.pack("<f", next_front_wing_value)
    return make_datagram(
        packet_format=packet_format,
        packet_id=5,
        packet_version=packet_version,
        session_uid=session_uid,
        frame=frame,
        session_time=session_time_s,
        player_car_index=player_car_index,
        body=body,
        sequence=sequence,
    )


def _flashback_packet(
    *,
    frame: int,
    sequence: int,
    session_time: float,
    target_time: float,
    session_uid: int = SESSION_UID,
    packet_format: int = 2025,
):
    details = struct.pack("<If", frame - 5, target_time) + b"\x00" * 4
    return make_datagram(
        packet_format=packet_format,
        packet_id=3,
        session_uid=session_uid,
        frame=frame,
        session_time=session_time,
        body=b"FLBK" + details,
        sequence=sequence,
    )


def _process(observer: _AcquisitionObserver, raw):
    observer.process(replace(raw, monotonic_ns=time.monotonic_ns()))


def _queue_frame(
    observer: _AcquisitionObserver,
    *,
    frame: int,
    sequence: int,
    player_car_index: int,
) -> None:
    _process(
        observer,
        _telemetry_packet(
            frame=frame,
            sequence=sequence,
            player_car_index=player_car_index,
        ),
    )
    _process(
        observer,
        _lap_packet(
            frame=frame,
            lap_number=3,
            distance_m=120.0,
            session_time=float(frame),
            current_lap_time_ms=frame * 10,
            sequence=sequence + 1,
            player_car_index=player_car_index,
            active_car_index=player_car_index,
        ),
    )


def _session_packet_for_mode(
    *, race: bool = False, session_type_id: int | None = None
):
    body = bytearray(_session_packet().payload[29:])
    if race:
        body[6] = 15
        body[665] = 27
        body[666] = 1
    elif session_type_id is not None:
        body[6] = session_type_id
    return make_datagram(
        packet_id=1,
        session_uid=SESSION_UID,
        frame=1,
        body=bytes(body),
    )


def _session_conditions_packet(
    *,
    frame: int = 100,
    sequence: int = 10,
    session_uid: int = SESSION_UID,
    packet_format: int = 2025,
    packet_version: int = 1,
    player_car_index: int = 255,
    session_time_s: float = 12.5,
    weather_id: int = 0,
    track_temperature_c: int = 0,
    air_temperature_c: int = 0,
    body: bytes | None = None,
):
    session_body = bytearray(_session_packet().payload[29:] if body is None else body)
    session_body[0] = weather_id
    session_body[1] = track_temperature_c & 0xFF
    session_body[2] = air_temperature_c & 0xFF
    if packet_format == 2026 and len(session_body) == 724:
        session_body.extend(bytes(897 - len(session_body)))
    return make_datagram(
        packet_format=packet_format,
        packet_id=1,
        packet_version=packet_version,
        session_uid=session_uid,
        frame=frame,
        session_time=session_time_s,
        player_car_index=player_car_index,
        body=bytes(session_body),
        sequence=sequence,
    )


def _motion_packet(
    *,
    frame: int = 100,
    sequence: int = 10,
    session_uid: int = SESSION_UID,
    packet_format: int = 2025,
    packet_version: int = 1,
    player_car_index: int = 0,
    session_time_s: float = 12.5,
    position: tuple[float, float, float] = (10.0, 20.0, 30.0),
    velocity: tuple[float, float, float] = (1.0, 2.0, 3.0),
    unrelated_car_position: tuple[float, float, float] | None = None,
    body: bytes | None = None,
):
    if body is None:
        car_count = 22 if packet_format == 2025 else 24
        record = _MOTION_CAR_F1_25 if packet_format == 2025 else _MOTION_CAR_2026
        records = []
        for car_index in range(car_count):
            car_position = position if car_index == player_car_index else (0.0, 0.0, 0.0)
            if (
                unrelated_car_position is not None
                and car_index == (player_car_index + 1) % car_count
            ):
                car_position = unrelated_car_position
            if packet_format == 2025:
                records.append(
                    record.pack(
                        *car_position,
                        *(velocity if car_index == player_car_index else (0.0, 0.0, 0.0)),
                        32767, 0, 0, 0, 32767, 0,
                        0.1, -0.2, 1.0,
                        0.4, 0.5, 0.6,
                    )
                )
            else:
                records.append(
                    record.pack(
                        *car_position,
                        *(velocity if car_index == player_car_index else (0.0, 0.0, 0.0)),
                        32767, 0, 0, 0, 32767, 0, 100, -200, 1000,
                        0.4, 0.5, 0.6,
                    )
                )
        body = b"".join(records)
    return make_datagram(
        packet_format=packet_format,
        packet_id=0,
        packet_version=packet_version,
        session_uid=session_uid,
        frame=frame,
        session_time=session_time_s,
        player_car_index=player_car_index,
        body=body,
        sequence=sequence,
    )


def _publish_live_motion(
    observer: _AcquisitionObserver,
    *,
    frame: int = 100,
    sequence: int = 10,
    session_uid: int = SESSION_UID,
    packet_format: int = 2025,
    player_car_index: int = 0,
    **kwargs,
) -> None:
    _process(
        observer,
        _motion_packet(
            frame=frame,
            sequence=sequence,
            session_uid=session_uid,
            packet_format=packet_format,
            player_car_index=player_car_index,
            **kwargs,
        ),
    )
    _process(
        observer,
        _advance(
            frame + 1,
            sequence + 1,
            session_uid=session_uid,
            player_car_index=player_car_index,
            packet_format=packet_format,
        ),
    )


def _publish_live_session_conditions(
    observer: _AcquisitionObserver,
    *,
    frame: int = 100,
    sequence: int = 10,
    session_uid: int = SESSION_UID,
    packet_format: int = 2025,
    player_car_index: int = 255,
    **kwargs,
) -> None:
    _process(
        observer,
        _session_conditions_packet(
            frame=frame,
            sequence=sequence,
            session_uid=session_uid,
            packet_format=packet_format,
            player_car_index=player_car_index,
            **kwargs,
        ),
    )
    _process(
        observer,
        _advance(
            frame + 1,
            sequence + 1,
            session_uid=session_uid,
            player_car_index=player_car_index,
            packet_format=packet_format,
        ),
    )


def test_live_telemetry_api_bounds_source_temperature_wire_values():
    telemetry = LiveTelemetryRecord(
        status="fresh",
        reason=None,
        age_ms=0,
        packet_format=2026,
        engine_temperature_c=255,
        brake_temperature_c=(0, 1, 65_534, 65_535),
        tyre_surface_temperature_c=(0, 1, 254, 255),
        tyre_inner_temperature_c=(0, 1, 254, 255),
    )

    assert telemetry.engine_temperature_c == 255
    assert telemetry.brake_temperature_c == (0, 1, 65_534, 65_535)
    assert telemetry.tyre_surface_temperature_c == (0, 1, 254, 255)

    with pytest.raises(ValidationError):
        LiveTelemetryRecord(
            status="fresh",
            reason=None,
            age_ms=0,
            tyre_surface_temperature_c=(0, 1, 255, 256),
        )

    with pytest.raises(ValidationError):
        LiveTelemetryRecord(
            status="fresh",
            reason=None,
            age_ms=0,
            brake_temperature_c=(0, 1, 2),
        )


def _publish_frame(
    observer: _AcquisitionObserver,
    *,
    frame: int = 10,
    sequence: int = 10,
    player_car_index: int = 0,
    invalid: int = 0,
    telemetry: bool = True,
    car_status: bool = False,
    car_status_bodies: tuple[bytes, ...] | None = None,
    car_status_player_index: int | None = None,
    lap_number: int = 3,
    current_lap_time_ms: int = 34_567,
    last_lap_time_ms: int = 0,
    session_time_s: float = 12.0,
    sector1_time_ms: int = 12_345,
    sector2_time_ms: int = 45_678,
    sector_id: int = 0,
    throttle: float = 0.75,
    brake: float = 0.2,
    engine_temperature_c: int = 95,
    brake_temperature_c: tuple[int, int, int, int] = (0, 0, 0, 0),
    tyre_surface_temperature_c: tuple[int, int, int, int] = (0, 0, 0, 0),
    tyre_inner_temperature_c: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> None:
    status_bodies = (
        car_status_bodies
        if car_status_bodies is not None
        else ((_status_body(),) if car_status else ())
    )
    if telemetry:
        _process(observer,
            _telemetry_packet(
                frame=frame,
                sequence=sequence,
                player_car_index=player_car_index,
                throttle=throttle,
                brake=brake,
                engine_temperature_c=engine_temperature_c,
                brake_temperature_c=brake_temperature_c,
                tyre_surface_temperature_c=tyre_surface_temperature_c,
                tyre_inner_temperature_c=tyre_inner_temperature_c,
            )
        )
    _process(observer,
        _lap_packet(
            frame=frame,
            lap_number=lap_number,
            distance_m=120.0,
            session_time=session_time_s,
            current_lap_time_ms=current_lap_time_ms,
            last_lap_time_ms=last_lap_time_ms,
            sector1_time_ms=sector1_time_ms,
            sector2_time_ms=sector2_time_ms,
            sector_id=sector_id,
            invalid=invalid,
            pit_status=2,
            driver_status=2,
            sequence=sequence + 1,
            player_car_index=player_car_index,
            active_car_index=player_car_index,
        )
    )
    for index, body in enumerate(status_bodies):
        _process(
            observer,
            _car_status_packet(
                frame=frame,
                sequence=sequence + 2 + index,
                player_car_index=(
                    player_car_index
                    if car_status_player_index is None
                    else car_status_player_index
                ),
                body=body,
            ),
        )
    _process(
        observer,
        _advance(
            frame + 1,
            sequence + 2 + len(status_bodies),
            player_car_index=player_car_index,
        ),
    )


def test_live_monitor_joins_reordered_player_packets_and_uses_canonical_values():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(
        observer,
        frame=100,
        player_car_index=1,
        invalid=1,
        throttle=0.0,
        brake=0.0,
    )

    live = observer.live_telemetry_snapshot()

    assert live["status"] == "fresh"
    assert live["session_uid"] == str(SESSION_UID)
    assert live["frame_identifier"] == 100
    assert live["player_car_index"] == 1
    assert live["lap_number"] == 3
    assert live["lap_time_ms"] == 34_567
    assert live["game_invalid"] is True
    assert live["pit_status_id"] == 2
    assert live["driver_status_id"] == 2
    assert live["speed_kph"] == 101
    assert live["gear"] == 0
    assert live["engine_rpm"] == 11_000
    assert live["engine_temperature_c"] == 95
    assert live["brake_temperature_c"] == [0, 0, 0, 0]
    assert live["tyre_surface_temperature_c"] == [0, 0, 0, 0]
    assert live["tyre_inner_temperature_c"] == [0, 0, 0, 0]
    assert live["throttle"] == 0
    assert live["brake"] == 0


def test_live_chart_source_epoch_and_session_time_follow_telemetry_boundaries():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    waiting = observer.live_telemetry_snapshot()
    assert isinstance(waiting["source_epoch"], str)
    assert waiting["session_time_s"] is None

    _publish_frame(observer, frame=100)
    fresh = observer.live_telemetry_snapshot()
    assert fresh["status"] == "fresh"
    assert fresh["source_epoch"] != waiting["source_epoch"]
    assert fresh["session_time_s"] == 12.0

    _process(
        observer,
        _flashback_packet(frame=102, sequence=20, session_time=12.1, target_time=5.0),
    )
    _process(observer, _advance(103, 21))
    rewound = observer.live_telemetry_snapshot()
    assert rewound["status"] == "unavailable"
    assert rewound["source_epoch"] != fresh["source_epoch"]

    _publish_frame(observer, frame=104, sequence=30)
    recovered = observer.live_telemetry_snapshot()
    assert recovered["status"] == "fresh"
    assert recovered["source_epoch"] == rewound["source_epoch"]
    assert recovered["session_time_s"] == 12.0

    previous_epoch = recovered["source_epoch"]
    _publish_frame(observer, frame=106, sequence=40, player_car_index=1)
    changed_player = observer.live_telemetry_snapshot()
    assert changed_player["status"] == "fresh"
    assert changed_player["source_epoch"] != previous_epoch
    assert changed_player["player_car_index"] == 1


def test_invalid_live_chart_source_time_does_not_invalidate_other_telemetry():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=10, session_time_s=86_401.0)

    live = observer.live_telemetry_snapshot()

    assert live["status"] == "fresh"
    assert live["session_time_s"] is None
    assert live["speed_kph"] == 100
    LiveTelemetryRecord.model_validate(
        {key: value for key, value in live.items() if not key.startswith("_")}
    )


def test_live_monitor_uses_replay_delivery_clock_without_changing_source_datagrams():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    delivery_start_ns = 100_000_000_000
    raw_packets = (
        _session_packet_for_mode(),
        _telemetry_packet(frame=10, sequence=10),
        _lap_packet(
            frame=10,
            lap_number=3,
            distance_m=120.0,
            session_time=12.0,
            current_lap_time_ms=34_567,
            sequence=11,
            player_car_index=0,
            active_car_index=0,
        ),
        _advance(frame=11, sequence=12),
    )
    source_packets = tuple(
        replace(raw, monotonic_ns=5_000_000_000) for raw in raw_packets
    )

    for index, raw in enumerate(source_packets):
        observer.process(
            raw,
            delivery_monotonic_ns=delivery_start_ns + index * 10_000_000,
        )

    telemetry = observer.live_telemetry_snapshot(
        now_monotonic_ns=delivery_start_ns + 150_000_000
    )
    assert telemetry["status"] == "fresh"
    assert telemetry["age_ms"] == 140
    assert all(raw.monotonic_ns == 5_000_000_000 for raw in source_packets)


def test_live_monitor_marks_missing_same_frame_car_telemetry_unavailable():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100, telemetry=False)

    live = observer.live_telemetry_snapshot()

    assert live["status"] == "unavailable"
    assert live["reason"] == "same_frame_car_telemetry_unavailable"
    assert live["lap_number"] == 3
    assert live["speed_kph"] is None
    assert live["throttle"] is None
    assert live["brake"] is None
    assert live["engine_temperature_c"] is None
    assert live["brake_temperature_c"] is None
    assert live["tyre_surface_temperature_c"] is None
    assert live["tyre_inner_temperature_c"] is None


def test_live_monitor_preserves_source_temperature_wheel_order_and_zeroes():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(
        observer,
        frame=100,
        engine_temperature_c=0,
        brake_temperature_c=(400, 401, 402, 403),
        tyre_surface_temperature_c=(91, 0, 93, 94),
        tyre_inner_temperature_c=(101, 102, 103, 104),
    )

    live = observer.live_telemetry_snapshot()

    assert live["engine_temperature_c"] == 0
    assert live["brake_temperature_c"] == [400, 401, 402, 403]
    assert live["tyre_surface_temperature_c"] == [91, 0, 93, 94]
    assert live["tyre_inner_temperature_c"] == [101, 102, 103, 104]


def test_live_car_status_joins_the_same_player_frame_and_exposes_canonical_values():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100, car_status=True)

    status = observer.live_car_status_snapshot()
    assert status["status"] == "fresh"
    assert status["reason"] is None
    assert status["session_uid"] == str(SESSION_UID)
    assert status["frame_identifier"] == 100
    assert status["player_car_index"] == 0
    assert status["fuel_in_tank_reported"] == 12.5
    assert status["fuel_remaining_laps"] == -1.25
    assert status["actual_tyre_compound"] == 20
    assert status["visual_tyre_compound"] == 17
    assert status["tyre_age_laps"] == 3
    assert status["front_brake_bias_percent"] == 54
    assert status["pit_limiter_active"] is False
    assert status["validation_flags"] == []


def test_live_lap_timing_is_exact_frame_and_independent_of_other_live_groups():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(
        observer,
        frame=100,
        telemetry=False,
        lap_number=4,
        current_lap_time_ms=82_345,
        last_lap_time_ms=80_123,
        sector1_time_ms=26_500,
        sector2_time_ms=30_250,
        sector_id=1,
    )

    timing = observer.live_lap_timing_snapshot()
    assert timing["status"] == "fresh"
    assert timing["reason"] is None
    assert timing["session_uid"] == str(SESSION_UID)
    assert timing["frame_identifier"] == 100
    assert timing["packet_format"] == 2025
    assert timing["player_car_index"] == 0
    assert timing["lap_number"] == 4
    assert timing["current_lap_time_ms"] == 82_345
    assert timing["current_sector"] == 2
    assert timing["previous_lap_time_ms"] == 80_123
    assert timing["sector1_time_ms"] == 26_500
    assert timing["sector2_time_ms"] == 30_250
    assert timing["validation_flags"] == []
    assert observer.live_telemetry_snapshot()["status"] == "unavailable"
    assert observer.live_car_status_snapshot()["status"] == "unavailable"


@pytest.mark.parametrize(
    ("sector_code", "expected_sector", "expected_flags"),
    ((0, 1, []), (1, 2, []), (2, 3, []), (255, None, ["invalid_current_sector"])),
)
def test_live_lap_timing_maps_and_validates_sector_codes(
    sector_code: int, expected_sector: int | None, expected_flags: list[str]
):
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100, telemetry=False, sector_id=sector_code)

    timing = observer.live_lap_timing_snapshot()
    assert timing["status"] == "fresh"
    assert timing["current_sector"] == expected_sector
    assert timing["validation_flags"] == expected_flags


def test_live_lap_timing_treats_zero_reported_values_as_unavailable():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(
        observer,
        frame=100,
        telemetry=False,
        lap_number=0,
        current_lap_time_ms=0,
        last_lap_time_ms=0,
        sector1_time_ms=0,
        sector2_time_ms=0,
        sector_id=0,
    )

    timing = observer.live_lap_timing_snapshot()
    assert timing["status"] == "fresh"
    assert timing["lap_number"] is None
    assert timing["current_lap_time_ms"] is None
    assert timing["current_sector"] == 1
    assert timing["previous_lap_time_ms"] is None
    assert timing["sector1_time_ms"] is None
    assert timing["sector2_time_ms"] is None


def test_live_lap_timing_clears_malformed_and_unsupported_player_packets():
    malformed = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(malformed, frame=100, telemetry=False)
    _process(
        malformed,
        make_datagram(
            packet_format=2025,
            packet_id=2,
            session_uid=SESSION_UID,
            frame=103,
            player_car_index=0,
            body=b"malformed lap data",
            sequence=30,
        ),
    )
    _process(malformed, _advance(104, 31))
    malformed_timing = malformed.live_lap_timing_snapshot()
    assert malformed_timing["status"] == "unavailable"
    assert malformed_timing["reason"] == "lap_data_decode_failed"
    assert malformed_timing["current_lap_time_ms"] is None
    assert malformed_timing["previous_lap_time_ms"] is None

    unsupported = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(unsupported, frame=100, telemetry=False)
    _process(
        unsupported,
        _lap_packet(
            frame=103,
            lap_number=4,
            distance_m=130.0,
            session_time=12.1,
            current_lap_time_ms=45_000,
            sequence=30,
            packet_version=2,
        ),
    )
    _process(unsupported, _advance(104, 31))
    unsupported_timing = unsupported.live_lap_timing_snapshot()
    assert unsupported_timing["status"] == "unsupported"
    assert unsupported_timing["reason"] == "lap_data_adapter_unsupported"
    assert unsupported_timing["current_lap_time_ms"] is None


def test_live_lap_timing_rejects_conflicting_same_frame_updates():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _process(
        observer,
        _lap_packet(
            frame=100,
            lap_number=4,
            distance_m=120.0,
            session_time=12.0,
            current_lap_time_ms=80_000,
            sequence=10,
            sector_id=0,
        ),
    )
    _process(
        observer,
        _lap_packet(
            frame=100,
            lap_number=4,
            distance_m=120.0,
            session_time=12.0,
            current_lap_time_ms=81_000,
            sequence=11,
            sector_id=1,
        ),
    )
    _process(observer, _advance(101, 12))

    timing = observer.live_lap_timing_snapshot()
    assert timing["status"] == "unavailable"
    assert timing["reason"] == "conflicting_lap_data_packets"
    assert timing["frame_identifier"] == 100
    assert timing["current_lap_time_ms"] is None


def test_live_lap_timing_ignores_other_car_changes_in_same_frame():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    first = _lap_packet(
        frame=100,
        lap_number=4,
        distance_m=120.0,
        session_time=12.0,
        current_lap_time_ms=80_000,
        sequence=10,
    )
    changed_other_car = bytearray(first.payload)
    struct.pack_into("<I", changed_other_car, 29 + _LAP_DATA_CAR.size, 99_999)
    second = replace(first, sequence=11, payload=bytes(changed_other_car))

    observer.process(replace(first, monotonic_ns=1_000_000_000))
    observer.process(replace(second, monotonic_ns=1_200_000_000))
    observer.process(replace(_advance(101, 12), monotonic_ns=1_300_000_000))

    timing = observer.live_lap_timing_snapshot(1_500_000_000)
    assert timing["status"] == "fresh"
    assert timing["reason"] is None
    assert timing["frame_identifier"] == 100
    assert timing["current_lap_time_ms"] == 80_000
    assert timing["age_ms"] == 300
    assert timing["_observed_monotonic_ns"] == 1_200_000_000


def test_live_lap_timing_uses_its_own_receive_time_and_staleness():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    received_ns = 1_000_000_000
    observer.process(
        replace(
            _lap_packet(
                frame=100,
                lap_number=4,
                distance_m=120.0,
                session_time=12.0,
                current_lap_time_ms=80_000,
                sequence=10,
            ),
            monotonic_ns=received_ns,
        )
    )
    observer.process(
        replace(_advance(101, 11), monotonic_ns=received_ns + 10_000_000)
    )

    at_limit = observer.live_lap_timing_snapshot(received_ns + 500_000_000)
    assert at_limit["status"] == "fresh"
    assert at_limit["age_ms"] == 500
    assert at_limit["_observed_monotonic_ns"] == received_ns
    stale = observer.live_lap_timing_snapshot(received_ns + 501_000_000)
    assert stale["status"] == "stale"
    assert stale["age_ms"] == 501


def test_live_lap_timing_requires_receive_provenance_for_fresh_status():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    observer._max_receive_time_entries = 1
    _publish_frame(observer, frame=100, telemetry=False, car_status=True)

    timing = observer.live_lap_timing_snapshot()
    assert timing["status"] == "unavailable"
    assert timing["reason"] == "receive_provenance_unavailable"
    assert timing["current_lap_time_ms"] == 34_567


def test_missing_car_status_does_not_weaken_the_speed_control_monitor():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100)

    telemetry = observer.live_telemetry_snapshot()
    status = observer.live_car_status_snapshot()
    assert telemetry["status"] == "fresh"
    assert telemetry["speed_kph"] == 100
    assert telemetry["throttle"] == 0.75
    assert status["status"] == "unavailable"
    assert status["reason"] == "status_packet_missing"
    assert status["fuel_in_tank_reported"] is None


def test_live_car_status_is_independent_of_missing_car_telemetry():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100, telemetry=False, car_status=True)

    assert observer.live_telemetry_snapshot()["status"] == "unavailable"
    status = observer.live_car_status_snapshot()
    assert status["status"] == "fresh"
    assert status["fuel_in_tank_reported"] == 12.5


def test_live_car_status_ages_from_the_oldest_lap_or_status_receive_time():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    base_ns = 1_000_000_000
    observer.process(
        replace(
            _lap_packet(
                frame=100,
                lap_number=3,
                distance_m=120.0,
                session_time=12.0,
                current_lap_time_ms=34_567,
                sequence=10,
            ),
            monotonic_ns=base_ns + 100_000_000,
        )
    )
    observer.process(
        replace(
            _car_status_packet(frame=100, sequence=11),
            monotonic_ns=base_ns + 250_000_000,
        )
    )
    observer.process(
        replace(_advance(101, 12), monotonic_ns=base_ns + 300_000_000)
    )

    status = observer.live_car_status_snapshot(base_ns + 600_000_000)
    assert status["status"] == "fresh"
    assert status["age_ms"] == 500
    assert status["_observed_monotonic_ns"] == base_ns + 100_000_000
    stale = observer.live_car_status_snapshot(base_ns + 601_000_000)
    assert stale["status"] == "stale"
    assert stale["age_ms"] == 501


def test_live_car_status_does_not_carry_values_from_a_status_only_frame():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100, car_status=True)
    assert observer.live_car_status_snapshot()["fuel_in_tank_reported"] == 12.5

    _process(observer, _car_status_packet(frame=101, sequence=20))
    _process(observer, _advance(102, 21))

    status = observer.live_car_status_snapshot()
    assert status["status"] == "unavailable"
    assert status["reason"] == "same_frame_player_lap_missing"
    assert status["frame_identifier"] == 101
    assert status["fuel_in_tank_reported"] is None


def test_live_car_status_nulls_invalid_fields_without_invalidating_packet():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    body = bytearray(_status_body())
    body[3] = 101
    _publish_frame(observer, frame=100, car_status=True)
    # A later frame carries one malformed field; other status fields remain usable.
    _process(observer, _lap_packet(
        frame=101,
        lap_number=3,
        distance_m=130.0,
        session_time=12.1,
        current_lap_time_ms=35_000,
        sequence=20,
    ))
    _process(observer, _car_status_packet(frame=101, sequence=21, body=bytes(body)))
    _process(observer, _advance(102, 22))

    status = observer.live_car_status_snapshot()
    assert status["status"] == "fresh"
    assert status["fuel_in_tank_reported"] == 12.5
    assert status["front_brake_bias_percent"] is None
    assert status["validation_flags"] == [
        "invalid_car_status_front_brake_bias_percent"
    ]


def test_live_car_status_reports_malformed_and_conflicting_packets():
    malformed = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(
        malformed,
        frame=100,
        car_status_bodies=(b"malformed",),
    )
    malformed_status = malformed.live_car_status_snapshot()
    assert malformed_status["status"] == "unavailable"
    assert malformed_status["reason"] == "status_packet_malformed_or_unsupported"
    assert malformed_status["fuel_in_tank_reported"] is None

    conflicting = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(
        conflicting,
        frame=100,
        car_status_bodies=(_status_body(fuel=12.5), _status_body(fuel=13.5)),
    )
    conflicting_status = conflicting.live_car_status_snapshot()
    assert conflicting_status["status"] == "unavailable"
    assert conflicting_status["reason"] == "conflicting_status_packets"
    assert conflicting_status["fuel_in_tank_reported"] is None


def test_live_car_status_rejects_a_conflicting_player_header():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100, car_status=True)
    _publish_frame(
        observer,
        frame=102,
        sequence=20,
        car_status=True,
        car_status_player_index=1,
    )

    status = observer.live_car_status_snapshot()
    assert status["status"] != "fresh"
    assert status.get("fuel_in_tank_reported") is None


def test_live_car_status_freshness_requires_provenance_for_every_selected_packet():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    observer._max_receive_time_entries = 1
    _publish_frame(observer, frame=100, car_status=True)

    status = observer.live_car_status_snapshot()
    assert status["status"] == "unavailable"
    assert status["reason"] == "receive_provenance_unavailable"
    assert status["fuel_in_tank_reported"] == 12.5


def test_live_car_status_and_telemetry_expire_independently():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    base_ns = 1_000_000_000
    observer.process(
        replace(
            _car_status_packet(frame=100, sequence=10),
            monotonic_ns=base_ns + 100_000_000,
        )
    )
    observer.process(
        replace(
            _lap_packet(
                frame=100,
                lap_number=3,
                distance_m=120.0,
                session_time=12.0,
                current_lap_time_ms=34_567,
                sequence=11,
            ),
            monotonic_ns=base_ns + 250_000_000,
        )
    )
    observer.process(
        replace(
            _telemetry_packet(frame=100, sequence=12),
            monotonic_ns=base_ns + 300_000_000,
        )
    )
    observer.process(
        replace(
            _advance(101, 13),
            monotonic_ns=base_ns + 310_000_000,
        )
    )

    telemetry = observer.live_telemetry_snapshot(base_ns + 700_000_000)
    status = observer.live_car_status_snapshot(base_ns + 700_000_000)
    assert telemetry["status"] == "fresh"
    assert telemetry["age_ms"] == 450
    assert status["status"] == "stale"
    assert status["age_ms"] == 600


def test_live_car_status_survives_frame_identifier_wrap():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=0xFFFFFFFE, car_status=True)
    assert observer.live_car_status_snapshot()["frame_identifier"] == 0xFFFFFFFE

    _publish_frame(observer, frame=0, sequence=20, car_status=True)
    status = observer.live_car_status_snapshot()
    assert status["status"] == "fresh"
    assert status["frame_identifier"] == 0
    assert status["fuel_in_tank_reported"] == 12.5


def test_live_lap_timing_survives_frame_identifier_wrap():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=0xFFFFFFFE, telemetry=False)
    assert observer.live_lap_timing_snapshot()["frame_identifier"] == 0xFFFFFFFE

    _publish_frame(observer, frame=0, sequence=20, telemetry=False, sector_id=2)
    timing = observer.live_lap_timing_snapshot()
    assert timing["status"] == "fresh"
    assert timing["frame_identifier"] == 0
    assert timing["current_sector"] == 3


def test_live_monitor_supports_race_and_unknown_context_without_policy_changes():
    time_trial = _AcquisitionObserver(reorder_window_frames=1)
    _process(time_trial, _session_packet_for_mode(race=False))
    _publish_frame(time_trial, frame=100, car_status=True)

    race = _AcquisitionObserver(reorder_window_frames=1)
    _process(race, _session_packet_for_mode(race=True))
    _publish_frame(race, frame=100, car_status=True)

    practice = _AcquisitionObserver(reorder_window_frames=1)
    _process(practice, _session_packet_for_mode(session_type_id=1))
    _publish_frame(practice, frame=100, car_status=True)

    qualifying = _AcquisitionObserver(reorder_window_frames=1)
    _process(qualifying, _session_packet_for_mode(session_type_id=5))
    _publish_frame(qualifying, frame=100, car_status=True)

    unknown = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(unknown, frame=100, car_status=True)

    assert time_trial.live_telemetry_snapshot()["status"] == "fresh"
    assert race.live_telemetry_snapshot()["status"] == "fresh"
    assert unknown.live_telemetry_snapshot()["status"] == "fresh"
    assert time_trial.live_car_status_snapshot()["status"] == "fresh"
    assert race.live_car_status_snapshot()["status"] == "fresh"
    assert unknown.live_car_status_snapshot()["status"] == "fresh"
    assert time_trial.live_lap_timing_snapshot()["status"] == "fresh"
    assert race.live_lap_timing_snapshot()["status"] == "fresh"
    assert practice.live_lap_timing_snapshot()["status"] == "fresh"
    assert qualifying.live_lap_timing_snapshot()["status"] == "fresh"
    assert unknown.live_lap_timing_snapshot()["status"] == "fresh"


def test_live_monitor_expires_sample_using_monotonic_receive_time():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100)
    sample = observer.live_telemetry_snapshot()
    observed_ns = sample["_observed_monotonic_ns"]

    at_limit = observer.live_telemetry_snapshot(observed_ns + 500_000_000)
    after_limit = observer.live_telemetry_snapshot(observed_ns + 501_000_000)

    assert at_limit["status"] == "fresh"
    assert at_limit["age_ms"] == 500
    assert after_limit["status"] == "stale"
    assert after_limit["age_ms"] == 501


def test_live_monitor_clears_and_quarantines_flashback_frame_then_recovers():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100)
    assert observer.live_telemetry_snapshot()["status"] == "fresh"

    _process(observer, _telemetry_packet(frame=101, sequence=20))
    _process(
        observer,
        _lap_packet(
            frame=101,
            lap_number=3,
            distance_m=140.0,
            session_time=12.1,
            current_lap_time_ms=35_000,
            sequence=21,
        ),
    )
    _process(observer, _flashback_packet(frame=101, sequence=22, session_time=12.1, target_time=5.0))
    _process(observer, _advance(102, 23))

    live = observer.live_telemetry_snapshot()
    assert live["status"] == "unavailable"
    assert live["reason"] == "flashback_boundary"
    assert live["speed_kph"] is None
    timing = observer.live_lap_timing_snapshot()
    assert timing["status"] == "unavailable"
    assert timing["reason"] == "flashback_boundary"
    assert timing["current_lap_time_ms"] is None

    _publish_frame(observer, frame=103, sequence=30)
    assert observer.live_telemetry_snapshot()["status"] == "fresh"
    assert observer.live_lap_timing_snapshot()["status"] == "fresh"


def test_live_monitor_regression_guard_ignores_event_only_frames():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100)

    _process(observer, _flashback_packet(frame=101, sequence=20, session_time=12.1, target_time=5.0))
    _process(observer, make_datagram(
        packet_id=3,
        session_uid=SESSION_UID,
        frame=102,
        session_time=10.0,
        body=b"SSTA" + b"\x00" * 12,
        sequence=21,
    ))
    _process(observer, _advance(103, 22))
    assert observer.live_telemetry_snapshot()["reason"] == "flashback_boundary"

    _process(
        observer,
        _telemetry_packet(frame=104, sequence=23),
    )
    _process(
        observer,
        _lap_packet(
            frame=104,
            lap_number=3,
            distance_m=150.0,
            session_time=5.1,
            current_lap_time_ms=200,
            sequence=24,
        ),
    )
    _process(observer, _advance(105, 25))

    assert observer.live_telemetry_snapshot()["status"] == "fresh"


def test_live_monitor_detects_clock_regression_between_same_frame_updates():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100)

    _process(observer, _telemetry_packet(frame=101, sequence=20))
    _process(
        observer,
        _lap_packet(
            frame=101,
            lap_number=3,
            distance_m=130.0,
            session_time=12.1,
            current_lap_time_ms=35_000,
            sequence=21,
        ),
    )
    _process(
        observer,
        _lap_packet(
            frame=101,
            lap_number=3,
            distance_m=140.0,
            session_time=11.9,
            current_lap_time_ms=35_100,
            sequence=22,
        ),
    )
    _process(observer, _advance(102, 23))

    live = observer.live_telemetry_snapshot()
    assert live["status"] == "unavailable"
    assert live["reason"] == "session_time_regression"
    assert live["speed_kph"] is None
    timing = observer.live_lap_timing_snapshot()
    assert timing["status"] == "unavailable"
    assert timing["reason"] == "session_time_regression"
    assert timing["current_lap_time_ms"] is None


def test_live_monitor_resets_on_player_and_session_change():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100, car_status=True)
    assert observer.live_telemetry_snapshot()["status"] == "fresh"
    assert observer.live_car_status_snapshot()["status"] == "fresh"
    assert observer.live_lap_timing_snapshot()["status"] == "fresh"

    _process(observer,
        _telemetry_packet(frame=110, sequence=20, player_car_index=1)
    )
    assert observer.live_telemetry_snapshot()["status"] == "waiting"
    assert observer.live_car_status_snapshot()["status"] == "waiting"
    assert observer.live_lap_timing_snapshot()["status"] == "waiting"
    _process(observer,
        _lap_packet(
            frame=110,
            lap_number=4,
            distance_m=5.0,
            current_lap_time_ms=100,
            session_time=14.0,
            sequence=21,
            player_car_index=1,
            active_car_index=1,
        )
    )
    _process(observer, _advance(111, 22, player_car_index=1))
    assert observer.live_telemetry_snapshot()["player_car_index"] == 1

    _process(observer,
        make_datagram(
            packet_id=255,
            session_uid=SESSION_UID + 1,
            frame=1,
            body=b"new session",
            sequence=23,
        )
    )
    live = observer.live_telemetry_snapshot()
    assert live["status"] == "waiting"
    assert "player_car_index" not in live
    assert observer.live_car_status_snapshot()["status"] == "waiting"
    assert observer.live_lap_timing_snapshot()["status"] == "waiting"


def test_live_monitor_reports_unsupported_packet_version_and_rejects_old_format():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100)

    _process(observer,
        _telemetry_packet(
            frame=110,
            sequence=20,
            packet_format=2026,
            packet_version=2,
        )
    )
    _process(
        observer,
        _car_status_packet(
            frame=110,
            sequence=21,
            packet_format=2026,
            packet_version=2,
        ),
    )
    _process(observer, _advance(111, 22, packet_format=2026))
    assert observer.live_telemetry_snapshot()["status"] == "unsupported"
    assert observer.live_car_status_snapshot()["status"] == "unsupported"

    _process(observer,
        make_datagram(
            packet_id=6,
            session_uid=SESSION_UID,
            frame=109,
            body=b"late old format",
            sequence=23,
        )
    )
    assert observer.live_telemetry_snapshot()["status"] == "unsupported"


def test_live_monitor_duplicate_envelopes_do_not_change_snapshot():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    telemetry = _telemetry_packet(frame=100, sequence=10)
    _process(observer, telemetry)
    _process(observer, telemetry)
    lap = _lap_packet(
        frame=100,
        lap_number=1,
        distance_m=1.0,
        current_lap_time_ms=100,
        session_time=1.0,
        sequence=11,
    )
    _process(observer, lap)
    _process(observer, lap)
    _process(observer, _advance(101, 12))

    assert observer.frames.duplicates_ignored == 2
    assert observer.live_telemetry_snapshot()["status"] == "fresh"
    assert observer.live_lap_timing_snapshot()["status"] == "fresh"


def test_live_lap_timing_does_not_promote_late_frames():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100, telemetry=False, current_lap_time_ms=80_000)
    _publish_frame(
        observer,
        frame=103,
        sequence=30,
        telemetry=False,
        current_lap_time_ms=83_000,
        sector_id=2,
    )

    late = _lap_packet(
        frame=101,
        lap_number=3,
        distance_m=150.0,
        session_time=99.0,
        current_lap_time_ms=10_000,
        sequence=40,
    )
    _process(observer, late)
    _process(observer, _advance(104, 41))

    timing = observer.live_lap_timing_snapshot()
    assert timing["status"] == "fresh"
    assert timing["frame_identifier"] == 103
    assert timing["current_lap_time_ms"] == 83_000
    assert timing["current_sector"] == 3
    assert observer.frames.late_packets_ignored >= 1


def test_live_monitor_player_change_barrier_rejects_older_buffered_snapshot():
    observer = _AcquisitionObserver(reorder_window_frames=3)
    _queue_frame(observer, frame=97, sequence=1, player_car_index=1)
    _process(observer, _advance(100, 3, player_car_index=1))
    assert observer.live_telemetry_snapshot()["frame_identifier"] == 97

    _queue_frame(observer, frame=100, sequence=10, player_car_index=1)
    _queue_frame(observer, frame=101, sequence=20, player_car_index=0)
    _queue_frame(observer, frame=102, sequence=30, player_car_index=1)
    _process(observer, _advance(103, 40, player_car_index=1))

    live = observer.live_telemetry_snapshot()
    assert live["status"] == "waiting"
    assert "frame_identifier" not in live


def test_live_monitor_discards_buffered_packets_from_previous_wire_format():
    observer = _AcquisitionObserver(reorder_window_frames=3)
    _queue_frame(observer, frame=97, sequence=1, player_car_index=0)
    _process(observer, _advance(100, 3))
    assert observer.live_telemetry_snapshot()["frame_identifier"] == 97

    _queue_frame(observer, frame=101, sequence=10, player_car_index=0)
    _process(
        observer,
        make_datagram(
            packet_format=2026,
            packet_id=200,
            session_uid=SESSION_UID,
            frame=104,
            sequence=20,
        ),
    )

    live = observer.live_telemetry_snapshot()
    assert observer.sessions.current_packet_format.value == 2026
    assert live["status"] == "waiting"
    assert "frame_identifier" not in live


def test_live_monitor_malformed_new_lap_clears_previous_values():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100)
    assert observer.live_telemetry_snapshot()["status"] == "fresh"

    malformed = _lap_packet(
        frame=102,
        lap_number=4,
        distance_m=130.0,
        session_time=14.0,
        sequence=20,
    )
    _process(observer, _telemetry_packet(frame=102, sequence=19))
    _process(observer, replace(malformed, payload=malformed.payload[:-1]))
    _process(observer, _advance(103, 21))

    live = observer.live_telemetry_snapshot()
    assert live["status"] == "unavailable"
    assert live["reason"] == "lap_data_decode_failed"
    assert live["frame_identifier"] == 102
    assert live["lap_number"] is None
    assert live["speed_kph"] is None


def test_live_monitor_ignores_delayed_unsupported_and_uid_zero_packets():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100)
    original = observer.live_telemetry_snapshot()

    _process(
        observer,
        _telemetry_packet(frame=100, sequence=30, packet_version=2),
    )
    _process(
        observer,
        make_datagram(
            packet_id=6,
            packet_version=2,
            session_uid=0,
            frame=200,
            sequence=31,
        ),
    )

    live = observer.live_telemetry_snapshot()
    assert live["status"] == original["status"]
    assert live["frame_identifier"] == original["frame_identifier"]
    assert live["speed_kph"] == original["speed_kph"]


def test_live_monitor_duplicate_does_not_refresh_receive_provenance():
    observer = _AcquisitionObserver(reorder_window_frames=3)
    telemetry = _telemetry_packet(frame=100, sequence=10)
    lap = _lap_packet(
        frame=100,
        lap_number=2,
        distance_m=10.0,
        session_time=11.0,
        current_lap_time_ms=1_234,
        sequence=11,
    )
    received_ns = time.monotonic_ns()
    observer.process(replace(telemetry, monotonic_ns=received_ns))
    observer.process(replace(lap, monotonic_ns=received_ns))
    _process(observer, _advance(101, 12))
    _process(observer, _advance(102, 13))
    observer.process(replace(telemetry, monotonic_ns=received_ns + 900_000_000))
    observer.process(replace(lap, monotonic_ns=received_ns + 900_000_000))
    _process(observer, _advance(103, 14))

    live = observer.live_telemetry_snapshot(received_ns + 900_000_000)
    assert observer.frames.duplicates_ignored == 2
    assert live["status"] == "stale"
    assert live["age_ms"] == 900


def test_live_monitor_malformed_telemetry_does_not_refresh_selected_values():
    observer = _AcquisitionObserver(reorder_window_frames=3)
    received_ns = time.monotonic_ns()
    telemetry = _telemetry_packet(frame=100, sequence=10)
    lap = _lap_packet(
        frame=100,
        lap_number=2,
        distance_m=10.0,
        session_time=11.0,
        current_lap_time_ms=1_234,
        sequence=11,
    )
    malformed = _telemetry_packet(frame=100, sequence=12, throttle=0.5)
    observer.process(replace(telemetry, monotonic_ns=received_ns))
    observer.process(replace(lap, monotonic_ns=received_ns))
    observer.process(
        replace(malformed, payload=malformed.payload[:-1], monotonic_ns=received_ns + 900_000_000)
    )
    _process(observer, _advance(101, 13))
    _process(observer, _advance(102, 14))
    _process(observer, _advance(103, 15))

    live = observer.live_telemetry_snapshot(received_ns + 900_000_000)
    assert live["status"] == "stale"
    assert live["age_ms"] == 900
    assert live["throttle"] == 0.75


def test_live_monitor_never_marks_missing_receive_provenance_fresh():
    observer = _AcquisitionObserver(reorder_window_frames=3)
    _publish_frame(observer, frame=100, sequence=1)

    for index in range(observer._max_receive_time_entries + 8):
        _process(
            observer,
            _telemetry_packet(
                frame=101,
                sequence=10 + index,
                throttle=(index + 1) / (observer._max_receive_time_entries + 9),
            ),
        )

    _process(observer, _advance(102, 5))
    _process(observer, _advance(103, 6))

    live = observer.live_telemetry_snapshot()
    assert live["status"] == "unavailable"
    assert live["reason"] == "receive_provenance_unavailable"
    assert live["age_ms"] is None
    assert live["throttle"] == 0.75


def test_live_monitor_requires_timestamp_for_every_selected_packet():
    observer = _AcquisitionObserver(reorder_window_frames=3)
    telemetry = _telemetry_packet(frame=100, sequence=10)
    lap = _lap_packet(
        frame=100,
        lap_number=2,
        distance_m=10.0,
        session_time=11.0,
        current_lap_time_ms=1_234,
        sequence=11,
    )
    _process(observer, telemetry)
    _process(observer, lap)
    _process(observer, _advance(101, 12))
    _process(observer, _advance(102, 13))
    telemetry_packet = observer.decoder.decode(telemetry)
    observer._receive_times.pop(
        (SESSION_UID, 100, telemetry_packet.wire_fingerprint)
    )
    _process(observer, _advance(103, 14))

    live = observer.live_telemetry_snapshot()
    assert live["status"] == "unavailable"
    assert live["reason"] == "receive_provenance_unavailable"
    assert live["age_ms"] is None


def test_managed_recording_exposes_freshness_and_hides_finished_live_state(
    tmp_path: Path,
):
    database = tmp_path / "archive.sqlite3"
    recordings_root = tmp_path / "recordings"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    app = create_app(
        database,
        recordings_root=recordings_root,
        control_token=_CONTROL_TOKEN,
        recording_host="127.0.0.1",
        recording_port=port,
    )

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                started = await client.post(
                    "/api/v1/recordings/start",
                    headers={"authorization": f"Bearer {_CONTROL_TOKEN}"},
                )
                assert started.status_code == 202
                recording_id = started.json()["data"]["recording_id"]
                for _ in range(200):
                    current = (
                        await client.get("/api/v1/recordings/current")
                    ).json()["data"]
                    if current["recording_id"] == recording_id:
                        if current["status"] == "recording":
                            break
                        assert current["status"] not in {
                            "failed",
                            "interrupted",
                        }
                    await asyncio.sleep(0.01)
                assert current["status"] == "recording"

                damage_bytes = [0] * 30
                damage_bytes[0:4] = [1, 2, 3, 4]
                damage_bytes[4:8] = [5, 6, 7, 8]
                damage_bytes[12:15] = [10, 11, 12]
                damage_bytes[21] = 13
                payloads = [
                    _telemetry_packet(
                        frame=100,
                        sequence=10,
                        engine_temperature_c=0,
                        brake_temperature_c=(400, 401, 402, 403),
                        tyre_surface_temperature_c=(91, 92, 93, 94),
                        tyre_inner_temperature_c=(101, 102, 103, 104),
                    ).payload,
                    _lap_packet(
                        frame=100,
                        lap_number=2,
                        distance_m=10.0,
                        session_time=11.0,
                        current_lap_time_ms=1_234,
                        last_lap_time_ms=80_456,
                        sector1_time_ms=26_543,
                        sector2_time_ms=30_987,
                        sector_id=2,
                        sequence=11,
                    ).payload,
                    _car_status_packet(frame=100, sequence=12).payload,
                    _car_damage_packet(
                        frame=100,
                        sequence=13,
                        wear=(0.0, 25.5, 50.0, 100.0),
                        byte_values=tuple(damage_bytes),
                    ).payload,
                    _car_setup_packet(frame=100, sequence=14).payload,
                    _session_conditions_packet(
                        frame=100,
                        sequence=15,
                        player_car_index=0,
                        weather_id=2,
                        track_temperature_c=35,
                        air_temperature_c=21,
                    ).payload,
                    _motion_packet(frame=100, sequence=16, player_car_index=0).payload,
                    _advance(103, 17).payload,
                ]
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    for payload in payloads:
                        sender.sendto(payload, ("127.0.0.1", port))

                for _ in range(100):
                    current = (
                        await client.get("/api/v1/recordings/current")
                    ).json()["data"]
                    live = current["progress"]["live_telemetry"]
                    if live["frame_identifier"] == 100:
                        break
                    await asyncio.sleep(0.01)

                assert live["status"] == "fresh"
                assert live["speed_kph"] == 100
                assert live["lap_time_ms"] == 1_234
                assert live["engine_temperature_c"] == 0
                assert live["brake_temperature_c"] == [400, 401, 402, 403]
                assert live["tyre_surface_temperature_c"] == [91, 92, 93, 94]
                assert live["tyre_inner_temperature_c"] == [101, 102, 103, 104]
                assert "_observed_monotonic_ns" not in live
                timing = current["progress"]["live_lap_timing"]
                assert timing["status"] == "fresh"
                assert timing["frame_identifier"] == 100
                assert timing["lap_number"] == 2
                assert timing["current_lap_time_ms"] == 1_234
                assert timing["current_sector"] == 3
                assert timing["previous_lap_time_ms"] == 80_456
                assert timing["sector1_time_ms"] == 26_543
                assert timing["sector2_time_ms"] == 30_987
                assert "_observed_monotonic_ns" not in timing
                status = current["progress"]["live_car_status"]
                assert status["status"] == "fresh"
                assert status["frame_identifier"] == 100
                assert status["fuel_in_tank_reported"] == 12.5
                assert "_observed_monotonic_ns" not in status
                damage = current["progress"]["live_car_damage"]
                assert damage["status"] == "fresh"
                assert damage["frame_identifier"] == 100
                assert damage["observation_count"] == 1
                assert damage["tyre_wear_percent"] == [0.0, 25.5, 50.0, 100.0]
                assert damage["engine_damage_percent"] == 13
                assert "_observed_monotonic_ns" not in damage
                setup = current["progress"]["live_car_setup"]
                assert setup["status"] == "fresh"
                assert setup["frame_identifier"] == 100
                assert setup["observation_count"] == 1
                assert setup["front_wing"] == 20
                assert setup["fuel_load"] == 30.5
                assert setup["next_front_wing_value"] == 45.5
                assert "_observed_monotonic_ns" not in setup
                conditions = current["progress"]["live_session_conditions"]
                assert conditions["status"] == "fresh"
                assert conditions["frame_identifier"] == 100
                assert conditions["observation_count"] == 1
                assert conditions["weather_id"] == 2
                assert conditions["weather_name"] == "overcast"
                assert conditions["track_temperature_c"] == 35
                assert conditions["air_temperature_c"] == 21
                assert "_observed_monotonic_ns" not in conditions
                motion = current["progress"]["live_motion"]
                assert motion["status"] == "fresh"
                assert motion["frame_identifier"] == 100
                assert motion["player_car_index"] == 0
                assert motion["observation_count"] == 1
                assert motion["world_position_m"] == [10.0, 20.0, 30.0]
                assert motion["world_velocity_mps"] == [1.0, 2.0, 3.0]
                assert "_observed_monotonic_ns" not in motion

                await asyncio.sleep(0.55)
                stale = (
                    await client.get("/api/v1/recordings/current")
                ).json()["data"]["progress"]["live_telemetry"]
                assert stale["status"] == "stale"
                assert stale["age_ms"] > 500
                stale_status = (
                    await client.get("/api/v1/recordings/current")
                ).json()["data"]["progress"]["live_car_status"]
                assert stale_status["status"] == "stale"
                assert stale_status["age_ms"] > 500
                stale_timing = (
                    await client.get("/api/v1/recordings/current")
                ).json()["data"]["progress"]["live_lap_timing"]
                assert stale_timing["status"] == "stale"
                assert stale_timing["age_ms"] > 500
                stale_damage = (
                    await client.get("/api/v1/recordings/current")
                ).json()["data"]["progress"]["live_car_damage"]
                assert stale_damage["status"] == "stale"
                assert stale_damage["age_ms"] > 500
                stale_setup = (
                    await client.get("/api/v1/recordings/current")
                ).json()["data"]["progress"]["live_car_setup"]
                assert stale_setup["status"] == "stale"
                assert stale_setup["age_ms"] > 500
                stale_conditions = (
                    await client.get("/api/v1/recordings/current")
                ).json()["data"]["progress"]["live_session_conditions"]
                assert stale_conditions["status"] == "stale"
                assert stale_conditions["age_ms"] > 500
                stale_motion = (
                    await client.get("/api/v1/recordings/current")
                ).json()["data"]["progress"]["live_motion"]
                assert stale_motion["status"] == "stale"
                assert stale_motion["age_ms"] > 500

                stopped = await client.post(
                    f"/api/v1/recordings/{recording_id}/stop",
                    headers={"authorization": f"Bearer {_CONTROL_TOKEN}"},
                )
                assert stopped.status_code == 202
                for _ in range(100):
                    completed = (
                        await client.get("/api/v1/recordings/current")
                    ).json()["data"]
                    if completed["status"] in {
                        "complete",
                        "failed",
                        "interrupted",
                    }:
                        break
                    await asyncio.sleep(0.01)

                assert completed["status"] == "complete"
                assert completed["progress"] is None
                capture = next(recordings_root.glob("f1e-*.f1ecap"))
                with CaptureReader(capture) as reader:
                    recorded = list(reader)
                assert [item.payload for item in recorded] == payloads

    asyncio.run(exercise())


def _publish_live_damage(
    observer: _AcquisitionObserver,
    *,
    frame: int = 100,
    sequence: int = 10,
    session_uid: int = SESSION_UID,
    player_car_index: int = 0,
    packet_format: int = 2025,
    packet_version: int = 1,
    wear: tuple[float, float, float, float] = (0.0, 25.5, 50.0, 100.0),
    byte_values: tuple[int, ...] | None = None,
) -> None:
    if byte_values is None:
        values = [0] * 30
        values[0:4] = [1, 2, 3, 4]
        values[4:8] = [5, 6, 7, 8]
        values[12:15] = [10, 11, 12]
        values[21] = 13
        byte_values = tuple(values)
    _process(
        observer,
        _car_damage_packet(
            frame=frame,
            sequence=sequence,
            session_uid=session_uid,
            player_car_index=player_car_index,
            packet_format=packet_format,
            packet_version=packet_version,
            wear=wear,
            byte_values=byte_values,
        ),
    )
    _process(
        observer,
        _advance(
            frame + 1,
            sequence + 1,
            session_uid=session_uid,
            player_car_index=player_car_index,
            packet_format=packet_format,
        ),
    )


@pytest.mark.parametrize(
    ("packet_format", "player_car_index"), ((2025, 0), (2026, 23))
)
def test_live_damage_maps_supported_formats_from_damage_only_frames(
    packet_format: int, player_car_index: int
):
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_damage(
        observer,
        player_car_index=player_car_index,
        packet_format=packet_format,
    )

    damage = observer.live_car_damage_snapshot()
    assert damage["status"] == "fresh"
    assert damage["session_uid"] == str(SESSION_UID)
    assert damage["packet_format"] == packet_format
    assert damage["frame_identifier"] == 100
    assert damage["player_car_index"] == player_car_index
    assert damage["tyre_wear_percent"] == [0.0, 25.5, 50.0, 100.0]
    assert damage["tyre_damage_percent"] == [1, 2, 3, 4]
    assert damage["brake_damage_percent"] == [5, 6, 7, 8]
    assert damage["front_left_wing_damage_percent"] == 10
    assert damage["front_right_wing_damage_percent"] == 11
    assert damage["rear_wing_damage_percent"] == 12
    assert damage["engine_damage_percent"] == 13
    assert damage["validation_flags"] == []
    assert damage["observation_count"] == 1
    assert observer.live_telemetry_snapshot()["status"] == "waiting"
    assert observer.live_lap_timing_snapshot()["status"] == "waiting"


def test_live_damage_retains_sparse_observation_and_ages_independently():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_damage(observer)
    initial = observer.live_car_damage_snapshot()
    observed_ns = initial["_observed_monotonic_ns"]

    _process(observer, _advance(102, 20))
    _process(observer, _advance(103, 21))
    later = observer.live_car_damage_snapshot(observed_ns + 501_000_000)

    assert later["status"] == "stale"
    assert later["age_ms"] == 501
    assert later["frame_identifier"] == 100
    assert later["tyre_wear_percent"] == [0.0, 25.5, 50.0, 100.0]
    assert later["observation_count"] == 1
    assert observer.live_telemetry_snapshot()["status"] == "waiting"


def test_live_damage_uses_oldest_agreeing_receive_time_and_duplicate_does_not_refresh():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    base_ns = 4_000_000_000
    packet = _car_damage_packet(frame=100, sequence=10)
    observer.process(replace(packet, monotonic_ns=base_ns))
    observer.process(replace(packet, monotonic_ns=base_ns + 300_000_000))
    observer.process(
        replace(_advance(101, 11), monotonic_ns=base_ns + 300_000_000)
    )

    damage = observer.live_car_damage_snapshot(base_ns + 300_000_000)
    assert damage["status"] == "fresh"
    assert damage["_observed_monotonic_ns"] == base_ns
    assert damage["age_ms"] == 300
    assert damage["observation_count"] == 1


def test_live_damage_invalid_fields_are_null_without_suppressing_other_values():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    bytes_ = [0] * 30
    bytes_[0:4] = [101, 2, 3, 4]
    bytes_[4:8] = [5, 6, 7, 8]
    bytes_[21] = 254
    _publish_live_damage(
        observer,
        wear=(float("nan"), 25.0, 50.0, 100.0),
        byte_values=tuple(bytes_),
    )

    damage = observer.live_car_damage_snapshot()
    assert damage["status"] == "fresh"
    assert damage["tyre_wear_percent"] == [None, 25.0, 50.0, 100.0]
    assert damage["tyre_damage_percent"] == [None, 2, 3, 4]
    assert damage["brake_damage_percent"] == [5, 6, 7, 8]
    assert damage["engine_damage_percent"] is None
    assert set(damage["validation_flags"]) == {
        "invalid_car_damage_tyre_wear_rl_percent",
        "invalid_car_damage_tyre_damage_rl_percent",
        "invalid_car_damage_engine_damage_percent",
    }


def test_live_damage_conflicts_malformed_and_unsupported_packets_fail_closed():
    conflicting = _AcquisitionObserver(reorder_window_frames=1)
    _process(
        conflicting,
        _car_damage_packet(frame=100, sequence=10, wear=(1.0, 2.0, 3.0, 4.0)),
    )
    _process(
        conflicting,
        _car_damage_packet(frame=100, sequence=11, wear=(4.0, 3.0, 2.0, 1.0)),
    )
    _process(conflicting, _advance(101, 12))
    conflict = conflicting.live_car_damage_snapshot()
    assert conflict["status"] == "unavailable"
    assert conflict["reason"] == "conflicting_car_damage_packets"
    assert conflict["tyre_wear_percent"] == [None, None, None, None]

    malformed = _AcquisitionObserver(reorder_window_frames=1)
    _process(
        malformed,
        _car_damage_packet(frame=100, sequence=10, body=b"short damage body"),
    )
    _process(malformed, _advance(101, 11))
    assert malformed.live_car_damage_snapshot()["reason"] == "car_damage_decode_failed"

    unsupported = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_damage(unsupported, packet_version=2)
    rejected = unsupported.live_car_damage_snapshot()
    assert rejected["status"] == "unsupported"
    assert rejected["reason"] == "car_damage_adapter_unsupported"


def test_live_damage_requires_provenance_for_every_agreeing_candidate():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    observer._max_receive_time_entries = 0
    _process(observer, _car_damage_packet(frame=100, sequence=10))
    _process(observer, _advance(101, 11))

    damage = observer.live_car_damage_snapshot()
    assert damage["status"] == "unavailable"
    assert damage["reason"] == "receive_provenance_unavailable"
    assert damage["age_ms"] is None
    assert damage["tyre_wear_percent"] == [None, None, None, None]
    assert damage["observation_count"] == 1


def test_live_damage_clears_on_player_and_flashback_boundaries():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_damage(observer)
    _process(observer, _advance(102, 20, player_car_index=1))
    cleared = observer.live_car_damage_snapshot()
    assert cleared["status"] == "waiting"
    assert cleared["observation_count"] == 0

    _publish_live_damage(observer, frame=103, sequence=21, player_car_index=1)
    assert observer.live_car_damage_snapshot()["observation_count"] == 1
    _process(
        observer,
        _flashback_packet(
            frame=105,
            sequence=30,
            session_time=14.0,
            target_time=12.0,
        ),
    )
    _process(observer, _advance(106, 31, player_car_index=1))
    rewound = observer.live_car_damage_snapshot()
    assert rewound["status"] == "waiting"
    assert rewound["observation_count"] == 0


def test_live_damage_resets_its_epoch_on_session_and_format_changes():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_damage(observer)
    next_session = SESSION_UID + 100
    _publish_live_damage(
        observer,
        frame=200,
        sequence=30,
        session_uid=next_session,
    )
    after_session = observer.live_car_damage_snapshot()
    assert after_session["session_uid"] == str(next_session)
    assert after_session["observation_count"] == 1

    _publish_live_damage(
        observer,
        frame=202,
        sequence=40,
        player_car_index=23,
        packet_format=2026,
        session_uid=next_session,
    )
    after_format = observer.live_car_damage_snapshot()
    assert after_format["session_uid"] == str(next_session)
    assert after_format["packet_format"] == 2026
    assert after_format["player_car_index"] == 23
    assert after_format["observation_count"] == 1


def test_live_damage_handles_frame_wrap_and_synthetic_session_time_rewind():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_damage(observer, frame=0xFFFFFFFE, sequence=10)
    _publish_live_damage(observer, frame=0, sequence=20)
    wrapped = observer.live_car_damage_snapshot()
    assert wrapped["frame_identifier"] == 0
    assert wrapped["observation_count"] == 2

    regressed = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(regressed, frame=100)
    _process(regressed, _car_damage_packet(frame=101, sequence=20))
    _process(
        regressed,
        _lap_packet(
            frame=101,
            lap_number=3,
            distance_m=125.0,
            session_time=1.0,
            current_lap_time_ms=1_000,
            sequence=21,
        ),
    )
    _process(regressed, _advance(102, 22))
    reset = regressed.live_car_damage_snapshot()
    assert reset["status"] == "waiting"
    assert reset["observation_count"] == 0


def test_live_damage_api_record_bounds_fixed_arrays_and_zeroes():
    damage = LiveCarDamageRecord(
        status="fresh",
        reason=None,
        age_ms=0,
        observation_count=1,
        tyre_wear_percent=(0.0, 25.5, None, 100.0),
        tyre_damage_percent=(0, 1, None, 100),
        brake_damage_percent=(0, 1, 2, 3),
        engine_damage_percent=0,
    )
    assert damage.tyre_wear_percent == (0.0, 25.5, None, 100.0)
    assert damage.engine_damage_percent == 0

    with pytest.raises(ValidationError):
        LiveCarDamageRecord(
            status="fresh",
            reason=None,
            age_ms=0,
            observation_count=1,
            tyre_damage_percent=(0, 1, 2),
        )
    with pytest.raises(ValidationError):
        LiveCarDamageRecord(
            status="fresh",
            reason=None,
            age_ms=0,
            observation_count=1,
            engine_damage_percent=101,
        )


def _publish_live_car_setup(
    observer: _AcquisitionObserver,
    *,
    frame: int = 100,
    sequence: int = 10,
    session_uid: int = SESSION_UID,
    player_car_index: int = 0,
    packet_format: int = 2025,
    packet_version: int = 1,
    session_time_s: float = 12.5,
    **kwargs,
) -> None:
    _process(
        observer,
        _car_setup_packet(
            frame=frame,
            sequence=sequence,
            session_uid=session_uid,
            player_car_index=player_car_index,
            packet_format=packet_format,
            packet_version=packet_version,
            session_time_s=session_time_s,
            **kwargs,
        ),
    )
    _process(
        observer,
        _advance(
            frame + 1,
            sequence + 1,
            session_uid=session_uid,
            player_car_index=player_car_index,
            packet_format=packet_format,
        ),
    )


@pytest.mark.parametrize(
    ("packet_format", "player_car_index"), ((2025, 0), (2026, 23))
)
def test_live_setup_maps_supported_formats_from_setup_only_frames(
    packet_format: int, player_car_index: int
):
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_car_setup(
        observer,
        player_car_index=player_car_index,
        packet_format=packet_format,
    )

    setup = observer.live_car_setup_snapshot()
    assert setup["status"] == "fresh"
    assert setup["session_uid"] == str(SESSION_UID)
    assert setup["packet_format"] == packet_format
    assert setup["frame_identifier"] == 100
    assert setup["player_car_index"] == player_car_index
    assert setup["session_time_s"] == 12.5
    assert setup["front_wing"] == 20
    assert setup["rear_wing"] == 30
    assert setup["on_throttle_differential"] == 50
    assert setup["off_throttle_differential"] == 55
    assert setup["front_camber"] == -3.5
    assert setup["rear_camber"] == -1.5
    assert setup["front_toe"] == pytest.approx(0.1)
    assert setup["rear_toe"] == pytest.approx(0.2)
    assert setup["front_suspension"] == 5
    assert setup["rear_suspension"] == 6
    assert setup["front_anti_roll_bar"] == 7
    assert setup["rear_anti_roll_bar"] == 8
    assert setup["front_suspension_height"] == 9
    assert setup["rear_suspension_height"] == 10
    assert setup["brake_pressure_percent"] == 100
    assert setup["brake_bias_percent"] == 55
    assert setup["engine_braking_percent"] == 20
    assert setup["rear_left_tyre_pressure_psi"] == 22.0
    assert setup["rear_right_tyre_pressure_psi"] == pytest.approx(22.1)
    assert setup["front_left_tyre_pressure_psi"] == pytest.approx(22.2)
    assert setup["front_right_tyre_pressure_psi"] == pytest.approx(22.3)
    assert setup["ballast"] == 10
    assert setup["fuel_load"] == 30.5
    assert setup["next_front_wing_value"] == 45.5
    assert setup["validation_flags"] == []
    assert setup["observation_count"] == 1
    assert observer.live_telemetry_snapshot()["status"] == "waiting"
    assert observer.live_lap_timing_snapshot()["status"] == "waiting"


def test_live_setup_retains_sparse_observation_and_ages_independently():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_car_setup(observer)
    initial = observer.live_car_setup_snapshot()
    observed_ns = initial["_observed_monotonic_ns"]

    _process(observer, _car_damage_packet(frame=102, sequence=20))
    _process(observer, _advance(103, 21))
    later = observer.live_car_setup_snapshot(observed_ns + 501_000_000)

    assert later["status"] == "stale"
    assert later["age_ms"] == 501
    assert later["frame_identifier"] == 100
    assert later["front_wing"] == 20
    assert later["observation_count"] == 1
    assert observer.live_car_damage_snapshot()["status"] == "fresh"


def test_live_setup_uses_oldest_receive_time_and_ignores_duplicates_late_and_other_car_changes():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    base_ns = 5_000_000_000
    first = _car_setup_packet(frame=100, sequence=10)
    other_car_changed = _car_setup_packet(
        frame=100,
        sequence=11,
        other_car_front_wing=99,
    )
    observer.process(replace(first, monotonic_ns=base_ns))
    observer.process(replace(first, monotonic_ns=base_ns + 200_000_000))
    observer.process(replace(other_car_changed, monotonic_ns=base_ns + 300_000_000))
    observer.process(
        replace(_advance(101, 12), monotonic_ns=base_ns + 300_000_000)
    )
    observer.process(
        replace(
            _car_setup_packet(frame=100, sequence=13, front_wing=77),
            monotonic_ns=base_ns + 400_000_000,
        )
    )

    setup = observer.live_car_setup_snapshot(base_ns + 400_000_000)
    assert setup["status"] == "fresh"
    assert setup["_observed_monotonic_ns"] == base_ns
    assert setup["age_ms"] == 400
    assert setup["observation_count"] == 1


@pytest.mark.parametrize("conflict_kind", ["record", "next_front_wing"])
def test_live_setup_rejects_conflicting_selected_player_evidence(conflict_kind: str):
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _process(observer, _car_setup_packet(frame=100, sequence=10))
    if conflict_kind == "record":
        conflict = _car_setup_packet(frame=100, sequence=11, front_wing=21)
    else:
        conflict = _car_setup_packet(
            frame=100, sequence=11, next_front_wing_value=46.0
        )
    _process(observer, conflict)
    _process(observer, _advance(101, 12))

    setup = observer.live_car_setup_snapshot()
    assert setup["status"] == "unavailable"
    assert setup["reason"] == "conflicting_car_setup_packets"
    assert setup["front_wing"] is None
    assert setup["next_front_wing_value"] is None
    assert setup["observation_count"] == 0


def test_live_setup_invalid_floats_are_null_and_flagged_independently():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_car_setup(
        observer,
        front_camber=float("nan"),
        next_front_wing_value=float("inf"),
    )

    setup = observer.live_car_setup_snapshot()
    assert setup["status"] == "fresh"
    assert setup["front_wing"] == 20
    assert setup["front_camber"] is None
    assert setup["rear_camber"] == -1.5
    assert setup["next_front_wing_value"] is None
    assert set(setup["validation_flags"]) == {
        "invalid_car_setup_front_camber",
        "invalid_car_setup_next_front_wing_value",
    }


def test_live_setup_malformed_unsupported_and_missing_provenance_are_unavailable():
    malformed = _AcquisitionObserver(reorder_window_frames=1)
    _process(
        malformed,
        _car_setup_packet(frame=100, sequence=10, body=b"short setup body"),
    )
    _process(malformed, _advance(101, 11))
    assert malformed.live_car_setup_snapshot()["reason"] == "car_setup_decode_failed"

    unsupported = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_car_setup(unsupported, packet_version=2)
    rejected = unsupported.live_car_setup_snapshot()
    assert rejected["status"] == "unsupported"
    assert rejected["reason"] == "car_setup_adapter_unsupported"

    no_provenance = _AcquisitionObserver(reorder_window_frames=1)
    no_provenance._max_receive_time_entries = 0
    _process(no_provenance, _car_setup_packet(frame=100, sequence=10))
    _process(no_provenance, _advance(101, 11))
    missing = no_provenance.live_car_setup_snapshot()
    assert missing["status"] == "unavailable"
    assert missing["reason"] == "receive_provenance_unavailable"
    assert missing["observation_count"] == 0


def test_live_setup_resets_on_player_session_format_rewind_and_frame_wrap():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_car_setup(observer)
    _process(observer, _advance(102, 20, player_car_index=1))
    assert observer.live_car_setup_snapshot()["status"] == "waiting"
    assert observer.live_car_setup_snapshot()["observation_count"] == 0

    _publish_live_car_setup(observer, frame=103, sequence=21, player_car_index=1)
    next_session = SESSION_UID + 100
    _publish_live_car_setup(
        observer,
        frame=200,
        sequence=30,
        player_car_index=1,
        session_uid=next_session,
    )
    assert observer.live_car_setup_snapshot()["observation_count"] == 1
    _publish_live_car_setup(
        observer,
        frame=202,
        sequence=40,
        player_car_index=23,
        packet_format=2026,
        session_uid=next_session,
    )
    assert observer.live_car_setup_snapshot()["packet_format"] == 2026
    assert observer.live_car_setup_snapshot()["player_car_index"] == 23
    assert observer.live_car_setup_snapshot()["observation_count"] == 1

    _process(
        observer,
        _flashback_packet(
            frame=204,
            sequence=50,
            session_time=14.0,
            target_time=12.0,
            session_uid=next_session,
            packet_format=2026,
        ),
    )
    _process(
        observer,
        _advance(205, 51, player_car_index=23, packet_format=2026, session_uid=next_session),
    )
    assert observer.live_car_setup_snapshot()["status"] == "waiting"
    assert observer.live_car_setup_snapshot()["observation_count"] == 0

    wrapped = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_car_setup(wrapped, frame=0xFFFFFFFE, sequence=10)
    _publish_live_car_setup(wrapped, frame=0, sequence=20)
    assert wrapped.live_car_setup_snapshot()["frame_identifier"] == 0
    assert wrapped.live_car_setup_snapshot()["observation_count"] == 2


def test_live_setup_api_record_bounds_wire_values_and_zeroes():
    setup = LiveCarSetupRecord(
        status="fresh",
        reason=None,
        age_ms=0,
        observation_count=1,
        front_wing=0,
        brake_bias_percent=0,
        front_camber=0.0,
        fuel_load=0.0,
        next_front_wing_value=0.0,
    )
    assert setup.front_wing == 0
    assert setup.brake_bias_percent == 0
    assert setup.fuel_load == 0.0
    assert setup.next_front_wing_value == 0.0

    with pytest.raises(ValidationError):
        LiveCarSetupRecord(
            status="fresh",
            reason=None,
            age_ms=0,
            observation_count=1,
            front_wing=256,
        )
    with pytest.raises(ValidationError):
        LiveCarSetupRecord(
            status="fresh",
            reason=None,
            age_ms=0,
            observation_count=1,
            front_camber=float("nan"),
        )


@pytest.mark.parametrize("packet_format", (2025, 2026))
def test_live_session_conditions_use_session_only_frames_and_keep_signed_unknown_values(
    packet_format: int,
):
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_session_conditions(
        observer,
        packet_format=packet_format,
        weather_id=254,
        track_temperature_c=0,
        air_temperature_c=-12,
    )

    conditions = observer.live_session_conditions_snapshot()
    assert conditions["status"] == "fresh"
    assert conditions["session_uid"] == str(SESSION_UID)
    assert conditions["packet_format"] == packet_format
    assert conditions["frame_identifier"] == 100
    assert conditions["weather_id"] == 254
    assert conditions["weather_name"] is None
    assert conditions["track_temperature_c"] == 0
    assert conditions["air_temperature_c"] == -12
    assert conditions["observation_count"] == 1
    assert observer.live_telemetry_snapshot()["status"] == "waiting"
    assert observer.live_lap_timing_snapshot()["status"] == "waiting"


def test_live_session_conditions_require_frame_agreement_and_use_oldest_receive_time():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    base_ns = 5_000_000_000
    first = _session_conditions_packet(
        frame=100, sequence=10, session_time_s=12.5, weather_id=1
    )
    agreeing = _session_conditions_packet(
        frame=100, sequence=11, session_time_s=12.6, weather_id=1
    )
    observer.process(replace(first, monotonic_ns=base_ns))
    observer.process(replace(agreeing, monotonic_ns=base_ns + 200_000_000))
    observer.process(replace(_advance(101, 12), monotonic_ns=base_ns + 300_000_000))

    conditions = observer.live_session_conditions_snapshot(base_ns + 400_000_000)
    assert conditions["status"] == "fresh"
    assert conditions["_observed_monotonic_ns"] == base_ns
    assert conditions["age_ms"] == 400
    assert conditions["observation_count"] == 1
    assert conditions["session_time_s"] == 12.5

    # A late copy and unrelated later frame cannot refresh the sparse observation.
    observer.process(replace(first, monotonic_ns=base_ns + 450_000_000))
    observer.process(
        replace(_advance(102, 13), monotonic_ns=base_ns + 500_000_000)
    )
    unchanged = observer.live_session_conditions_snapshot(base_ns + 501_000_000)
    assert unchanged["observation_count"] == 1
    assert unchanged["_observed_monotonic_ns"] == base_ns
    assert unchanged["age_ms"] == 501
    assert unchanged["status"] == "stale"


def test_live_session_conditions_reject_conflict_malformed_unsupported_and_missing_provenance():
    conflicting = _AcquisitionObserver(reorder_window_frames=1)
    _process(conflicting, _session_conditions_packet(frame=100, sequence=10, weather_id=0))
    _process(conflicting, _session_conditions_packet(frame=100, sequence=11, weather_id=3))
    _process(conflicting, _advance(101, 12))
    rejected = conflicting.live_session_conditions_snapshot()
    assert rejected["status"] == "unavailable"
    assert rejected["reason"] == "conflicting_session_context_packets"
    assert rejected["observation_count"] == 0

    malformed = _AcquisitionObserver(reorder_window_frames=1)
    _process(
        malformed,
        make_datagram(
            packet_id=1,
            session_uid=SESSION_UID,
            frame=100,
            body=_session_packet().payload[29:-1],
            sequence=10,
        ),
    )
    _process(malformed, _advance(101, 11))
    assert malformed.live_session_conditions_snapshot()["reason"] == (
        "session_context_decode_failed"
    )

    unsupported = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_session_conditions(unsupported, packet_version=2)
    unsupported_result = unsupported.live_session_conditions_snapshot()
    assert unsupported_result["status"] == "unsupported"
    assert unsupported_result["reason"] == "session_context_adapter_unsupported"
    assert unsupported_result["observation_count"] == 0

    no_provenance = _AcquisitionObserver(reorder_window_frames=1)
    no_provenance._max_receive_time_entries = 0
    _publish_live_session_conditions(no_provenance)
    missing = no_provenance.live_session_conditions_snapshot()
    assert missing["status"] == "unavailable"
    assert missing["reason"] == "receive_provenance_unavailable"
    assert missing["observation_count"] == 0


def test_live_session_conditions_cache_eviction_cannot_create_a_fresh_observation():
    observer = _AcquisitionObserver(reorder_window_frames=3)
    observer._max_receive_time_entries = 1
    _process(
        observer,
        _session_conditions_packet(frame=100, sequence=10, player_car_index=0),
    )
    for frame, sequence in ((101, 11), (102, 12), (103, 13)):
        _process(
            observer,
            _car_status_packet(
                frame=frame, sequence=sequence, player_car_index=0
            ),
        )
    _process(observer, _advance(104, 14, player_car_index=0))

    conditions = observer.live_session_conditions_snapshot()
    assert conditions["status"] == "unavailable"
    assert conditions["reason"] == "receive_provenance_unavailable"
    assert conditions["observation_count"] == 0


def test_live_session_conditions_survive_player_changes_and_reset_on_boundaries():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_session_conditions(observer, player_car_index=0)
    observed = observer.live_session_conditions_snapshot()
    _process(observer, _advance(102, 20, player_car_index=1))
    changed_player = observer.live_session_conditions_snapshot()
    assert changed_player["status"] == "fresh"
    assert changed_player["observation_count"] == 1
    assert changed_player["_observed_monotonic_ns"] == observed[
        "_observed_monotonic_ns"
    ]

    _publish_live_session_conditions(observer, frame=103, sequence=21, player_car_index=1)
    _process(
        observer,
        _flashback_packet(frame=105, sequence=30, session_time=14.0, target_time=12.0),
    )
    _process(observer, _advance(106, 31, player_car_index=1))
    rewound = observer.live_session_conditions_snapshot()
    assert rewound["status"] == "waiting"
    assert rewound["observation_count"] == 0

    next_session = SESSION_UID + 100
    _publish_live_session_conditions(
        observer, frame=1, sequence=40, session_uid=next_session, player_car_index=1
    )
    new_session = observer.live_session_conditions_snapshot()
    assert new_session["session_uid"] == str(next_session)
    assert new_session["observation_count"] == 1

    _publish_live_session_conditions(
        observer,
        frame=200,
        sequence=50,
        session_uid=next_session,
        packet_format=2026,
        player_car_index=23,
    )
    new_format = observer.live_session_conditions_snapshot()
    assert new_format["packet_format"] == 2026
    assert new_format["frame_identifier"] == 200
    assert new_format["observation_count"] == 1


def test_live_session_conditions_wrap_frames_and_api_preserves_signed_values():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_session_conditions(observer, frame=0xFFFFFFFE, sequence=10)
    _publish_live_session_conditions(observer, frame=0, sequence=20)
    wrapped = observer.live_session_conditions_snapshot()
    assert wrapped["frame_identifier"] == 0
    assert wrapped["observation_count"] == 2

    conditions = LiveSessionConditionsRecord(
        status="fresh",
        reason=None,
        age_ms=0,
        observation_count=1,
        weather_id=254,
        air_temperature_c=-12,
        track_temperature_c=0,
    )
    assert conditions.weather_id == 254
    assert conditions.air_temperature_c == -12
    assert conditions.track_temperature_c == 0
    with pytest.raises(ValidationError):
        LiveSessionConditionsRecord(
            status="fresh", reason=None, age_ms=0, observation_count=1,
            air_temperature_c=-129,
        )


def test_live_motion_api_requires_finite_fixed_xyz_vectors():
    motion = LiveMotionRecord(
        status="fresh",
        reason=None,
        age_ms=0,
        observation_count=1,
        player_car_index=23,
        world_position_m=(0.0, -2.5, 10.25),
        world_velocity_mps=(0.0, -1.0, 3.5),
    )
    assert motion.player_car_index == 23
    assert motion.world_position_m == (0.0, -2.5, 10.25)
    assert motion.world_velocity_mps == (0.0, -1.0, 3.5)
    with pytest.raises(ValidationError):
        LiveMotionRecord(
            status="fresh",
            reason=None,
            age_ms=0,
            observation_count=1,
            world_position_m=(1.0, 2.0),
        )
    with pytest.raises(ValidationError):
        LiveMotionRecord(
            status="fresh",
            reason=None,
            age_ms=0,
            observation_count=1,
            world_velocity_mps=(1.0, float("nan"), 3.0),
        )


@pytest.mark.parametrize(("packet_format", "player_index"), ((2025, 0), (2026, 23)))
def test_live_motion_maps_supported_formats_from_motion_only_frames(
    packet_format: int, player_index: int
):
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_motion(
        observer,
        packet_format=packet_format,
        player_car_index=player_index,
        position=(0.0, -2.5, 10.25),
        velocity=(0.0, -1.0, 3.5),
    )

    motion = observer.live_motion_snapshot()
    assert motion["status"] == "fresh"
    assert motion["session_uid"] == str(SESSION_UID)
    assert motion["packet_format"] == packet_format
    assert motion["player_car_index"] == player_index
    assert motion["frame_identifier"] == 100
    assert motion["session_time_s"] == 12.5
    assert motion["world_position_m"] == (0.0, -2.5, 10.25)
    assert motion["world_velocity_mps"] == (0.0, -1.0, 3.5)
    assert motion["validation_flags"] == []
    assert motion["observation_count"] == 1
    assert observer.live_telemetry_snapshot()["status"] == "waiting"
    assert observer.live_lap_timing_snapshot()["status"] == "waiting"


def test_live_motion_agrees_on_selected_raw_record_ignores_other_cars_and_uses_oldest_time():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    base_ns = 5_000_000_000
    first = _motion_packet(frame=100, sequence=10, session_time_s=12.5)
    other_car_changed = _motion_packet(
        frame=100,
        sequence=11,
        session_time_s=12.6,
        unrelated_car_position=(50.0, 60.0, 70.0),
    )
    observer.process(replace(first, monotonic_ns=base_ns))
    observer.process(replace(other_car_changed, monotonic_ns=base_ns + 200_000_000))
    observer.process(replace(_advance(101, 12), monotonic_ns=base_ns + 300_000_000))

    motion = observer.live_motion_snapshot(base_ns + 400_000_000)
    assert motion["status"] == "fresh"
    assert motion["world_position_m"] == (10.0, 20.0, 30.0)
    assert motion["world_velocity_mps"] == (1.0, 2.0, 3.0)
    assert motion["session_time_s"] == 12.5
    assert motion["_observed_monotonic_ns"] == base_ns
    assert motion["age_ms"] == 400
    assert motion["observation_count"] == 1


def test_live_motion_conflicts_only_when_selected_records_differ():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _process(observer, _motion_packet(frame=100, sequence=10))
    _process(
        observer,
        _motion_packet(frame=100, sequence=11, position=(11.0, 20.0, 30.0)),
    )
    _process(observer, _advance(101, 12))

    motion = observer.live_motion_snapshot()
    assert motion["status"] == "unavailable"
    assert motion["reason"] == "conflicting_selected_player_motion_records"
    assert motion["world_position_m"] is None
    assert motion["world_velocity_mps"] is None
    assert motion["observation_count"] == 0


def test_live_motion_is_unavailable_when_same_frame_player_identity_conflicts():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _process(observer, _advance(100, 10, player_car_index=0))
    _process(observer, _advance(100, 11, player_car_index=1))
    _process(observer, _motion_packet(frame=100, sequence=12, player_car_index=0))
    observer.finish()

    motion = observer.live_motion_snapshot()
    assert motion["status"] == "unavailable"
    assert motion["reason"] == "player_identity_unavailable"
    assert motion["player_car_index"] is None
    assert motion["observation_count"] == 0


def test_live_motion_validates_position_and_velocity_independently():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_motion(
        observer,
        position=(float("nan"), 2.0, 3.0),
        velocity=(0.0, -1.0, 3.5),
    )

    motion = observer.live_motion_snapshot()
    assert motion["status"] == "fresh"
    assert motion["world_position_m"] is None
    assert motion["world_velocity_mps"] == (0.0, -1.0, 3.5)
    assert motion["validation_flags"] == ["invalid_motion_world_position"]

    _publish_live_motion(
        observer, frame=102, sequence=20, position=(1.0, 2.0, 3.0),
        velocity=(1.0, float("inf"), 3.0),
    )
    motion = observer.live_motion_snapshot()
    assert motion["world_position_m"] == (1.0, 2.0, 3.0)
    assert motion["world_velocity_mps"] is None
    assert motion["validation_flags"] == ["invalid_motion_world_velocity"]


def test_live_motion_rejects_malformed_unsupported_and_incomplete_timestamp_evidence():
    malformed = _AcquisitionObserver(reorder_window_frames=1)
    short_body = _motion_packet().payload[29:-1]
    _process(
        malformed,
        make_datagram(
            packet_id=0,
            session_uid=SESSION_UID,
            frame=100,
            player_car_index=0,
            body=short_body,
            sequence=10,
        ),
    )
    _process(malformed, _advance(101, 11))
    assert malformed.live_motion_snapshot()["reason"] == "motion_decode_failed"

    unsupported = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_motion(unsupported, packet_version=2)
    rejected = unsupported.live_motion_snapshot()
    assert rejected["status"] == "unsupported"
    assert rejected["reason"] == "motion_adapter_unsupported"
    assert rejected["observation_count"] == 0

    no_provenance = _AcquisitionObserver(reorder_window_frames=1)
    no_provenance._max_receive_time_entries = 0
    _publish_live_motion(no_provenance)
    missing = no_provenance.live_motion_snapshot()
    assert missing["status"] == "unavailable"
    assert missing["reason"] == "receive_provenance_unavailable"
    assert missing["observation_count"] == 0

    partial = _AcquisitionObserver(reorder_window_frames=1)
    partial._max_receive_time_entries = 1
    _process(partial, _motion_packet(frame=100, sequence=10))
    _process(
        partial,
        _motion_packet(
            frame=100,
            sequence=11,
            unrelated_car_position=(20.0, 30.0, 40.0),
        ),
    )
    _process(partial, _advance(101, 12))
    evicted = partial.live_motion_snapshot()
    assert evicted["status"] == "unavailable"
    assert evicted["reason"] == "receive_provenance_unavailable"
    assert evicted["observation_count"] == 0


def test_live_motion_does_not_refresh_on_duplicates_late_or_unrelated_frames():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    first = _motion_packet(frame=100, sequence=10)
    _process(observer, first)
    _process(observer, _advance(101, 11))
    initial = observer.live_motion_snapshot()
    observed_ns = initial["_observed_monotonic_ns"]

    _process(observer, replace(first, sequence=12))
    _process(
        observer,
        _motion_packet(frame=100, sequence=13, position=(99.0, 98.0, 97.0)),
    )
    _process(observer, _car_damage_packet(frame=102, sequence=14))
    _process(observer, _advance(103, 15))
    aged = observer.live_motion_snapshot(observed_ns + 501_000_000)
    assert aged["status"] == "stale"
    assert aged["age_ms"] == 501
    assert aged["_observed_monotonic_ns"] == observed_ns
    assert aged["world_position_m"] == (10.0, 20.0, 30.0)
    assert aged["observation_count"] == 1


def test_live_motion_resets_on_player_session_format_and_explicit_rewind():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_motion(observer, player_car_index=0)
    _process(observer, _advance(102, 20, player_car_index=1))
    assert observer.live_motion_snapshot()["status"] == "waiting"
    assert observer.live_motion_snapshot()["observation_count"] == 0

    _publish_live_motion(observer, frame=103, sequence=21, player_car_index=1)
    _process(
        observer,
        _flashback_packet(
            frame=105, sequence=30, session_time=14.0, target_time=12.0,
        ),
    )
    _process(observer, _advance(106, 31, player_car_index=1))
    assert observer.live_motion_snapshot()["status"] == "waiting"

    next_session = SESSION_UID + 100
    _publish_live_motion(
        observer,
        frame=1,
        sequence=40,
        session_uid=next_session,
        player_car_index=1,
    )
    assert observer.live_motion_snapshot()["session_uid"] == str(next_session)
    assert observer.live_motion_snapshot()["observation_count"] == 1

    _publish_live_motion(
        observer,
        frame=200,
        sequence=50,
        session_uid=next_session,
        packet_format=2026,
        player_car_index=23,
    )
    changed_format = observer.live_motion_snapshot()
    assert changed_format["packet_format"] == 2026
    assert changed_format["player_car_index"] == 23
    assert changed_format["observation_count"] == 1


def test_live_motion_resets_on_synthetic_session_time_regression_and_frame_wraps():
    regressed = _AcquisitionObserver(reorder_window_frames=1)
    _process(
        regressed,
        _motion_packet(frame=100, sequence=10, session_time_s=12.0),
    )
    _process(
        regressed,
        _lap_packet(
            frame=100,
            lap_number=3,
            distance_m=120.0,
            session_time=12.0,
            current_lap_time_ms=12_000,
            sequence=11,
        ),
    )
    _process(regressed, _advance(101, 12))
    assert regressed.live_motion_snapshot()["status"] == "fresh"
    _process(
        regressed,
        _motion_packet(frame=102, sequence=13, session_time_s=1.0),
    )
    _process(
        regressed,
        _lap_packet(
            frame=102,
            lap_number=1,
            distance_m=5.0,
            session_time=1.0,
            current_lap_time_ms=1_000,
            sequence=14,
        ),
    )
    _process(regressed, _advance(103, 15))
    assert regressed.live_motion_snapshot()["status"] == "waiting"
    assert regressed.live_motion_snapshot()["observation_count"] == 0

    wrapped = _AcquisitionObserver(reorder_window_frames=1)
    _publish_live_motion(wrapped, frame=0xFFFFFFFE, sequence=10)
    _publish_live_motion(wrapped, frame=0, sequence=20)
    assert wrapped.live_motion_snapshot()["frame_identifier"] == 0
    assert wrapped.live_motion_snapshot()["observation_count"] == 2
