from dataclasses import replace
import struct

import pytest

from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.sessions.context import RuleSet, SessionType
from f1_engineer.sessions.lap_tracker import LapDisposition, LapObservation, LapTracker
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.lap_data import LapDataDecoder
from f1_engineer.udp.models import PacketFormat
from f1_engineer.udp.session_context import SessionContextDecoder
from tests.test_lap_tracking import LAP_RECORD, SESSION_UID, _lap_packet, _session_packet


def _boundary(packet_format=PacketFormat.F1_25):
    context = SessionContextDecoder().decode(PacketDecoder().decode(_session_packet())).context
    context = replace(context, track_length_m=5408, packet_format=packet_format,
                      session_type=SessionType.SHORT_PRACTICE, rule_set=RuleSet.PRACTICE_QUALIFYING)
    packet = PacketDecoder().decode(_lap_packet(
        frame=32402, lap_number=3, distance_m=5407.572265625,
        session_time=604.1229858398438, driver_status=3, sector_id=2,
    ))
    before_data = replace(LapDataDecoder().decode(packet).lap_data.cars[0],
                          total_distance_m=10816.455078125)
    before = LapObservation(SESSION_UID, 32402, 604.1229858398438, 0, before_data,
                            context, (), frame_ordinal=8605, association_epoch=1,
                            association_packet_format=packet_format.value)
    after = replace(before, frame_identifier=32403, frame_ordinal=8606,
                    session_time_s=604.1405029296875,
                    data=replace(before_data, lap_distance_m=0.0361328125,
                                 total_distance_m=10817.8017578125, current_lap_time_ms=16,
                                 driver_status_id=1, sector_id=0))
    return before, after


def _complete(tracker, start):
    end = replace(start, frame_identifier=38015, frame_ordinal=14219,
                  session_time_s=start.session_time_s + 92.7,
                  data=replace(start.data, lap_distance_m=5407,
                               total_distance_m=start.data.total_distance_m + 5407,
                               current_lap_time_ms=92700, sector_id=2))
    tracker.observe(end)
    transition = replace(end, frame_identifier=38016, frame_ordinal=14220,
                         session_time_s=end.session_time_s + 0.016,
                         data=replace(end.data, current_lap_number=4, lap_distance_m=0.1,
                                      current_lap_time_ms=0, last_lap_time_ms=92796, sector_id=0))
    return tracker.observe(transition)[0]


@pytest.mark.parametrize("packet_format", list(PacketFormat))
def test_outlap_wrap_records_the_start_and_preserves_normal_completion(packet_format):
    before, after = _boundary(packet_format)
    tracker = LapTracker()
    assert tracker.observe(before) == ()
    assert tracker.active_attempt_id(0) is None
    tracker.observe(after)
    attempt = _complete(tracker, after)
    assert attempt.disposition is LapDisposition.COMPLETED
    assert attempt.start_observed is True
    assert attempt.start_frame_identifier == 32403
    assert attempt.start_frame_ordinal == 8606
    assert attempt.lap_time_ms == 92796
    assert attempt.pit_encountered is False
    assert "lap_start_not_observed" not in attempt.exclusion_reasons
    tracker.finish()
    assert tracker.attempts[-1].start_observed is True


def test_outlap_boundary_is_recognised_through_wire_decoding_and_frame_assembly():
    session = _session_packet()
    payload = bytearray(session.payload)
    struct.pack_into("<H", payload, 29 + 4, 5408)
    payload[29 + 6] = 4
    payload[29 + 665] = 4
    payload[29 + 666] = 0
    pipeline = TelemetryPipeline()
    pipeline.process(replace(session, payload=bytes(payload)))
    before, after = _boundary()
    for observation in (before, after, replace(
        after, frame_identifier=38016, frame_ordinal=14220,
        session_time_s=after.session_time_s + 92.796,
        data=replace(after.data, current_lap_number=4, last_lap_time_ms=92796),
    )):
        raw = _lap_packet(
            frame=observation.frame_identifier, lap_number=observation.data.current_lap_number,
            distance_m=observation.data.lap_distance_m, session_time=observation.session_time_s,
            current_lap_time_ms=observation.data.current_lap_time_ms,
            last_lap_time_ms=observation.data.last_lap_time_ms,
            driver_status=observation.data.driver_status_id, sector_id=observation.data.sector_id,
            sequence=observation.frame_identifier,
        )
        payload = bytearray(raw.payload)
        fields = list(LAP_RECORD.unpack_from(payload, 29))
        fields[11] = observation.data.total_distance_m
        LAP_RECORD.pack_into(payload, 29, *fields)
        pipeline.process(replace(raw, payload=bytes(payload)))
    pipeline.finish()
    attempt = next(attempt for attempt in pipeline.laps.attempts
                   if attempt.disposition is LapDisposition.COMPLETED)
    assert attempt.start_observed is True
    assert attempt.start_frame_identifier == 32403
    assert attempt.lap_number == 3


