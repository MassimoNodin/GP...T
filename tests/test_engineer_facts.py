from dataclasses import fields, replace
import socket
import time

import pytest
from fastapi.testclient import TestClient

from f1_engineer.api.app import create_app
from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.engineer_facts import LiveEngineerFacts, qualify_snapshot
from f1_engineer.processing.evidence import EvidenceStore
from f1_engineer.processing.runtime import LiveSessionRuntime
from f1_engineer.udp.car_status import CarStatusData
from f1_engineer.udp.decoder import PacketDecoder
from tests.helpers import make_datagram
from tests.test_detail_pipeline import _pipeline_frame
from tests.test_detail_policy import frame, lap
from tests.test_session_evidence import admitted_packets


def source(*, ordinal=5, lap_number=1, sector=0, pit=0, last_ms=100_000):
    data = replace(lap(), current_lap_number=lap_number, sector_id=sector,
                   pit_status_id=pit, last_lap_time_ms=last_ms,
                   sector1_time_ms=30_000, sector2_time_ms=35_000)
    return replace(frame([data]), frame_ordinal=ordinal)


def observe(facts, value, *, statuses=None, session_time=None):
    facts.observe(value, frame_identifier=value.frame_ordinal,
                  session_time_s=float(value.frame_ordinal) if session_time is None else session_time,
                  received_monotonic_ns=value.frame_ordinal * 1_000_000_000,
                  captured_at_ns=value.frame_ordinal * 1_000_000_000,
                  statuses=statuses)


def status(compound):
    values = {item.name: 0 for item in fields(CarStatusData)}
    values.update(actual_tyre_compound=compound, visual_tyre_compound=compound, tyre_age_laps=1)
    return CarStatusData(**values)


def test_decoder_preserves_receipt_provenance_without_changing_packet_equality():
    raw = make_datagram()
    packet = PacketDecoder().decode(raw)
    assert packet.received_monotonic_ns == raw.monotonic_ns
    assert packet.captured_at_ns == raw.captured_at_ns
    assert packet == replace(packet, received_monotonic_ns=123, captured_at_ns=456)


def test_initial_observation_has_bound_facts_but_no_invented_events():
    facts = LiveEngineerFacts()
    observe(facts, source())
    snapshot = facts.snapshot()
    assert snapshot["status"] == "available"
    assert snapshot["scope"] == {"session_uid": 99, "packet_format": 2025, "epoch": 2}
    assert snapshot["drivers"][0]["identity"]["participant_identity_fingerprint"] == "identity-0-1"
    assert snapshot["drivers"][0]["lap"]["last_lap_time_ms"] == 100_000
    assert snapshot["drivers"][0]["tyre"]["reason"] == "status_packet_missing"
    assert snapshot["events"] == []
    snapshot["drivers"][0]["lap"]["last_lap_time_ms"] = 1
    assert facts.snapshot()["drivers"][0]["lap"]["last_lap_time_ms"] == 100_000


def test_lap_transition_is_one_event_with_source_time_and_missing_timing_explicit():
    facts = LiveEngineerFacts()
    observe(facts, source())
    following = source(ordinal=6, lap_number=2, last_ms=0)
    observe(facts, following)
    observe(facts, following)
    event, = facts.snapshot()["events"]
    assert event["kind"] == "lap_completed"
    assert event["lap_number"] == 1
    assert event["lap_time_ms"] is None
    assert event["received_monotonic_ns"] == 6_000_000_000
    assert event["qualification"] == "observed_lap_transition"


