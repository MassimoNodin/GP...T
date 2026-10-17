from dataclasses import replace
import json

import pytest

from f1_engineer.processing.engineer_facts import LiveEngineerFacts, qualify_snapshot
from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.models import PacketFrame
from tests.helpers import make_datagram
from tests.test_detail_policy import frame, lap
from tests.test_engineer_facts import observe
from tests.test_session_history import _body
from tests.test_detail_pipeline import _pipeline_frame, _lap_packet, RECORD, SESSION


def live(facts, *, ordinal=5, number=1, elapsed=0, distance=0, cars=1, identity=None,
         session_time=None, fmt=2025, last_ms=100_000):
    value = replace(frame([replace(lap(), current_lap_number=number,
                                   current_lap_time_ms=elapsed, lap_distance_m=distance)
                           for car in range(cars)], distances=[distance] * cars, format=fmt),
                    frame_ordinal=ordinal)
    value = replace(value, lap_data=tuple(replace(data, last_lap_time_ms=last_ms) for data in value.lap_data))
    value = replace(value, verified_tenures=tuple(replace(tenure, end_frame_ordinal_exclusive=1000)
                                                for tenure in value.verified_tenures))
    if identity is not None:
        value = replace(value, verified_tenures=tuple(
            replace(tenure, participant_identity_fingerprint=identity) for tenure in value.verified_tenures))
    observe(facts, value, session_time=session_time)


def history(facts, *, ordinal=6, car=0, times=(100_000,), best=1, flags=15,
            player=0, uid=99, fmt=2025, epoch=2, assessable=True, conflicted=False,
            duplicates=(), body=None, session_time=None):
    payload = bytearray(_body(car_index=car, laps=tuple(
        (timing, 30_000, 0, 35_000, 0, 35_000, 0, flags) for timing in times)))
    payload[3] = best
    packet = PacketDecoder().decode(make_datagram(
        packet_format=fmt, packet_id=11, session_uid=uid, frame=ordinal,
        session_time=float(ordinal) if session_time is None else session_time,
        player_car_index=player, body=bytes(payload) if body is None else body))
    packet = replace(packet, received_monotonic_ns=ordinal * 1_000_000_000,
                     captured_at_ns=ordinal * 1_000_000_000)
    facts.reported_history.consume((packet, *duplicates), frame_ordinal=ordinal,
                                   epoch=epoch, assessable=assessable, conflicted=conflicted)
    return packet


def view(facts, car=0):
    return facts.snapshot()["drivers"][car]["reported_history"]


def test_reported_history_is_attributable_scalar_timing_not_detailed_observation():
    facts = LiveEngineerFacts()
    live(facts)
    live(facts, ordinal=6, number=2)
    history(facts)
    result = view(facts)
    assert result["status"] == "available"
    assert result["laps"] == [{"lap_number": 1, "lap_time_ms": 100_000,
                              "sector_times_ms": [30_000, 35_000, 35_000], "validity_flags": 15}]
    assert result["session_best"] == {"lap_number": 1, "lap_time_ms": 100_000}
    assert result["coaching_eligible"] is False
    assert result["qualification"] == "game_reported_scalar_timing"
    result["laps"][0]["lap_time_ms"] = 1
    assert view(facts)["laps"][0]["lap_time_ms"] == 100_000


def test_all_car_timing_does_not_require_opponent_detail_permission():
    facts = LiveEngineerFacts()
    live(facts, cars=2)
    live(facts, ordinal=6, number=2, cars=2)
    history(facts, car=1)
    assert view(facts, 1)["session_best"]["lap_time_ms"] == 100_000
    assert "observation" not in facts.snapshot()["drivers"][1]
    assert view(facts)["status"] == "unavailable"


def test_late_start_never_attributes_old_slot_laps_to_current_driver():
    facts = LiveEngineerFacts()
    live(facts, number=3, elapsed=10_000, distance=100)
    live(facts, ordinal=6, number=4)
    history(facts, times=(90_000, 95_000, 100_000), best=1)
    assert view(facts)["laps"] == []
    assert view(facts)["session_best_reason"] == "reported_best_not_attributable_or_completed"
    live(facts, ordinal=7, number=5)
    history(facts, ordinal=7, times=(110_000, 110_000, 110_000, 100_000), best=4)
    assert [row["lap_number"] for row in view(facts)["laps"]] == [4]
    assert view(facts)["session_best"]["lap_number"] == 4


@pytest.mark.parametrize("change", ["identity", "rewind", "skip", "clock", "elapsed", "removed", "invalidate"])
def test_rebinding_or_discontinuity_discards_old_history(change):
    facts = LiveEngineerFacts()
    live(facts)
    live(facts, ordinal=6, number=2, elapsed=5_000)
    history(facts)
    if change == "identity":
        live(facts, ordinal=7, number=2, identity="new")
    elif change == "rewind":
        live(facts, ordinal=7, number=1)
    elif change == "skip":
        live(facts, ordinal=7, number=4)
    elif change == "clock":
        live(facts, ordinal=7, number=2, session_time=1)
    elif change == "elapsed":
        live(facts, ordinal=7, number=2, elapsed=1)
    elif change == "removed":
        live(facts, ordinal=7, cars=0)
        live(facts, ordinal=8, number=2)
    else:
        facts.invalidate("restart")
        live(facts, ordinal=7, number=2)
    assert view(facts)["source"] is None
    assert view(facts)["laps"] == []


@pytest.mark.parametrize("options", [{"player": 1}, {"uid": 10}, {"fmt": 2026}, {"epoch": 3},
                                    {"assessable": False}, {"conflicted": True}, {"body": b"\0"},
                                    {"body": b""}])
