from __future__ import annotations

from dataclasses import replace
import struct

import pytest

from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.analysis.service import _sector_timing_difference
from f1_engineer.sessions.lap_tracker import LapAttempt, LapDisposition
from f1_engineer.sessions.session_history import (
    SessionHistoryAccumulator,
    SessionHistoryObservation,
)
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.models import PacketFormat, PacketFrame
from f1_engineer.udp.session_history import (
    SESSION_HISTORY_BODY_SIZE,
    SessionHistoryDecoder,
    SessionHistoryLap,
    SessionHistoryPacket,
    SessionHistoryTyreStint,
)
from tests.helpers import make_datagram


_LAP = struct.Struct("<IHBHBHBB")


def _body(
    *,
    car_index: int = 7,
    laps: tuple[tuple[int, int, int, int, int, int, int, int], ...] = (),
    stints: tuple[tuple[int, int, int], ...] = (),
    counts: tuple[int, int] | None = None,
) -> bytes:
    body = bytearray(SESSION_HISTORY_BODY_SIZE)
    lap_count, stint_count = counts or (len(laps), len(stints))
    body[:7] = bytes((car_index, lap_count, stint_count, 0, 1, 2, 3))
    for index, values in enumerate(laps):
        _LAP.pack_into(body, 7 + index * _LAP.size, *values)
    for index, values in enumerate(stints):
        struct.pack_into("<BBB", body, 7 + 100 * _LAP.size + index * 3, *values)
    return bytes(body)


def _decode(
    *,
    packet_format: int = 2025,
    player_car_index: int = 7,
    body: bytes | None = None,
    packet_version: int = 1,
    sequence: int = 19,
    frame: int = 40,
):
    raw = make_datagram(
        packet_format=packet_format,
        packet_id=11,
        packet_version=packet_version,
        session_uid=88,
        frame=frame,
        session_time=42.5,
        body=body if body is not None else _body(car_index=player_car_index),
        sequence=sequence,
        player_car_index=player_car_index,
    )
    packet = PacketDecoder().decode(raw)
    return SessionHistoryDecoder().decode(packet, frame_ordinal=6)


@pytest.mark.parametrize(
    ("packet_format", "car_index"),
    [(PacketFormat.F1_25, 21), (PacketFormat.SEASON_PACK_2026, 23)],
)
def test_session_history_decodes_formats_and_preserves_timing_parts(
    packet_format: PacketFormat, car_index: int
) -> None:
    body = _body(
        car_index=car_index,
        laps=((79_295, 27_446, 0, 17_987, 0, 33_861, 0, 0x1E),),
        stints=((5, 1, 2), (255, 3, 4)),
    )

    result = _decode(
        packet_format=int(packet_format), player_car_index=car_index, body=body
    )

    assert result.error is None
    assert result.history is not None
    assert result.history.packet_format == int(packet_format)
    assert result.history.frame_ordinal == 6
    assert result.history.source_sequence == 19
    assert result.history.car_index == car_index
    lap = result.history.lap_history[0]
    assert lap.sector1_time_ms_part == 27_446
    assert lap.sector1_time_minutes_part == 0
    assert lap.sector1_time_ms == 27_446
    assert lap.validity_flags == 0x1E
    assert lap.unknown_validity_bits == 0x10
    assert lap.to_dict()["sector_sum_residual_ms"] == -1
    assert [stint.to_dict()["current_stint"] for stint in result.history.tyre_stints] == [
        False,
        True,
    ]


def test_session_history_combines_sector_minutes_without_normalizing_raw_parts() -> None:
    result = _decode(
        body=_body(
            car_index=7,
            laps=((181_000, 1_000, 1, 60_000, 0, 60_000, 1, 0x0F),),
        )
    )
    assert result.history is not None
    lap = result.history.lap_history[0]
    assert (lap.sector1_time_ms, lap.sector2_time_ms, lap.sector3_time_ms) == (
        61_000,
        60_000,
        120_000,
    )
    assert lap.sector1_time_ms_part == 1_000
    assert lap.sector1_time_minutes_part == 1
    assert lap.to_dict()["sector1_time_available"] is True
    assert lap.sector_sum_residual_ms == 60_000