def test_sector_pit_and_compound_transitions_are_separate_events():
    facts = LiveEngineerFacts()
    observe(facts, source(), statuses={0: (status(18), None)})
    observe(facts, source(ordinal=6, sector=1, pit=1), statuses={0: (status(18), None)})
    observe(facts, source(ordinal=7, sector=1, pit=0), statuses={0: (status(16), None)})
    events = facts.snapshot()["events"]
    assert [event["kind"] for event in events] == [
        "sector_completed", "pit_entered", "pit_exited", "tyre_compound_changed"]
    assert events[0]["sector"] == 1
    assert events[0]["sector_time_ms"] == 30_000
    assert events[1]["pre_pit_position"] == 1
    assert events[1]["pre_pit_field"][0]["position"] == 1
    assert events[1]["pre_pit_received_monotonic_ns"] == 5_000_000_000
    assert events[1]["pre_pit_stop_count"] == 0
    assert events[1]["pre_pit_tyre"]["actual_compound"] == 18
    assert events[1]["pre_pit_field"][0]["leader_gap_ms"] == 0
    assert events[-1]["actual_compound"] == 16


@pytest.mark.parametrize("number", [3, 4])
def test_lap_rewind_or_skipped_laps_does_not_invent_completions(number):
    facts = LiveEngineerFacts()
    observe(facts, source())
    observe(facts, source(ordinal=6, lap_number=number, pit=1))
    assert [event["kind"] for event in facts.snapshot()["events"]] == ["driver_discontinuity"]


def test_session_time_rewind_resets_driver_transition_baseline():
    facts = LiveEngineerFacts()
    observe(facts, source())
    observe(facts, source(ordinal=6, lap_number=2), session_time=4.0)
    assert facts.snapshot()["events"][0]["kind"] == "driver_discontinuity"


def test_positive_lap_number_rewind_does_not_emit_a_completion():
    facts = LiveEngineerFacts()
    observe(facts, source(lap_number=2))
    observe(facts, source(ordinal=6, lap_number=1))
    assert [event["kind"] for event in facts.snapshot()["events"]] == ["driver_discontinuity"]


def test_player_binding_change_resets_event_baselines():
    facts = LiveEngineerFacts()
    observe(facts, source())
    observe(facts, replace(source(ordinal=6, lap_number=2), player_car_index=None))
    assert facts.snapshot()["events"] == []


def test_missing_or_zero_compound_is_unavailable_and_does_not_create_change_event():
    facts = LiveEngineerFacts()
    observe(facts, source(), statuses={0: (status(18), None)})
    observe(facts, source(ordinal=6), statuses={0: (status(0), None)})
    assert facts.snapshot()["drivers"][0]["tyre"]["reason"] == "tyre_compound_unavailable"
    observe(facts, source(ordinal=7), statuses={0: (status(16), None)})
    assert facts.snapshot()["events"] == []


def test_nonplayer_pit_does_not_claim_to_snapshot_the_player_pre_pit_field():
    facts = LiveEngineerFacts()
    observe(facts, replace(source(), player_car_index=None))
    observe(facts, replace(source(ordinal=6, pit=1), player_car_index=None))
    event, = facts.snapshot()["events"]
    assert "pre_pit_field" not in event


@pytest.mark.parametrize("change", ["scope", "identity", "tenure", "format"])
def test_changed_binding_does_not_compare_unrelated_driver_observations(change):
    facts = LiveEngineerFacts()
    observe(facts, source())
    following = source(ordinal=6, lap_number=2)
    tenure, = following.verified_tenures
    if change == "scope":
        following = replace(following, lifecycle_epoch=3,
                            verified_tenures=(replace(tenure, lifecycle_epoch=3),))
    elif change == "format":
        following = replace(following, packet_format=2026,
                            verified_tenures=(replace(tenure, packet_format=2026),))
    else:
        changed = (replace(tenure, participant_identity_fingerprint="new-driver") if change == "identity"
                   else replace(tenure, tenure_ordinal=2))
        following = replace(following, verified_tenures=(changed,))
    observe(facts, following)
    assert facts.snapshot()["events"] == []


