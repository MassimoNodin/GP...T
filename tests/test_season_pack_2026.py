from __future__ import annotations

import json
import math
import sqlite3
import struct
import time
from dataclasses import replace

import pytest

from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.analysis.trajectory import build_observed_trajectory
from f1_engineer.recording.capture import CaptureWriter
from f1_engineer.recording.service import _AcquisitionObserver
from f1_engineer.sessions.context import GameMode, RuleSet, SessionType
from f1_engineer.sessions.lap_tracker import LapDisposition
from f1_engineer.storage.importer import import_capture, list_laps, list_sessions
from f1_engineer.storage.query import (
    TRAJECTORY_TRACE_COLUMNS,
    load_attempt_trace,
    load_reference_inventory,
)
from f1_engineer.udp.car_telemetry import CarTelemetryDecoder
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.lap_data import LapDataDecoder
from f1_engineer.udp.motion import MotionDecoder
from f1_engineer.udp.models import RawDatagram
from f1_engineer.udp.participants import ParticipantsDecoder
from f1_engineer.udp.session_context import SessionContextDecoder
from tests.helpers import make_datagram


_SESSION_BODY_SIZE = 897
_LAP_RECORD = struct.Struct("<IIHBHBHBHBfff15BHHBfB")
_TELEMETRY_RECORD = struct.Struct("<HfffBbHBBH4H4B4BB4f4B")
_MOTION_RECORD = struct.Struct("<6f9h3f")
_CAR_STATUS_RECORD = struct.Struct("<5B3f2H2BH3Bb3fB4fB")
_PARTICIPANT_RECORD = struct.Struct("<BHHHBBB32sBBHBB12B")
_F1_25_PARTICIPANT_RECORD = struct.Struct("<7B32s2BH14B")
_SESSION_UID = 26_001


def _session_body(
    *, session_type: int = 18, game_mode: int = 5, ruleset: int = 2
) -> bytes:
    body = bytearray(_SESSION_BODY_SIZE)
    struct.pack_into(
        "<BbbBHBbBHHBBBBBB",
        body,
        0,
        0,
        26,
        25,
        1,
        5_279,
        session_type,
        42,
        13,
        600,
        600,
        80,
        0,
        0,
        255,
        0,
        0,
    )
    body[125] = 0  # network game
    body[126] = 0  # weather forecast samples
    body[665] = game_mode
    body[666] = ruleset
    body[679] = 1  # equal car performance
    body[703] = 0  # weekend sessions
    return bytes(body)


def _lap_fields(
    car_index: int,
    *,
    lap_number: int | None = None,
    distance_m: float | None = None,
    current_time_ms: int = 1_000,
    last_lap_time_ms: int = 0,
    invalid: int = 0,
    driver_status: int = 4,
    result_status: int = 2,
) -> tuple[object, ...]:
    return (
        last_lap_time_ms,
        current_time_ms,
        123,
        2,
        234,
        1,
        345,
        0,
        456,
        0,
        float(car_index if distance_m is None else distance_m),
        float(car_index * 100),
        0.25,
        car_index + 1,
        car_index + 1 if lap_number is None else lap_number,
        0,
        2,
        1,
        invalid,
        3,
        4,
        0,
        0,
        0,
        1,
        driver_status,
        result_status,
        0,
        400,
        500,
        0,
        301.5,
        255,
    )


def _lap_body(
    *,
    active_car_index: int = 23,
    lap_number: int = 1,
    distance_m: float = 100.0,
    current_time_ms: int = 1_000,
    last_lap_time_ms: int = 0,
    invalid: int = 0,
    driver_status: int = 4,
) -> bytes:
    records = []
    for car_index in range(24):
        if car_index == active_car_index:
            fields = _lap_fields(
                car_index,
                lap_number=lap_number,
                distance_m=distance_m,
                current_time_ms=current_time_ms,
                last_lap_time_ms=last_lap_time_ms,
                invalid=invalid,
                driver_status=driver_status,
            )
        else:
            fields = _lap_fields(
                car_index,
                lap_number=car_index + 1,
                distance_m=-1.0,
                driver_status=0,
                result_status=0,
            )
        records.append(_LAP_RECORD.pack(*fields))
    return b"".join(records) + bytes((23, 255))


