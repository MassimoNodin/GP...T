from __future__ import annotations

import struct
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from f1_engineer.analysis.quality import summarize_car_damage_observations
from f1_engineer.pipeline import TelemetryPipeline, _join_car_damage
from f1_engineer.recording.capture import CaptureWriter
from f1_engineer.storage.importer import import_capture, list_laps
from f1_engineer.storage.parquet import (
    TRACE_SCHEMA_V1,
    TRACE_SCHEMA_V2,
    TRACE_SCHEMA_V3,
    read_trace,
)
from f1_engineer.udp.car_damage import (
    CarDamageDecoder,
    _CAR_DAMAGE_V1_CAR,
    _F1_25_CAR_DAMAGE_V1_BODY_SIZE,
    _SEASON_PACK_2026_CAR_DAMAGE_V1_BODY_SIZE,
)
from f1_engineer.udp.decoder import PacketDecoder
from tests.helpers import make_datagram
from tests.test_lap_tracking import SESSION_UID, _lap_packet, _session_packet
from tests.test_season_pack_2026 import _SESSION_UID as SESSION_UID_2026
from tests.test_season_pack_2026 import _lap_body as _lap_body_2026
from tests.test_season_pack_2026 import _packet as _packet_2026


def _damage_record(
    *,
    wear: tuple[float, float, float, float] = (1.25, 2.5, 3.75, 4.25),
    values: tuple[int, ...] | None = None,
) -> bytes:
    tail = [0] * 30 if values is None else list(values)
    if values is None:
        tail[0] = 12  # RL tyre damage
        tail[4] = 13  # RL brake damage
        tail[8] = 14  # RL blistering
        tail[12] = 15  # front-left wing damage
        tail[18] = 1  # DRS fault
        tail[19] = 0  # ERS fault
        tail[28] = 0  # engine blown
        tail[29] = 1  # engine seized
    return _CAR_DAMAGE_V1_CAR.pack(*wear, *tail)


def _damage_body(
    *,
    packet_format: int = 2025,
    player_record: bytes | None = None,
    other_record: bytes | None = None,
) -> bytes:
    count = 22 if packet_format == 2025 else 24
    player = player_record or _damage_record()
    other = other_record or player
    return player + other * (count - 1)


def _damage_datagram(
    body: bytes,
    *,
    frame: int = 10,
    sequence: int = 1,
    player_car_index: int = 0,
    packet_format: int = 2025,
    packet_version: int = 1,
):
    return make_datagram(
        packet_format=packet_format,
        packet_id=10,
        packet_version=packet_version,
        session_uid=SESSION_UID if packet_format == 2025 else SESSION_UID_2026,
        frame=frame,
        player_car_index=player_car_index,
        body=body,
        sequence=sequence,
    )


def _pipeline_sample(damage_packets: tuple[tuple[bytes, int], ...]):
    pipeline = TelemetryPipeline(reorder_window_frames=1)
    raws = [
        _damage_datagram(body, sequence=index, player_car_index=player_index)
        for index, (body, player_index) in enumerate(damage_packets, start=1)
    ]
    raws.append(
        _lap_packet(
            frame=10,
            lap_number=1,
            distance_m=100,
            session_time=1,
            sequence=10,
        )
    )
    for raw in raws:
        pipeline.process(raw)
    result = pipeline.process(
        make_datagram(packet_id=255, session_uid=SESSION_UID, frame=11, sequence=11)
    )
    assert len(result.car_samples) == 1
    return pipeline, result.car_samples[0]


