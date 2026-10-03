from __future__ import annotations

import math
from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.pipeline import _join_car_status
from f1_engineer.analysis.quality import inspect_attempt_quality
from f1_engineer.recording.capture import CaptureWriter
from f1_engineer.storage.importer import import_capture, list_laps
from f1_engineer.telemetry.canonical import make_car_sample
from f1_engineer.udp.car_status import (
    CarStatusDecoder,
    _F1_25_CAR_STATUS_V1_CAR,
    _SEASON_PACK_2026_CAR_STATUS_V1_CAR,
)
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.lap_data import LapDataDecoder
from tests.helpers import make_datagram
from tests.test_lap_tracking import SESSION_UID, _lap_packet, _session_packet


def _status_fields(*, fuel: float = 12.5) -> tuple[int | float, ...]:
    return (
        1, 0, 2, 54, 0,
        fuel, 100.0, -1.25,
        15_000, 4_000, 8, 1, 0,
        20, 17, 3, -1,
        900.0, 160.0, 3.5, 2,
        10.0, 5.0, 7.0, 0,
    )


def _status_body(*, packet_format: int = 2025, fuel: float = 12.5) -> bytes:
    if packet_format == 2025:
        record = _F1_25_CAR_STATUS_V1_CAR.pack(*_status_fields(fuel=fuel))
        return record * 22
    fields_2026 = (*_status_fields(fuel=fuel)[:23], 9.5, 7.0, 0)
    record = _SEASON_PACK_2026_CAR_STATUS_V1_CAR.pack(*fields_2026)
    return record * 24


def _pipeline_sample(
    status_packets: tuple[bytes, ...],
    *,
    status_player_indexes: tuple[int, ...] | None = None,
    status_packet_versions: tuple[int, ...] | None = None,
    lap_player_index: int = 0,
):
    pipeline = TelemetryPipeline(reorder_window_frames=1)
    indexes = status_player_indexes or (0,) * len(status_packets)
    versions = status_packet_versions or (1,) * len(status_packets)
    raws = [
        make_datagram(
            packet_id=7,
            packet_version=version,
            session_uid=SESSION_UID,
            frame=10,
            player_car_index=index,
            body=body,
            sequence=index + 1,
        )
        for index, version, body in zip(indexes, versions, status_packets)
    ]
    raws.append(
        _lap_packet(
            frame=10,
            lap_number=1,
            distance_m=100,
            session_time=1,
            player_car_index=lap_player_index,
            sequence=10,
        )
    )
    for raw in raws:
        pipeline.process(raw)
    result = pipeline.process(
        make_datagram(
            packet_id=255,
            session_uid=SESSION_UID,
            frame=11,
            sequence=11,
        )
    )
    assert len(result.car_samples) == 1
    return pipeline, result.car_samples[0]


def test_car_status_v1_decodes_f1_25_and_2026_layouts() -> None:
    f1_25_packet = PacketDecoder().decode(
        make_datagram(packet_id=7, body=_status_body())
    )
    season_pack_packet = PacketDecoder().decode(
        make_datagram(packet_format=2026, packet_id=7, body=_status_body(packet_format=2026))
    )

    f1_25 = CarStatusDecoder().decode(f1_25_packet)
    season_pack = CarStatusDecoder().decode(season_pack_packet)

    assert f1_25.error is None and f1_25.car_status is not None
    assert season_pack.error is None and season_pack.car_status is not None
    assert len(f1_25.car_status.cars) == 22
    assert len(season_pack.car_status.cars) == 24
    assert f1_25.car_status.cars[0].fuel_in_tank_reported == 12.5
    assert f1_25.car_status.cars[0].vehicle_fia_flag == -1
    assert f1_25.car_status.cars[0].ers_harvest_limit_per_lap is None
    assert f1_25.car_status.cars[0].ers_deployed_this_lap == 7.0
    assert season_pack.car_status.cars[0].ers_harvest_limit_per_lap == 9.5
    assert season_pack.car_status.cars[0].ers_deployed_this_lap == 7.0


def test_car_status_decoder_rejects_unsupported_version_and_body_size() -> None:
    decoder = CarStatusDecoder()
    unsupported = decoder.decode(
        PacketDecoder().decode(
            make_datagram(packet_id=7, packet_version=2, body=_status_body())
        )
    )
    malformed = decoder.decode(
        PacketDecoder().decode(make_datagram(packet_id=7, body=b"short"))
    )

    assert "unsupported car status" in (unsupported.error or "")
    assert "must be 1210 bytes" in (malformed.error or "")


def test_car_status_joins_same_frame_in_either_packet_order_and_keeps_raw_sentinels() -> None:
    pipeline = TelemetryPipeline(reorder_window_frames=1)
    status = make_datagram(
        packet_id=7,
        session_uid=SESSION_UID,
        frame=10,
        body=_status_body(),
        sequence=1,
    )
    lap = _lap_packet(
        frame=10,
        lap_number=1,
        distance_m=100,
        session_time=1,
        sequence=2,
    )
    pipeline.process(lap)
    pipeline.process(status)
    result = pipeline.process(
        make_datagram(packet_id=255, session_uid=SESSION_UID, frame=11, sequence=3)
    )

    sample = result.car_samples[0]
    assert sample.car_status_available is True
    assert sample.car_status_unavailable_reason is None
    assert sample.traction_control == 1
    assert sample.anti_lock_brakes is False
    assert sample.front_brake_bias_percent == 54
    assert sample.fuel_remaining_laps == -1.25
    assert sample.vehicle_fia_flag == -1
    assert sample.drs_activation_distance_m == 0
    assert pipeline.player_car_status_samples == 1
    assert pipeline.missing_player_car_status_samples == 0


