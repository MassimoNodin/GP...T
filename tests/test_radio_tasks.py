from copy import deepcopy
from dataclasses import replace

import pytest

from f1_engineer.radio.command import submit_question
from f1_engineer.radio.listening import VoiceInput
from f1_engineer.radio.questions import plan_question
from f1_engineer.radio.tasks import LapReportRegistry, parse_task_request
from tests.test_live_driver_names import named_live
from tests.test_radio_questions import live


def finish_lap(controller, value, *, cursor=1, number=4, now=11, elapsed=99000, event_elapsed=99000):
    value["drivers"][0]["lap"].update(current_lap_number=number, last_lap_time_ms=elapsed)
    value["event_cursor"] = cursor
    value["received_monotonic_ns"] += 1_000_000_000
    value["events"] = [{"id": cursor, "scope": deepcopy(value["scope"]),
        "driver": deepcopy(value["drivers"][0]["identity"]), "kind": "lap_completed",
        "qualification": "observed_lap_transition", "lap_number": number - 1,
        "lap_time_ms": event_elapsed, "received_monotonic_ns": value["received_monotonic_ns"]}]
    controller.feed(value, now=now)


def install(controller, radio, request="tell me at the end of each lap my last lap and position"):
    controller.handle_request(request, now=10)
    radio.cancel()


@pytest.mark.parametrize("text,laps", [
    ("tell me at the end of each lap my last lap", None),
    ("tell me at the end of the lap my last lap", None),
    ("tell me at the end of lap my last lap", None),
    ("report at end of every lap my last lap", None),
    ("tell me at the end of next lap my last lap", 1),
    ("tell me at the end of each lap my last lap for the next 3 laps", 3),
])
def test_bounded_report_intents(text, laps):
    action = parse_task_request(text)
    assert action.kind == "create" and action.question == "my last lap" and action.laps == laps


@pytest.mark.parametrize("text,kind,identifier", [
    ("What are you tracking?", "list", None), ("list tasks", "list", None),
    ("cancel all tracking", "cancel_all", None), ("cancel task 2", "cancel", 2),
    ("update task 2 tell me at the end of each lap my position", "update", 2),
])
def test_closed_task_controls(text, kind, identifier):
    action = parse_task_request(text)
    assert action.kind == kind and action.task_id == identifier


@pytest.mark.parametrize("text", ["quiet", "quit", "cancel all tracking and quit",
    "cancel task 0", "update task 1 invent a strategy", "track braking next lap", "x" * 1025])
def test_unrelated_controls_and_unbounded_input_are_not_task_actions(text):
    assert parse_task_request(text) is None


@pytest.mark.parametrize("text", [
    "tell me at end of each lap my last lap for next 21 laps",
    "tell me at end of next lap my last lap for next 3 laps",
])
def test_invalid_duration_refused(text):
    with pytest.raises(ValueError):
        parse_task_request(text)


def test_create_list_report_once_per_event_and_suppress_default_duplicate():
    controller, radio, events, value = named_live()
    install(controller, radio, "tell me at the end of each lap my and Leclerc last lap and position")
    controller.handle_request("what are you tracking", now=10)
    assert "Task 1" in radio.pending[-1].text and "leclerc" in radio.pending[-1].text
    assert "until cancelled" in radio.pending[-1].text
    radio.cancel()
    finish_lap(controller, value)
    reports = [message for message in radio.pending if message.owner == "lap_reports"]
    assert len(reports) == 1 and len(radio.pending) == 1
    assert "lap 3 99.000 seconds" in reports[0].text
    assert "Charles Leclerc: lap 2 101.000 seconds" in reports[0].text
    controller.feed(value, now=11.1)
    assert len(radio.pending) == 1


def test_initial_history_is_not_reported_and_creation_uses_current_cursor():
    controller, radio, events, value = live()
    finish_lap(controller, value, cursor=5)
    install(controller, radio)
    controller.feed(value, now=11)
    assert not radio.pending
    finish_lap(controller, value, cursor=6, number=5, now=12)
    assert len(radio.pending) == 1 and "Lap 4 reports" in radio.pending[0].text


def test_duplicate_event_after_one_shot_expiry_does_not_add_default_lap_call():
    controller, radio, events, value = live()
    install(controller, radio, "report at end of next lap my position")
    original_feed = controller.feed
    def duplicate_feed(snapshot, **kwargs):
        snapshot["events"].append(deepcopy(snapshot["events"][0]))
        original_feed(snapshot, **kwargs)
    controller.feed = duplicate_feed
    finish_lap(controller, value)
    assert len(radio.pending) == 1 and radio.pending[0].owner == "lap_reports"


def test_expiry_without_an_event_removes_obsolete_owned_output():
    controller, radio, events, value = live()
    install(controller, radio, "report at end of each lap my position for next 2 laps")
    finish_lap(controller, value)
    radio.tick(now=11)
    value["drivers"][0]["lap"]["current_lap_number"] = 5
    value["events"] = []
    controller.feed(value, now=12)
    assert radio.active is None and controller.status()["tasks"][0]["state"] == "completed"


