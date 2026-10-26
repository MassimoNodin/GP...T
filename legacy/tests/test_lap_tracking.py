from __future__ import annotations

import struct
from pathlib import Path

from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.sessions.lap_tracker import LapDisposition
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.lap_data import LapDataDecoder
from f1_engineer.udp.models import RawDatagram
from tests.helpers import make_datagram


SESSION_FIXTURE = Path(__file__).parent / "fixtures" / "f1_25_session_packet_v1.bin"
SESSION_UID = 14237356543050158953
LAP_RECORD = struct.Struct("<IIHBHBHBHBfff15BHHBfB")


def _session_packet(*, race: bool = False) -> RawDatagram:
    payload = bytearray(SESSION_FIXTURE.read_bytes())
    if race:
        body = memoryview(payload)[29:]
        body[6] = 15
        body[665] = 27
        body[666] = 1
    return RawDatagram(
        sequence=0,
        captured_at_ns=1,
        monotonic_ns=1,
        source_host="127.0.0.1",
        source_port=20777,
        payload=bytes(payload),
    )


def _lap_body(
    *,
    lap_number: int,
    distance_m: float,
    current_lap_time_ms: int = 0,
    last_lap_time_ms: int = 0,
    sector1_time_ms: int = 12_345,
    sector2_time_ms: int = 45_678,
    sector_id: int = 0,
    invalid: int = 0,
    driver_status: int = 1,
    pit_status: int = 0,
    active_car_index: int = 0,
) -> bytes:
    records = []
    for car_index in range(22):
        fields = (
            last_lap_time_ms if car_index == active_car_index else 0,
            current_lap_time_ms if car_index == active_car_index else 0,
            sector1_time_ms,
            0,
            sector2_time_ms,
            0,
            0,
            0,
            0,
            0,
            distance_m if car_index == active_car_index else 0.0,
            5_000.0 if car_index == active_car_index else 0.0,
            0.0,
            1 if car_index == active_car_index else 0,
            lap_number if car_index == active_car_index else 0,
            pit_status if car_index == active_car_index else 0,
            0,
            sector_id if car_index == active_car_index else 0,
            invalid if car_index == active_car_index else 0,
            0,
            0,
            0,
            0,
            0,
            1,
            driver_status if car_index == active_car_index else 0,
            2 if car_index == active_car_index else 0,
            0,
            0,
            0,
            0,
            0.0,
            255,
        )
        records.append(LAP_RECORD.pack(*fields))
    return b"".join(records) + bytes((0, 255))


def _lap_packet(
    *,
    frame: int,
    lap_number: int,
    distance_m: float,
    session_time: float,
    current_lap_time_ms: int = 0,
    last_lap_time_ms: int = 0,
    sector1_time_ms: int = 12_345,
    sector2_time_ms: int = 45_678,
    sector_id: int = 0,
    invalid: int = 0,
    driver_status: int = 1,
    pit_status: int = 0,
    sequence: int = 1,
    player_car_index: int = 0,
    active_car_index: int = 0,
    packet_format: int = 2025,
    packet_version: int = 1,
) -> RawDatagram:
    return make_datagram(
        packet_format=packet_format,
        packet_id=2,
        packet_version=packet_version,
        session_uid=SESSION_UID,
        frame=frame,
        player_car_index=player_car_index,
        session_time=session_time,
        body=_lap_body(
            lap_number=lap_number,
            distance_m=distance_m,
            current_lap_time_ms=current_lap_time_ms,
            last_lap_time_ms=last_lap_time_ms,
            sector1_time_ms=sector1_time_ms,
            sector2_time_ms=sector2_time_ms,
            sector_id=sector_id,
            invalid=invalid,
            driver_status=driver_status,
            pit_status=pit_status,
            active_car_index=active_car_index,
        ),
        sequence=sequence,
    )