def test_car_damage_v1_decodes_both_exact_record_layouts_and_wheel_order() -> None:
    assert _CAR_DAMAGE_V1_CAR.size == 46
    assert _F1_25_CAR_DAMAGE_V1_BODY_SIZE == 1_012
    assert _SEASON_PACK_2026_CAR_DAMAGE_V1_BODY_SIZE == 1_104
    decoder = CarDamageDecoder()
    f1_packet = PacketDecoder().decode(
        _damage_datagram(_damage_body(), packet_format=2025)
    )
    pack_packet = PacketDecoder().decode(
        _damage_datagram(_damage_body(packet_format=2026), packet_format=2026)
    )

    f1 = decoder.decode(f1_packet)
    pack = decoder.decode(pack_packet)

    assert f1.error is None and f1.car_damage is not None
    assert pack.error is None and pack.car_damage is not None
    assert len(f1.car_damage.cars) == 22
    assert len(pack.car_damage.cars) == 24
    player = f1.car_damage.cars[0]
    assert player.tyres_wear_percent == (1.25, 2.5, 3.75, 4.25)
    assert player.tyres_damage_percent == (12, 0, 0, 0)
    assert player.brakes_damage_percent == (13, 0, 0, 0)
    assert player.tyre_blisters_percent == (14, 0, 0, 0)
    assert player.front_left_wing_damage_percent == 15
    assert player.drs_fault == 1
    assert player.ers_fault == 0
    assert player.engine_blown == 0
    assert player.engine_seized == 1
    assert player.raw_record == _damage_record()


def test_car_damage_decoder_rejects_unsupported_version_and_wrong_body_size() -> None:
    decoder = CarDamageDecoder()
    unsupported = decoder.decode(
        PacketDecoder().decode(
            _damage_datagram(_damage_body(), packet_version=2)
        )
    )
    malformed = decoder.decode(
        PacketDecoder().decode(_damage_datagram(b"short"))
    )

    assert "unsupported car damage" in (unsupported.error or "")
    assert "must be 1012 bytes" in (malformed.error or "")


def test_car_damage_joins_exact_frame_and_ignores_other_car_record_conflicts() -> None:
    body_a = _damage_body()
    other_record = bytearray(_damage_record())
    other_record[-5] = 99
    body_b = _damage_body(other_record=bytes(other_record))
    pipeline, sample = _pipeline_sample(((body_a, 0), (body_b, 0)))

    assert sample.car_damage_available
    assert sample.car_damage_unavailable_reason is None
    assert sample.tyre_wear_rl_percent == 1.25
    assert sample.tyre_wear_fr_percent == 4.25
    assert sample.tyre_damage_rl_percent == 12
    assert sample.front_left_wing_damage_percent == 15
    assert sample.drs_fault is True
    assert sample.ers_fault is False
    assert sample.engine_seized is True
    assert pipeline.car_damage_packets_admitted == 2
    assert pipeline.car_damage_packets_decoded == 2
    assert pipeline.player_car_damage_samples == 1


def test_car_damage_missing_malformed_unsupported_and_selected_conflict_are_explicit() -> None:
    missing_pipeline, missing = _pipeline_sample(())
    malformed_pipeline, malformed = _pipeline_sample(((b"bad", 0),))
    # Send v2 directly so the packet is admitted but unsupported.
    pipeline = TelemetryPipeline(reorder_window_frames=1)
    pipeline.process(
        _damage_datagram(_damage_body(), packet_version=2, sequence=1)
    )
    pipeline.process(
        _lap_packet(frame=10, lap_number=1, distance_m=100, session_time=1, sequence=2)
    )
    result = pipeline.process(
        make_datagram(packet_id=255, session_uid=SESSION_UID, frame=11, sequence=3)
    )
    unsupported_sample = result.car_samples[0]

    changed = bytearray(_damage_record())
    changed[16] = 19
    conflict_pipeline, conflict = _pipeline_sample(
        ((_damage_body(), 0), (_damage_body(player_record=bytes(changed)), 0))
    )

    assert missing.car_damage_available is False
    assert missing.car_damage_unavailable_reason == "damage_packet_missing"
    assert malformed.car_damage_unavailable_reason == "damage_packet_malformed_or_unsupported"
    assert malformed_pipeline.car_damage_packets_admitted == 1
    assert malformed_pipeline.car_damage_packets_decoded == 0
    assert unsupported_sample.car_damage_unavailable_reason == "damage_packet_malformed_or_unsupported"
    assert len(pipeline.car_damage_decode_errors) == 1
    assert conflict.car_damage_unavailable_reason == "conflicting_damage_packets"
    assert missing_pipeline.missing_player_car_damage_samples == 1
    assert conflict_pipeline.missing_player_car_damage_samples == 1