def test_recognising_the_start_does_not_override_game_invalidity():
    before, after = _boundary()
    after = replace(after, data=replace(after.data, current_lap_invalid_id=1))
    tracker = LapTracker()
    tracker.observe(before)
    tracker.observe(after)
    attempt = _complete(tracker, after)
    assert attempt.start_observed is True
    assert attempt.game_valid is False
    assert attempt.reference_eligible is False


@pytest.mark.parametrize("case", [
    "midtrack", "late_start", "total_distance_rewind", "total_distance_jump",
    "flying_before", "inlap_after", "running_timer_before", "late_timer_after",
    "wrong_sector", "pit_lane", "pit_timer", "unknown_context", "changed_track",
    "changed_length", "wrong_mode", "changed_epoch", "unknown_scope", "changed_format",
    "reversed_time", "long_gap", "reversed_frame", "silence_boundary", "flashback_boundary",
    "no_previous",
])
def test_outlap_wrap_does_not_certify_discontinuities_or_missing_boundaries(case):
    before, after = _boundary()
    if case == "midtrack":
        before = replace(before, data=replace(before.data, lap_distance_m=2000))
    elif case == "late_start":
        after = replace(after, data=replace(after.data, lap_distance_m=200))
    elif case == "total_distance_rewind":
        after = replace(after, data=replace(after.data, total_distance_m=10000))
    elif case == "total_distance_jump":
        after = replace(after, data=replace(after.data, total_distance_m=11000))
    elif case == "flying_before":
        before = replace(before, data=replace(before.data, driver_status_id=1))
    elif case == "inlap_after":
        after = replace(after, data=replace(after.data, driver_status_id=4))
    elif case == "running_timer_before":
        before = replace(before, data=replace(before.data, current_lap_time_ms=90000))
    elif case == "late_timer_after":
        after = replace(after, data=replace(after.data, current_lap_time_ms=2000))
    elif case == "wrong_sector":
        before = replace(before, data=replace(before.data, sector_id=1))
    elif case == "pit_lane":
        before = replace(before, data=replace(before.data, pit_status_id=1))
    elif case == "pit_timer":
        before = replace(before, data=replace(before.data, pit_lane_timer_active=True))
    elif case == "unknown_context":
        before = replace(before, session_context=None)
    elif case == "changed_track":
        before = replace(before, session_context=replace(before.session_context, track_id=99))
    elif case == "changed_length":
        before = replace(before, session_context=replace(before.session_context, track_length_m=6000))
    elif case == "wrong_mode":
        before = replace(before, session_context=replace(before.session_context, rule_set=RuleSet.RACE))
        after = replace(after, session_context=before.session_context)
    elif case == "changed_epoch":
        after = replace(after, association_epoch=2)
    elif case == "unknown_scope":
        after = replace(after, association_scope_assessable=False)
    elif case == "changed_format":
        after = replace(after, association_packet_format=2026)
    elif case == "reversed_time":
        after = replace(after, session_time_s=before.session_time_s - 1)
    elif case == "long_gap":
        after = replace(after, session_time_s=before.session_time_s + 10)
    elif case == "reversed_frame":
        after = replace(after, frame_ordinal=before.frame_ordinal - 1)
    tracker = LapTracker()
    if case != "no_previous":
        tracker.observe(before)
    if case == "silence_boundary":
        tracker.close_segment(SESSION_UID, reason="telemetry_silence")
    elif case == "flashback_boundary":
        tracker.close_lifecycle_boundary(SESSION_UID, reason="flashback")
    tracker.observe(after)
    attempt = _complete(tracker, after)
    assert attempt.start_observed is False
    assert "lap_start_not_observed" in attempt.exclusion_reasons
