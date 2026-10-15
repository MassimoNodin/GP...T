from copy import deepcopy
from dataclasses import replace

import pytest

from f1_engineer.driver_names import display_driver_name, driver_name_aliases, driver_name_key
from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.processing.engineer_facts import LiveEngineerFacts
from f1_engineer.radio.questions import plan_question
from f1_engineer.sessions.car_lap_inventory import CarLapInventoryTracker
from f1_engineer.udp.decoder import PacketDecoder
from tests.helpers import make_datagram
from tests.test_car_lap_inventory import _observe, _participants_packet, _SESSION_UID
from tests.test_detail_pipeline import _pipeline_frame
from tests.test_detail_policy import frame, lap
from tests.test_radio_questions import live


def named_packet(ordinal=1, *, names=None, format=2025):
    count = 24 if format == 2026 else 22
    packet = _participants_packet(ordinal, packet_format=format, active_count=count, slot_count=count)
    body = bytearray(packet.body)
    size, offset = (60, 10) if format == 2026 else (57, 7)
    for car, name in (names or {1: "Charles Leclerc", 2: "Lando Norris"}).items():
        encoded = name.encode("utf-8")
        assert len(encoded) <= 32
        start = 1 + car * size + offset
        body[start:start + 32] = encoded.ljust(32, b"\0")
    return PacketDecoder().decode(make_datagram(packet_format=format, packet_id=4,
        session_uid=_SESSION_UID, frame=ordinal, session_time=ordinal / 60, body=bytes(body), sequence=ordinal))


def bound_source(*, names=None, format=2025):
    tracker = CarLapInventoryTracker()
    _observe(tracker, 1, participants=(named_packet(names=names, format=format),),
             packet_format=format, slot_count=24 if format == 2026 else 22)
    ordinals = {_SESSION_UID: 5}
    data = [replace(lap(), current_lap_number=3, last_lap_time_ms=103500) for index in range(3)]
    source = replace(frame(data), session_uid=_SESSION_UID, lifecycle_epoch=0, packet_format=format,
                     verified_tenures=tracker.active_tenures(ordinals))
    return tracker, source, tracker.active_participants(ordinals)


def publish(source, participants):
    facts = LiveEngineerFacts()
    facts.observe(source, frame_identifier=5, session_time_s=5, received_monotonic_ns=5_000_000_000,
                  captured_at_ns=5_000_000_000, participants=participants)
    return facts.snapshot()


def named_live():
    controller, radio, events, value = live()
    for row, name in zip(value["drivers"], ("Player One", "Charles Leclerc", "Lando Norris")):
        row["participant"] = {"status": "available", "reason": None,
            "identity": deepcopy(row["identity"]),
            "value": {"name": name, "driver_id": row["car_index"], "team_id": 2, "race_number": 16}}
    controller.feed(value, now=10)
    return controller, radio, events, value


@pytest.mark.parametrize("format", [2025, 2026])
def test_real_roster_names_reach_facts_with_exact_binding(format):
    tracker, source, participants = bound_source(format=format)
    value = publish(source, participants)
    row = value["drivers"][1]
    assert row["participant"]["status"] == "available"
    assert row["participant"]["value"]["name"] == "Charles Leclerc"
    assert row["participant"]["identity"] == row["identity"]
    assert participants[1].matches(source.verified_tenures[1])


@pytest.mark.parametrize("fault", ["missing", "duplicate", "tenure", "scope", "fingerprint"])
def test_bad_participant_binding_does_not_publish_name_or_break_timing(fault):
    tracker, source, participants = bound_source()
    items = list(participants)
    if fault == "missing":
        items.pop(1)
    elif fault == "duplicate":
        items.append(items[1])
    elif fault == "tenure":
        items[1] = replace(items[1], tenure=replace(items[1].tenure, tenure_ordinal=2))
    elif fault == "scope":
        items[1] = replace(items[1], tenure=replace(items[1].tenure, lifecycle_epoch=2))
    else:
        items[1] = replace(items[1], participant=replace(items[1].participant, name="Different Driver"))
    value = publish(source, tuple(items))
    row = value["drivers"][1]
    assert row["participant"]["value"] is None
    assert row["participant"]["reason"] == "participant_binding_unavailable"
    assert row["lap"]["last_lap_time_ms"] == 103500 and value["status"] == "available"


