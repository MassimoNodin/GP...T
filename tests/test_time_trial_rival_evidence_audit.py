from __future__ import annotations

import importlib.util
import struct
from pathlib import Path

from f1_engineer.recording.capture import CaptureWriter
from f1_engineer.udp.car_telemetry import _F1_25_CAR_TELEMETRY_V1_CAR
from f1_engineer.udp.lap_data import _LAP_DATA_V1_CAR
from f1_engineer.udp.motion import _MOTION_CAR_V1
from tests.helpers import make_datagram


ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "time_trial_rival_evidence",
    ROOT / "scripts" / "audits" / "time_trial_rival_evidence.py",
)
assert _SPEC is not None and _SPEC.loader is not None
audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit)


def _role(**overrides: object) -> dict[str, object]:
    role: dict[str, object] = {
        "index_missing": 0,
        "index_out_of_range": 0,
        "index_in_range": 0,
        "index_equals_player": 0,
        "synced_frames": 0,
        "lap_distance_min": None,
        "lap_distance_max": None,
        "speed_min_kph": None,
        "speed_max_kph": None,
        "brake_positive_frames": 0,
        "throttle_positive_frames": 0,
        "gears": [],
        "motion_span_m": 0.0,
        "last_lap_time_positive_frames": 0,
        "per_lap_distance_span": {},
        "equal_speed_frames": 0,
        "both_moving_frames": 0,
    }
    role.update(overrides)
    return role


def _covered_role() -> dict[str, object]:
    return _role(
        index_in_range=30,
        synced_frames=30,
        lap_distance_min=0.0,
        lap_distance_max=900.0,
        speed_min_kph=40.0,
        speed_max_kph=280.0,
        brake_positive_frames=8,
        throttle_positive_frames=20,
        gears=[3, 4, 5],
        motion_span_m=120.0,
        per_lap_distance_span={"1": {"min_m": 0.0, "max_m": 900.0, "samples": 30}},
        both_moving_frames=30,
        equal_speed_frames=0,
        last_lap_time_positive_frames=4,
    )


def test_documented_time_trial_dataset_sizes() -> None:
    assert audit._F1_25_TIME_TRIAL_DATASET.size == 24
    assert audit._SEASON_PACK_2026_TIME_TRIAL_DATASET.size == 25
    parsed = audit.parse_time_trial_body(2025, b"\x00" * 72)
    assert parsed["parsed"] is True
    assert parsed["datasets"][2]["team_id"] == 0
    body = audit._SEASON_PACK_2026_TIME_TRIAL_DATASET.pack(
        4, 300, 80_000, 20_000, 25_000, 35_000, 0, 1, 0, 1, 0, 1
    )
    parsed_2026 = audit.parse_time_trial_body(2026, body * 3)
    assert parsed_2026["parsed"] is True
    assert parsed_2026["datasets"][0]["team_id"] == 300
    assert parsed_2026["datasets"][0]["lap_time_ms"] == 80_000
    rejected = audit.parse_time_trial_body(2025, b"\x00" * 71)
    assert rejected["parsed"] is False
    assert rejected["reason"] == "unexpected_body_size"


def test_index_or_lap_time_alone_is_not_a_detailed_trace() -> None:
    indexed = audit.conclude_rival(
        _role(index_in_range=100),
        packet14_valid_lap_times=0,
        history_positive_lap_times=0,
        track_length_m=5276,
    )
    assert indexed["label"] == "not_established"
    assert indexed["reasons"]
    timed = audit.conclude_rival(
        _role(index_missing=100, last_lap_time_positive_frames=0),
        packet14_valid_lap_times=3,
        history_positive_lap_times=0,
        track_length_m=5276,
    )
    assert timed["label"] == "timing_only"
    copied = audit.conclude_rival(
        _covered_role() | {"equal_speed_frames": 30},
        packet14_valid_lap_times=1,
        history_positive_lap_times=0,
        track_length_m=1000,
    )
    assert copied["label"] != "detailed_channels_with_lap_coverage"
    assert "selected_slot_speed_matches_player_while_both_move" in copied["reasons"]


def test_full_channel_coverage_is_distinct_from_a_partial_trace() -> None:
    covered = audit.conclude_rival(
        _covered_role(),
        packet14_valid_lap_times=1,
        history_positive_lap_times=1,
        track_length_m=1000,
    )
    assert covered["label"] == "detailed_channels_with_lap_coverage"
    partial = audit.conclude_rival(
        _covered_role()
        | {"per_lap_distance_span": {"1": {"min_m": 0.0, "max_m": 150.0, "samples": 30}}},
        packet14_valid_lap_times=0,
        history_positive_lap_times=0,
        track_length_m=5276,
    )
    assert partial["label"] == "detailed_channels_partial"
    no_brake = audit.conclude_rival(
        _covered_role() | {"brake_positive_frames": 0},
        packet14_valid_lap_times=0,
        history_positive_lap_times=0,
        track_length_m=1000,
    )
    assert no_brake["label"] == "detailed_channels_partial"
    assert "brake_channel_not_observed" in no_brake["reasons"]


