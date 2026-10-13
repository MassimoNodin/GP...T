from copy import deepcopy
from dataclasses import replace

import pytest

from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.processing.engineer_facts import LiveEngineerFacts, qualify_snapshot
from f1_engineer.udp.car_telemetry import CarTelemetryDecoder
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.models import PacketFormat, PacketFrame
from tests.helpers import make_datagram
from tests.test_detail_pipeline import SESSION, _context, _lap_packet, _roster_packet
from tests.test_detail_policy import frame, lap
from tests.test_engineer_facts import source
from tests.test_live_monitor import _telemetry_packet
from tests.test_radio_questions import live
from tests.test_standalone_radio import lap_event


def telemetry(value=None):
    value = value or source()
    packet = PacketDecoder().decode(_telemetry_packet(frame=value.frame_ordinal, sequence=0))
    packet = replace(packet, header=replace(packet.header, session_uid=value.session_uid,
                     session_time=float(value.frame_ordinal), player_car_index=value.player_car_index))
    data = CarTelemetryDecoder().decode(packet).telemetry
    return packet, data


def facts(value=None, packets=None):
    value = value or source()
    result = LiveEngineerFacts()
    result.observe(value, frame_identifier=value.frame_ordinal, session_time_s=float(value.frame_ordinal),
                   received_monotonic_ns=value.frame_ordinal * 1_000_000_000,
                   captured_at_ns=value.frame_ordinal * 1_000_000_000,
                   telemetry_packets=(telemetry(value),) if packets is None else packets)
    return result


def observation(result):
    return result.snapshot()["drivers"][0]["observation"]


def test_bound_context_and_observation_are_measurements_not_corner_authority():
    result = facts()
    snapshot = result.snapshot()
    assert snapshot["context"]["status"] == "available"
    assert snapshot["context"]["value"]["session_type"] == "race"
    assert not snapshot["context"]["layout_verified"] and not snapshot["context"]["coaching_eligible"]
    detail = observation(result)
    assert detail["status"] == "available" and detail["value"]["speed_kph"] == 100
    assert detail["value"]["brake"] == pytest.approx(0.2)
    assert detail["identity"] == snapshot["drivers"][0]["identity"]
    assert detail["source"]["frame_ordinal"] == 5 and detail["source"]["epoch"] == 2
    assert not detail["coaching_eligible"]
    snapshot["context"]["value"]["track_id"] = 999
    detail["value"]["brake"] = 1
    assert result.snapshot()["context"]["value"]["track_id"] == 0
    assert observation(result)["value"]["brake"] == pytest.approx(0.2)


@pytest.mark.parametrize("change", ["missing", "session", "format"])
def test_context_missing_or_misbound_is_explicitly_unavailable(change):
    value = source()
    context = value.context
    context = None if change == "missing" else replace(context, **(
        {"session_uid": 100} if change == "session" else {"packet_format": PacketFormat.SEASON_PACK_2026}))
    snapshot = facts(replace(value, context=context)).snapshot()
    assert snapshot["context"]["status"] == "unavailable" and snapshot["context"]["value"] is None
    assert snapshot["status"] == "available"


def test_qualification_hides_context_and_detail_without_mutating_cached_facts():
    result = facts()
    snapshot = qualify_snapshot(result.snapshot(), now_ns=9_000_000_000, stale_after_s=3)
    assert snapshot["status"] == "unavailable" and not snapshot["drivers"]
    assert snapshot["context"]["value"] is None
    assert result.snapshot()["context"]["status"] == "available"


@pytest.mark.parametrize("field,broken", [("session_uid", 100), ("overall_frame_identifier", 4),
                                          ("player_car_index", 1), ("session_time", 4.9),
                                          ("session_time", float("nan"))])
def test_telemetry_envelope_mismatch_does_not_contaminate_lap_facts(field, broken):
    packet, data = telemetry()
    packet = replace(packet, header=replace(packet.header, **{field: broken}))
    result = facts(packets=((packet, data),))
    assert observation(result)["status"] == "unavailable"
    assert result.snapshot()["drivers"][0]["lap"]["last_lap_time_ms"] == 100000