def test_named_reference_fixed_but_role_reference_refreshes():
    controller, radio, events, value = named_live()
    install(controller, radio, "tell me at the end of each lap Leclerc and leader last lap")
    value["drivers"][1]["lap"]["car_position"] = 3
    value["drivers"][2]["lap"]["car_position"] = 1
    finish_lap(controller, value)
    assert "Charles Leclerc" in radio.pending[0].text and "Lando Norris" in radio.pending[0].text
    value["drivers"][1]["identity"]["tenure_ordinal"] = 2
    value["drivers"][1]["participant"]["identity"] = deepcopy(value["drivers"][1]["identity"])
    radio.cancel()
    finish_lap(controller, value, cursor=2, number=5, now=12)
    assert "leclerc: driver unavailable" in radio.pending[0].text
    assert "Lando Norris" in radio.pending[0].text


def test_next_lap_report_completes_once_and_new_task_does_not_reuse_id():
    controller, radio, events, value = live()
    install(controller, radio, "tell me at the end of next lap my last lap")
    assert controller.status()["tasks"][0]["until_lap"] == 3
    finish_lap(controller, value)
    assert controller.status()["tasks"][0]["state"] == "completed"
    assert radio.pending[0].owner == "lap_reports"
    radio.cancel()
    finish_lap(controller, value, cursor=2, number=5, now=12)
    assert not any(message.owner == "lap_reports" for message in radio.pending)
    controller.handle_request("tell me at the end of each lap my position", now=12)
    assert controller.status()["tasks"][0]["task_id"] == 2


def test_finite_report_expires_on_lap_count_and_quiet_does_not_create_backlog():
    controller, radio, events, value = live()
    install(controller, radio, "tell me at end of each lap my position for next 2 laps")
    radio.quiet()
    finish_lap(controller, value)
    assert not radio.pending and controller.status()["tasks"][0]["state"] == "active"
    finish_lap(controller, value, cursor=2, number=5, now=12)
    assert not radio.pending and controller.status()["tasks"][0]["state"] == "completed"
    radio.muted = False
    controller.feed(value, now=12.1)
    assert not radio.pending


def test_listening_suppresses_call_but_consumes_one_shot():
    controller, radio, events, value = live()
    install(controller, radio, "report at end of next lap my position")
    radio.suspended = True
    finish_lap(controller, value)
    assert not radio.pending and controller.status()["tasks"][0]["state"] == "completed"
    radio.suspended = False
    controller.feed(value, now=11.1)
    assert not radio.pending


@pytest.mark.parametrize("boundary", ["scope", "rewind", "skip", "history_gap", "stale", "driver_event"])
def test_scope_discontinuity_and_loss_clear_tasks_and_owned_output(boundary):
    controller, radio, events, value = live()
    install(controller, radio)
    finish_lap(controller, value)
    radio.tick(now=11)
    assert radio.active.owner == "lap_reports"
    if boundary == "stale":
        controller.check_freshness(now=15)
    else:
        if boundary == "scope":
            value["scope"]["epoch"] += 1
        elif boundary == "rewind":
            value["drivers"][0]["lap"]["current_lap_number"] = 2
        elif boundary == "skip":
            value["drivers"][0]["lap"]["current_lap_number"] = 6
        elif boundary == "history_gap":
            value.update(event_floor=3, event_cursor=4, events=[])
        else:
            value["event_cursor"] = 2
            value["events"][0].update(id=2, kind="driver_discontinuity")
        controller.feed(value, now=12)
    assert controller.status()["tasks"] == []
    assert not radio.active and not any(message.owner == "lap_reports" for message in radio.pending)


@pytest.mark.parametrize("number", [0, True, "4", None])
def test_unknown_or_malformed_player_lap_clears_registered_reports(number):
    controller, radio, events, value = live()
    install(controller, radio)
    value["drivers"][0]["lap"]["current_lap_number"] = number
    controller.feed(value, now=11)
    assert controller.status()["tasks"] == []


def test_update_and_cancel_invalidate_owned_pending_active_and_repeat_messages():
    controller, radio, events, value = live()
    install(controller, radio)
    finish_lap(controller, value)
    radio.tick(now=11)
    controller.handle_request("update task 1 tell me at end of each lap my gap to leader", now=11)
    assert radio.active is None and controller.status()["tasks"][0]["version"] == 2
    assert not any(message.owner == "lap_reports" for message in radio.pending)
    radio.cancel()
    finish_lap(controller, value, cursor=2, number=5, now=12)
    radio.tick(now=12)
    radio.playback.playing = False
    radio.tick(now=12.1)
    assert radio.repeat(now=12.2)
    controller.handle_request("cancel task 1", now=12.2)
    assert not any(message.owner == "lap_reports" for message in radio.pending)
    assert not radio.repeat(now=12.3) and controller.status()["tasks"] == []


