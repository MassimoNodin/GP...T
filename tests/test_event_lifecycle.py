from __future__ import annotations

import struct
from dataclasses import replace

from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.sessions.lap_tracker import LapDisposition
from f1_engineer.sessions.lifecycle import LifecycleEvent, reconcile_attempt_lifecycle
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.events import EventDecoder
from tests.helpers import make_datagram
from tests.test_lap_tracking import SESSION_UID, _lap_packet


def _event_packet(
    *,
    frame: int,
    code: bytes = b"FLBK",
    target_frame: int = 12,
    target_time: float = 20.0,
    sequence: int,
    packet_format: int = 2025,
    packet_version: int = 1,
    session_time: float = 90.0,
    session_uid: int = SESSION_UID,
    tail: bytes = b"\x00\x00\x00\x00",
):
    body = code + struct.pack("<If", target_frame, target_time) + tail
    return make_datagram(
        packet_format=packet_format,
        packet_id=3,
        packet_version=packet_version,
        session_uid=session_uid,
        frame=frame,
        session_time=session_time,
        body=body,
        sequence=sequence,
    )


def test_event_decoder_preserves_union_and_reads_flashback_target_in_both_formats() -> None:
    for packet_format in (2025, 2026):
        packet = PacketDecoder().decode(
            _event_packet(frame=100, packet_format=packet_format, sequence=packet_format)
        )

        decoded = EventDecoder().decode(packet)

        assert decoded.code_bytes == b"FLBK"
        assert decoded.code == "FLBK"
        assert decoded.details == struct.pack("<If", 12, 20.0) + b"\x00" * 4
        assert decoded.details_length_bytes == 12
        assert decoded.details_truncated is False
        assert decoded.target_frame_identifier == 12
        assert decoded.target_session_time_s == 20.0
        assert decoded.error is None


def test_event_decoder_marks_unsupported_and_malformed_variants() -> None:
    decoder = EventDecoder()

    unsupported = decoder.decode(
        PacketDecoder().decode(_event_packet(frame=1, packet_version=2, sequence=1))
    )
    malformed = decoder.decode(
        PacketDecoder().decode(
            make_datagram(packet_id=3, frame=2, body=b"FLBKshort", sequence=2)
        )
    )
    unknown = decoder.decode(
        PacketDecoder().decode(
            _event_packet(frame=3, code=b"ZZZZ", sequence=3)
        )
    )

    assert unsupported.error == "unsupported_event_version"
    assert unsupported.code == "FLBK"
    assert malformed.error == "malformed_event_body_size"
    assert malformed.code == "FLBK"
    assert unknown.error == "unknown_event_code"


def test_event_decoder_bounds_malformed_details_and_reports_original_length() -> None:
    packet = PacketDecoder().decode(
        _event_packet(frame=1, sequence=1, tail=b"x" * 100_000)
    )

    decoded = EventDecoder().decode(packet)

    assert decoded.error == "malformed_event_body_size"
    assert len(decoded.details) == 12
    assert decoded.details_length_bytes == 100_008
    assert decoded.details_truncated is True

    pipeline = TelemetryPipeline(reorder_window_frames=1)
    pipeline.process(_event_packet(frame=1, sequence=2, tail=b"x" * 100_000))
    boundary = pipeline.process(
        make_datagram(packet_id=255, session_uid=SESSION_UID, frame=2, sequence=3)
    )
    stored = (*boundary.lifecycle_events, *pipeline.finish_with_outputs().lifecycle_events)
    persisted = next(event for event in stored if event.event_code == "FLBK")
    assert len(persisted.details_hex) <= 24
    assert persisted.details_length_bytes == 100_008
    assert persisted.details_truncated is True


