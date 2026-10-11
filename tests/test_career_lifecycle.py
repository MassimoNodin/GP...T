from __future__ import annotations

import struct
from dataclasses import replace

import pytest

from f1_engineer.analysis.session_comparison import compare_session_laps
from f1_engineer.analysis.session_reference import select_session_reference
from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.evidence import EvidenceStore, EvidenceUnavailable
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.events import EventDecoder
from tests.helpers import make_datagram
from tests.test_lap_tracking import SESSION_FIXTURE, SESSION_UID
from tests.test_session_evidence import admitted_packets


def _career_session(session_type_id=1):
    body = bytearray(SESSION_FIXTURE.read_bytes()[29:])
    body[6] = session_type_id
    body[665] = 28
    body[666] = 0
    return make_datagram(packet_id=1, session_uid=SESSION_UID, frame=0,
                         session_time=0, body=bytes(body))


def _event(frame, body=b"BUTN" + bytes(12)):
    return make_datagram(packet_id=3, session_uid=SESSION_UID, frame=frame,
                         session_time=frame * 0.05, player_car_index=7, body=body)


@pytest.fixture
def career_coordinator(tmp_path):
    store = EvidenceStore(tmp_path / "career.sqlite3")
    coordinator = SessionCoordinator(store, "career-lifecycle-regression")
    try:
        yield store, coordinator
    finally:
        coordinator.close()


def _ingest(coordinator, packets):
    for sequence, packet in enumerate(packets):
        coordinator.ingest(replace(packet, sequence=sequence))


def _player_attempts(store):
    session = store.sessions()[0]["id"]
    attempts = sorted((attempt for attempt in store.attempts(session)
                       if attempt["role"] == "player"),
                      key=lambda attempt: attempt["payload"]["lap_number"])
    return session, attempts


@pytest.mark.parametrize("session_type_id,session_type", [(1, "practice_1"), (5, "qualifying_1")])
@pytest.mark.parametrize("event_first", [False, True], ids=["session-first", "event-first"])
def test_career_global_events_preserve_published_player_diagnostic_evidence(
    career_coordinator, session_type_id, session_type, event_first,
):
    store, coordinator = career_coordinator
    startup = [_career_session(session_type_id), _event(0, b"SSTA" + bytes(12))]
    if event_first:
        startup.reverse()
    packets = [*startup, _event(1, b"DRSE" + bytes(12))]
    for packet in admitted_packets(frames=70):
        frame = struct.unpack_from("<I", packet.payload, 19)[0]
        if frame == 30:
            continue
        if frame == 31 and packet.payload[6] == 2:
            packets.append(_event(30))
        packets.append(packet)
    _ingest(coordinator, packets)

    session, attempts = _player_attempts(store)
    completed = [attempt for attempt in attempts
                 if attempt["payload"]["disposition"] == "completed"
                 and attempt["payload"]["start_observed"]]
    assert [attempt["payload"]["lap_number"] for attempt in completed] == [2, 3]
    assert len({attempt["driver"] for attempt in completed}) == 1
    assert len({attempt["manifest"]["epoch"] for attempt in completed}) == 1
    for attempt in completed:
        assert attempt["binding_verified"] is True
        assert attempt["driver"] is not None
        assert attempt["readiness"]["state"] == "published"
        assert attempt["readiness"]["reason"] == "completed_observed_lap"
        assert attempt["payload"]["association_scope_assessable"] is True
        assert attempt["payload"]["car_index"] == 0
        assert attempt["manifest"]["format"] == 2025
        assert attempt["rows"] > 0
        assert attempt["payload"]["game_valid"] is True
        assert attempt["payload"]["pit_encountered"] is False
        assert attempt["payload"]["reference_eligible"] is False
        assert "mode_policy_not_implemented" in attempt["payload"]["exclusion_reasons"]
        context = attempt["payload"]["context_segments"][0]["context"]
        assert context["game_mode"] == "driver_career_25"
        assert context["session_type"] == session_type
        assert context["rule_set"] == "practice_qualifying"

    reference, target = completed
    selected = select_session_reference(store, session, target["id"])
    assert selected["status"] == "available"
    assert selected["reference"] == reference["id"]
    report = compare_session_laps(
        store, session, target["id"], reference["id"], policy="practice_qualifying",
    )
    assert selected["diagnostic_only"] is True
    assert selected["coaching_eligible"] is False
    assert selected["ranking_eligible"] is False
    assert report["diagnostic_only"] is True
    assert report["lap_time_difference_ms"] == 0
    assert report["channels"]["brake"]["coverage"] > 0.9
    assert store.report(report["id"]) == report
    assert coordinator.pipeline.event_packets_decoded == 3
    assert coordinator.pipeline.event_decode_error_count == 0