@pytest.mark.parametrize(
    ("body", "packet_version", "expected"),
    [
        (_body(counts=(101, 0)), 1, "invalid_session_history_lap_count"),
        (_body(counts=(0, 9)), 1, "invalid_session_history_stint_count"),
        (_body(car_index=22), 1, "invalid_session_history_car_index"),
        (_body(), 2, "unsupported_session_history_version"),
        (b"short", 1, "malformed_session_history_body_size"),
    ],
)
def test_session_history_rejects_unsupported_or_malformed_packets(
    body: bytes, packet_version: int, expected: str
) -> None:
    result = _decode(body=body, packet_version=packet_version)
    assert result.history is None
    assert result.error == expected


def test_f1_25_rejects_2026_only_player_index_but_2026_accepts_index_23() -> None:
    f1_25 = _decode(packet_format=2025, player_car_index=23, body=_body(car_index=23))
    season_pack = _decode(
        packet_format=2026, player_car_index=23, body=_body(car_index=23)
    )
    assert f1_25.error == "invalid_session_history_car_index"
    assert season_pack.error is None


def _packet(
    *,
    frame_ordinal: int,
    lap_time_ms: int = 79_295,
    sector1: int = 27_446,
    sector2: int = 17_987,
    sector3: int = 33_861,
    flags: int = 0x0F,
    packet_format: int = 2025,
    car_index: int = 7,
    session_uid: int = 88,
) -> SessionHistoryPacket:
    lap = SessionHistoryLap(
        lap_index=0,
        lap_time_ms=lap_time_ms,
        sector1_time_ms_part=sector1,
        sector1_time_minutes_part=0,
        sector2_time_ms_part=sector2,
        sector2_time_minutes_part=0,
        sector3_time_ms_part=sector3,
        sector3_time_minutes_part=0,
        validity_flags=flags,
    )
    return SessionHistoryPacket(
        session_uid=session_uid,
        frame_identifier=frame_ordinal,
        overall_frame_identifier=frame_ordinal,
        session_time_s=float(frame_ordinal),
        frame_ordinal=frame_ordinal,
        source_sequence=frame_ordinal + 100,
        packet_format=packet_format,
        packet_version=1,
        header_player_car_index=car_index,
        car_index=car_index,
        num_laps=1,
        num_tyre_stints=1,
        best_lap_time_lap_number=1,
        best_sector1_lap_number=2,
        best_sector2_lap_number=3,
        best_sector3_lap_number=4,
        lap_history=(lap,),
        tyre_stints=(SessionHistoryTyreStint(0, 255, 18, 17),),
    )


def _attempt(
    *,
    completion_ordinal: int = 10,
    epoch: int = 0,
    start_epoch: int | None = None,
    scope_assessable: bool = True,
    packet_format: int = 2025,
    start_packet_format: int | None = None,
    lap_number: int = 1,
    lap_time_ms: int = 79_295,
    attempt_id: str = "88:7:1",
) -> LapAttempt:
    return LapAttempt(
        attempt_id=attempt_id,
        attempt_number=1,
        session_uid=88,
        car_index=7,
        lap_number=lap_number,
        disposition=LapDisposition.COMPLETED,
        start_frame_identifier=2,
        end_frame_identifier=9,
        start_session_time_s=2.0,
        end_session_time_s=9.0,
        lap_time_ms=lap_time_ms,
        game_valid=True,
        start_observed=True,
        pit_encountered=False,
        sample_count=8,
        context_segments=(),
        exclusion_reasons=(),
        reference_eligible=True,
        start_frame_ordinal=2,
        end_frame_ordinal=9,
        completion_frame_ordinal=completion_ordinal,
        start_association_epoch=epoch if start_epoch is None else start_epoch,
        association_epoch=epoch,
        association_scope_assessable=scope_assessable,
        start_association_packet_format=(
            packet_format
            if start_packet_format is None
            else start_packet_format
        ),
        association_packet_format=packet_format,
    )