def _telemetry_fields(car_index: int) -> tuple[object, ...]:
    return (
        200 + car_index,
        0.75,
        -0.25,
        0.2,
        55,
        5,
        12_000 + car_index,
        1,
        90,
        0x1234,
        400,
        401,
        402,
        403,
        81,
        82,
        83,
        84,
        71,
        72,
        73,
        74,
        95 + car_index,
        23.1,
        23.2,
        23.3,
        23.4,
        0,
        1,
        2,
        3,
    )


def _telemetry_body() -> bytes:
    return b"".join(
        _TELEMETRY_RECORD.pack(*_telemetry_fields(car_index))
        for car_index in range(24)
    ) + bytes((2, 255, 6))


def _motion_body(*, invalid_player_groups: bool = False) -> bytes:
    records = []
    for car_index in range(24):
        position = (float(100 + car_index), 20.0, 30.0)
        velocity = (1.0, 2.0, 3.0)
        forward = (32767, 0, 0)
        yaw = 0.4
        if invalid_player_groups and car_index == 23:
            position = (math.nan, 20.0, 30.0)
            velocity = (1.0, math.inf, 3.0)
            forward = (0, 0, 0)
            yaw = math.inf
        records.append(
            _MOTION_RECORD.pack(
                *position,
                *velocity,
                *forward,
                0,
                32767,
                0,
                1250,
                -750,
                1000,
                yaw,
                0.5,
                0.6,
            )
        )
    return b"".join(records)


def _car_status_body() -> bytes:
    records = []
    for car_index in range(24):
        records.append(
            _CAR_STATUS_RECORD.pack(
                1, 0, 2, 54, 0,
                12.5 + car_index, 100.0, -1.0,
                15_000, 4_000, 8, 1, 0,
                20, 17, 3, -1,
                900.0, 160.0, 3.5, 2,
                10.0, 5.0, 9.5, 7.0, 0,
            )
        )
    return b"".join(records)


def _participants_body(
    *, active_count: int = 24, driver_id: int = 513, team_id: int = 65535
) -> bytes:
    name = "Écurie 🚗".encode("utf-8")
    records = []
    for car_index in range(24):
        records.append(
            _PARTICIPANT_RECORD.pack(
                0,
                driver_id if car_index == 23 else car_index,
                1025 if car_index == 23 else 0,
                team_id if car_index == 23 else 3,
                1,
                44,
                8,
                name.ljust(32, b"\0") if car_index == 23 else bytes(32),
                1,
                1,
                1024,
                0,
                4,
                *range(12),
            )
        )
    return bytes((active_count,)) + b"".join(records)


def _f1_25_participants_body(name: str) -> bytes:
    encoded_name = name.encode("utf-8")
    records = []
    for car_index in range(22):
        records.append(
            _F1_25_PARTICIPANT_RECORD.pack(
                0,
                7,
                0,
                3,
                1,
                44,
                8,
                encoded_name.ljust(32, b"\0") if car_index == 0 else bytes(32),
                1,
                1,
                99,
                0,
                3,
                *([0] * 12),
            )
        )
    return bytes((22,)) + b"".join(records)


def _packet(
    packet_id: int,
    body: bytes,
    *,
    frame: int = 1,
    sequence: int = 0,
    player_car_index: int = 23,
    packet_version: int = 1,
) -> RawDatagram:
    return make_datagram(
        packet_format=2026,
        packet_id=packet_id,
        packet_version=packet_version,
        session_uid=_SESSION_UID,
        frame=frame,
        player_car_index=player_car_index,
        body=body,
        sequence=sequence,
    )


def test_2026_session_v1_decodes_documented_modes_formula_and_madrid() -> None:
    raw = _packet(1, _session_body())
    result = SessionContextDecoder().decode(PacketDecoder().decode(raw))

    assert result.error is None
    assert result.context is not None
    assert result.context.packet_format.value == 2026
    assert result.context.track_id == 42
    assert result.context.track_name == "Madrid"
    assert result.context.formula_id == 13
    assert result.context.session_type is SessionType.TIME_TRIAL
    assert result.context.game_mode is GameMode.TIME_TRIAL
    assert result.context.rule_set is RuleSet.TIME_TRIAL
    assert result.context.track_length_m == 5_279


@pytest.mark.parametrize(
    ("offset", "invalid_count"),
    ((725, 9), (790, 9), (855, 5)),
)
def test_2026_session_v1_rejects_out_of_range_zone_counts(
    offset: int, invalid_count: int
) -> None:
    body = bytearray(_session_body())
    body[offset] = invalid_count
    result = SessionContextDecoder().decode(
        PacketDecoder().decode(_packet(1, bytes(body)))
    )

    assert result.context is None
    assert "invalid" in result.error