def test_inactive_result_status_blocks_a_detailed_comparison_claim() -> None:
    inactive = audit.conclude_rival(
        _covered_role() | {"result_status": {"0": 30}, "outside_active_count": 30},
        packet14_valid_lap_times=1,
        history_positive_lap_times=1,
        track_length_m=1000,
    )
    assert inactive["label"] == "detailed_channels_with_lap_coverage"
    assert inactive["blocks_detailed_comparison"] is True
    assert "indexed_slot_result_status_never_active" in inactive["reasons"]
    assert "indexed_slot_outside_reported_active_cars" in inactive["reasons"]
    active = audit.conclude_rival(
        _covered_role() | {"result_status": {"2": 30}, "outside_active_count": 0},
        packet14_valid_lap_times=1,
        history_positive_lap_times=1,
        track_length_m=1000,
    )
    assert active["blocks_detailed_comparison"] is False
    assert "indexed_slot_result_status_never_active" not in active["reasons"]


def test_speed_that_disagrees_with_lap_distance_blocks_detailed_comparison() -> None:
    disagreed = audit.conclude_rival(
        _covered_role()
        | {
            "result_status": {"2": 30},
            "outside_active_count": 0,
            "kinematic_frames": 30,
            "kinematic_mismatch_frames": 20,
        },
        packet14_valid_lap_times=1,
        history_positive_lap_times=1,
        track_length_m=1000,
    )
    assert disagreed["label"] == "detailed_channels_with_lap_coverage"
    assert disagreed["blocks_detailed_comparison"] is True
    assert "reported_speed_disagrees_with_lap_distance" in disagreed["reasons"]


def test_player_collision_and_missing_index_have_explicit_reasons() -> None:
    collision = audit.conclude_rival(
        _covered_role() | {"index_equals_player": 30},
        packet14_valid_lap_times=0,
        history_positive_lap_times=0,
        track_length_m=1000,
    )
    assert collision["distinct_selected_frames"] == 0
    assert "rival_index_matches_player_whenever_selected" in collision["reasons"]
    assert collision["label"] != "detailed_channels_with_lap_coverage"


