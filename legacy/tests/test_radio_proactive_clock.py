import pytest

from f1_engineer.radio.proactive_selection import parse_selection_context, selection_context
from f1_engineer.radio.scheduler import RadioMessage
from tests.test_radio_proactive import automatic_live, closing, step
from tests.test_radio_tasks import finish_lap
from tests.test_radio_target_pace import finish, pace_live


def observe(controller, value, index, *, gap=None, poll=True):
    value["events"] = []
    value["received_monotonic_ns"] += 1_000_000_000
    if gap is not None:
        value["drivers"][2]["lap"]["delta_to_car_in_front_ms"] = gap
    controller.feed(value, now=10 + index)
    if poll:
        controller.poll_proactive(now=10 + index)


def window(controller, value, *, start=1, end=30, poll=True):
    for index in range(start, end + 1):
        observe(controller, value, index, gap=5000 - index * 30, poll=poll)


def rolling_live():
    controller, radio, events, value = automatic_live()
    value["drivers"][2]["lap"]["delta_to_car_in_front_ms"] = 5000
    controller.proactive.clear()
    controller.feed(value, now=10)
    return controller, radio, events, value


@pytest.mark.parametrize("kind", ["answer", "proactive"])
def test_busy_speech_defers_development_until_playback_finishes(kind):
    controller, radio, events, value = automatic_live()
    step(controller, radio, value, 0, behind=4000)
    step(controller, radio, value, 1, behind=3600)
    radio.cancel()
    radio.submit(RadioMessage("in-progress", radio.scope, "Lap time.", kind, 30), now=12)
    radio.tick(now=12)
    for row, offset in zip(value["drivers"], (200, 300, 100)):
        row["lap"].update(current_lap_number=6, total_distance_m=25000 + offset)
    value["drivers"][2]["lap"]["delta_to_car_in_front_ms"] = 3200
    finish_lap(controller, value, cursor=3, number=6, now=13)
    assert radio.active.key == "in-progress" and not radio.pending
    assert controller.status()["retained_developments"] == 1
    assert not controller.proactive_policy.started
    assert any(event.get("reason") == "radio_busy" for event in events)
    radio.playback.playing = False
    controller.feed(value, now=14)
    radio.tick(now=14)
    controller.poll_proactive(now=14.05)
    radio.tick(now=14.05)
    assert radio.active.owner == "automatic_gaps"
    assert "car behind is closing" in radio.active.text
    assert not any(call[0] == "cancel" for call in radio.playback.calls)
    assert controller.status()["retained_developments"] == 0
    assert len(controller.proactive_policy.started) == 1


def test_retained_development_replaces_an_unspoken_default_lap():
    controller, radio, events, value = automatic_live()
    closing(controller, radio, value)
    radio.cancel()
    radio.submit(RadioMessage("lap:late", radio.scope, "Routine lap.", "proactive", 25,
                              owner="lap_time"), now=13)
    controller.poll_proactive(now=13.1)
    assert len(radio.pending) == 1 and radio.pending[0].owner == "automatic_gaps"
    assert any(event.get("reason") == "replaced_by_development" for event in events)


def test_workload_guard_can_retry_without_consuming_announcement():
    controller, radio, events, value = automatic_live()
    closing(controller, radio, value)
    original_observation = controller._aligned_observation
    controller._aligned_observation = lambda player, now: ({"value": {"brake": 0.5}}, 0.1)
    radio.tick(now=13.1)
    assert not radio.active and not controller.proactive_policy.started
    controller.poll_proactive(now=13.2)
    assert not radio.pending
    controller._aligned_observation = original_observation
    controller.feed(value, now=14)
    controller.poll_proactive(now=14)
    radio.tick(now=14)
    assert radio.active.owner == "automatic_gaps"
    assert len(controller.proactive_policy.started) == 1


@pytest.mark.parametrize("change", ["identity", "pit", "scope", "quiet", "listening", "outage", "mode"])
def test_waiting_development_is_discarded_when_context_changes(change):
    controller, radio, events, value = automatic_live()
    closing(controller, radio, value)
    radio.cancel()
    if change == "identity":
        value["drivers"][2]["identity"]["tenure_ordinal"] += 1
    elif change == "pit":
        value["drivers"][2]["lap"]["pit_status_id"] = 1
    elif change == "scope":
        value["scope"]["epoch"] += 1
    elif change == "quiet":
        radio.quiet()
    elif change == "listening":
        radio.suspended = True
    elif change == "outage":
        controller.feed({"status": "unavailable", "reason": "telemetry_missing"}, now=13.1)
    else:
        controller.set_proactive_mode("off")
    if change != "outage":
        controller.feed(value, now=13.1)
    controller.poll_proactive(now=13.1)
    assert not radio.pending and not controller._developments
    assert not controller.proactive_policy.started
    assert any(event["event"] == "radio_proactive_discarded" for event in events)


