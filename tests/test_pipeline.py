from __future__ import annotations

from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.telemetry.frames import FrameAssembler
from f1_engineer.udp.decoder import PacketDecoder
from tests.helpers import make_datagram
from tests.test_lap_tracking import SESSION_UID, _lap_packet


def test_pipeline_reports_session_transitions_and_ignores_retired_uid() -> None:
    pipeline = TelemetryPipeline()

    first = pipeline.process(make_datagram(session_uid=100))
    second = pipeline.process(make_datagram(session_uid=200, sequence=1))
    delayed = pipeline.process(make_datagram(session_uid=100, sequence=2))

    assert [event.kind for event in first.session_events] == ["session_started"]
    assert [event.kind for event in second.session_events] == [
        "session_ended",
        "session_started",
    ]
    assert delayed.session_events == ()
    assert pipeline.sessions.current_session_uid == 200


def test_pipeline_flushes_frames_when_a_session_ends() -> None:
    pipeline = TelemetryPipeline()

    first = pipeline.process(make_datagram(session_uid=100, frame=10))
    second = pipeline.process(make_datagram(session_uid=200, frame=20, sequence=1))

    assert first.completed_frames == ()
    assert [
        (frame.session_uid, frame.overall_frame_identifier)
        for frame in second.completed_frames
    ] == [(100, 10)]


def test_completion_reset_sample_belongs_to_incoming_lap_attempt() -> None:
    pipeline = TelemetryPipeline(reorder_window_frames=1)
    pipeline.process(
        _lap_packet(
            frame=10,
            lap_number=1,
            distance_m=100,
            session_time=1,
            current_lap_time_ms=1_000,
            sequence=1,
        )
    )
    pipeline.process(
        _lap_packet(
            frame=11,
            lap_number=2,
            distance_m=0,
            session_time=2,
            current_lap_time_ms=0,
            last_lap_time_ms=80_000,
            sequence=2,
        )
    )

    result = pipeline.process(
        make_datagram(
            packet_id=255,
            session_uid=SESSION_UID,
            frame=12,
            sequence=3,
        )
    )

    assert len(result.car_samples) == 1
    assert result.car_samples[0].frame_identifier == 11
    assert result.car_samples[0].attempt_id == f"{SESSION_UID}:0:2"
    assert result.car_samples[0].car_telemetry_available is False


def test_frame_assembler_evicts_old_session_watermarks() -> None:
    decoder = PacketDecoder()
    assembler = FrameAssembler(max_tracked_sessions=1)

    first = decoder.decode(make_datagram(session_uid=100, frame=10))
    second = decoder.decode(make_datagram(session_uid=200, frame=20, sequence=1))
    third = decoder.decode(make_datagram(session_uid=300, frame=30, sequence=2))

    assert assembler.add(first) == ()
    evicted_first = assembler.add(second)
    evicted_second = assembler.add(third)
    remaining = assembler.flush()

    assert [frame.session_uid for frame in evicted_first] == [100]
    assert [frame.session_uid for frame in evicted_second] == [200]
    assert [frame.session_uid for frame in remaining] == [300]


def test_frame_assembler_deduplicates_packet_ids_within_a_pending_frame() -> None:
    decoder = PacketDecoder()
    assembler = FrameAssembler(max_open_frames=4, max_pending_packets=2)
    first = decoder.decode(make_datagram(packet_id=0, frame=10))
    second = decoder.decode(make_datagram(packet_id=1, frame=10, sequence=1))
    duplicate = decoder.decode(make_datagram(packet_id=0, frame=10, sequence=2))

    assert assembler.add(first) == ()
    assert assembler.add(duplicate) == ()
    assert assembler.duplicates_ignored == 1
    assert assembler.add(second) == ()
    frames = assembler.flush()
    assert len(frames) == 1
    assert [packet.header.packet_id for packet in frames[0].packets] == [0, 1]


def test_frame_assembler_bounds_frames_and_ignores_late_packets() -> None:
    decoder = PacketDecoder()
    assembler = FrameAssembler(max_open_frames=1)
    frame_one = decoder.decode(make_datagram(packet_id=0, frame=10))
    frame_two = decoder.decode(make_datagram(packet_id=0, frame=11, sequence=1))

    assert assembler.add(frame_one) == ()
    emitted = assembler.add(frame_two)
    assert len(emitted) == 1
    assert emitted[0].overall_frame_identifier == 10
    assert assembler.add(frame_one) == ()
    assert assembler.late_packets_ignored == 1