def test_2026_session_v1_rejects_wrong_size_and_unknown_version() -> None:
    decoder = SessionContextDecoder()
    packet_decoder = PacketDecoder()

    malformed = decoder.decode(
        packet_decoder.decode(_packet(1, _session_body()[:-1]))
    )
    unsupported = decoder.decode(
        packet_decoder.decode(
            _packet(1, _session_body(), packet_version=2)
        )
    )

    assert malformed.context is None
    assert "897 bytes" in malformed.error
    assert unsupported.context is None
    assert "unsupported session packet adapter" in unsupported.error


def test_2026_lap_data_v1_decodes_24_records_and_time_sentinels() -> None:
    packet = PacketDecoder().decode(_packet(2, _lap_body(lap_number=24)))

    result = LapDataDecoder().decode(packet)

    assert result.error is None
    assert result.lap_data is not None
    assert len(result.lap_data.cars) == 24
    assert result.lap_data.cars[22].current_lap_number == 23
    assert result.lap_data.cars[23].current_lap_number == 24
    assert result.lap_data.cars[23].sector1_time_ms == 120_123
    assert result.lap_data.time_trial_pb_car_index == 23
    assert result.lap_data.time_trial_rival_car_index == 255


def test_2026_lap_data_v1_rejects_wrong_size_and_unknown_version() -> None:
    decoder = LapDataDecoder()
    packet_decoder = PacketDecoder()
    body = _lap_body()

    malformed = decoder.decode(packet_decoder.decode(_packet(2, body[:-1])))
    unsupported = decoder.decode(
        packet_decoder.decode(_packet(2, body, packet_version=2))
    )

    assert malformed.lap_data is None
    assert "1370 bytes" in malformed.error
    assert unsupported.lap_data is None
    assert "unsupported lap data packet adapter" in unsupported.error


def test_2026_car_telemetry_v1_uses_one_byte_engine_temperature() -> None:
    assert _LAP_RECORD.size == 57
    assert _TELEMETRY_RECORD.size == 59
    packet = PacketDecoder().decode(_packet(6, _telemetry_body()))

    result = CarTelemetryDecoder().decode(packet)

    assert result.error is None
    assert result.telemetry is not None
    assert len(result.telemetry.cars) == 24
    player = result.telemetry.cars[23]
    assert player.speed_kph == 223
    assert player.gear == 5
    assert player.engine_temperature_c == 118
    assert player.drs == 1
    assert player.tyre_pressure_psi == pytest.approx((23.1, 23.2, 23.3, 23.4))
    assert result.telemetry.suggested_gear == 6


def test_2026_car_telemetry_v1_rejects_wrong_size_and_unknown_version() -> None:
    decoder = CarTelemetryDecoder()
    packet_decoder = PacketDecoder()
    body = _telemetry_body()

    malformed = decoder.decode(packet_decoder.decode(_packet(6, body[:-1])))
    unsupported = decoder.decode(
        packet_decoder.decode(_packet(6, body, packet_version=2))
    )

    assert malformed.telemetry is None
    assert "1419 bytes" in malformed.error
    assert unsupported.telemetry is None
    assert "unsupported car telemetry packet adapter" in unsupported.error


def test_2026_motion_v1_decodes_24_cars_and_quantized_g_force() -> None:
    assert _MOTION_RECORD.size == 54
    body = _motion_body()
    assert len(body) == 1_296
    result = MotionDecoder().decode(PacketDecoder().decode(_packet(0, body)))

    assert result.error is None
    assert result.motion is not None
    assert len(result.motion.cars) == 24
    player = result.motion.cars[23]
    assert player.world_position_m == (123.0, 20.0, 30.0)
    assert player.world_velocity_mps == (1.0, 2.0, 3.0)
    assert player.world_forward == (1.0, 0.0, 0.0)
    assert player.world_right == (0.0, 1.0, 0.0)
    assert player.g_force == pytest.approx((1.25, -0.75, 1.0))
    assert (player.yaw_rad, player.pitch_rad, player.roll_rad) == pytest.approx(
        (0.4, 0.5, 0.6)
    )
    assert player.validation_flags == ()


