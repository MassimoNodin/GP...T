from __future__ import annotations

import math
import struct
import time
from dataclasses import replace

import pytest

from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.recording.capture import CaptureWriter
from f1_engineer.recording.service import _AcquisitionObserver
from f1_engineer.sessions.context import GameMode, RuleSet, SessionType
from f1_engineer.sessions.lap_tracker import LapDisposition
from f1_engineer.storage.importer import import_capture, list_laps, list_sessions
from f1_engineer.storage.query import load_attempt_trace, load_reference_inventory
from f1_engineer.udp.car_telemetry import CarTelemetryDecoder
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.lap_data import LapDataDecoder
from f1_engineer.udp.models import RawDatagram
from f1_engineer.udp.session_context import SessionContextDecoder
from tests.helpers import make_datagram


_SESSION_BODY_SIZE = 897
_LAP_RECORD = struct.Struct("<IIHBHBHBHBfff15BHHBfB")
_TELEMETRY_RECORD = struct.Struct("<HfffBbHBBH4H4B4BB4f4B")
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
    process(_packet(255, b"advance", frame=102, sequence=6))

    live = observer.live_telemetry_snapshot()

    assert live["status"] == "fresh"
    assert live["player_car_index"] == 23
    assert live["speed_kph"] == 223
    assert live["throttle"] == 0.75


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
    assert session["pipeline_version"] == "player-traces-v9-2026-player-trace"
    attempts = list_laps(database_path)
    complete = next(
        attempt for attempt in attempts if attempt["disposition"] == "completed"
    )
    assert complete["car_index"] == 23
    assert complete["game_valid"] is True
    assert complete["reference_eligible"] is False
    assert "mode_policy_not_implemented" in complete["exclusion_reasons"]
    assert complete["trace_schema_version"] == 2

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
    assert stored.trace_schema_version == 2
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