def test_pending_notice_expires_despite_continuing_fresh_telemetry():
    controller, radio, events, value = automatic_live()
    closing(controller, radio, value)
    radio.cancel()
    radio.submit(RadioMessage("long-answer", radio.scope, "Answer.", "answer", 60), now=13)
    radio.tick(now=13)
    for index in range(4, 25):
        observe(controller, value, index)
    assert not controller._developments and not radio.pending
    assert any(event.get("reason") == "expired" for event in events)


def test_waiting_notice_uses_live_freshness_not_the_original_three_second_deadline():
    controller, radio, events, value = automatic_live()
    closing(controller, radio, value)
    radio.cancel()
    radio.submit(RadioMessage("long-answer", radio.scope, "Answer.", "answer", 60), now=13)
    radio.tick(now=13)
    for index in range(4, 9):
        observe(controller, value, index)
    radio.playback.playing = False
    radio.tick(now=18)
    controller.poll_proactive(now=18.05)
    radio.tick(now=18.05)
    assert radio.active.owner == "automatic_gaps"


def test_periodic_clock_detects_a_mid_lap_development_without_new_lap_events():
    controller, radio, events, value = rolling_live()
    window(controller, value)
    assert radio.pending[0].owner == "automatic_gaps"
    assert "over 30 seconds" in radio.pending[0].text
    assert not value["events"] and value["event_cursor"] == 0
    evaluations = [event for event in events if event["event"] == "radio_proactive_evaluation"]
    assert len(evaluations) == 2 and evaluations[-1]["candidates"] == 1
    radio.tick(now=40)
    window(controller, value, start=31, end=45)
    assert len([event for event in events if event["event"] == "radio_proactive_started"]) == 1


@pytest.mark.parametrize("pattern", ["stable", "oscillating", "outage", "identity", "pit", "lapped", "practice"])
def test_short_windows_require_consistent_comparable_racing_evidence(pattern):
    controller, radio, events, value = rolling_live()
    for index in range(1, 31):
        gap = 5000 if pattern == "stable" else 5000 + (300 if index % 2 else 0) if pattern == "oscillating" else 5000 - index * 30
        if index == 15:
            if pattern == "outage":
                controller.feed({"status": "unavailable", "reason": "telemetry_missing"}, now=24.5)
            elif pattern == "identity":
                value["drivers"][2]["identity"]["tenure_ordinal"] += 1
            elif pattern == "pit":
                value["drivers"][2]["lap"]["pit_status_id"] = 1
            elif pattern == "lapped":
                value["drivers"][2]["lap"]["total_distance_m"] -= 5000
            elif pattern == "practice":
                value["context"]["value"]["session_type"] = "practice_1"
        observe(controller, value, index, gap=gap)
    assert not any(message.owner == "automatic_gaps" for message in radio.pending)


def test_periodic_model_selection_only_runs_for_a_new_candidate():
    controller, radio, events, value = automatic_live()
    calls = []
    class Selector:
        thread = None
        generation = 1
        last_decision = None
        def submit(self, notices, event, **kwargs):
            calls.append(notices)
            return True
        def poll(self, **kwargs):
            return None
        def cancel(self):
            pass
    controller.selector = Selector()
    controller.set_proactive_mode("model")
    value["drivers"][2]["lap"]["delta_to_car_in_front_ms"] = 5000
    controller.feed(value, now=10)
    window(controller, value)
    assert len(calls) == 1 and not radio.pending
    controller.selector.last_decision = (1, None)
    observe(controller, value, 31, gap=4070)
    assert not controller._developments
    window(controller, value, start=32, end=45)
    assert len(calls) == 1 and not radio.pending


def test_rolling_gap_selection_preserves_time_basis_and_rejects_forged_windows():
    controller, radio, events, value = rolling_live()
    window(controller, value)
    notice = controller._developments["gap", "behind"]["notice"]
    context = selection_context("race", (notice,))
    assert context["candidates"][0]["sample_times_ms"] == [0, 15000, 30000]
    assert context["candidates"][0]["laps"][0][0] == context["candidates"][0]["laps"][-1][0]
    for invalid in ([0, 100, 30000], [0, 15000, 30001], [False, 15000, 30000], None):
        context["candidates"][0]["sample_times_ms"] = invalid
        with pytest.raises(ValueError, match="proactive_context_invalid"):
            parse_selection_context(context)


def test_answer_still_preempts_automatic_speech_but_not_the_other_way_round():
    controller, radio, events, value = automatic_live()
    closing(controller, radio, value)
    radio.tick(now=13)
    radio.submit(RadioMessage("answer:urgent", radio.scope, "Requested answer.", "answer", 30), now=13.1)
    radio.tick(now=13.1)
    assert radio.active.kind == "answer"
    assert any(event.get("reason") == "interrupted_by_answer" for event in events)