def test_format_mismatch_and_missing_telemetry_are_unavailable():
    packet, data = telemetry()
    assert observation(facts(packets=()))["reason"] == "player_telemetry_missing"
    packet = replace(packet, packet_format=PacketFormat.SEASON_PACK_2026)
    assert observation(facts(packets=((packet, data),)))["status"] == "unavailable"


@pytest.mark.parametrize("field,broken", [("received_monotonic_ns", None), ("captured_at_ns", None),
                                          ("received_monotonic_ns", True), ("received_monotonic_ns", 2_000_000_000),
                                          ("received_monotonic_ns", 8_000_000_000), ("captured_at_ns", -1)])
def test_receipt_provenance_is_required_and_bounded(field, broken):
    packet, data = telemetry()
    packet = replace(packet, **{field: broken})
    assert observation(facts(packets=((packet, data),)))["reason"] == "player_telemetry_provenance_unavailable"


@pytest.mark.parametrize("field,broken", [("speed_kph", True), ("speed_kph", -1), ("speed_kph", 65536),
                                          ("brake", float("nan")), ("brake", True), ("brake", 1.1),
                                          ("throttle", -0.1), ("steering", float("inf")), ("gear", 9)])
def test_invalid_wire_values_abstain(field, broken):
    packet, data = telemetry()
    data = replace(data, cars=(replace(data.cars[0], **{field: broken}),) + data.cars[1:])
    assert observation(facts(packets=((packet, data),)))["reason"] == "player_telemetry_values_invalid"


def test_conflicts_fail_closed_but_identical_duplicates_are_safe():
    packet, data = telemetry()
    assert observation(facts(packets=((packet, data), (packet, data))))["status"] == "available"
    other = replace(data, cars=(replace(data.cars[0], speed_kph=101),) + data.cars[1:])
    assert observation(facts(packets=((packet, data), (packet, other))))["reason"] == "player_telemetry_conflicted"


def test_other_drivers_never_receive_detailed_observations():
    value = frame([lap(), lap()], player=1)
    result = facts(value).snapshot()
    assert "observation" not in result["drivers"][0]
    assert result["drivers"][1]["observation"]["value"]["speed_kph"] == 101


def test_pipeline_exports_same_frame_player_data_without_changing_detail_selection():
    pipeline = TelemetryPipeline(detail_profile="demand_v1")
    roster, lap_packet = _roster_packet(1), _lap_packet(1)
    original = PacketDecoder().decode(_telemetry_packet(frame=1, sequence=1))
    packet = PacketDecoder().decode(make_datagram(packet_id=6, session_uid=SESSION, frame=1,
                                                 session_time=1 / 60, body=original.body, sequence=1))
    pipeline.sessions.observe(roster)
    pipeline.sessions.update_context(_context(), 1)
    pipeline._process_frames((PacketFrame(SESSION, 1, (roster, lap_packet, packet)),))
    snapshot = pipeline.engineer_facts.snapshot()
    assert snapshot["drivers"][0]["observation"]["status"] == "available"
    assert snapshot["context"]["value"]["track_id"] == 1
    assert all("observation" not in driver for driver in snapshot["drivers"][1:])
    assert pipeline.observation_rows_created == 2


def radio_ready():
    controller, radio, events, value = live()
    value.update(frame_identifier=5, frame_ordinal=5, session_time_s=5.0)
    player = value["drivers"][0]
    player["lap"]["lap_distance_m"] = 100.0
    player["observation"] = {"status": "available", "identity": deepcopy(player["identity"]),
        "value": {"speed_kph": 200, "brake": 0.2, "lap_number": 3, "lap_distance_m": 100.0,
                  "current_lap_time_ms": 12000},
        "source": {**value["scope"], "frame_identifier": 5, "frame_ordinal": 5, "session_time_s": 5.0,
                   "lap_received_monotonic_ns": value["received_monotonic_ns"],
                   "telemetry_received_monotonic_ns": value["received_monotonic_ns"],
                   "telemetry_captured_at_ns": 1}}
    controller.feed(value, now=10)
    return controller, radio, events, value


