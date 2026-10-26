from dataclasses import replace

import pytest

from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.processing.detail_policy import DetailDemandTarget
from f1_engineer.processing.engineer_facts import LiveEngineerFacts
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.models import PacketFrame
from tests.helpers import make_datagram
from tests.test_detail_pipeline import SESSION, _lap_packet, _pipeline_frame, _roster_packet
from tests.test_detail_policy import frame, lap
from tests.test_live_monitor import _telemetry_packet
from tests.test_live_player_observations import telemetry


def demanded_frame():
    value = frame([lap(), lap()], player=0)
    target = DetailDemandTarget("radio-region-1", value.verified_tenures[1])
    return replace(value, demands=(target,))


def observe(value, packets=None, *, facts=None):
    facts = facts or LiveEngineerFacts()
    packet, data = telemetry(value)
    data = replace(data, cars=(data.cars[0], replace(data.cars[1], speed_kph=180, brake=0.4)) + data.cars[2:])
    facts.observe(value, frame_identifier=value.frame_ordinal, session_time_s=float(value.frame_ordinal),
                  received_monotonic_ns=value.frame_ordinal * 1_000_000_000,
                  captured_at_ns=value.frame_ordinal * 1_000_000_000,
                  telemetry_packets=((packet, data),) if packets is None else packets)
    return facts


def opponent(facts):
    return next(row for row in facts.snapshot()["drivers"] if row["car_index"] == 1)


def test_explicit_detail_demand_exposes_only_bound_same_frame_observation():
    value = demanded_frame()
    value = replace(value, lap_data=(value.lap_data[0], replace(value.lap_data[1], current_lap_number=7,
                    lap_distance_m=150.0, current_lap_time_ms=12000)))
    row = opponent(observe(value))
    result = row["observation"]
    assert result["status"] == "available" and result["identity"] == row["identity"]
    assert result["value"]["speed_kph"] == 180 and result["value"]["brake"] == 0.4
    assert result["value"]["lap_number"] == 7 and result["value"]["lap_distance_m"] == 150
    assert result["source"]["frame_ordinal"] == value.frame_ordinal
    assert result["detail_task_ids"] == ["radio-region-1"] and not result["coaching_eligible"]


def test_automatic_nearby_selection_does_not_expose_opponent_observation():
    value = replace(demanded_frame(), demands=())
    assert "observation" not in opponent(observe(value))


def test_released_demand_is_removed_on_next_frame_without_retaining_old_sample():
    value = demanded_frame()
    facts = observe(value)
    assert opponent(facts)["observation"]["status"] == "available"
    observe(replace(value, frame_ordinal=6, demands=()), facts=facts)
    assert "observation" not in opponent(facts)


@pytest.mark.parametrize("field,broken", [("tenure_ordinal", 2), ("participant_identity_fingerprint", "replacement"),
                                         ("session_uid", 100), ("packet_format", 2026), ("lifecycle_epoch", 3)])
def test_stale_demand_binding_never_exposes_replacement(field, broken):
    value = demanded_frame()
    stale = replace(value.demands[0], tenure=replace(value.demands[0].tenure, **{field: broken}))
    assert "observation" not in opponent(observe(replace(value, demands=(stale,))))


def test_valid_owner_does_not_launder_stale_owner_for_same_car():
    value = demanded_frame()
    stale = DetailDemandTarget("stale", replace(value.demands[0].tenure, tenure_ordinal=2))
    result = opponent(observe(replace(value, demands=value.demands + (stale,))))["observation"]
    assert result["detail_task_ids"] == ["radio-region-1"]


def test_multiple_current_owners_are_sorted_and_deduplicated():
    value = demanded_frame()
    other = DetailDemandTarget("another-owner", value.demands[0].tenure)
    result = opponent(observe(replace(value, demands=value.demands + (other, other))))["observation"]
    assert result["detail_task_ids"] == ["another-owner", "radio-region-1"]


@pytest.mark.parametrize("identifier", ["", "bad owner", "x" * 65, None])
def test_invalid_owner_ids_fail_closed(identifier):
    value = demanded_frame()
    assert "observation" not in opponent(observe(replace(value, demands=(replace(value.demands[0], task_id=identifier),))))


def test_conflicting_owner_identity_and_excessive_owner_budget_fail_closed():
    value = demanded_frame()
    conflict = replace(value.demands[0], tenure=value.verified_tenures[0])
    assert "observation" not in opponent(observe(replace(value, demands=value.demands + (conflict,))))
    excessive = tuple(replace(value.demands[0], task_id=f"owner-{index}") for index in range(5))
    assert "observation" not in opponent(observe(replace(value, demands=excessive)))


def test_conflicted_frame_does_not_expose_opponent_detail():
    assert "observation" not in opponent(observe(replace(demanded_frame(), frame_conflicted=True)))


@pytest.mark.parametrize("status", [0, 1, 3, 4, 5, 6, 7, True])
def test_non_racing_or_unknown_result_status_has_no_measurement(status):
    value = demanded_frame()
    value = replace(value, lap_data=(value.lap_data[0], replace(value.lap_data[1], result_status_id=status)))
    result = opponent(observe(value))["observation"]
    assert result["status"] == "unavailable" and result["reason"] == "opponent_not_racing"
    assert result["value"] is None and result["source"] is None