def test_lap_data_decoder_reads_all_car_records_and_combines_times() -> None:
    raw = _lap_packet(
        frame=50,
        lap_number=2,
        distance_m=0.35,
        session_time=1.5,
        current_lap_time_ms=250,
        last_lap_time_ms=79_295,
    )
    packet = PacketDecoder().decode(raw)

    result = LapDataDecoder().decode(packet)

    assert result.error is None
    assert result.lap_data is not None
    assert len(result.lap_data.cars) == 22
    player = result.lap_data.cars[0]
    assert player.last_lap_time_ms == 79_295
    assert player.current_lap_time_ms == 250
    assert player.sector1_time_ms == 12_345
    assert player.sector2_time_ms == 45_678
    assert player.current_lap_number == 2
    assert abs(player.lap_distance_m - 0.35) < 1e-5
    assert result.lap_data.time_trial_pb_car_index == 0
    assert result.lap_data.time_trial_rival_car_index == 255


def test_lap_data_decoder_rejects_truncated_body() -> None:
    raw = _lap_packet(
        frame=50,
        lap_number=2,
        distance_m=0.35,
        session_time=1.5,
    )
    packet = PacketDecoder().decode(raw)
    truncated = packet.__class__(
        header=packet.header,
        packet_format=packet.packet_format,
        packet_kind=packet.packet_kind,
        body=packet.body[:-1],
        wire_fingerprint=packet.wire_fingerprint,
    )

    result = LapDataDecoder().decode(truncated)

    assert result.lap_data is None
    assert result.error is not None
    assert "must be 1256 bytes" in result.error


def test_melbourne_recording_lifecycle_keeps_invalid_and_partial_attempts() -> None:
    pipeline = TelemetryPipeline()
    pipeline.process(_session_packet())
    updates = (
        (50, 1, -0.7, 0, 0, 0),
        (51, 1, 0.5, 0, 0, 0),
        (60, 1, 100.0, 5_000, 0, 1),
        (100, 1, 5_275.5, 79_270, 0, 1),
        (101, 2, 0.3, 0, 79_295, 0),
        (110, 2, 120.0, 5_000, 79_295, 1),
        (150, 2, 5_276.0, 81_447, 79_295, 1),
        (151, 3, 0.4, 0, 81_437, 0),
        (160, 3, 400.0, 6_000, 81_437, 1),
    )
    for frame, lap, distance, current_ms, last_ms, invalid in updates:
        pipeline.process(
            _lap_packet(
                frame=frame,
                lap_number=lap,
                distance_m=distance,
                session_time=frame / 60,
                current_lap_time_ms=current_ms,
                last_lap_time_ms=last_ms,
                invalid=invalid,
                sequence=frame,
            )
        )
    pipeline.finish()

    attempts = pipeline.laps.attempts
    assert [attempt.disposition for attempt in attempts] == [
        LapDisposition.COMPLETED,
        LapDisposition.COMPLETED,
        LapDisposition.PARTIAL,
    ]
    assert [attempt.lap_time_ms for attempt in attempts[:2]] == [79_295, 81_437]
    assert all(attempt.game_valid is False for attempt in attempts[:2])
    assert not any(attempt.reference_eligible for attempt in attempts)
    assert all("game_marked_invalid" in attempt.exclusion_reasons for attempt in attempts)
    assert "capture_ended_before_lap_completion" in attempts[2].exclusion_reasons


def test_valid_time_trial_attempt_is_reference_eligible_but_race_is_not() -> None:
    for race, expected in ((False, True), (True, False)):
        pipeline = TelemetryPipeline()
        pipeline.process(_session_packet(race=race))
        for frame, distance, current_ms, lap, last_ms in (
            (50, -0.5, 0, 1, 0),
            (51, 0.5, 0, 1, 0),
            (80, 5_275.5, 79_270, 1, 0),
            (81, 0.3, 0, 2, 79_295),
        ):
            pipeline.process(
                _lap_packet(
                    frame=frame,
                    lap_number=lap,
                    distance_m=distance,
                    session_time=frame / 60,
                    current_lap_time_ms=current_ms,
                    last_lap_time_ms=last_ms,
                    sequence=frame,
                )
            )
        pipeline.finish()

        completed = [
            attempt
            for attempt in pipeline.laps.attempts
            if attempt.disposition is LapDisposition.COMPLETED
        ]
        assert len(completed) == 1
        assert completed[0].game_valid is True
        assert completed[0].reference_eligible is expected
        if race:
            assert "mode_policy_not_implemented" in completed[0].exclusion_reasons