def test_capacity_pressure_drops_an_older_frame_instead_of_emitting_out_of_order() -> None:
    decoder = PacketDecoder()
    assembler = FrameAssembler(max_open_frames=1)
    newer = decoder.decode(make_datagram(packet_id=0, frame=52))
    older = decoder.decode(make_datagram(packet_id=0, frame=50, sequence=1))

    assert assembler.add(newer) == ()
    assert assembler.add(older) == ()
    assert assembler.overflow_packets_dropped == 1
    assert [frame.overall_frame_identifier for frame in assembler.flush()] == [52]


def test_frame_assembler_can_flush_one_session_without_retiring_its_watermark() -> None:
    decoder = PacketDecoder()
    assembler = FrameAssembler()
    assembler.add(decoder.decode(make_datagram(session_uid=100, frame=10)))
    assembler.add(decoder.decode(make_datagram(session_uid=100, frame=11, sequence=1)))
    assembler.add(decoder.decode(make_datagram(session_uid=200, frame=20, sequence=2)))

    flushed = assembler.flush_session(100)
    assert [frame.overall_frame_identifier for frame in flushed] == [10, 11]
    assert assembler.add(
        decoder.decode(make_datagram(session_uid=100, frame=10, sequence=3))
    ) == ()
    assert assembler.late_packets_ignored == 1
    assert [frame.session_uid for frame in assembler.flush()] == [200]


def test_frame_assembler_uses_a_small_reorder_window() -> None:
    decoder = PacketDecoder()
    assembler = FrameAssembler(reorder_window_frames=2)
    older = decoder.decode(make_datagram(packet_id=0, frame=10))
    newer = decoder.decode(make_datagram(packet_id=0, frame=12, sequence=1))

    assert assembler.add(older) == ()
    emitted = assembler.add(newer)
    assert [frame.overall_frame_identifier for frame in emitted] == [10]
    assert assembler.add(older) == ()
    assert assembler.late_packets_ignored == 1


def test_frame_assembler_emits_reordered_frames_in_overall_frame_order() -> None:
    decoder = PacketDecoder()
    assembler = FrameAssembler(reorder_window_frames=3)
    for frame in (12, 10, 11):
        assembler.add(decoder.decode(make_datagram(packet_id=0, frame=frame)))

    completed = assembler.add(
        decoder.decode(make_datagram(packet_id=0, frame=15, sequence=3))
    )

    assert [frame.overall_frame_identifier for frame in completed] == [10, 11, 12]


def test_frame_watermark_handles_32_bit_wraparound() -> None:
    decoder = PacketDecoder()
    assembler = FrameAssembler(reorder_window_frames=2)
    before_wrap = decoder.decode(make_datagram(packet_id=0, frame=0xFFFFFFFE))
    after_wrap = decoder.decode(make_datagram(packet_id=0, frame=0, sequence=1))

    assert assembler.add(before_wrap) == ()
    emitted = assembler.add(after_wrap)
    assert [frame.overall_frame_identifier for frame in emitted] == [0xFFFFFFFE]


def test_frame_assembler_bounds_total_pending_packets() -> None:
    decoder = PacketDecoder()
    assembler = FrameAssembler(max_open_frames=4, max_pending_packets=1)
    frame_one = decoder.decode(make_datagram(packet_id=0, frame=10))
    frame_two = decoder.decode(make_datagram(packet_id=1, frame=10, sequence=1))

    assert assembler.add(frame_one) == ()
    emitted = assembler.add(frame_two)
    assert len(emitted) == 1
    assert [packet.header.packet_id for packet in emitted[0].packets] == [0]
    assert assembler.overflow_packets_dropped == 1
    assert assembler.flush() == ()
    assert assembler.add(frame_two) == ()
    assert assembler.late_packets_ignored == 1


def test_frame_assembler_preserves_distinct_envelopes_with_the_same_packet_id() -> None:
    decoder = PacketDecoder()
    assembler = FrameAssembler(max_open_frames=4)
    event_one = decoder.decode(
        make_datagram(packet_id=3, frame=10, body=b"EVENT-A")
    )
    event_two = decoder.decode(
        make_datagram(packet_id=3, frame=10, body=b"EVENT-B", sequence=1)
    )

    assert assembler.add(event_one) == ()
    assert assembler.add(event_two) == ()
    frames = assembler.flush()
    assert len(frames) == 1
    assert [packet.body for packet in frames[0].packets] == [b"EVENT-A", b"EVENT-B"]


def test_frame_assembler_flushes_all_pending_frames() -> None:
    decoder = PacketDecoder()
    assembler = FrameAssembler(max_open_frames=3)
    assembler.add(decoder.decode(make_datagram(frame=1, packet_id=0)))
    assembler.add(decoder.decode(make_datagram(frame=2, packet_id=0, sequence=1)))

    frames = assembler.flush()

    assert [frame.overall_frame_identifier for frame in frames] == [1, 2]
    assert assembler.flush() == ()