def test_registry_capacity_budget_duplicate_and_failed_update_preserve_state():
    controller, radio, events, value = live()
    install(controller, radio)
    controller.handle_request("tell me at end of each lap my last lap and position", now=10)
    assert len(controller.status()["tasks"]) == 1
    controller.handle_request("tell me at end of each lap leader position", now=10)
    controller.handle_request("tell me at end of each lap car behind position", now=10)
    assert len(controller.status()["tasks"]) == 3
    controller.handle_request("tell me at end of each lap my last lap and position", now=10)
    assert "Task 1 active" in radio.pending[-1].text
    original = deepcopy(controller.status()["tasks"])
    controller.handle_request("update task 1 tell me at end of each lap my and leader and car behind last lap current lap position", now=10)
    assert controller.status()["tasks"] == original
    assert "nine total" in radio.pending[-1].text
    controller.handle_request("tell me at end of each lap P3 position", now=10)
    assert controller.status()["tasks"] == original


def test_ambiguous_named_creation_unsupported_payload_and_missing_task_do_not_mutate():
    controller, radio, events, value = named_live()
    value["drivers"][2]["participant"]["value"]["name"] = "Arthur Leclerc"
    controller.feed(value, now=10)
    controller.handle_request("tell me at end of each lap Leclerc last lap", now=10)
    assert not controller.status()["tasks"] and "ambiguous" in radio.pending[-1].text
    controller.handle_request("tell me at end of each lap my braking points", now=10)
    assert not controller.status()["tasks"]
    controller.handle_request("update task 99 tell me at end of each lap my position", now=10)
    assert "does not exist" in radio.pending[-1].text


def test_partial_missing_own_lap_time_still_reports_available_position():
    controller, radio, events, value = live()
    install(controller, radio)
    finish_lap(controller, value, elapsed=0, event_elapsed=None)
    assert "last lap unavailable" in radio.pending[0].text and "position 2" in radio.pending[0].text


def test_report_requires_current_lap_and_matching_completed_time():
    controller, radio, events, value = live()
    install(controller, radio)
    finish_lap(controller, value, elapsed=99999, event_elapsed=99000)
    assert not any(message.owner == "lap_reports" for message in radio.pending)


def test_stale_task_report_cannot_start_or_repeat():
    controller, radio, events, value = live()
    install(controller, radio)
    finish_lap(controller, value)
    assert radio.pending[0].dispatch_before == pytest.approx(13.9)
    radio.tick(now=14)
    assert not radio.active and not radio.pending and not radio.repeat(now=14)


def test_combined_reports_are_one_message_and_changes_remove_whole_bundle():
    controller, radio, events, value = live()
    install(controller, radio, "tell me at end of each lap my position")
    controller.handle_request("tell me at end of each lap leader position", now=10)
    radio.cancel()
    finish_lap(controller, value)
    assert len(radio.pending) == 1 and "Task 1" in radio.pending[0].text and "Task 2" in radio.pending[0].text
    controller.handle_request("cancel task 1", now=11)
    assert [task["task_id"] for task in controller.status()["tasks"]] == [2]
    assert not any(message.owner == "lap_reports" for message in radio.pending)


def test_registry_view_copies_and_reference_lengths_are_checked():
    registry = LapReportRegistry()
    plan = plan_question("my position")
    with pytest.raises(ValueError):
        registry.install(plan, (), cursor=0, lap=3, laps=None)
    registry.install(plan, (None,), cursor=0, lap=3, laps=None)
    registry.views()[0]["targets"].append("leader")
    assert registry.views()[0]["targets"] == ["self"]


def test_owned_cancellation_preserves_unrelated_answers():
    controller, radio, events, value = live()
    install(controller, radio)
    finish_lap(controller, value)
    answer = replace(radio.pending[0], key="unrelated", kind="answer", owner=None)
    radio.submit(answer, now=11)
    radio.cancel_owner("lap_reports")
    assert [message.key for message in radio.pending] == ["unrelated"]


def test_explicit_draft_submission_can_create_and_cancel_tasks_but_not_console_controls():
    controller, radio, events, value = live()
    voice = VoiceInput(enabled=False)
    voice._scope = radio.scope
    voice._draft = "tell me at end of each lap my position"
    submit_question("submit", controller, voice, now=10)
    assert len(controller.status()["tasks"]) == 1 and not voice.status()["draft_available"]
    voice._draft = "cancel all tracking"
    submit_question("submit", controller, voice, now=10)
    assert controller.status()["tasks"] == [] and not radio.muted
    voice._draft = "quit"
    submit_question("submit", controller, voice, now=10)
    assert radio.pending[-1].text == "Didn't catch that. Say again."


def test_no_creation_without_fresh_telemetry_and_task_controls_dont_poison_conversation():
    controller, radio, events, value = live()
    controller.answer_question("leader last lap", now=10)
    install(controller, radio)
    assert controller.status()["conversation_turns"] == 1
    controller.handle_request("tell me at end of each lap my position", now=14)
    assert controller.status()["tasks"] == [] and "Fresh player" in radio.pending[-1].text


def test_unavailable_task_notifications_do_not_echo_backend_diagnostics():
    controller, radio, events, value = live()
    install(controller, radio)
    controller.feed({"status": "unavailable", "reason": "Bearer secret diagnostic"}, now=11)
    ended = next(event for event in events if event["event"] == "radio_task_ended")
    assert ended["reason"] == "unavailable"
    assert "secret" not in str(events)