def test_2026_motion_v1_preserves_valid_groups_when_other_groups_are_invalid() -> None:
    result = MotionDecoder().decode(
        PacketDecoder().decode(_packet(0, _motion_body(invalid_player_groups=True)))
    )

    assert result.motion is not None
    player = result.motion.cars[23]
    assert player.world_position_m is None
    assert player.world_velocity_mps is None
    assert player.world_forward is None
    assert player.world_right == (0.0, 1.0, 0.0)
    assert player.g_force == pytest.approx((1.25, -0.75, 1.0))
    assert player.yaw_rad is None
    assert player.pitch_rad == pytest.approx(0.5)
    assert player.roll_rad == pytest.approx(0.6)
    assert set(player.validation_flags) == {
        "invalid_motion_world_position",
        "invalid_motion_world_velocity",
        "invalid_motion_world_forward",
        "invalid_motion_yaw",
    }


def test_2026_motion_v1_rejects_wrong_size_and_unknown_version() -> None:
    decoder = MotionDecoder()
    packet_decoder = PacketDecoder()
    body = _motion_body()

    malformed = decoder.decode(packet_decoder.decode(_packet(0, body[:-1])))
    unsupported = decoder.decode(
        packet_decoder.decode(_packet(0, body, packet_version=2))
    )

    assert malformed.motion is None
    assert "1296 bytes" in malformed.error
    assert unsupported.motion is None
    assert "unsupported motion packet adapter" in unsupported.error


def test_2026_participants_v1_preserves_wide_ids_sentinels_and_utf8_names() -> None:
    assert _PARTICIPANT_RECORD.size == 60
    body = _participants_body()
    assert len(body) == 1_441
    result = ParticipantsDecoder().decode(PacketDecoder().decode(_packet(4, body)))

    assert result.error is None
    assert result.participants is not None
    assert result.participants.active_car_count == 24
    assert len(result.participants.cars) == 24
    player = result.participants.cars[23]
    assert player.driver_id == 513
    assert player.network_id == 1025
    assert player.team_id == 65535
    assert player.name == "Écurie 🚗"
    assert player.tech_level == 1024
    assert player.colours_rgb == ((0, 1, 2), (3, 4, 5), (6, 7, 8), (9, 10, 11))


@pytest.mark.parametrize("body", (_participants_body()[:-1], _participants_body(active_count=25)))
def test_2026_participants_v1_rejects_bad_size_and_active_count(body: bytes) -> None:
    result = ParticipantsDecoder().decode(PacketDecoder().decode(_packet(4, body)))

    assert result.participants is None
    assert result.error is not None


def test_2026_participants_v1_rejects_unknown_version() -> None:
    result = ParticipantsDecoder().decode(
        PacketDecoder().decode(_packet(4, _participants_body(), packet_version=2))
    )

    assert result.participants is None
    assert "unsupported participants packet adapter" in result.error


@pytest.mark.parametrize(
    ("session_fields", "expected_reason"),
    (
        ((18, 5, 2), "mode_policy_not_implemented"),
        ((15, 28, 1), "mode_policy_not_implemented"),
        ((250, 250, 250), "session_context_unknown"),
    ),
)
def test_2026_stream_keeps_attempts_but_never_enables_references(
    session_fields: tuple[int, int, int], expected_reason: str
) -> None:
    session_type, game_mode, ruleset = session_fields
    pipeline = TelemetryPipeline()
    datagrams = (
        _packet(
            1,
            _session_body(
                session_type=session_type, game_mode=game_mode, ruleset=ruleset
            ),
            frame=1,
            sequence=1,
        ),
        _packet(
            2,
            _lap_body(distance_m=-1.0, driver_status=0),
            frame=10,
            sequence=2,
        ),
        _packet(6, _telemetry_body(), frame=10, sequence=3),
        _packet(2, _lap_body(distance_m=100.0), frame=11, sequence=4),
        _packet(6, _telemetry_body(), frame=11, sequence=5),
        _packet(7, _car_status_body(), frame=11, sequence=8),
        _packet(
            2,
            _lap_body(
                lap_number=2,
                distance_m=1.0,
                current_time_ms=100,
                last_lap_time_ms=80_000,
            ),
            frame=12,
            sequence=6,
        ),
        _packet(6, _telemetry_body(), frame=12, sequence=7),
    )
    for datagram in datagrams:
        pipeline.process(datagram)

    flushed = pipeline.finish_with_outputs()
    completed = next(
        attempt
        for attempt in flushed.lap_attempts
        if attempt.disposition is LapDisposition.COMPLETED
    )

    assert completed.car_index == 23
    assert completed.game_valid is True
    assert completed.reference_eligible is False
    assert expected_reason in completed.exclusion_reasons
    player_sample = next(
        sample
        for sample in flushed.car_samples
        if sample.frame_identifier == 11 and sample.car_index == 23
    )
    assert math.isclose(player_sample.speed_mps or 0, 223 / 3.6)
    assert player_sample.drs_active is True
    assert player_sample.motion_available is False
    assert player_sample.world_position_x_m is None
    assert player_sample.car_status_available is True
    assert pipeline.participants_packets_decoded == 0