def _observe(
    accumulator: SessionHistoryAccumulator,
    packet: SessionHistoryPacket,
    *,
    epoch: int = 0,
    scope_assessable: bool = True,
) -> None:
    accumulator.observe(
        SessionHistoryObservation(
            packet=packet,
            association_epoch=epoch,
            scope_assessable=scope_assessable,
        )
    )


def test_history_must_be_admitted_after_attempt_finalization() -> None:
    accumulator = SessionHistoryAccumulator()
    _observe(accumulator, _packet(frame_ordinal=10))
    _observe(accumulator, _packet(frame_ordinal=11))

    evidence = accumulator.reconcile((_attempt(),))["88:7:1"]

    assert evidence.status == "matched"
    assert evidence.source is not None
    assert evidence.source["frame_ordinal"] == 11
    assert evidence.sector1_time_ms == 27_446


def test_registered_attempt_keeps_first_qualifying_identical_snapshot() -> None:
    accumulator = SessionHistoryAccumulator()
    attempt = _attempt()
    _observe(accumulator, _packet(frame_ordinal=9))
    accumulator.register_attempt(attempt)
    _observe(accumulator, _packet(frame_ordinal=11))
    _observe(accumulator, _packet(frame_ordinal=12))

    evidence = accumulator.reconcile((attempt,))["88:7:1"]

    assert evidence.status == "matched"
    assert evidence.source is not None
    assert evidence.source["frame_ordinal"] == 11


def test_zero_time_history_row_does_not_conflict_with_positive_match() -> None:
    accumulator = SessionHistoryAccumulator()
    _observe(accumulator, _packet(frame_ordinal=11, lap_time_ms=0))
    _observe(accumulator, _packet(frame_ordinal=12))

    evidence = accumulator.reconcile((_attempt(),))["88:7:1"]

    assert evidence.status == "matched"
    assert evidence.source is not None
    assert evidence.source["frame_ordinal"] == 12


def test_last_populated_lap_row_can_match_a_finalized_completed_attempt() -> None:
    accumulator = SessionHistoryAccumulator()
    # The packet count includes a current-lap record in some states and a
    # completed final record in others; exact attempt proof settles the match.
    _observe(accumulator, _packet(frame_ordinal=11))

    evidence = accumulator.reconcile((_attempt(),))["88:7:1"]

    assert evidence.status == "matched"


def test_scope_epoch_and_format_prevent_cross_branch_association() -> None:
    accumulator = SessionHistoryAccumulator()
    _observe(accumulator, _packet(frame_ordinal=11), epoch=0)

    evidence = accumulator.reconcile((_attempt(epoch=1),))["88:7:1"]

    assert evidence.status == "unavailable"
    assert evidence.reasons == ("no_post_finalization_player_history",)


def test_attempt_crossing_player_or_format_scope_is_ambiguous() -> None:
    accumulator = SessionHistoryAccumulator()
    _observe(accumulator, _packet(frame_ordinal=11), epoch=1)

    player_crossing = _attempt(epoch=1, start_epoch=0)
    format_crossing = _attempt(
        epoch=1, start_epoch=1, start_packet_format=2026, packet_format=2025
    )
    result = accumulator.reconcile(
        (replace(player_crossing, attempt_id="player"), replace(format_crossing, attempt_id="format"))
    )

    assert result["player"].reasons == ("attempt_crosses_association_boundary",)
    assert result["format"].reasons == ("attempt_crosses_association_boundary",)