def test_synthetic_capture_keeps_timing_and_channels_separate(tmp_path: Path) -> None:
    datagrams = []
    datagrams.append(
        make_datagram(
            packet_id=14,
            frame=1,
            session_time=1.0,
            body=_time_trial_body(car_index=255, lap_time_ms=79_295, valid=1),
            sequence=1,
        )
    )
    for frame in range(1, 26):
        distance = frame * 6.0
        datagrams.append(
            _lap_packet(
                frame=frame,
                session_time=frame / 20,
                sequence=frame * 10,
                pb_index=0,
                rival_index=255,
                samples={0: {"distance_m": distance, "speed_placeholder": True}},
            )
        )
        datagrams.append(_telemetry_packet(frame=frame, sequence=frame * 10 + 1, speeds={0: 100}))
        datagrams.append(_motion_packet(frame=frame, sequence=frame * 10 + 2, positions={0: (distance, 1.0)}))
    absent = audit.audit_datagrams(datagrams)
    session = absent["sessions"][0]
    assert session["rival_conclusion"]["label"] == "timing_only"
    assert session["roles"]["rival"]["index_in_range"] == 0
    assert session["time_trial_packet"]["rival"]["valid_positive_lap_times"] == 1
    assert session["roles"]["rival"]["brake_positive_frames"] == 0

    traced = []
    for frame in range(1, 26):
        rival_distance = frame * 8.0
        traced.append(
            _lap_packet(
                frame=frame,
                session_time=frame / 20,
                sequence=frame * 10,
                pb_index=255,
                rival_index=3,
                samples={
                    0: {"distance_m": frame * 3.0, "last_lap_time_ms": 0},
                    3: {
                        "distance_m": rival_distance,
                        "last_lap_time_ms": 81_000 if frame == 25 else 0,
                        "lap_number": 1,
                        "result_status": 2,
                    },
                },
            )
        )
        traced.append(
            _telemetry_packet(
                frame=frame,
                sequence=frame * 10 + 1,
                speeds={0: 120, 3: 40 + frame},
                brakes={3: 0.8 if frame % 5 == 0 else 0.0},
                throttles={0: 1.0, 3: 0.4 if frame % 5 else 1.0},
                gears={0: 6, 3: 2 + (frame // 8)},
            )
        )
        traced.append(
            _motion_packet(
                frame=frame,
                sequence=frame * 10 + 2,
                positions={0: (frame * 3.0, 0.0), 3: (100.0 + rival_distance, 30.0)},
            )
        )
    traced.append(
        _lap_packet(
            frame=40,
            session_time=3.0,
            sequence=400,
            pb_index=255,
            rival_index=4,
            samples={3: {"distance_m": 10.0}, 4: {"distance_m": 20.0}},
        )
    )
    traced.append(_telemetry_packet(frame=40, sequence=401, speeds={3: 150, 4: 80}))
    traced.append(_motion_packet(frame=40, sequence=402, positions={3: (10.0, 10.0), 4: (20.0, 20.0)}))
    present = audit.audit_datagrams(traced)
    present_session = present["sessions"][0]
    rival = present_session["roles"]["rival"]
    assert rival["selected_indices"] == [3, 4]
    assert rival["synced_frames"] >= 20
    assert rival["brake_positive_frames"] >= 3
    assert present_session["selection_transitions"] == 1
    assert present_session["stale_rival_movement_frames"] > 0
    assert present_session["rival_conclusion"]["label"] == "detailed_channels_partial"
    assert rival["index_equals_player"] == 0

    capture_path = tmp_path / "synthetic.f1ecap"
    with CaptureWriter(capture_path, {"fixture": "time-trial-rival-audit"}) as writer:
        for datagram in traced:
            writer.write(datagram)
    before = capture_path.read_bytes()
    report = audit.audit_capture(capture_path)
    assert capture_path.read_bytes() == before
    assert report["file"]["reader_error"] is None
    assert report["file"]["complete"] is True
    assert report["sessions"][0]["roles"]["rival"]["selected_indices"] == [3, 4]


def test_production_code_does_not_consume_rival_indices_or_packet_14() -> None:
    hits = []
    packet_uses = []
    for path in (ROOT / "f1_engineer").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT / "f1_engineer").as_posix()
        if "time_trial_pb_car_index" in text or "time_trial_rival_car_index" in text:
            hits.append(relative)
        if "PacketId.TIME_TRIAL" in text or "packet_id == 14" in text:
            packet_uses.append(relative)
    assert hits == ["udp/lap_data.py"]
    assert packet_uses == []
    pipeline = (ROOT / "f1_engineer" / "pipeline.py").read_text(encoding="utf-8")
    assert "session_history_non_player_packets" in pipeline
    assert "time_trial_rival_car_index" not in pipeline


def _lap_packet(
    *,
    frame: int,
    session_time: float,
    sequence: int,
    pb_index: int,
    rival_index: int,
    samples: dict[int, dict[str, object]],
) -> object:
    records = []
    for car_index in range(22):
        sample = samples.get(car_index, {})
        distance = float(sample.get("distance_m", 0.0))
        records.append(
            _LAP_DATA_V1_CAR.pack(
                int(sample.get("last_lap_time_ms", 0)),
                int(sample.get("current_lap_time_ms", int(distance * 10))),
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                distance,
                distance,
                0.0,
                1 if car_index in samples else 0,
                int(sample.get("lap_number", 1 if car_index in samples else 0)),
                0,
                0,
                0,
                int(sample.get("invalid", 0)),
                0,
                0,
                0,
                0,
                0,
                1,
                1 if car_index in samples else 0,
                int(sample.get("result_status", 2 if car_index in samples else 0)),
                0,
                0,
                0,
                0,
                0.0,
                255,
            )
        )
    return make_datagram(
        packet_id=2,
        frame=frame,
        session_time=session_time,
        body=b"".join(records) + bytes((pb_index, rival_index)),
        sequence=sequence,
        player_car_index=0,
    )


def _telemetry_packet(
    *,
    frame: int,
    sequence: int,
    speeds: dict[int, int],
    brakes: dict[int, float] | None = None,
    throttles: dict[int, float] | None = None,
    gears: dict[int, int] | None = None,
) -> object:
    brakes = brakes or {}
    throttles = throttles or {}
    gears = gears or {}
    records = []
    for car_index in range(22):
        records.append(
            _F1_25_CAR_TELEMETRY_V1_CAR.pack(
                speeds.get(car_index, 0),
                throttles.get(car_index, 0.0),
                0.0,
                brakes.get(car_index, 0.0),
                0,
                gears.get(car_index, 0),
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0.0,
                0.0,
                0.0,
                0.0,
                0,
                0,
                0,
                0,
            )
        )
    return make_datagram(
        packet_id=6,
        frame=frame,
        session_time=frame / 20,
        body=b"".join(records) + bytes((0, 255, 0)),
        sequence=sequence,
    )


def _motion_packet(
    *,
    frame: int,
    sequence: int,
    positions: dict[int, tuple[float, float]],
) -> object:
    records = []
    for car_index in range(22):
        x_position, z_position = positions.get(car_index, (0.0, 0.0))
        records.append(
            _MOTION_CAR_V1.pack(
                x_position,
                0.0,
                z_position,
                0.0,
                0.0,
                0.0,
                0,
                0,
                0,
                0,
                0,
                0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
            )
        )
    return make_datagram(
        packet_id=0,
        frame=frame,
        session_time=frame / 20,
        body=b"".join(records),
        sequence=sequence,
    )


def _time_trial_body(*, car_index: int, lap_time_ms: int, valid: int) -> bytes:
    dataset = audit._F1_25_TIME_TRIAL_DATASET.pack(
        car_index, 0, lap_time_ms, 20_000, 25_000, 34_295, 0, 0, 0, 0, 0, valid
    )
    empty = audit._F1_25_TIME_TRIAL_DATASET.pack(255, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    return empty + empty + dataset


def test_motion_record_matches_decoder_layout() -> None:
    assert _MOTION_CAR_V1.size == 60
    assert _LAP_DATA_V1_CAR.size == 57
    assert struct.calcsize("<BBIIIIBBBBBB") == 24
