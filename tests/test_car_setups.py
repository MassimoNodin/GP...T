from __future__ import annotations

import math
import struct

from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.udp.car_setups import CarSetupsDecoder
from f1_engineer.udp.decoder import PacketDecoder
from tests.helpers import make_datagram


_RECORD = struct.Struct("<4B4f9B4fBf")
_SESSION_UID = 9_900_200


def _record(*, front_wing: int = 45, fuel_load: float = 25.5) -> bytes:
    return _RECORD.pack(
        front_wing,
        38,
        55,
        30,
        -3.5,
        -2.5,
        0.1,
        0.2,
        4,
        5,
        6,
        7,
        8,
        9,
        95,
        55,
        10,
        22.0,
        23.0,
        24.0,
        25.0,
        50,
        fuel_load,
    )


def _body(*, packet_format: int = 2025, player: bytes | None = None, next_wing=12.5):
    count = 22 if packet_format == 2025 else 24
    car = _record()
    cars = [car for _ in range(count)]
    if player is not None:
        cars[3] = player
    return b"".join(cars) + struct.pack("<f", next_wing)


def _setup_packet(
    frame: int,
    sequence: int,
    *,
    packet_format: int = 2025,
    player_car_index: int = 3,
    body: bytes | None = None,
):
    return make_datagram(
        packet_format=packet_format,
        packet_id=5,
        session_uid=_SESSION_UID,
        frame=frame,
        session_time=frame / 60,
        player_car_index=player_car_index,
        body=body if body is not None else _body(packet_format=packet_format),
        sequence=sequence,
    )


def _observations(packets, *, reorder_window_frames: int = 1):
    pipeline = TelemetryPipeline(reorder_window_frames=reorder_window_frames)
    result = []
    for packet in packets:
        result.extend(pipeline.process(packet).player_car_setup_observations)
    result.extend(pipeline.finish_with_outputs().player_car_setup_observations)
    return pipeline, result


def test_car_setups_v1_decodes_both_format_layouts():
    decoder = CarSetupsDecoder()
    f1_25_packet = PacketDecoder().decode(
        make_datagram(packet_id=5, body=_body())
    )
    season_pack_packet = PacketDecoder().decode(
        make_datagram(packet_format=2026, packet_id=5, body=_body(packet_format=2026))
    )

    f1_25 = decoder.decode(f1_25_packet)
    season_pack = decoder.decode(season_pack_packet)

    assert f1_25.error is None and f1_25.setups is not None
    assert season_pack.error is None and season_pack.setups is not None
    assert len(f1_25.setups.cars) == 22
    assert len(season_pack.setups.cars) == 24
    assert f1_25.setups.cars[0].front_wing == 45
    assert f1_25.setups.cars[0].front_camber == -3.5
    assert f1_25.setups.cars[0].rear_left_tyre_pressure_psi == 22.0
    assert f1_25.setups.cars[0].fuel_load == 25.5
    assert f1_25.setups.next_front_wing_value == 12.5
    assert season_pack.setups.cars[23].front_wing == 45


def test_car_setups_v1_rejects_unsupported_version_and_wrong_body_length():
    decoder = CarSetupsDecoder()
    unsupported = decoder.decode(
        PacketDecoder().decode(
            make_datagram(packet_id=5, packet_version=2, body=_body())
        )
    )
    malformed = decoder.decode(
        PacketDecoder().decode(make_datagram(packet_id=5, body=b"short"))
    )

    assert "unsupported Car Setups" in (unsupported.error or "")
    assert "must be 1104 bytes" in (malformed.error or "")


def test_non_finite_setup_fields_are_individually_unavailable():
    bad_record = bytearray(_record())
    struct.pack_into("<f", bad_record, 4, math.nan)
    packet = PacketDecoder().decode(
        make_datagram(packet_id=5, body=_body(player=bytes(bad_record), next_wing=math.inf))
    )

    decoded = CarSetupsDecoder().decode(packet)

    assert decoded.error is None and decoded.setups is not None
    player = decoded.setups.cars[3]
    assert player.front_camber is None
    assert player.invalid_fields == ("front_camber",)
    assert decoded.setups.cars[0].front_camber == -3.5
    assert decoded.setups.next_front_wing_value is None
    assert decoded.setups.invalid_packet_fields == ("next_front_wing_value",)