def test_flashback_quarantines_its_frame_and_supersedes_completed_branch() -> None:
    pipeline = TelemetryPipeline(reorder_window_frames=1)
    lifecycle_events: list[LifecycleEvent] = []
    samples = []

    def send(raw):
        result = pipeline.process(raw)
        lifecycle_events.extend(result.lifecycle_events)
        samples.extend(result.car_samples)

    send(
        _lap_packet(
            frame=10,
            lap_number=1,
            distance_m=-0.5,
            session_time=10.0,
            sequence=10,
        )
    )
    send(
        _lap_packet(
            frame=11,
            lap_number=1,
            distance_m=0.5,
            session_time=10.1,
            sequence=11,
        )
    )
    send(
        _lap_packet(
            frame=20,
            lap_number=1,
            distance_m=5_000.0,
            current_lap_time_ms=79_000,
            session_time=89.0,
            sequence=20,
        )
    )
    send(
        _lap_packet(
            frame=21,
            lap_number=2,
            distance_m=0.5,
            last_lap_time_ms=79_500,
            session_time=89.1,
            sequence=21,
        )
    )
    send(
        _event_packet(frame=22, target_frame=12, target_time=20.0, sequence=22)
    )
    send(
        _lap_packet(
            frame=22,
            lap_number=2,
            distance_m=100.0,
            current_lap_time_ms=10_000,
            session_time=90.0,
            sequence=23,
        )
    )
    send(make_datagram(packet_id=255, session_uid=SESSION_UID, frame=23, session_time=20.1, sequence=24))
    send(
        _lap_packet(
            frame=24,
            lap_number=2,
            distance_m=10.0,
            current_lap_time_ms=100,
            session_time=20.2,
            sequence=25,
        )
    )
    send(
        _lap_packet(
            frame=25,
            lap_number=2,
            distance_m=-0.5,
            current_lap_time_ms=200,
            session_time=20.3,
            sequence=26,
        )
    )
    send(
        _lap_packet(
            frame=26,
            lap_number=2,
            distance_m=0.5,
            current_lap_time_ms=300,
            session_time=20.4,
            sequence=27,
        )
    )
    pipeline.finish_with_outputs()
    attempts = pipeline.laps.attempts
    reconciled, links, truncated, work = reconcile_attempt_lifecycle(
        tuple(attempts), tuple(lifecycle_events)
    )

    first_completed = next(item for item in reconciled if item.disposition is LapDisposition.COMPLETED)
    boundary_attempt = next(item for item in reconciled if "flashback" in item.exclusion_reasons)
    recovered_attempt = max(reconciled, key=lambda item: item.attempt_number)
    flashback = next(item for item in lifecycle_events if item.cause == "flashback")
    assert flashback.current_overall_frame_identifier == 22
    assert flashback.target_game_frame_identifier == 12
    assert flashback.target_session_time_s == 20.0
    assert first_completed.superseded is True
    assert first_completed.lifecycle_assessed is True
    assert boundary_attempt.disposition is LapDisposition.ABANDONED
    assert "flashback" in boundary_attempt.exclusion_reasons
    assert recovered_attempt.start_observed is True
    assert recovered_attempt.start_frame_identifier == 26
    assert not any(sample.frame_identifier == 22 for sample in samples)
    assert any(relation == "superseded_by_flashback" for _, _, relation in links)
    assert not truncated
    assert work > 0
    assert first_completed.reference_eligible is False
    assert "superseded_by_flashback" in first_completed.exclusion_reasons


def test_conflicting_flashback_targets_mark_prior_attempts_unassessed() -> None:
    pipeline = TelemetryPipeline(reorder_window_frames=1)
    pipeline.process(
        _lap_packet(
            frame=10,
            lap_number=1,
            distance_m=10.0,
            current_lap_time_ms=100,
            session_time=10.0,
            sequence=10,
        )
    )
    pipeline.process(_event_packet(frame=11, target_frame=5, target_time=5.0, sequence=11))
    pipeline.process(_event_packet(frame=11, target_frame=6, target_time=6.0, sequence=12))
    boundary = pipeline.process(
        make_datagram(packet_id=255, session_uid=SESSION_UID, frame=12, sequence=13)
    )
    events = list(boundary.lifecycle_events)
    pipeline.finish_with_outputs()
    events.extend(pipeline.drain_lifecycle_events())
    reconciled, links, truncated, _ = reconcile_attempt_lifecycle(
        tuple(pipeline.laps.attempts), tuple(events)
    )

    assert len([event for event in events if event.event_code == "FLBK"]) == 2
    assert all(event.evidence_status == "ambiguous" for event in events if event.event_code == "FLBK")
    assert all(attempt.superseded is None for attempt in reconciled)
    assert all(not attempt.lifecycle_assessed for attempt in reconciled)
    assert all(not attempt.reference_eligible for attempt in reconciled)
    assert all("lifecycle_evidence_unassessed" in attempt.exclusion_reasons for attempt in reconciled)
    assert len(links) <= len(reconciled)
    assert not truncated