@pytest.mark.parametrize("change", ["conflict", "scope", "ambiguous", "expired", "wrong_epoch"])
def test_unverified_driver_or_conflicted_frame_is_unavailable(change):
    facts = LiveEngineerFacts()
    value = source()
    tenure, = value.verified_tenures
    if change == "conflict":
        value = replace(value, lap_data_conflicted=True)
    elif change == "scope":
        value = replace(value, association_scope_assessable=False)
    elif change == "ambiguous":
        value = replace(value, verified_tenures=(tenure, tenure))
    elif change == "expired":
        value = replace(value, verified_tenures=(replace(tenure, end_frame_ordinal_exclusive=5),))
    else:
        value = replace(value, verified_tenures=(replace(tenure, lifecycle_epoch=3),))
    observe(facts, value)
    assert facts.snapshot()["status"] == "unavailable"
    assert facts.snapshot()["drivers"] == []


def test_event_capacity_and_invalidation_expose_cursor_loss():
    facts = LiveEngineerFacts(event_limit=2)
    for ordinal in range(5, 9):
        observe(facts, source(ordinal=ordinal, lap_number=ordinal - 4))
    snapshot = facts.snapshot()
    assert [event["id"] for event in snapshot["events"]] == [2, 3]
    assert snapshot["events_dropped"] == 1
    assert snapshot["event_floor"] == 1
    facts.invalidate("telemetry_silence")
    assert facts.snapshot()["event_floor"] == 3
    assert facts.snapshot()["events"] == []
    observe(facts, source(ordinal=9, lap_number=5))
    assert facts.snapshot()["events"] == []


@pytest.mark.parametrize("now_ns,state,reason", [
    (8_000_000_000, "receiving", "lap_observations_stale"),
    (4_000_000_000, "receiving", "observation_clock_mismatch"),
    (5_000_000_000, "stale", "runtime_stale"),
    (5_000_000_000, "failed", "runtime_failed"),
])
def test_freshness_is_based_on_packet_receipt_not_publication(now_ns, state, reason):
    facts = LiveEngineerFacts()
    observe(facts, source())
    snapshot = qualify_snapshot(facts.snapshot(), now_ns=now_ns, stale_after_s=3,
                                runtime_state=state)
    assert snapshot["reason"] == reason
    assert snapshot["drivers"] == []
    assert snapshot["events"] == []
    assert facts.snapshot()["status"] == "available"


def test_missing_receipt_provenance_is_not_published_as_current():
    facts = LiveEngineerFacts()
    facts.observe(source(), frame_identifier=5, session_time_s=5,
                  received_monotonic_ns=None, captured_at_ns=None)
    assert facts.snapshot()["reason"] == "observation_provenance_unavailable"


def test_pipeline_retains_field_timing_independent_of_detail_selection():
    pipeline = TelemetryPipeline(detail_profile="demand_v1")
    _pipeline_frame(pipeline, 1)
    snapshot = pipeline.engineer_facts.snapshot()
    assert len(snapshot["drivers"]) == 22
    assert pipeline.observation_rows_created == 2
    _pipeline_frame(pipeline, 2, duplicate=True)
    assert pipeline.engineer_facts.snapshot()["drivers"] == []


def test_pipeline_identity_replacement_updates_live_binding():
    pipeline = TelemetryPipeline()
    _pipeline_frame(pipeline, 1)
    old = pipeline.engineer_facts.snapshot()["drivers"][5]["identity"]
    _pipeline_frame(pipeline, 2, driver_ids={5: 99})
    current = pipeline.engineer_facts.snapshot()["drivers"][5]["identity"]
    assert current != old


def test_coordinator_exports_logical_session_and_clears_gap_history(tmp_path):
    coordinator = SessionCoordinator(EvidenceStore(tmp_path / "evidence.sqlite3"), "facts-test")
    try:
        for packet in admitted_packets(frames=15, lap_frames=5):
            coordinator.ingest(packet)
        snapshot = coordinator.engineer_snapshot()
        assert snapshot["status"] == "available"
        assert len(snapshot["session_id"]) == 32
        assert snapshot["generation"] == coordinator.generation
        assert any(event["kind"] == "lap_completed" for event in snapshot["events"])
        coordinator.gap("test_disconnect")
        assert coordinator.engineer_snapshot()["reason"] == "test_disconnect"
        assert coordinator.engineer_snapshot()["events"] == []
    finally:
        coordinator.close()