def test_car_damage_canonical_validation_is_independent_per_field() -> None:
    tail = [0] * 30
    tail[0] = 101  # RL tyre damage
    tail[18] = 2  # invalid DRS flag
    record = _damage_record(wear=(float("nan"), 0.0, 100.0, 100.1), values=tuple(tail))
    pipeline, sample = _pipeline_sample(((_damage_body(player_record=record), 0),))

    assert sample.car_damage_available
    assert sample.tyre_wear_rl_percent is None
    assert sample.tyre_wear_rr_percent == 0.0
    assert sample.tyre_wear_fl_percent == 100.0
    assert sample.tyre_wear_fr_percent is None
    assert sample.tyre_damage_rl_percent is None
    assert sample.drs_fault is None
    assert "invalid_car_damage_tyre_wear_rl_percent" in sample.validation_flags
    assert "invalid_car_damage_tyre_wear_fr_percent" in sample.validation_flags
    assert "invalid_car_damage_tyre_damage_rl_percent" in sample.validation_flags
    assert "invalid_car_damage_drs_fault" in sample.validation_flags
    assert pipeline.player_car_damage_samples == 1


def test_car_damage_join_supports_2026_player_index_23() -> None:
    lap_packet = PacketDecoder().decode(
        _packet_2026(
            2,
            _lap_body_2026(active_car_index=23),
            frame=10,
            player_car_index=23,
        )
    )
    damage_packet = PacketDecoder().decode(
        _packet_2026(
            10,
            _damage_body(packet_format=2026),
            frame=10,
            player_car_index=23,
        )
    )
    damage = CarDamageDecoder().decode(damage_packet)

    joined, reason = _join_car_damage(
        lap_packet,
        23,
        [(damage_packet, damage.car_damage, damage.error)],
    )

    assert reason is None
    assert joined is not None
    assert joined.raw_record == _damage_record()

    f1_lap_packet = PacketDecoder().decode(
        _lap_packet(
            frame=10,
            lap_number=1,
            distance_m=100,
            session_time=1,
            player_car_index=0,
        )
    )
    wrong_player_packet = PacketDecoder().decode(
        _damage_datagram(_damage_body(), player_car_index=1)
    )
    wrong_player = CarDamageDecoder().decode(wrong_player_packet)
    mismatch, reason = _join_car_damage(
        f1_lap_packet,
        0,
        [(wrong_player_packet, wrong_player.car_damage, wrong_player.error)],
    )
    assert mismatch is None
    assert reason == "player_index_mismatch"

    f1_damage_packet = PacketDecoder().decode(
        _damage_datagram(_damage_body())
    )
    f1_damage = CarDamageDecoder().decode(f1_damage_packet)
    out_of_range, reason = _join_car_damage(
        f1_lap_packet,
        23,
        [(f1_damage_packet, f1_damage.car_damage, f1_damage.error)],
    )
    assert out_of_range is None
    assert reason == "player_index_mismatch"

    wrong_format, reason = _join_car_damage(
        f1_lap_packet,
        0,
        [(damage_packet, damage.car_damage, damage.error)],
    )
    assert wrong_format is None
    assert reason == "wire_format_mismatch"