@pytest.mark.parametrize("name", ["", "   ", "Leclerc\n", "\u202eLeclerc", "Leclerc\u200b"])
def test_unusable_roster_names_are_unavailable(name):
    tracker, source, participants = bound_source(names={1: name})
    row = publish(source, participants)["drivers"][1]
    assert row["participant"]["reason"] == "participant_name_unavailable"
    assert row["participant"]["value"] is None


def test_conflicted_slots_and_interruptions_do_not_retain_names():
    tracker, source, participants = bound_source()
    old = participants[1]
    _observe(tracker, 2, participants=(named_packet(2), named_packet(2, names={1: "Arthur Leclerc", 2: "Lando Norris"})))
    assert all(item.tenure.car_index != 1 for item in tracker.active_participants())
    assert any(item.tenure.car_index == 2 for item in tracker.active_participants())
    _observe(tracker, 3, participants=(named_packet(3, names={1: "Arthur Leclerc"}),))
    new = next(item for item in tracker.active_participants() if item.tenure.car_index == 1)
    assert new.participant.name == "Arthur Leclerc" and not old.matches(new.tenure)
    tracker.interrupt(4, "test_gap")
    assert tracker.active_participants() == ()


def test_unchanged_identity_names_survive_frames_without_new_roster():
    tracker, source, participants = bound_source()
    _observe(tracker, 2)
    current = tracker.active_participants({_SESSION_UID: 5})
    assert current[1].participant.name == "Charles Leclerc"
    assert current[1].tenure.participant_identity_fingerprint == participants[1].tenure.participant_identity_fingerprint


def test_pipeline_publishes_names_without_changing_detail_selection():
    pipeline = TelemetryPipeline(detail_profile="demand_v1")
    _pipeline_frame(pipeline, 1)
    value = pipeline.engineer_facts.snapshot()
    assert value["drivers"][5]["participant"]["value"]["name"] == "Driver 5"
    assert pipeline.observation_rows_created == 2
    assert all(row["participant"]["identity"] == row["identity"] for row in value["drivers"])


@pytest.mark.parametrize("question", ["Leclerc last lap", "CHARLES LECLERC'S last lap",
    '"Charles Leclerc" last lap', '"Charles Leclerc"' + "'s last lap", "Leclerc’s last lap"])
def test_full_names_surnames_case_and_possessives(question):
    controller, radio, events, value = named_live()
    controller.answer_question(question, now=10)
    assert "Charles Leclerc: lap 2 101.000 seconds" in radio.pending[-1].text


def test_multi_named_targets_and_facts():
    controller, radio, events, value = named_live()
    controller.answer_question("my and Leclerc and Norris last lap and position", now=10)
    text = radio.pending[-1].text
    assert "103.500 seconds" in text and "Charles Leclerc" in text and "Lando Norris" in text
    assert "position 1" in text and "position 3" in text


def test_duplicate_surname_requires_clarification_but_full_name_resolves():
    controller, radio, events, value = named_live()
    value["drivers"][2]["participant"]["value"]["name"] = "Arthur Leclerc"
    controller.feed(value, now=11)
    controller.answer_question("Leclerc last lap", now=11)
    assert "ambiguous" in radio.pending[-1].text and "Use a full name" in radio.pending[-1].text
    controller.answer_question("Charles Leclerc last lap", now=11)
    assert "101.000 seconds" in radio.pending[-1].text