def test_missing_opponent_telemetry_keeps_timing_and_owner_but_no_fake_zero():
    result = opponent(observe(demanded_frame(), packets=()))
    assert result["lap"]["car_position"] == 2
    assert result["observation"]["reason"] == "opponent_telemetry_missing"
    assert result["observation"]["value"] is None
    assert result["observation"]["detail_task_ids"] == ["radio-region-1"]


@pytest.mark.parametrize("field,broken", [("speed_kph", True), ("speed_kph", -1), ("speed_kph", 65536),
                                         ("brake", float("nan")), ("brake", True), ("brake", 1.1)])
def test_invalid_opponent_values_abstain(field, broken):
    value = demanded_frame()
    packet, data = telemetry(value)
    data = replace(data, cars=(data.cars[0], replace(data.cars[1], **{field: broken})) + data.cars[2:])
    result = opponent(observe(value, packets=((packet, data),)))["observation"]
    assert result["reason"] == "opponent_telemetry_values_invalid" and result["value"] is None


def test_misaligned_clock_or_conflicting_packets_do_not_expose_opponent_values():
    value = demanded_frame()
    packet, data = telemetry(value)
    wrong = replace(packet, header=replace(packet.header, session_time=4.9))
    assert opponent(observe(value, packets=((wrong, data),)))["observation"]["reason"] == "opponent_telemetry_clock_mismatch"
    other = replace(data, cars=(data.cars[0], replace(data.cars[1], speed_kph=99)) + data.cars[2:])
    assert opponent(observe(value, packets=((packet, data), (packet, other))))["observation"]["reason"] == "opponent_telemetry_conflicted"


def test_snapshot_copy_cannot_change_owned_observation():
    facts = observe(demanded_frame())
    result = opponent(facts)
    result["observation"]["detail_task_ids"].clear()
    result["observation"]["value"]["speed_kph"] = 1
    assert opponent(facts)["observation"]["detail_task_ids"] == ["radio-region-1"]
    assert opponent(facts)["observation"]["value"]["speed_kph"] == 180


@pytest.mark.parametrize("field,broken", [("session_uid", 100), ("overall_frame_identifier", 4),
                                         ("player_car_index", 1)])
def test_wrong_telemetry_envelope_never_attaches_opponent_values(field, broken):
    value = demanded_frame()
    packet, data = telemetry(value)
    packet = replace(packet, header=replace(packet.header, **{field: broken}))
    result = opponent(observe(value, packets=((packet, data),)))["observation"]
    assert result["reason"] == "opponent_telemetry_missing" and result["value"] is None


@pytest.mark.parametrize("field,broken", [("received_monotonic_ns", None), ("received_monotonic_ns", True),
                                         ("received_monotonic_ns", 8_000_000_000), ("captured_at_ns", -1)])
def test_unusable_opponent_receipt_provenance_abstains(field, broken):
    value = demanded_frame()
    packet, data = telemetry(value)
    packet = replace(packet, **{field: broken})
    result = opponent(observe(value, packets=((packet, data),)))["observation"]
    assert result["reason"] == "opponent_telemetry_provenance_unavailable" and result["value"] is None


def test_replacement_participant_does_not_inherit_old_owner():
    value = demanded_frame()
    replacement = replace(value.verified_tenures[1], tenure_ordinal=2,
                          participant_identity_fingerprint="replacement")
    value = replace(value, verified_tenures=(value.verified_tenures[0], replacement))
    assert "observation" not in opponent(observe(value))


def test_pipeline_passes_actual_detail_demands_without_exposing_automatic_neighbours():
    pipeline = TelemetryPipeline(detail_profile="demand_v1")
    _pipeline_frame(pipeline, 1)
    tenure = next(item for item in pipeline.car_lap_inventory.active_tenures({SESSION: 1}) if item.car_index == 3)
    pipeline.detail_demands = (DetailDemandTarget("radio-opponent", tenure),)
    original = PacketDecoder().decode(_telemetry_packet(frame=2, sequence=2))
    packet = PacketDecoder().decode(make_datagram(packet_id=6, session_uid=SESSION, frame=2,
                                                 session_time=2 / 60, body=original.body, sequence=2))
    pipeline._process_frames((PacketFrame(SESSION, 2, (_roster_packet(2), _lap_packet(2), packet)),))
    rows = pipeline.engineer_facts.snapshot()["drivers"]
    selected = next(row for row in rows if row["car_index"] == 3)
    assert selected["observation"]["status"] == "available"
    assert selected["observation"]["detail_task_ids"] == ["radio-opponent"]
    assert all("observation" not in row for row in rows if row["car_index"] not in (0, 3))


def test_full_trace_profile_does_not_implicitly_authorize_live_opponent_observations():
    pipeline = TelemetryPipeline(detail_profile="full")
    _pipeline_frame(pipeline, 1, profile="full")
    tenure = next(item for item in pipeline.car_lap_inventory.active_tenures({SESSION: 1}) if item.car_index == 3)
    pipeline.detail_demands = (DetailDemandTarget("radio-opponent", tenure),)
    original = PacketDecoder().decode(_telemetry_packet(frame=2, sequence=2))
    packet = PacketDecoder().decode(make_datagram(packet_id=6, session_uid=SESSION, frame=2,
                                                 session_time=2 / 60, body=original.body, sequence=2))
    pipeline._process_frames((PacketFrame(SESSION, 2, (_roster_packet(2), _lap_packet(2), packet)),))
    rows = pipeline.engineer_facts.snapshot()["drivers"]
    assert all("observation" not in row for row in rows if row["car_index"] != 0)