def test_lap_attempt_preserves_context_changes_with_effective_frames() -> None:
    pipeline = TelemetryPipeline()
    pipeline.process(_session_packet())
    for frame, distance, current_ms, lap, last_ms in (
        (50, -0.5, 0, 1, 0),
        (51, 0.5, 0, 1, 0),
    ):
        pipeline.process(
            _lap_packet(
                frame=frame,
                lap_number=lap,
                distance_m=distance,
                session_time=frame / 60,
                current_lap_time_ms=current_ms,
                last_lap_time_ms=last_ms,
                sequence=frame,
            )
        )

    changed_context_body = bytearray(SESSION_FIXTURE.read_bytes()[29:])
    changed_context_body[0] = 1
    pipeline.process(
        make_datagram(
            packet_id=1,
            session_uid=SESSION_UID,
            frame=60,
            session_time=1.0,
            body=bytes(changed_context_body),
            sequence=60,
        )
    )
    for frame, distance, current_ms, lap, last_ms in (
        (65, 100.0, 5_000, 1, 0),
        (80, 5_275.5, 79_270, 1, 0),
        (81, 0.3, 0, 2, 79_295),
    ):
        pipeline.process(
            _lap_packet(
                frame=frame,
                lap_number=lap,
                distance_m=distance,
                session_time=frame / 60,
                current_lap_time_ms=current_ms,
                last_lap_time_ms=last_ms,
                sequence=frame,
            )
        )
    pipeline.finish()

    completed = next(
        attempt
        for attempt in pipeline.laps.attempts
        if attempt.disposition is LapDisposition.COMPLETED
    )
    assert len(completed.context_segments) == 2
    assert completed.context_segments[0].from_frame_identifier == 51
    assert completed.context_segments[0].context.weather_id == 0
    assert completed.context_segments[1].from_frame_identifier == 60
    assert completed.context_segments[1].context.weather_id == 1
    assert completed.reference_eligible


def test_unknown_context_segment_excludes_a_completed_lap() -> None:
    pipeline = TelemetryPipeline()
    pipeline.process(_session_packet())
    for frame, distance in ((50, -0.5), (51, 0.5)):
        pipeline.process(
            _lap_packet(
                frame=frame,
                lap_number=1,
                distance_m=distance,
                session_time=frame / 60,
                sequence=frame,
            )
        )

    time_trial_body = SESSION_FIXTURE.read_bytes()[29:]
    pipeline.process(
        make_datagram(
            packet_id=1,
            session_uid=SESSION_UID,
            frame=61,
            session_time=61 / 60,
            body=time_trial_body,
            sequence=61,
        )
    )
    unknown_body = bytearray(time_trial_body)
    unknown_body[665] = 250
    pipeline.process(
        make_datagram(
            packet_id=1,
            session_uid=SESSION_UID,
            frame=60,
            session_time=60 / 60,
            body=bytes(unknown_body),
            sequence=60,
        )
    )
    for frame, lap, distance, current_ms, last_ms in (
        (65, 1, 100.0, 5_000, 0),
        (80, 1, 5_275.5, 79_270, 0),
    ):
        pipeline.process(
            _lap_packet(
                frame=frame,
                lap_number=lap,
                distance_m=distance,
                session_time=frame / 60,
                current_lap_time_ms=current_ms,
                last_lap_time_ms=last_ms,
                sequence=frame,
            )
        )
    unknown_body = bytearray(time_trial_body)
    unknown_body[665] = 250
    pipeline.process(
        make_datagram(
            packet_id=1,
            session_uid=SESSION_UID,
            frame=81,
            session_time=81 / 60,
            body=bytes(unknown_body),
            sequence=81,
        )
    )
    pipeline.process(
        _lap_packet(
            frame=82,
            lap_number=2,
            distance_m=0.3,
            session_time=82 / 60,
            last_lap_time_ms=79_295,
            sequence=82,
        )
    )
    pipeline.finish()

    completed = next(
        attempt
        for attempt in pipeline.laps.attempts
        if attempt.disposition is LapDisposition.COMPLETED
    )
    assert [segment.from_frame_identifier for segment in completed.context_segments] == [
        51,
        60,
        61,
        81,
    ]
    assert completed.context_segments[1].context.game_mode is None
    assert completed.context_segments[2].context.game_mode.value == "time_trial"
    assert completed.context_segments[3].context.game_mode is None
    assert "session_context_unknown" in completed.exclusion_reasons
    assert not completed.reference_eligible


