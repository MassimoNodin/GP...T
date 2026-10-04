from __future__ import annotations

import asyncio
import socket
import struct
import time
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from f1_engineer.api.app import create_app
from f1_engineer.recording.service import _AcquisitionObserver
from f1_engineer.recording.capture import CaptureReader
from tests.helpers import make_datagram
from tests.test_car_status import _status_body
from tests.test_lap_tracking import SESSION_UID, _lap_packet, _session_packet


_TELEMETRY_CAR = struct.Struct("<HfffBbHBBH4H4B4BH4f4B")
_LAP_DATA_CAR = struct.Struct("<IIHBHBHBHBfff15BHHBfB")
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
):
    records = []
    for car_index in range(22):
        fields = (
            (100 + car_index, throttle, 0.0, brake, 0, 0, 11_000, 0, 0, 0)
            + (0,) * 12
            + (95,)
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


def _flashback_packet(*, frame: int, sequence: int, session_time: float, target_time: float):
    details = struct.pack("<If", frame - 5, target_time) + b"\x00" * 4
    return make_datagram(
        packet_id=3,
        session_uid=SESSION_UID,
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
    sector1_time_ms: int = 12_345,
    sector2_time_ms: int = 45_678,
    sector_id: int = 0,
    throttle: float = 0.75,
    brake: float = 0.2,
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
            )
        )
    _process(observer,
        _lap_packet(
            frame=frame,
            lap_number=lap_number,
            distance_m=120.0,
            session_time=12.0,
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
    assert live["throttle"] == 0
    assert live["brake"] == 0


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

                payloads = [
                    _telemetry_packet(frame=100, sequence=10).payload,
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
                    _advance(103, 13).payload,
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
