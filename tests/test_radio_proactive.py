from dataclasses import replace
from types import SimpleNamespace

import pytest

from f1_engineer.radio.pace import PaceReference
from f1_engineer.radio.proactive import GapNotice, ProactivePaceTracker, ProactiveSpeechPolicy
from f1_engineer.radio.scheduler import RadioMessage
from tests.test_radio_gap_changes import gap_live
from tests.test_radio_tasks import finish_lap
from tests.test_standalone_radio import scheduler


def automatic_live():
    controller, radio, events, value = gap_live()
    value["context"] = {"status": "available", "value": {
        "session_uid": value["scope"]["session_uid"],
        "packet_format": value["scope"]["packet_format"],
        "session_type": "race", "track_length_m": 5000}}
    for row, offset in zip(value["drivers"], (200, 300, 100)):
        row["lap"]["total_distance_m"] = 10_000 + offset
    controller.feed(value, now=10)
    return controller, radio, events, value


def step(controller, radio, value, index, *, ahead=8000, behind=8000):
    radio.cancel()
    number = index + 4
    for row, offset in zip(value["drivers"], (200, 300, 100)):
        row["lap"].update(current_lap_number=number, total_distance_m=(number - 1) * 5000 + offset)
    value["drivers"][0]["lap"]["delta_to_car_in_front_ms"] = ahead
    value["drivers"][2]["lap"]["delta_to_car_in_front_ms"] = behind
    finish_lap(controller, value, cursor=index + 1, number=number, now=11 + index)
    return [message for message in radio.pending if message.owner == "automatic_gaps"]


def closing(controller, radio, value):
    assert not step(controller, radio, value, 0, behind=4000)
    assert not step(controller, radio, value, 1, behind=3600)
    return step(controller, radio, value, 2, behind=3200)


def test_automatic_closing_requires_three_boundaries_and_replaces_default_lap():
    controller, radio, events, value = automatic_live()
    notices = closing(controller, radio, value)
    assert len(notices) == len(radio.pending) == 1
    assert notices[0].text == "The car behind is closing. Gap 3.2 seconds, down 0.8 over two laps."
    assert not controller.tasks.active()
    candidate = next(event for event in events if event["event"] == "radio_proactive_candidate")
    assert candidate["change_ms"] == -800 and len(candidate["boundaries"]) == 3
    assert controller.proactive.complete(5) == []


def test_unchanged_trend_is_silent_but_near_closing_is_a_new_development():
    controller, radio, events, value = automatic_live()
    assert closing(controller, radio, value)
    assert not step(controller, radio, value, 3, behind=2800)
    assert not step(controller, radio, value, 4, behind=2400)
    assert step(controller, radio, value, 5, behind=1800)


def test_behind_closing_outranks_simultaneous_ahead_closing():
    controller, radio, events, value = automatic_live()
    for index, gap in enumerate((4000, 3600, 3200)):
        notices = step(controller, radio, value, index, ahead=gap, behind=gap)
    assert len(notices) == 1 and "car behind is closing" in notices[0].text
    assert any(event.get("reason") == "higher_ranked_development" for event in events)


def test_crossing_attention_range_can_announce_an_existing_far_away_trend():
    controller, radio, events, value = automatic_live()
    for index, gap in enumerate((12_000, 11_600, 11_200, 10_800)):
        assert not step(controller, radio, value, index, behind=gap)
    assert step(controller, radio, value, 4, behind=9900)


@pytest.mark.parametrize("mode", ["quiet", "listening"])
def test_suppressed_development_does_not_build_a_backlog(mode):
    controller, radio, events, value = automatic_live()
    if mode == "quiet":
        radio.quiet()
    else:
        radio.suspended = True
    assert not closing(controller, radio, value)
    radio.muted = radio.suspended = False
    assert not step(controller, radio, value, 3, behind=2800)
    assert any(event.get("reason") == mode for event in events)