def test_future_flashback_target_is_ambiguous_and_fails_closed() -> None:
    pipeline = TelemetryPipeline(reorder_window_frames=1)
    events: list[LifecycleEvent] = []

    def send(raw):
        result = pipeline.process(raw)
        events.extend(result.lifecycle_events)

    send(
        _lap_packet(
            frame=10,
            lap_number=1,
            distance_m=100.0,
            current_lap_time_ms=10_000,
            session_time=10.0,
            sequence=1,
        )
    )
    send(
        _event_packet(
            frame=11,
            target_frame=20,
            target_time=20.0,
            session_time=10.1,
            sequence=2,
        )
    )
    send(make_datagram(packet_id=255, session_uid=SESSION_UID, frame=12, sequence=3))
    flushed = pipeline.finish_with_outputs()
    events.extend(flushed.lifecycle_events)

    reconciled, _, _, _ = reconcile_attempt_lifecycle(
        tuple(pipeline.laps.attempts), tuple(events)
    )
    flashback = next(event for event in events if event.event_code == "FLBK")
    assert flashback.evidence_status == "ambiguous"
    assert flashback.target_session_time_s is None
    assert reconciled
    assert all(attempt.superseded is None for attempt in reconciled)
    assert all(not attempt.reference_eligible for attempt in reconciled)


def test_event_only_frame_does_not_consume_flashback_recovery_baseline() -> None:
    pipeline = TelemetryPipeline(reorder_window_frames=1)
    events: list[LifecycleEvent] = []

    def send(raw):
        events.extend(pipeline.process(raw).lifecycle_events)

    send(
        _lap_packet(
            frame=10,
            lap_number=1,
            distance_m=100.0,
            current_lap_time_ms=10_000,
            session_time=10.0,
            sequence=1,
        )
    )
    send(
        _event_packet(
            frame=11,
            target_frame=5,
            target_time=5.0,
            session_time=10.1,
            sequence=2,
        )
    )
    send(
        _event_packet(
            frame=12,
            code=b"SSTA",
            session_time=10.2,
            sequence=3,
        )
    )
    send(
        _lap_packet(
            frame=13,
            lap_number=1,
            distance_m=110.0,
            current_lap_time_ms=100,
            session_time=5.1,
            sequence=4,
        )
    )
    send(make_datagram(packet_id=255, session_uid=SESSION_UID, frame=14, sequence=5))
    events.extend(pipeline.finish_with_outputs().lifecycle_events)

    assert [event.cause for event in events].count("flashback") == 1
    assert [event.cause for event in events].count("session_time_regression") == 0


def test_same_frame_lap_clock_regression_is_quarantined() -> None:
    pipeline = TelemetryPipeline(reorder_window_frames=1)
    events: list[LifecycleEvent] = []
    samples = []

    def send(raw):
        result = pipeline.process(raw)
        events.extend(result.lifecycle_events)
        samples.extend(result.car_samples)

    send(
        _lap_packet(
            frame=10,
            lap_number=1,
            distance_m=100.0,
            current_lap_time_ms=10_000,
            session_time=100.0,
            sequence=1,
        )
    )
    send(
        _lap_packet(
            frame=11,
            lap_number=1,
            distance_m=110.0,
            current_lap_time_ms=11_000,
            session_time=101.0,
            sequence=2,
        )
    )
    send(
        _lap_packet(
            frame=11,
            lap_number=1,
            distance_m=120.0,
            current_lap_time_ms=12_000,
            session_time=90.0,
            sequence=3,
        )
    )
    send(
        _lap_packet(
            frame=11,
            lap_number=1,
            distance_m=130.0,
            current_lap_time_ms=13_000,
            session_time=102.0,
            sequence=4,
        )
    )
    send(make_datagram(packet_id=255, session_uid=SESSION_UID, frame=12, sequence=5))
    events.extend(pipeline.finish_with_outputs().lifecycle_events)

    boundary = next(event for event in events if event.cause == "session_time_regression")
    assert boundary.evidence_status == "same_frame_clock_regression"
    assert boundary.prior_session_time_s == 101.0
    assert boundary.session_time_s == 90.0
    assert not any(sample.frame_identifier == 11 for sample in samples)