def test_career_same_frame_non_event_player_conflict_remains_fail_closed(career_coordinator):
    store, coordinator = career_coordinator
    packets = [_career_session()]
    for packet in admitted_packets(frames=70):
        if packet.payload[6] == 6 and struct.unpack_from("<I", packet.payload, 19)[0] == 1:
            payload = bytearray(packet.payload)
            payload[27] = 7
            packet = replace(packet, payload=bytes(payload))
        packets.append(packet)
    _ingest(coordinator, packets)

    _, attempts = _player_attempts(store)
    completed = [attempt for attempt in attempts if attempt["payload"]["disposition"] == "completed"]
    assert completed
    assert all(attempt["readiness"]["state"] == "quarantined" for attempt in completed)
    assert all(attempt["payload"]["association_scope_assessable"] is False for attempt in completed)


@pytest.mark.parametrize("body,error", [
    (b"ZZZZ" + bytes(12), "unknown_event_code"),
    (b"SSTAshort", "malformed_event_body_size"),
])
def test_career_untrusted_event_remains_fail_closed(career_coordinator, body, error):
    store, coordinator = career_coordinator
    event = _event(0, body)
    assert EventDecoder().decode(PacketDecoder().decode(event)).error == error
    _ingest(coordinator, [_career_session(), event, *admitted_packets(frames=70)])

    session, attempts = _player_attempts(store)
    completed = [attempt for attempt in attempts if attempt["payload"]["disposition"] == "completed"]
    assert completed
    assert all(attempt["readiness"]["state"] == "quarantined" for attempt in completed)
    assert all(attempt["payload"]["association_scope_assessable"] is False for attempt in completed)
    assert coordinator.pipeline.event_decode_error_count == 1
    assert select_session_reference(store, session, completed[-1]["id"])["status"] != "available"
    with pytest.raises(EvidenceUnavailable, match="attempt_not_measurement_ready"):
        compare_session_laps(store, session, completed[-1]["id"], completed[-2]["id"],
                             policy="practice_qualifying")


def test_career_event_only_frame_preserves_established_player_binding(career_coordinator):
    store, coordinator = career_coordinator
    packets = [_career_session()]
    for packet in admitted_packets(frames=70):
        frame = struct.unpack_from("<I", packet.payload, 19)[0]
        if frame == 30:
            continue
        if frame == 31 and packet.payload[6] == 2:
            packets.append(_event(30))
        packets.append(packet)
    _ingest(coordinator, packets)

    _, attempts = _player_attempts(store)
    completed = [attempt for attempt in attempts
                 if attempt["payload"]["disposition"] == "completed"
                 and attempt["payload"]["start_observed"]]
    assert [attempt["payload"]["lap_number"] for attempt in completed] == [2, 3]
    assert len({attempt["driver"] for attempt in completed}) == 1
    assert len({attempt["manifest"]["epoch"] for attempt in completed}) == 1
    assert all(attempt["binding_verified"] for attempt in completed)
    assert all(attempt["readiness"]["state"] == "published" for attempt in completed)
    assert all(attempt["payload"]["association_scope_assessable"] for attempt in completed)
    assert coordinator.pipeline.event_packets_decoded == 1


def test_career_flashback_event_still_invalidates_crossing_player_lap(career_coordinator):
    store, coordinator = career_coordinator
    packets = [_career_session()]
    for packet in admitted_packets(frames=70):
        frame = struct.unpack_from("<I", packet.payload, 19)[0]
        if frame == 30 and packet.payload[6] == 2:
            packets.append(_event(30, b"FLBK" + struct.pack("<If", 10, 0.5) + bytes(4)))
        packets.append(packet)
    _ingest(coordinator, packets)

    _, attempts = _player_attempts(store)
    crossing = [attempt for attempt in attempts if attempt["payload"]["lap_number"] == 2]
    assert crossing
    assert all(attempt["readiness"]["state"] == "quarantined" for attempt in crossing)
    assert any(attempt["payload"]["disposition"] == "abandoned" for attempt in crossing)
    assert all(not attempt["payload"]["start_observed"] for attempt in crossing
               if attempt["payload"]["disposition"] == "completed")
    assert coordinator.pipeline.event_code_counts["FLBK"] == 1
    assert coordinator.pipeline.event_decode_error_count == 0