def test_unqualified_history_never_exports_timing(options):
    facts = LiveEngineerFacts()
    live(facts)
    live(facts, ordinal=6, number=2)
    history(facts, **options)
    assert view(facts)["source"] is None


def test_conflicting_duplicate_invalidates_instead_of_selecting_one():
    facts = LiveEngineerFacts()
    live(facts)
    live(facts, ordinal=6, number=2)
    packet = history(facts)
    history(facts, times=(90_000,), duplicates=(packet,))
    assert view(facts)["reason"] == "session_history_conflicted"
    history(facts, ordinal=7)
    live(facts, ordinal=7, number=2)
    assert view(facts)["laps"][0]["lap_time_ms"] == 100_000


def test_identical_wire_duplicates_are_idempotent():
    facts = LiveEngineerFacts()
    live(facts)
    live(facts, ordinal=6, number=2)
    packet = history(facts)
    history(facts, duplicates=(packet,))
    assert view(facts)["session_best"]["lap_number"] == 1


def test_history_ahead_of_lap_facts_is_withheld_until_lap_catches_up():
    facts = LiveEngineerFacts()
    live(facts)
    history(facts)
    assert view(facts)["reason"] == "session_history_ahead_of_live_lap"
    live(facts, ordinal=6, number=2)
    assert view(facts)["session_best"]["lap_number"] == 1


@pytest.mark.parametrize("times,best,flags,reason", [
    ((100_000,), 0, 15, "reported_best_not_attributable_or_completed"),
    ((100_000,), 1, 14, "reported_best_invalid"),
    ((100_000,), 1, 31, "reported_best_invalid"),
    ((0,), 1, 15, "reported_best_not_attributable_or_completed"),
    ((100_000, 90_000), 1, 15, "reported_best_selector_conflicted"),
    ((100_000,), 1, 15, "session_history_table_incomplete"),
])
def test_best_selector_never_substitutes_a_different_lap(times, best, flags, reason):
    facts = LiveEngineerFacts()
    live(facts)
    live(facts, ordinal=6, number=2, last_ms=times[0])
    live(facts, ordinal=7, number=3 if len(times) == 2 or reason == "session_history_table_incomplete" else 2)
    history(facts, ordinal=7, times=times, best=best, flags=flags)
    assert view(facts)["session_best"] is None
    assert view(facts)["session_best_reason"] == reason


def test_partial_current_lap_is_never_exposed_as_completed():
    facts = LiveEngineerFacts()
    live(facts)
    live(facts, ordinal=6, number=2)
    history(facts, times=(100_000, 10_000), best=2)
    assert len(view(facts)["laps"]) == 1
    assert view(facts)["session_best"] is None


def test_published_timing_must_match_observed_completion_for_this_identity():
    facts = LiveEngineerFacts()
    live(facts)
    live(facts, ordinal=6, number=2, last_ms=101_000)
    history(facts)
    assert view(facts)["laps"] == []
    assert view(facts)["session_best"] is None


def test_stale_history_keeps_numbered_timings_but_cannot_claim_current_best():
    facts = LiveEngineerFacts()
    live(facts)
    live(facts, ordinal=6, number=2)
    history(facts)
    live(facts, ordinal=9, number=2)
    assert view(facts)["laps"][0]["lap_time_ms"] == 100_000
    assert view(facts)["session_best_reason"] == "session_history_stale"
    qualified = qualify_snapshot(facts.snapshot(), now_ns=12_000_000_000, stale_after_s=3)
    assert qualified["drivers"] == []


def test_full_field_history_payload_is_bounded_below_radio_response_limit():
    facts = LiveEngineerFacts()
    live(facts, cars=24, fmt=2026)
    for number in range(2, 102):
        live(facts, ordinal=number + 4, number=number, cars=24, fmt=2026)
    for car in range(24):
        history(facts, ordinal=105, car=car, times=(100_000,) * 100, fmt=2026)
    assert len(view(facts)["laps"]) == 100
    assert all(len(row["reported_history"]["laps"]) == 100 for row in facts.snapshot()["drivers"])
    assert len(json.dumps(facts.snapshot()).encode()) < 1_048_576


def test_pipeline_exposes_opponent_history_without_changing_player_enrichment():
    pipeline = TelemetryPipeline(detail_profile="demand_v1")
    _pipeline_frame(pipeline, 1)
    for number in (2, 3):
        packet = _lap_packet(number)
        body = bytearray(packet.body)
        for car in range(22):
            values = list(RECORD.unpack_from(body, car * RECORD.size))
            values[0] = 100_000
            values[14] = number
            RECORD.pack_into(body, car * RECORD.size, *values)
        lap_packet = replace(packet, body=bytes(body))
        payload = bytearray(_body(car_index=1, laps=((110_000, 30_000, 0, 40_000, 0, 40_000, 0, 15),
                                                    (100_000, 30_000, 0, 35_000, 0, 35_000, 0, 15))))
        payload[3] = 2
        history_packet = PacketDecoder().decode(make_datagram(
            packet_id=11, session_uid=SESSION, frame=number, session_time=number / 60,
            player_car_index=0, body=bytes(payload)))
        history_packet = replace(history_packet, received_monotonic_ns=lap_packet.received_monotonic_ns)
        pipeline._process_frames((PacketFrame(SESSION, number, (lap_packet, history_packet)),))
    result = view(pipeline.engineer_facts, 1)
    assert result["laps"][0]["lap_number"] == 2
    assert result["session_best"] == {"lap_number": 2, "lap_time_ms": 100_000}
    assert pipeline.session_history_non_player_packets == 2
    assert pipeline.drain_session_history() == ()