def test_named_followup_keeps_identity_not_name_or_position():
    controller, radio, events, value = named_live()
    controller.answer_question("Leclerc last lap", now=10)
    value["drivers"][1]["lap"].update(car_position=3, current_lap_time_ms=45000)
    value["drivers"][2]["lap"]["car_position"] = 1
    controller.feed(value, now=11)
    controller.answer_question("and his current lap?", now=11)
    assert "Previously selected Charles Leclerc" in radio.pending[-1].text
    assert "45.000 seconds" in radio.pending[-1].text
    value["drivers"][1]["identity"]["tenure_ordinal"] = 2
    value["drivers"][1]["participant"]["identity"] = deepcopy(value["drivers"][1]["identity"])
    controller.feed(value, now=12)
    controller.answer_question("and position?", now=12)
    assert "unavailable" in radio.pending[-1].text


@pytest.mark.parametrize("fault", ["old_identity", "status", "name", "metadata"])
def test_radio_ignores_unbound_or_invalid_name_metadata(fault):
    controller, radio, events, value = named_live()
    participant = value["drivers"][1]["participant"]
    if fault == "old_identity":
        participant["identity"]["tenure_ordinal"] = 20
    elif fault == "status":
        participant["status"] = "unavailable"
    elif fault == "name":
        participant["value"]["name"] = "Leclerc\u202e"
    else:
        participant["value"]["driver_id"] = True
    controller.feed(value, now=11)
    controller.answer_question("Leclerc last lap", now=11)
    assert "101.000 seconds" not in radio.pending[-1].text
    controller.answer_question("leader last lap", now=11)
    assert "101.000 seconds" in radio.pending[-1].text


def test_reserved_names_require_quotes_and_numeric_suffixes_do_not_bind():
    controller, radio, events, value = named_live()
    value["drivers"][1]["participant"]["value"]["name"] = "me"
    controller.feed(value, now=11)
    controller.answer_question("my last lap", now=11)
    assert "103.500 seconds" in radio.pending[-1].text
    controller.answer_question('"me" last lap', now=11)
    assert "101.000 seconds" in radio.pending[-1].text
    assert driver_name_aliases("Driver 1") == ("driver 1",)


@pytest.mark.parametrize("question", ["__named_0__ last lap", "__named_999__ last lap",
    "track Leclerc next lap", "Unknown Person last lap"])
def test_unknown_or_unsupported_named_requests_abstain(question):
    controller, radio, events, value = named_live()
    controller.answer_question(question, now=10)
    assert "101.000 seconds" not in radio.pending[-1].text
    assert controller.status()["conversation_turns"] == 0


def test_names_are_literal_not_regex_and_longest_match_wins():
    names = ("dr.v", "charles", "charles leclerc", "james's")
    assert plan_question("DR.V last lap", names=names).targets == ("name:dr.v",)
    assert plan_question("Charles Leclerc last lap", names=names).targets == ("name:charles leclerc",)
    assert plan_question("James's last lap", names=names).targets == ("name:james's",)
    with pytest.raises(ValueError):
        plan_question("DrxV last lap", names=names)


def test_name_normalization_and_display_safety():
    assert display_driver_name("  Charles   Leclerc  ") == "Charles Leclerc"
    assert driver_name_key("Ｌｅｃｌｅｒｃ") == "leclerc"
    assert driver_name_aliases("Sergio Pérez") == ("sergio pérez", "pérez")
    assert display_driver_name("\u202eLeclerc") is None
    assert display_driver_name("😀") is None
    assert display_driver_name("x" * 65) is None


def test_real_roster_to_facts_to_named_radio_answer():
    tracker, source, participants = bound_source()
    value = publish(source, participants)
    value.update(generation="1" * 32, session_id="2" * 32, age_s=0.1)
    controller, radio, events, unused = live()
    controller.feed(value, now=10)
    controller.answer_question("Leclerc last lap", now=10)
    assert "Charles Leclerc: lap 2 103.500 seconds" in radio.pending[-1].text