@pytest.mark.parametrize("player_index", (24, 255))
def test_2026_pipeline_rejects_invalid_player_and_sentinel_indices(
    player_index: int,
) -> None:
    pipeline = TelemetryPipeline()
    pipeline.process(_packet(1, _session_body(), frame=1, sequence=1))
    pipeline.process(
        _packet(
            2,
            _lap_body(active_car_index=23),
            frame=10,
            sequence=2,
            player_car_index=player_index,
        )
    )

    flushed = pipeline.finish_with_outputs()

    assert any(
        f"invalid player car index {player_index}" in error
        for error in flushed.lap_data_errors
    )
    assert flushed.car_samples == ()


def test_2026_packet_16_does_not_poison_live_packet_6_snapshot() -> None:
    observer = _AcquisitionObserver(reorder_window_frames=1)

    def process(raw: RawDatagram) -> None:
        observer.process(replace(raw, monotonic_ns=time.monotonic_ns()))

    process(_packet(1, _session_body(), frame=1, sequence=1))
    process(_packet(2, _lap_body(), frame=100, sequence=2))
    process(_packet(6, _telemetry_body(), frame=100, sequence=3))
    process(_packet(16, bytes(240), frame=100, sequence=4))
    process(
        _packet(6, b"unsupported update", frame=100, sequence=5, packet_version=2)
    )
    process(_packet(7, _car_status_body(), frame=100, sequence=7))
    process(_packet(255, b"advance", frame=102, sequence=8))

    live = observer.live_telemetry_snapshot()

    assert live["status"] == "fresh"
    assert live["player_car_index"] == 23
    assert live["speed_kph"] == 223
    assert live["throttle"] == 0.75
    live_status = observer.live_car_status_snapshot()
    assert live_status["status"] == "fresh"
    assert live_status["player_car_index"] == 23
    assert live_status["fuel_in_tank_reported"] == 35.5
    assert live_status["actual_tyre_compound"] == 20
    assert live_status["tyre_age_laps"] == 3


def test_2026_pipeline_joins_motion_by_frame_and_player_without_carry_forward() -> None:
    pipeline = TelemetryPipeline(reorder_window_frames=1)
    outputs = []
    datagrams = (
        _packet(1, _session_body(), frame=1, sequence=1),
        _packet(0, _motion_body(), frame=10, sequence=2),
        _packet(
            2,
            _lap_body(distance_m=-1.0, driver_status=0),
            frame=10,
            sequence=3,
        ),
        _packet(0, _motion_body(), frame=11, sequence=4),
        _packet(2, _lap_body(distance_m=100.0), frame=11, sequence=5),
        _packet(7, _car_status_body(), frame=11, sequence=8),
        _packet(
            2,
            _lap_body(lap_number=2, distance_m=1.0, current_time_ms=100),
            frame=12,
            sequence=6,
        ),
        _packet(255, b"advance", frame=14, sequence=7),
    )
    for datagram in datagrams:
        outputs.extend(pipeline.process(datagram).car_samples)
    outputs.extend(pipeline.finish_with_outputs().car_samples)

    by_frame = {sample.frame_identifier: sample for sample in outputs}
    assert by_frame[11].car_index == 23
    assert by_frame[11].motion_available is True
    assert by_frame[11].world_position_x_m == 123.0
    assert by_frame[11].g_force_lateral == pytest.approx(1.25)
    assert by_frame[11].car_status_available is True
    assert by_frame[11].fuel_in_tank_reported == pytest.approx(35.5)
    assert by_frame[11].vehicle_fia_flag == -1
    assert by_frame[12].motion_available is False
    assert by_frame[12].world_position_x_m is None
    assert by_frame[12].car_status_available is False
    assert by_frame[12].car_status_unavailable_reason == "status_packet_missing"
    assert pipeline.player_motion_samples == 2
    assert pipeline.missing_player_motion_samples == 1
    assert pipeline.player_car_status_samples == 1
    assert pipeline.missing_player_car_status_samples == 1