def test_reconciliation_links_only_state_changes_and_sweeps_attempts_once() -> None:
    pipeline = TelemetryPipeline(reorder_window_frames=1)
    pipeline.process(
        _lap_packet(
            frame=10,
            lap_number=1,
            distance_m=10.0,
            current_lap_time_ms=100,
            session_time=10.0,
            sequence=1,
        )
    )
    pipeline.finish_with_outputs()
    template_attempt = pipeline.laps.attempts[0]
    attempts = tuple(
        replace(
            template_attempt,
            attempt_id=f"attempt-{index}",
            attempt_number=index,
            disposition=LapDisposition.COMPLETED,
            end_frame_ordinal=index,
            end_session_time_s=10.0 + index,
            lap_time_ms=80_000,
            reference_eligible=True,
        )
        for index in range(1, 101)
    )
    template_event = LifecycleEvent(
        session_uid=SESSION_UID,
        event_ordinal=0,
        frame_ordinal=10_000,
        current_frame_identifier=10_000,
        current_overall_frame_identifier=10_000,
        packet_format=2025,
        packet_version=1,
        event_code="FLBK",
        event_kind="flashback",
        session_time_s=100.0,
        target_game_frame_identifier=1,
        target_session_time_s=1_000.0,
        prior_session_time_s=100.0,
        cause="flashback",
        evidence_status="verified",
        details_hex="",
    )
    verified_events = tuple(
        replace(template_event, event_ordinal=index, frame_ordinal=10_000 + index)
        for index in range(1, 101)
    )
    verified_attempts, verified_links, _, verified_work = reconcile_attempt_lifecycle(
        attempts, verified_events
    )
    uncertain_events = tuple(
        replace(
            template_event,
            event_ordinal=100 + index,
            frame_ordinal=20_000 + index,
            cause="event_evidence_unknown",
            evidence_status="unknown_event_code",
            target_session_time_s=None,
        )
        for index in range(1, 101)
    )
    uncertain_attempts, uncertain_links, _, uncertain_work = reconcile_attempt_lifecycle(
        attempts, (*verified_events, *uncertain_events)
    )
    capped_attempts, capped_links, truncated, capped_work = reconcile_attempt_lifecycle(
        attempts, verified_events, max_work=0
    )

    assert verified_attempts
    assert len(verified_links) == 0
    assert verified_work == len(attempts)
    assert len(uncertain_attempts) == len(attempts)
    assert len(uncertain_links) == len(attempts)
    assert uncertain_work == 2 * len(attempts)
    assert all(attempt.superseded is None for attempt in uncertain_attempts)
    assert all(not attempt.reference_eligible for attempt in uncertain_attempts)
    assert len(capped_attempts) == len(attempts)
    assert not capped_links and capped_work == 0
    assert truncated == frozenset({SESSION_UID})
    assert all(not attempt.lifecycle_assessed for attempt in capped_attempts)


def test_session_time_regression_is_a_boundary_without_using_wrapped_frame_order() -> None:
    pipeline = TelemetryPipeline(reorder_window_frames=1)
    pipeline.process(
        _lap_packet(
            frame=0xFFFFFFFE,
            lap_number=1,
            distance_m=100.0,
            current_lap_time_ms=10_000,
            session_time=100.0,
            sequence=1,
        )
    )
    pipeline.process(
        _lap_packet(
            frame=0xFFFFFFFF,
            lap_number=1,
            distance_m=110.0,
            current_lap_time_ms=11_000,
            session_time=101.0,
            sequence=2,
        )
    )
    result = pipeline.process(
        _lap_packet(
            frame=0,
            lap_number=1,
            distance_m=120.0,
            current_lap_time_ms=1_000,
            session_time=50.0,
            sequence=3,
        )
    )
    flushed = pipeline.finish_with_outputs()
    events = (*result.lifecycle_events, *flushed.lifecycle_events)

    regression = next(event for event in events if event.cause == "session_time_regression")
    assert regression.frame_ordinal == 3
    assert regression.current_overall_frame_identifier == 0
    assert regression.prior_session_time_s == 101.0
    assert pipeline.frames.late_packets_ignored == 0


def test_uid_zero_event_is_counted_without_creating_persistable_lifecycle_rows() -> None:
    pipeline = TelemetryPipeline(reorder_window_frames=1)
    pipeline.process(
        _event_packet(frame=1, code=b"SSTA", session_uid=0, session_time=0.0, sequence=1)
    )
    result = pipeline.process(
        make_datagram(
            packet_id=255,
            session_uid=0,
            frame=2,
            session_time=0.1,
            sequence=2,
        )
    )

    assert pipeline.event_packets_decoded == 1
    assert result.lifecycle_events == ()
    assert pipeline.sessions.current_session_uid is None