@pytest.mark.parametrize("change", ["identity", "pit", "lapped", "race_context", "scope"])
def test_queued_notice_invalidates_when_its_evidence_changes(change):
    controller, radio, events, value = automatic_live()
    assert closing(controller, radio, value)
    rival = value["drivers"][2]
    if change == "identity":
        rival["identity"]["tenure_ordinal"] += 1
    elif change == "pit":
        rival["lap"]["pit_status_id"] = 1
    elif change == "lapped":
        rival["lap"]["total_distance_m"] -= 5000
    elif change == "race_context":
        value["context"]["value"]["session_type"] = "qualifying_1"
    else:
        value["scope"]["epoch"] += 1
    controller.feed(value, now=13.1)
    assert not any(message.owner == "automatic_gaps" for message in radio.pending)


def test_live_gap_drift_invalidates_queued_notice():
    controller, radio, events, value = automatic_live()
    assert closing(controller, radio, value)
    value["drivers"][2]["lap"]["delta_to_car_in_front_ms"] = 4200
    controller.feed(value, now=13.1)
    assert not radio.pending
    assert any(event.get("reason") == "gap_changed" for event in events)


@pytest.mark.parametrize("change", ["practice", "missing_context", "missing_distance", "unknown_status"])
def test_missing_or_non_racing_evidence_never_generates_an_automatic_call(change):
    controller, radio, events, value = automatic_live()
    if change == "practice":
        value["context"]["value"]["session_type"] = "practice_1"
    elif change == "missing_context":
        value.pop("context")
    elif change == "missing_distance":
        value["context"]["value"].pop("track_length_m")
    else:
        value["drivers"][0]["lap"]["result_status_id"] = None
    assert not closing(controller, radio, value)


def test_observation_outage_requires_new_comparable_boundaries():
    controller, radio, events, value = automatic_live()
    assert closing(controller, radio, value)
    controller.feed({"status": "unavailable", "reason": "telemetry_missing"}, now=13.1)
    assert not radio.pending
    assert not step(controller, radio, value, 3, behind=2800)
    assert not step(controller, radio, value, 4, behind=2400)
    assert not step(controller, radio, value, 5, behind=2000)
    assert step(controller, radio, value, 6, behind=1600)


def test_dispatch_rejects_stale_notice_without_spending_speech_budget():
    controller, radio, events, value = automatic_live()
    assert closing(controller, radio, value)
    radio.tick(now=16)
    assert radio.active is None
    assert not controller.proactive_policy.started


def test_budget_is_recorded_only_when_playback_starts():
    controller, radio, events, value = automatic_live()
    assert closing(controller, radio, value)
    assert not controller.proactive_policy.started
    radio.tick(now=13.1)
    assert list(controller.proactive_policy.started) == [13.1]
    assert any(event["event"] == "radio_proactive_started" for event in events)


def test_explicit_report_takes_precedence_over_automatic_notice():
    controller, radio, events, value = automatic_live()
    controller.handle_request("tell me at the end of each lap my position", now=10)
    radio.cancel()
    assert not closing(controller, radio, value)
    assert any(message.owner == "lap_reports" for message in radio.pending)
    assert any(event.get("reason") == "explicit_report" for event in events)


def test_repeated_source_event_does_not_queue_another_automatic_call():
    controller, radio, events, value = automatic_live()
    assert closing(controller, radio, value)
    key = radio.pending[0].key
    controller.feed(value, now=13.1)
    assert [message.key for message in radio.pending] == [key]


def test_stable_boundaries_rearm_a_resolved_trend():
    controller, radio, events, value = automatic_live()
    assert closing(controller, radio, value)
    radio.tick(now=13)
    controller.proactive_policy.clear()
    for index, gap in enumerate((3200, 3200, 3200, 2800), start=3):
        assert not step(controller, radio, value, index, behind=gap)
    assert step(controller, radio, value, 7, behind=2400)