def test_2026_import_persists_motion_and_wide_participant_snapshot(tmp_path) -> None:
    capture_path = tmp_path / "season-pack-2026-with-motion.f1ecap"
    database_path = tmp_path / "state" / "f1.sqlite3"
    datagrams = (
        _packet(1, _session_body(), frame=1, sequence=1),
        _packet(4, _participants_body(), frame=1, sequence=2),
        _packet(
            2,
            _lap_body(distance_m=-1.0, driver_status=0),
            frame=10,
            sequence=3,
        ),
        _packet(0, _motion_body(), frame=10, sequence=4),
        _packet(2, _lap_body(distance_m=100.0), frame=11, sequence=5),
        _packet(0, _motion_body(), frame=11, sequence=6),
        _packet(6, _telemetry_body(), frame=11, sequence=7),
        _packet(
            2,
            _lap_body(
                lap_number=2,
                distance_m=1.0,
                current_time_ms=100,
                last_lap_time_ms=80_000,
            ),
            frame=12,
            sequence=8,
        ),
        _packet(0, _motion_body(), frame=12, sequence=9),
        _packet(6, _telemetry_body(), frame=12, sequence=10),
    )
    with CaptureWriter(capture_path, {"fixture": "season-pack-2026-motion"}) as writer:
        for datagram in datagrams:
            writer.write(datagram)

    imported = import_capture(capture_path, database_path)

    assert imported.status == "complete"
    assert imported.motion_packets == 3
    assert imported.participant_packets == 1
    assert imported.player_motion_samples == 2
    assert imported.missing_player_motion_samples == 0
    sessions = list_sessions(database_path)
    assert sessions[0]["pipeline_version"] == "player-traces-v13-session-history"
    attempts = list_laps(database_path)
    complete = next(attempt for attempt in attempts if attempt["disposition"] == "completed")
    stored = load_attempt_trace(
        database_path,
        complete["attempt_key"],
        columns=TRAJECTORY_TRACE_COLUMNS,
    )
    assert stored is not None
    assert stored.samples
    assert stored.samples[0]["motion_available"] is True
    assert stored.samples[0]["world_position_x_m"] == 123.0
    assert stored.samples[0]["g_force_lateral"] == pytest.approx(1.25)
    trajectory = build_observed_trajectory(
        attempt_key=stored.attempt_key,
        run_id=stored.run_id,
        session_uid=stored.session_uid,
        car_index=stored.car_index,
        disposition=stored.disposition,
        lap_time_ms=stored.lap_time_ms,
        game_valid=stored.game_valid,
        reference_eligible=stored.reference_eligible,
        exclusion_reasons=stored.exclusion_reasons,
        trace_sha256=stored.trace_sha256,
        trace_schema_version=stored.trace_schema_version,
        context_segments=stored.context_segments,
        samples=stored.samples,
    )
    assert trajectory["is_centreline"] is False
    assert trajectory["units"]["g_force"] == "g"
    assert trajectory["source"]["trace_sha256"] == stored.trace_sha256
    assert trajectory["coverage"]["position_sample_count"] == len(stored.samples)
    assert trajectory["segments"][0]["points"][0]["g_force_g"]["lateral"] == pytest.approx(1.25)

    with sqlite3.connect(database_path) as connection:
        snapshot_json = connection.execute(
            "SELECT participant_json FROM driver_snapshots WHERE car_index=23"
        ).fetchone()[0]
    participant = json.loads(snapshot_json)
    assert participant["driver_id"] == 513
    assert participant["network_id"] == 1025
    assert participant["team_id"] == 65535
    assert participant["name"] == "Écurie 🚗"

    repeated = import_capture(capture_path, database_path)
    assert repeated.run_id == imported.run_id
    assert repeated.already_imported is True