def test_car_status_missing_index_mismatch_malformed_and_conflict_are_explicit() -> None:
    body = _status_body()
    missing_pipeline, missing = _pipeline_sample(())
    mismatch_pipeline, mismatch = _pipeline_sample((body,), status_player_indexes=(1,))
    malformed_pipeline, malformed = _pipeline_sample((b"bad",))
    conflict_pipeline, conflict = _pipeline_sample(
        (body, _status_body(fuel=14.0)), status_player_indexes=(0, 0)
    )
    unsupported_pipeline, unsupported = _pipeline_sample(
        (body,), status_packet_versions=(2,)
    )

    assert missing.car_status_available is False
    assert missing.car_status_unavailable_reason == "status_packet_missing"
    assert mismatch.car_status_unavailable_reason == "player_index_mismatch"
    assert malformed.car_status_unavailable_reason == "status_packet_malformed_or_unsupported"
    assert conflict.car_status_unavailable_reason == "conflicting_status_packets"
    assert unsupported.car_status_unavailable_reason == "status_packet_malformed_or_unsupported"
    assert len(unsupported_pipeline.car_status_decode_errors) == 1
    for pipeline in (missing_pipeline, mismatch_pipeline, malformed_pipeline, conflict_pipeline):
        assert pipeline.missing_player_car_status_samples == 1


def test_car_status_rejects_wire_format_mismatch_at_join() -> None:
    lap_packet = PacketDecoder().decode(
        _lap_packet(
            frame=10,
            lap_number=1,
            distance_m=100,
            session_time=1,
        )
    )
    status_packet = PacketDecoder().decode(
        make_datagram(
            packet_format=2026,
            packet_id=7,
            session_uid=SESSION_UID,
            frame=10,
            body=_status_body(packet_format=2026),
        )
    )
    decoded_status = CarStatusDecoder().decode(status_packet)

    car_status, reason = _join_car_status(
        lap_packet,
        0,
        [(status_packet, decoded_status.car_status, decoded_status.error)],
    )

    assert car_status is None
    assert reason == "wire_format_mismatch"


def test_nonfinite_and_invalid_car_status_fields_are_independently_nullable() -> None:
    lap = LapDataDecoder().decode(
        PacketDecoder().decode(
            _lap_packet(
                frame=1,
                lap_number=1,
                distance_m=10,
                session_time=1,
            )
        )
    )
    assert lap.lap_data is not None
    status_fields = list(_status_fields(fuel=float("nan")))
    status_fields[1] = 2
    status_fields[3] = 110
    raw_status = _F1_25_CAR_STATUS_V1_CAR.pack(*status_fields)
    decoded = CarStatusDecoder().decode(
        PacketDecoder().decode(make_datagram(packet_id=7, body=raw_status * 22))
    )
    assert decoded.car_status is not None
    sample = make_car_sample(
        session_uid=SESSION_UID,
        frame_identifier=1,
        session_time_s=1,
        car_index=0,
        attempt_id="attempt",
        lap=lap.lap_data.cars[0],
        telemetry=None,
        car_status=decoded.car_status.cars[0],
    )

    assert sample.car_status_available
    assert math.isnan(decoded.car_status.cars[0].fuel_in_tank_reported)
    assert sample.fuel_in_tank_reported is None
    assert sample.anti_lock_brakes is None
    assert sample.front_brake_bias_percent is None
    assert sample.vehicle_fia_flag == -1
    assert "invalid_car_status_fuel_in_tank_reported" in sample.validation_flags
    assert "invalid_car_status_anti_lock_brakes" in sample.validation_flags
    assert "invalid_car_status_front_brake_bias_percent" in sample.validation_flags
    assert "invalid_car_status_vehicle_fia_flag" in sample.validation_flags


def test_quality_distinguishes_persisted_invalid_status_from_missing(tmp_path) -> None:
    capture_path = tmp_path / "invalid-status.f1ecap"
    database_path = tmp_path / "state" / "f1.sqlite3"
    status_record = _F1_25_CAR_STATUS_V1_CAR.pack(
        *_status_fields(fuel=float("nan"))
    )
    with CaptureWriter(capture_path, {"fixture": "invalid-car-status"}) as writer:
        writer.write(_session_packet())
        writer.write(
            _lap_packet(
                frame=48,
                lap_number=1,
                distance_m=-1,
                session_time=0.9,
                sequence=1,
            )
        )
        writer.write(
            _lap_packet(
                frame=49,
                lap_number=1,
                distance_m=100,
                session_time=1,
                sequence=2,
            )
        )
        writer.write(
            make_datagram(
                packet_id=7,
                session_uid=SESSION_UID,
                frame=49,
                body=status_record * 22,
                sequence=3,
            )
        )

    imported = import_capture(capture_path, database_path)
    attempt = list_laps(database_path)[0]
    report = inspect_attempt_quality(database_path, attempt["attempt_key"])

    assert imported.car_status_packets == 1
    assert report is not None
    status = report["observed_status"]
    assert status["matched_sample_count"] == 1
    fuel = status["fields"]["fuel_in_tank_reported"]
    assert fuel == {"valid_count": 0, "missing_count": 0, "invalid_count": 1}
    fia_flag = status["fields"]["vehicle_fia_flag"]
    assert fia_flag["invalid_count"] == 1
    assert status["first_last_observed"]["vehicle_fia_flag"]["first"]["value"] == -1