def test_race_transition_mid_attempt_excludes_a_time_trial_reference() -> None:
    pipeline = TelemetryPipeline()
    pipeline.process(_session_packet())
    for frame, distance in ((50, -0.5), (51, 0.5)):
        pipeline.process(
            _lap_packet(
                frame=frame,
                lap_number=1,
                distance_m=distance,
                session_time=frame / 60,
                sequence=frame,
            )
        )

    race_body = bytearray(SESSION_FIXTURE.read_bytes()[29:])
    race_body[6] = 15
    race_body[665] = 27
    race_body[666] = 1
    pipeline.process(
        make_datagram(
            packet_id=1,
            session_uid=SESSION_UID,
            frame=60,
            body=bytes(race_body),
            sequence=60,
        )
    )
    for frame, lap, distance, current_ms, last_ms in (
        (60, 1, 100.0, 5_000, 0),
        (80, 1, 5_275.5, 79_270, 0),
        (81, 2, 0.3, 0, 79_295),
    ):
        pipeline.process(
            _lap_packet(
                frame=frame,
                lap_number=lap,
                distance_m=distance,
                session_time=frame / 60,
                current_lap_time_ms=current_ms,
                last_lap_time_ms=last_ms,
                sequence=frame,
            )
        )
    pipeline.finish()

    completed = next(
        attempt
        for attempt in pipeline.laps.attempts
        if attempt.disposition is LapDisposition.COMPLETED
    )
    assert len(completed.context_segments) == 2
    assert completed.context_segments[1].context.session_type.value == "race"
    assert "mode_policy_not_implemented" in completed.exclusion_reasons
    assert "session_mode_or_track_changed" in completed.exclusion_reasons
    assert not completed.reference_eligible


def test_format_change_flushes_old_frames_before_closing_the_active_attempt() -> None:
    pipeline = TelemetryPipeline()
    pipeline.process(_session_packet())
    for frame, distance in ((50, 0.5), (51, 100.0), (60, 200.0)):
        pipeline.process(
            _lap_packet(
                frame=frame,
                lap_number=1,
                distance_m=distance,
                session_time=frame / 60,
                current_lap_time_ms=frame * 100,
                sequence=frame,
            )
        )

    pipeline.process(
        make_datagram(
            packet_format=2026,
            packet_id=0,
            session_uid=SESSION_UID,
            frame=61,
            sequence=61,
        )
    )
    pipeline.process(
        make_datagram(
            packet_format=2026,
            packet_id=0,
            session_uid=SESSION_UID,
            frame=64,
            sequence=64,
        )
    )
    pipeline.finish()

    assert len(pipeline.laps.attempts) == 1
    attempt = pipeline.laps.attempts[0]
    assert attempt.disposition is LapDisposition.PARTIAL
    assert "packet_format_changed" in attempt.exclusion_reasons


def test_delayed_old_format_lap_data_is_ignored_after_a_format_change() -> None:
    pipeline = TelemetryPipeline()
    pipeline.process(_session_packet())
    for frame, distance in ((50, 0.5), (51, 100.0), (55, 200.0)):
        pipeline.process(
            _lap_packet(
                frame=frame,
                lap_number=1,
                distance_m=distance,
                session_time=frame / 60,
                current_lap_time_ms=frame * 100,
                sequence=frame,
            )
        )

    pipeline.process(
        make_datagram(
            packet_format=2026,
            packet_id=0,
            session_uid=SESSION_UID,
            frame=57,
            sequence=57,
        )
    )
    delayed_old_format = _lap_packet(
        frame=56,
        lap_number=1,
        distance_m=250.0,
        session_time=56 / 60,
        current_lap_time_ms=5_600,
        sequence=56,
    )
    pipeline.process(delayed_old_format)
    pipeline.process(
        make_datagram(
            packet_format=2026,
            packet_id=0,
            session_uid=SESSION_UID,
            frame=60,
            sequence=60,
        )
    )
    pipeline.finish()

    assert len(pipeline.laps.attempts) == 1
    assert "packet_format_changed" in pipeline.laps.attempts[0].exclusion_reasons
    assert pipeline.frames.late_packets_ignored >= 1