def test_import_ignores_delayed_participant_packet_from_prior_wire_format(tmp_path) -> None:
    capture_path = tmp_path / "format-transition.f1ecap"
    database_path = tmp_path / "state" / "f1.sqlite3"
    datagrams = (
        make_datagram(
            packet_format=2025,
            packet_id=255,
            session_uid=_SESSION_UID,
            frame=1,
            sequence=1,
            body=b"start old format",
        ),
        make_datagram(
            packet_format=2025,
            packet_id=4,
            session_uid=_SESSION_UID,
            frame=2,
            sequence=2,
            body=_f1_25_participants_body("Old Format"),
        ),
        _packet(255, b"switch to 2026", frame=3, sequence=3),
        make_datagram(
            packet_format=2025,
            packet_id=4,
            session_uid=_SESSION_UID,
            frame=2,
            sequence=4,
            body=_f1_25_participants_body("Delayed Old Format"),
        ),
        _packet(4, _participants_body(), frame=5, sequence=5),
    )
    with CaptureWriter(capture_path, {"fixture": "format-transition"}) as writer:
        for datagram in datagrams:
            writer.write(datagram)

    imported = import_capture(capture_path, database_path)

    assert imported.status == "complete"
    assert list_sessions(database_path)[0]["packet_format"] == 2026
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT car_index, effective_frame, participant_json "
            "FROM driver_snapshots ORDER BY effective_frame, car_index"
        ).fetchall()

    assert len(rows) == 46
    assert {row[1] for row in rows} == {2, 5}
    old_player = json.loads(next(row[2] for row in rows if row[0] == 0 and row[1] == 2))
    new_player = json.loads(next(row[2] for row in rows if row[0] == 23 and row[1] == 5))
    assert old_player["name"] == "Old Format"
    assert new_player["name"] == "Écurie 🚗"


def test_2026_capture_import_query_and_reimport_are_stable(tmp_path) -> None:
    capture_path = tmp_path / "season-pack-2026.f1ecap"
    database_path = tmp_path / "state" / "f1.sqlite3"
    datagrams = (
        _packet(1, _session_body(), frame=1, sequence=1),
        _packet(
            2,
            _lap_body(distance_m=-1.0, driver_status=0),
            frame=10,
            sequence=2,
        ),
        _packet(2, _lap_body(distance_m=100.0), frame=11, sequence=3),
        _packet(6, _telemetry_body(), frame=11, sequence=4),
        _packet(
            2,
            _lap_body(
                lap_number=2,
                distance_m=1.0,
                current_time_ms=100,
                last_lap_time_ms=80_000,
            ),
            frame=12,
            sequence=5,
        ),
        _packet(6, _telemetry_body(), frame=12, sequence=6),
    )
    with CaptureWriter(capture_path, {"fixture": "season-pack-2026"}) as writer:
        for datagram in datagrams:
            writer.write(datagram)

    imported = import_capture(capture_path, database_path)

    assert imported.status == "complete"
    assert imported.already_imported is False
    assert imported.motion_packets == 0
    assert imported.participant_packets == 0
    assert imported.player_motion_samples == imported.missing_player_motion_samples
    session = list_sessions(database_path)[0]
    assert session["packet_format"] == 2026
    assert session["context"]["track_name"] == "Madrid"
    assert session["pipeline_version"] == "player-traces-v13-session-history"
    attempts = list_laps(database_path)
    complete = next(
        attempt for attempt in attempts if attempt["disposition"] == "completed"
    )
    assert complete["car_index"] == 23
    assert complete["game_valid"] is True
    assert complete["reference_eligible"] is False
    assert "mode_policy_not_implemented" in complete["exclusion_reasons"]
    assert complete["trace_schema_version"] == 3

    stored = load_attempt_trace(
        database_path,
        complete["attempt_key"],
        columns=[
            "car_index",
            "speed_mps",
            "drs_active",
            "motion_available",
            "world_position_x_m",
        ],
    )
    assert stored is not None
    assert stored.trace_schema_version == 3
    assert len(stored.samples) == 1
    assert stored.samples[0]["car_index"] == 23
    assert stored.samples[0]["motion_available"] is False
    assert stored.samples[0]["world_position_x_m"] is None
    inventory = load_reference_inventory(database_path, complete["attempt_key"])
    assert inventory is not None
    assert all(attempt.reference_eligible is False for attempt in inventory.attempts)

    repeated = import_capture(capture_path, database_path)
    assert repeated.run_id == imported.run_id
    assert repeated.already_imported is True
    assert len(list_laps(database_path)) == len(attempts)