def test_car_damage_quality_summary_is_bounded_sparse_and_schema_aware() -> None:
    base = {
        "frame_identifier": 15,
        "session_time_s": 2.5,
        "lap_distance_m": 120.0,
        "validation_flags": ["invalid_car_damage_drs_fault"],
        "car_damage_available": True,
        "car_damage_unavailable_reason": None,
        "tyre_wear_rl_percent": 1.5,
        "drs_fault": None,
    }
    summary = summarize_car_damage_observations((base, {}), trace_schema_version=4)

    assert summary["version"] == "car-damage-observations-v1"
    assert summary["status"] == "available"
    assert summary["matched_sample_count"] == 1
    assert summary["missing_join_sample_count"] == 1
    assert summary["fields"]["tyre_wear_rl_percent"] == {
        "valid_count": 1,
        "missing_count": 1,
        "invalid_count": 0,
    }
    assert summary["fields"]["drs_fault"] == {
        "valid_count": 0,
        "missing_count": 1,
        "invalid_count": 1,
    }
    assert summary["first_last_observed"]["tyre_wear_rl_percent"]["first"] == {
        "value": 1.5,
        "frame_identifier": 15,
        "session_time_s": 2.5,
        "lap_distance_m": 120.0,
    }
    assert len(summary["fields"]) == 34
    legacy = summarize_car_damage_observations((base,), trace_schema_version=3)
    assert legacy["status"] == "unavailable_in_trace_schema"
    assert legacy["matched_sample_count"] is None
    assert legacy["fields"] is None
    no_joins = summarize_car_damage_observations(({},), trace_schema_version=4)
    assert no_joins["status"] == "no_joined_samples"
    assert no_joins["matched_sample_count"] == 0
    assert no_joins["fields"]["tyre_wear_rl_percent"]["missing_count"] == 1


@pytest.mark.parametrize("schema", (TRACE_SCHEMA_V1, TRACE_SCHEMA_V2, TRACE_SCHEMA_V3))
def test_legacy_trace_reader_synthesizes_null_car_damage_fields(tmp_path, schema) -> None:
    path = tmp_path / f"schema-v{schema.metadata[b'trace_schema_version'].decode()}.parquet"
    row = {name: None for name in schema.names}
    row.update(
        {
            "session_uid": "123",
            "frame_identifier": 1,
            "session_time_s": 1.0,
            "car_index": 0,
            "attempt_id": "legacy",
            "lap_number": 1,
            "current_lap_time_ms": 1,
            "car_telemetry_available": False,
            "validation_flags": [],
        }
    )
    pq.write_table(pa.Table.from_pylist([row], schema=schema), path)

    _, table = read_trace(
        path,
        columns=["car_damage_available", "car_damage_unavailable_reason", "tyre_wear_rl_percent"],
    )

    assert table.to_pylist() == [
        {
            "car_damage_available": None,
            "car_damage_unavailable_reason": None,
            "tyre_wear_rl_percent": None,
        }
    ]


def test_car_damage_capture_import_persists_v4_and_quality(tmp_path) -> None:
    capture_path = tmp_path / "car-damage.f1ecap"
    database_path = tmp_path / "state" / "f1.sqlite3"
    with CaptureWriter(capture_path, {"fixture": "car-damage"}) as writer:
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
        writer.write(_damage_datagram(_damage_body(), frame=49, sequence=3))
        writer.write(_damage_datagram(_damage_body(), frame=49, sequence=4))

    imported = import_capture(capture_path, database_path)
    attempt = list_laps(database_path)[0]
    from f1_engineer.analysis.quality import inspect_attempt_quality

    report = inspect_attempt_quality(database_path, attempt["attempt_key"])

    assert imported.car_damage_packets_raw == 2
    assert imported.car_damage_packets_admitted == 1
    assert imported.car_damage_packets_decoded == 1
    assert imported.player_car_damage_samples == 1
    assert imported.missing_player_car_damage_samples == 0
    assert report is not None
    damage = report["car_damage_observations"]
    assert damage["status"] == "available"
    assert damage["matched_sample_count"] == 1
    assert damage["sample_count"] == 1
    assert damage["first_last_observed"]["tyre_wear_rl_percent"]["first"]["value"] == 1.25
    reimported = import_capture(capture_path, database_path)
    assert reimported.already_imported is True
    assert reimported.run_id == imported.run_id