def test_pace_development_survives_a_spoken_answer_and_combines_the_lap_time():
    controller, radio, events, value = pace_live()
    value["context"] = {"status": "available", "value": {
        "session_uid": value["scope"]["session_uid"], "packet_format": value["scope"]["packet_format"],
        "session_type": "race", "track_length_m": 5000}}
    for number, elapsed in ((4, 99000), (5, 100000), (6, 100300)):
        finish(controller, radio, value, number, elapsed)
    radio.cancel()
    radio.submit(RadioMessage("answer:comparison", radio.scope, "Your comparison.", "answer", 30), now=13)
    radio.tick(now=13)
    value["received_monotonic_ns"] += 1_000_000_000
    value["event_cursor"] = 4
    value["drivers"][0]["lap"].update(current_lap_number=7, last_lap_time_ms=100700)
    value["events"] = [{**value["events"][0], "id": 4, "lap_number": 6, "lap_time_ms": 100700,
                        "received_monotonic_ns": value["received_monotonic_ns"]}]
    controller.feed(value, now=14)
    assert ("pace", "self") in controller._developments and not radio.pending
    radio.playback.playing = False
    controller.feed(value, now=15)
    radio.tick(now=15)
    controller.poll_proactive(now=15.05)
    radio.tick(now=15.05)
    assert radio.active.key.startswith("automatic-pace:")
    assert "Lap 6: 1 minute 40.700 seconds." in radio.active.text and "getting slower" in radio.active.text
    assert len(controller.proactive_policy.started) == 1


@pytest.mark.parametrize("mode", ["quiet", "listening"])
def test_periodic_developments_detected_while_muted_do_not_replay_on_resume(mode):
    controller, radio, events, value = rolling_live()
    if mode == "quiet":
        radio.quiet()
    else:
        radio.suspended = True
    window(controller, value)
    assert not controller._developments and not radio.pending
    radio.muted = radio.suspended = False
    window(controller, value, start=31, end=45)
    assert not controller._developments and not radio.pending
    assert any(event.get("reason") == mode for event in events)


def test_replacing_a_notice_removes_only_its_queued_copy():
    controller, radio, events, value = automatic_live()
    closing(controller, radio, value)
    old_key = radio.pending[0].key
    new_notices = step(controller, radio, value, 3, behind=1800)
    assert len(new_notices) == 1 and new_notices[0].key != old_key
    assert len(controller._developments) == 1
    assert any(event.get("reason") == "replaced_by_newer_development" for event in events)


def test_development_priority_never_interrupts_an_active_routine_lap():
    controller, radio, events, value = automatic_live()
    radio.submit(RadioMessage("lap:active", radio.scope, "Lap time.", "proactive", 30,
                              owner="lap_time"), now=10)
    radio.tick(now=10)
    radio.submit(RadioMessage("automatic-gap:direct", radio.scope, "Closing.", "proactive", 30,
                              owner="automatic_gaps"), now=10.1)
    assert radio.active.key == "lap:active" and radio.playback.busy()
    assert radio.pending[0].key == "automatic-gap:direct"


def test_short_window_evaluations_do_not_consume_lap_boundary_hysteresis():
    controller, radio, events, value = automatic_live()
    closing(controller, radio, value)
    radio.tick(now=13)
    for index in range(4, 47):
        observe(controller, value, index, gap=3200)
    state = controller.proactive.states["behind"]
    assert state.basis == "laps" and state.trend == "closing"
    assert state.neutral_boundaries == 0 and state.window_neutral >= 2


def test_requested_lap_report_keeps_priority_without_discarding_the_development():
    controller, radio, events, value = automatic_live()
    controller.handle_request("tell me at the end of each lap my position", now=10)
    radio.cancel()
    assert not closing(controller, radio, value)
    assert controller._developments and radio.pending[0].owner == "lap_reports"
    radio.tick(now=13)
    radio.playback.playing = False
    controller.feed(value, now=14)
    radio.tick(now=14)
    controller.poll_proactive(now=14.05)
    radio.tick(now=14.05)
    assert radio.active.owner == "automatic_gaps"


def test_retention_deadline_limits_dispatch_without_truncating_started_speech():
    controller, radio, events, value = automatic_live()
    closing(controller, radio, value)
    radio.cancel()
    radio.submit(RadioMessage("answer:long", radio.scope, "Answer.", "answer", 60), now=13)
    radio.tick(now=13)
    for index in range(4, 23):
        observe(controller, value, index)
    radio.playback.playing = False
    radio.tick(now=32)
    controller.poll_proactive(now=32.1)
    radio.tick(now=32.1)
    assert radio.active.owner == "automatic_gaps" and radio.active.dispatch_before < 33
    radio.tick(now=33.1)
    assert radio.active.owner == "automatic_gaps" and radio.playback.busy()