def test_unknown_scope_and_zero_sector_values_remain_explicit() -> None:
    accumulator = SessionHistoryAccumulator()
    _observe(
        accumulator,
        _packet(frame_ordinal=11, sector1=0, flags=0x0D),
        scope_assessable=False,
    )
    evidence = accumulator.reconcile(
        (replace(_attempt(), association_scope_assessable=False),)
    )["88:7:1"]
    assert evidence.status == "unavailable"
    assert evidence.reasons == ("association_scope_uncertain",)
    assert evidence.sector1_time_ms is None


def test_disagreeing_snapshots_are_conflicting_and_examples_are_bounded() -> None:
    accumulator = SessionHistoryAccumulator()
    _observe(accumulator, _packet(frame_ordinal=11))
    for ordinal in range(12, 18):
        _observe(
            accumulator,
            _packet(frame_ordinal=ordinal, sector1=27_000 + ordinal),
        )

    evidence = accumulator.reconcile((_attempt(),))["88:7:1"]

    assert evidence.status == "conflicting"
    assert len(evidence.candidates) == 3


def test_association_work_and_pipeline_drop_mark_session_truncated() -> None:
    accumulator = SessionHistoryAccumulator(max_work=1)
    _observe(accumulator, _packet(frame_ordinal=11))
    accumulator.mark_truncated(88)

    evidence = accumulator.reconcile((_attempt(),))["88:7:1"]

    assert evidence.status == "truncated"


def test_sector_deltas_keep_validity_separate_and_require_positive_times() -> None:
    difference = _sector_timing_difference(
        {
            "status": "matched",
            "sector1_time_ms": 29_000,
            "sector1_valid": False,
            "sector2_time_ms": None,
            "sector2_valid": True,
            "sector3_time_ms": 30_000,
            "sector3_valid": True,
            "sector_sum_residual_ms": 3,
        },
        {
            "status": "matched",
            "sector1_time_ms": 28_000,
            "sector1_valid": True,
            "sector2_time_ms": 20_000,
            "sector2_valid": True,
            "sector3_time_ms": 31_000,
            "sector3_valid": True,
            "sector_sum_residual_ms": -2,
        },
    )

    assert difference["direction"] == "target_minus_reference"
    assert difference["sectors"]["sector1"]["target_minus_reference_ms"] == 1_000
    assert difference["sectors"]["sector1"]["target_valid"] is False
    assert difference["sectors"]["sector2"]["target_minus_reference_ms"] is None
    assert difference["sectors"]["sector3"]["target_minus_reference_ms"] == -1_000


def test_pipeline_uses_one_association_epoch_per_boundary_frame() -> None:
    pipeline = TelemetryPipeline()
    first = PacketDecoder().decode(
        make_datagram(
            packet_id=3,
            session_uid=88,
            frame=10,
            body=b"BAD!",
            sequence=1,
            player_car_index=7,
        )
    )
    second = PacketDecoder().decode(
        make_datagram(
            packet_id=3,
            session_uid=88,
            frame=10,
            body=b"NOPE",
            sequence=2,
            player_car_index=7,
        )
    )
    result = pipeline._process_lifecycle(PacketFrame(88, 10, (first, second)), 1)
    assert result[2] == 1
    assert result[3] is False


def test_pipeline_closes_association_epoch_for_player_and_format_changes() -> None:
    pipeline = TelemetryPipeline()

    def process(frame: int, *, fmt: int = 2025, player: int = 7) -> int:
        packet = PacketDecoder().decode(
            make_datagram(
                packet_format=fmt,
                packet_id=255,
                session_uid=88,
                frame=frame,
                body=b"opaque",
                sequence=frame,
                player_car_index=player,
            )
        )
        return pipeline._process_lifecycle(PacketFrame(88, frame, (packet,)), frame)[2]

    assert process(1) == 0
    assert process(2, player=8) == 1
    assert process(3, player=8) == 1
    assert process(4, fmt=2026, player=8) == 2
    assert process(5, fmt=2025, player=8) == 3