def test_speech_policy_limits_topics_and_global_chatter():
    policy = ProactiveSpeechPolicy()
    behind = GapNotice("behind", "rival", 1, "closing", 5, 3200, -800, False)
    ahead = replace(behind, role="ahead")
    policy.record_started(behind, now=0)
    assert policy.suppression(ahead, now=10) == "global_cooldown"
    assert policy.suppression(behind, now=31) == "topic_cooldown"
    assert policy.suppression(ahead, now=31) is None
    policy.record_started(ahead, now=31)
    assert policy.suppression(behind, now=91) == "chatter_budget"
    assert policy.suppression(behind, now=121) is None


def test_scheduler_dispatch_guard_drops_obsolete_message_without_start_callback():
    radio, playback, events = scheduler()
    starts = []
    radio.submit(RadioMessage("guarded", "race", "Old gap.", "proactive", 20,
                              dispatch_guard=lambda now: "development_superseded",
                              on_started=starts.append), now=0)
    radio.tick(now=1)
    assert not playback.calls and not starts
    assert events[-1]["reason"] == "development_superseded"


def test_explicit_repeat_does_not_run_automatic_policy_hooks_again():
    radio, playback, events = scheduler()
    starts = []
    radio.submit(RadioMessage("guarded", "race", "Gap three seconds.", "proactive", 20,
                              dispatch_guard=lambda now: None, on_started=starts.append), now=0)
    radio.tick(now=0)
    assert radio.repeat(now=1)
    radio.tick(now=1)
    assert starts == [0] and radio.active.kind == "answer"


def test_pace_changes_are_measured_not_diagnosed_and_share_the_speech_budget():
    tracker = ProactivePaceTracker()
    pace = SimpleNamespace(eligible=True, compound=16)
    tracker.observe(pace, eligible=True, now=10, age_s=0.1)
    records = [(number, PaceReference(number, elapsed, 16))
               for number, elapsed in ((4, 100000), (5, 100300), (6, 100700))]
    notices = tracker.complete(records)
    assert len(notices) == 1
    assert "getting slower" in notices[0].text and "conditions may differ" in notices[0].text
    assert tracker.validate(notices[0], now=10.1) is None
    assert not tracker.complete(records)
    policy = ProactiveSpeechPolicy()
    policy.record_started(notices[0], now=10)
    gap = GapNotice("behind", "rival", 1, "closing", 6, 3200, -800, False)
    assert policy.suppression(gap, now=11) == "global_cooldown"
    pace.compound = 17
    tracker.observe(pace, eligible=True, now=10.2, age_s=0.1)
    assert tracker.validate(notices[0], now=10.3) == "development_superseded"


def test_automatic_pace_uses_only_fully_observed_valid_laps():
    from tests.test_radio_target_pace import finish, pace_live

    controller, radio, events, value = pace_live()
    value["context"] = {"status": "available", "value": {
        "session_uid": value["scope"]["session_uid"], "packet_format": value["scope"]["packet_format"],
        "session_type": "race", "track_length_m": 5000}}
    for number, elapsed in ((4, 99000), (5, 100000), (6, 100300), (7, 100700)):
        finish(controller, radio, value, number, elapsed)
    assert radio.pending[-1].key.startswith("automatic-pace:")
    assert "getting slower" in radio.pending[-1].text
    assert controller.status()["automatic_pace"]["comparable_laps"] == 3


def test_disabling_proactive_clears_candidates_but_keeps_explicit_answers():
    controller, radio, events, value = automatic_live()
    assert closing(controller, radio, value)
    controller.set_proactive_mode("off")
    assert not radio.pending and not controller.proactive.states
    controller.answer_last_lap(now=13.1)
    assert radio.pending[-1].kind == "answer"
    assert not step(controller, radio, value, 3, behind=2800)
    assert controller.status()["proactive_mode"] == "off"


@pytest.mark.parametrize("brake,steering", [(0.3, 0.0), (0.0, -0.2)])
def test_known_driver_workload_prevents_automatic_dispatch(brake, steering):
    controller, radio, events, value = automatic_live()
    assert closing(controller, radio, value)
    controller._aligned_observation = lambda player, now: ({"value": {"brake": brake, "steering": steering}}, 0.1)
    radio.tick(now=13.1)
    assert radio.active is None
    assert events[-1]["reason"] == "driver_workload"