def test_setup_observations_follow_admitted_frame_order_across_wrap_and_ignore_late():
    packets = [
        _setup_packet(0xFFFF_FFFE, 1, body=_body(player=_record(front_wing=1))),
        _setup_packet(0xFFFF_FFFF, 2, body=_body(player=_record(front_wing=2))),
        _setup_packet(0, 3, body=_body(player=_record(front_wing=3))),
    ]
    pipeline, observations = _observations(packets, reorder_window_frames=3)

    assert [item.overall_frame_identifier for item in observations] == [
        0xFFFF_FFFE,
        0xFFFF_FFFF,
        0,
    ]
    assert [item.frame_ordinal for item in observations] == [1, 2, 3]
    assert [item.setup.front_wing for item in observations] == [1, 2, 3]
    assert pipeline.car_setups_packets_decoded == 3

    late_pipeline, late_observations = _observations(
        [
            _setup_packet(10, 1, body=_body(player=_record(front_wing=10))),
            _setup_packet(12, 2, body=_body(player=_record(front_wing=12))),
            _setup_packet(11, 3, body=_body(player=_record(front_wing=11))),
        ]
    )
    assert [item.overall_frame_identifier for item in late_observations] == [10, 12]
    assert [item.setup.front_wing for item in late_observations] == [10, 12]
    assert late_pipeline.frames.late_packets_ignored == 1


def test_conflicting_player_setup_is_unavailable_but_other_car_changes_are_ignored():
    differing_player = _body(player=_record(front_wing=46))
    observations = _observations(
        [
            _setup_packet(20, 1, body=_body()),
            _setup_packet(20, 2, body=differing_player),
            _setup_packet(21, 3),
        ]
    )[1]
    assert observations[0].status == "unavailable"
    assert observations[0].reason == "conflicting_player_car_setup_evidence"
    assert observations[0].setup is None
    assert observations[0].source_packet_count == 2

    other_car_change = bytearray(_body())
    other_car_change[:50] = _record(front_wing=99)
    harmless = _observations(
        [
            _setup_packet(22, 1, body=_body()),
            _setup_packet(22, 2, body=bytes(other_car_change)),
            _setup_packet(23, 3),
        ]
    )[1]
    assert harmless[0].status == "observed"
    assert harmless[0].setup.front_wing == 45
    assert harmless[0].source_packet_count == 2


def test_lifecycle_boundary_setup_is_unavailable_fence():
    boundary_event = make_datagram(
        packet_id=3,
        session_uid=_SESSION_UID,
        frame=31,
        session_time=1.0,
        player_car_index=3,
        body=b"FLBK" + struct.pack("<If", 10, 0.5) + b"\0" * 4,
        sequence=2,
    )
    _, observations = _observations(
        [
            _setup_packet(30, 1, body=_body(player=_record(front_wing=30))),
            boundary_event,
            _setup_packet(31, 3, body=_body(player=_record(front_wing=31))),
            _setup_packet(32, 4, body=_body(player=_record(front_wing=32))),
        ]
    )

    assert [item.overall_frame_identifier for item in observations] == [30, 31, 32]
    assert observations[0].status == "observed"
    assert observations[1].status == "unavailable"
    assert observations[1].reason == "lifecycle_boundary"
    assert observations[1].association_epoch == observations[2].association_epoch
    assert observations[2].setup.front_wing == 32


def test_setup_observation_buffer_emits_a_bounded_truncation_fence():
    pipeline = TelemetryPipeline(reorder_window_frames=5)
    pipeline.max_buffered_player_car_setup_observations = 1
    for frame in range(3):
        pipeline.process(_setup_packet(frame + 1, frame + 1))
    observations = pipeline.finish_with_outputs().player_car_setup_observations

    assert len(observations) == 2
    assert observations[0].status == "observed"
    assert observations[1].status == "truncated"
    assert pipeline.player_car_setup_observations_dropped == 2