def test_runtime_reads_only_cached_snapshot_and_rechecks_freshness(tmp_path, monkeypatch):
    runtime = LiveSessionRuntime(EvidenceStore(tmp_path / "evidence.sqlite3"), host="127.0.0.1", port=0)
    facts = LiveEngineerFacts()
    observe(facts, source())
    runtime._engineer_snapshot_cache = facts.snapshot()
    runtime.state = "receiving"
    monkeypatch.setattr("f1_engineer.processing.runtime.time.perf_counter_ns", lambda: 6_000_000_000)
    assert runtime.engineer_snapshot()["status"] == "available"
    monkeypatch.setattr("f1_engineer.processing.runtime.time.perf_counter_ns", lambda: 9_000_000_000)
    assert runtime.engineer_snapshot()["status"] == "unavailable"
    assert runtime._engineer_snapshot_cache["drivers"]


def test_live_facts_endpoint_requires_auth_and_explains_disabled_acquisition(tmp_path):
    app = create_app(tmp_path / "archive.sqlite3", recordings_root=tmp_path / "recordings",
                     control_token="engineer-facts-test-token", automatic_acquisition=False)
    with TestClient(app) as client:
        assert client.get("/api/v2/engineer/live").status_code == 403
        response = client.get("/api/v2/engineer/live",
                              headers={"Authorization": "Bearer engineer-facts-test-token"})
        assert response.status_code == 200
        assert response.json()["data"]["reason"] == "live_runtime_disabled"


def test_unstarted_or_inactive_lap_cannot_generate_completion():
    facts = LiveEngineerFacts()
    observe(facts, source(lap_number=0))
    observe(facts, source(ordinal=6, lap_number=1))
    inactive = source(ordinal=7, lap_number=2)
    inactive = replace(inactive, lap_data=(replace(inactive.lap_data[0], result_status_id=0),))
    observe(facts, inactive)
    assert facts.snapshot()["events"] == []


def test_new_session_invalidates_current_facts_before_new_lap_frame_is_assembled():
    pipeline = TelemetryPipeline()
    _pipeline_frame(pipeline, 1)
    assert pipeline.engineer_facts.snapshot()["status"] == "available"
    pipeline.process(make_datagram(session_uid=123, frame=1))
    assert pipeline.engineer_facts.snapshot()["scope"] is None
    assert pipeline.engineer_facts.snapshot()["drivers"] == []


def test_pipeline_finish_invalidates_current_facts():
    pipeline = TelemetryPipeline()
    _pipeline_frame(pipeline, 1)
    pipeline.finish_with_outputs(interruption_reason="test_interrupt")
    assert pipeline.engineer_facts.snapshot()["reason"] == "test_interrupt"
    assert pipeline.engineer_facts.snapshot()["events"] == []


def test_live_udp_publishes_facts_then_invalidates_them_on_silence(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    runtime = LiveSessionRuntime(EvidenceStore(tmp_path / "live.sqlite3"),
                                 host="127.0.0.1", port=port, stale_after_s=0.5)
    runtime.start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            for packet in admitted_packets(frames=12, lap_frames=5):
                sender.sendto(packet.payload, ("127.0.0.1", port))
                time.sleep(0.01)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            snapshot = runtime.engineer_snapshot()
            if snapshot["status"] == "available":
                break
            time.sleep(0.01)
        assert snapshot["status"] == "available", runtime.status()
        assert snapshot["session_id"]
        assert snapshot["drivers"]
        assert snapshot["age_s"] >= 0
        deadline = time.monotonic() + 10
        while runtime.state != "stale" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert runtime.engineer_snapshot()["status"] == "unavailable"
        assert runtime.engineer_snapshot()["drivers"] == []
    finally:
        runtime.close()