def test_browser_free_speed_and_brake_queries_do_not_claim_braking_points():
    controller, radio, events, value = radio_ready()
    controller.handle_request("what is my speed and brake input", now=10)
    assert "200 kilometres per hour" in radio.pending[-1].text
    assert "20.0 percent, not a braking point" in radio.pending[-1].text
    controller.handle_request("my and the leader speed", now=10)
    assert "200 kilometres per hour" in radio.pending[-1].text and "opponent detail is not exposed" in radio.pending[-1].text


@pytest.mark.parametrize("section,field,broken", [("source", "epoch", 3), ("source", "frame_identifier", 4),
    ("source", "lap_received_monotonic_ns", 1), ("source", "telemetry_received_monotonic_ns", None),
    ("value", "lap_number", 2), ("value", "current_lap_time_ms", 12001), ("value", "lap_distance_m", 101.0),
    ("value", "speed_kph", True), ("value", "brake", float("nan")), ("value", "brake", 1.1),
    ("source", "frame_identifier", True), ("source", "session_time_s", True),
    ("source", "telemetry_received_monotonic_ns", 2**65), ("value", "lap_distance_m", None)])
def test_radio_abstains_on_misaligned_or_invalid_player_evidence(section, field, broken):
    controller, radio, events, value = radio_ready()
    value["drivers"][0]["observation"][section][field] = broken
    controller.feed(value, now=10)
    controller.handle_request("my speed" if field == "speed_kph" else "my brake input", now=10)
    assert "unavailable" in radio.pending[-1].text


@pytest.mark.parametrize("offset,now", [(3_000_000_000, 10), (-1_000_000_000, 10), (1_000_000_000, 12)])
def test_radio_checks_telemetry_age_independently_of_lap_age(offset, now):
    controller, radio, events, value = radio_ready()
    value["drivers"][0]["observation"]["source"]["telemetry_received_monotonic_ns"] -= offset
    controller.feed(value, now=10)
    controller.handle_request("my speed", now=now)
    assert "unavailable" in radio.pending[-1].text


def test_queued_answer_and_repeat_expire_with_the_oldest_observation_not_just_lap_data():
    controller, radio, events, value = radio_ready()
    value["drivers"][0]["observation"]["source"]["telemetry_received_monotonic_ns"] -= 2_000_000_000
    controller.feed(value, now=10)
    controller.handle_request("my speed and last lap", now=10)
    assert radio.pending[-1].observation_age_s == pytest.approx(2.1)
    assert radio.pending[-1].dispatch_before == pytest.approx(10.9)
    radio.tick(now=11)
    assert radio.active is None and not radio.pending and not radio.repeat(now=11)


def test_telemetry_age_does_not_leak_into_subsequent_lap_only_answers():
    controller, radio, events, value = radio_ready()
    value["drivers"][0]["observation"]["source"]["telemetry_received_monotonic_ns"] -= 2_000_000_000
    controller.feed(value, now=10)
    controller.handle_request("my speed", now=10)
    controller.handle_request("my last lap", now=10)
    assert radio.pending[-1].observation_age_s == pytest.approx(0.1)
    assert radio.pending[-1].dispatch_before == pytest.approx(12.9)


def test_lap_report_dispatch_uses_current_player_telemetry_age():
    controller, radio, events, value = radio_ready()
    controller.handle_request("report at end of each lap my speed", now=10)
    radio.cancel()
    value["drivers"][0]["lap"]["current_lap_number"] = 4
    value["drivers"][0]["observation"]["value"]["lap_number"] = 4
    value["drivers"][0]["observation"]["source"]["telemetry_received_monotonic_ns"] -= 2_000_000_000
    value["event_cursor"] = 1
    event = lap_event(1)
    event["lap_number"] = 3
    value["events"] = [event]
    controller.feed(value, now=10.1)
    assert len(radio.pending) == 1 and "200 kilometres per hour" in radio.pending[-1].text
    assert radio.pending[-1].observation_age_s == pytest.approx(2.1)
    assert radio.pending[-1].dispatch_before == pytest.approx(11.0)
