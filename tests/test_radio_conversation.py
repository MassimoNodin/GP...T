from copy import deepcopy
from types import SimpleNamespace

import pytest

from f1_engineer.radio.command import dispatch_voice_request, request_listening
from f1_engineer.radio.conversation import ConversationMemory, QuestionContext, TaskContext
from f1_engineer.radio.listening import VoiceInput
from f1_engineer.radio.pace import PaceReference
from f1_engineer.radio.questions import QuestionPlan, plan_question
from f1_engineer.radio.regions import RegionWindow
from f1_engineer.radio.tasks import LapReportRegistry, parse_task_request
from f1_engineer.radio.watches import RaceWatchTracker, WatchState
from tests.test_live_driver_names import named_live
from tests.test_radio_tasks import finish_lap, install


@pytest.mark.parametrize("phrase", ["track that for another two laps", "keep tracking that for another 2 laps!",
                                   "continue tracking that for another two laps", "extend task 1 by 2 laps"])
def test_extension_intents(phrase):
    action = parse_task_request(phrase)
    assert action.kind == "extend" and action.laps == 2


@pytest.mark.parametrize("phrase", ["track that for another 21 laps", "extend task 1 by 99 laps"])
def test_extension_intent_bounds(phrase):
    with pytest.raises(ValueError):
        parse_task_request(phrase)


@pytest.mark.parametrize("previous,expected", [
    (QuestionPlan(("self",), ("last_lap",)), "last_sector2"),
    (QuestionPlan(("name:leclerc",), ("current_lap",)), "current_sector2"),
    (QuestionPlan(("self", "name:leclerc"), ("last_sector1",)), "last_sector2"),
])
def test_sector_only_inherits_exact_targets(previous, expected):
    result = plan_question("Only sector two!", previous)
    assert result.targets == previous.targets and result.metrics == (expected,) and result.inherited_targets


@pytest.mark.parametrize("previous,phrase", [
    (None, "only sector one"),
    (QuestionPlan(("self",), ("position",)), "only sector one"),
    (QuestionPlan(("self",), ("last_lap", "current_lap")), "only sector one"),
    (QuestionPlan(("self",), ("current_lap",)), "only sector three"),
    (QuestionPlan(("self",), ("last_lap",)), "his position"),
    (QuestionPlan(("name:leclerc", "name:hamilton"), ("last_lap",)), "his position"),
])
def test_ambiguous_followups_clarify(previous, phrase):
    with pytest.raises(ValueError):
        plan_question(phrase, previous)


def registry_task(*, laps=3, regional=False):
    registry = LapReportRegistry()
    task = registry.install(QuestionPlan(("self",), ("regional_observations" if regional else "consistency",)),
                            (None,), cursor=7, lap=4, laps=laps,
                            region_window=RegionWindow(100, 200, 5000) if regional else None)
    return registry, task


def test_extension_preserves_consistency_origin_and_fixed_configuration():
    registry, task = registry_task()
    extended = registry.extend(task.task_id, task.version, additional_laps=2, cursor=20, lap=5)
    assert extended.until_lap == 8 and extended.version == 2
    assert extended.first_lap == task.first_lap and extended.created_cursor == task.created_cursor
    assert extended.plan == task.plan and extended.fixed_bindings == task.fixed_bindings


def test_indefinite_extension_becomes_finite_from_current_lap():
    registry, task = registry_task(laps=None)
    extended = registry.extend(task.task_id, task.version, additional_laps=2, cursor=20, lap=9)
    assert extended.until_lap == 10 and extended.first_lap == 4


def test_regional_extension_restarts_next_lap_without_changing_window():
    registry, task = registry_task(regional=True)
    extended = registry.extend(task.task_id, task.version, additional_laps=2, cursor=20, lap=5)
    assert extended.first_lap == 6 and extended.until_lap == 8 and extended.created_cursor == 20
    assert extended.region_window == task.region_window and extended.region_reference == task.region_reference


@pytest.mark.parametrize("changes", [{"task_id": True}, {"version": True}, {"lap": None}, {"lap": True},
                                    {"cursor": False}, {"lap": 256}, {"additional_laps": True},
                                    {"additional_laps": 0}, {"additional_laps": 20}, {"version": 2}, {"lap": 7}])
def test_invalid_extension_is_atomic(changes):
    registry, task = registry_task()
    fields = dict(task_id=task.task_id, version=task.version, additional_laps=2, cursor=10, lap=4)
    fields.update(changes)
    before = registry.views()
    with pytest.raises(ValueError):
        registry.extend(**fields)
    assert registry.views() == before


@pytest.mark.parametrize("end", ["complete", "cancel"])
def test_ended_tasks_cannot_be_revived(end):
    registry, task = registry_task()
    registry.complete_lap(6) if end == "complete" else registry.cancel(task.task_id)
    with pytest.raises(ValueError):
        registry.extend(task.task_id, task.version, additional_laps=2, cursor=10, lap=6)


def test_watch_extension_preserves_boundaries_but_clears_old_notice():
    tracker = RaceWatchTracker()
    state = WatchState(history=[(1, 2)], notice="closing")
    tracker.states[(1, 1)] = state
    tracker.extend_version(1, 1, 2)
    assert tracker.states == {(1, 2): state} and state.history == [(1, 2)] and state.notice is None


def test_memory_is_bounded_and_copies_inputs_and_outputs():
    memory = ConversationMemory()
    task = {"task_id": 1, "version": 1, "state": "active"}
    for index in range(20):
        memory.record("instruction", request=f"request {index}", response="ack", tasks=[task])
    task["version"] = 2
    context = memory.context()
    context[-1]["tasks"][0]["version"] = 3
    assert len(memory.context()) == 16 and memory.context()[0]["request"] == "request 4"
    assert memory.context()[-1]["tasks"][0]["version"] == 1


def test_implicit_task_extension_does_not_change_question_context():
    controller, radio, events, value = named_live()
    controller.handle_request("Leclerc last lap", now=10)
    previous = deepcopy(controller._history)
    install(controller, radio, "coach my consistency for the next 3 laps")
    controller.handle_request("track that for another two laps", now=10)
    assert controller.tasks.active()[0].version == 2 and "extended" in radio.pending[-1].text
    assert controller._history == previous


def test_stale_capture_refuses_extension_after_task_update():
    controller, radio, events, value = named_live()
    install(controller, radio)
    captured = controller.capture_task_context()
    controller.handle_request("update task 1 report at end of each lap my position", now=10)
    radio.cancel()
    before = controller.tasks.views()
    controller.handle_request("extend task 1 by 2 laps", now=10, task_context=captured)
    assert controller.tasks.views() == before and "changed or ended" in radio.pending[-1].text


@pytest.mark.parametrize("context", [None, TaskContext("different scope", (1, 1), ((1, 1),))])
def test_missing_or_cross_session_voice_reference_never_uses_live_task(context):
    controller, radio, events, value = named_live()
    install(controller, radio)
    voice = SimpleNamespace(task_context=context, question_context=None)
    dispatch_voice_request("track that for another two laps", controller, voice, now=10)
    assert controller.tasks.active()[0].version == 1
    assert "No same-session" in radio.pending[-1].text


def test_fixed_driver_replacement_refuses_extension():
    controller, radio, events, value = named_live()
    install(controller, radio, "report at end of each lap Leclerc last lap")
    controller._drivers[1]["identity"]["tenure_ordinal"] += 1
    controller.handle_request("extend task 1 by 2 laps", now=10)
    assert controller.tasks.active()[0].version == 1 and "will not rebind" in radio.pending[-1].text


def test_multitask_report_requires_explicit_task_identifier():
    controller, radio, events, value = named_live()
    install(controller, radio)
    install(controller, radio, "report at end of each lap my position")
    finish_lap(controller, value)
    controller.handle_request("track that for another two laps", now=11)
    assert all(task.version == 1 for task in controller.tasks.active())
    assert "Which active task" in radio.pending[-1].text
    controller.handle_request("extend task 1 by 2 laps", now=11)
    assert controller.tasks.active()[0].version == 2


def test_memory_queries_disclose_computed_historical_result_and_forget():
    controller, radio, events, value = named_live()
    install(controller, radio)
    finish_lap(controller, value)
    controller.handle_request("what did you last report?", now=11)
    assert "Historical computed tracking report, not proof you heard it" in radio.pending[-1].text
    assert "Lap 3 reports" in radio.pending[-1].text
    controller.handle_request("what was my last request?", now=11)
    assert "end of each lap" in radio.pending[-1].text
    controller.forget_conversation()
    assert not controller.conversation.context() and not controller._history
    assert events[-1]["event"] == "radio_conversation_cleared"


def test_scope_loss_clears_memory():
    controller, radio, events, value = named_live()
    install(controller, radio)
    controller.feed({"status": "unavailable", "reason": "no_telemetry"}, now=11)
    assert not controller.conversation.context()


def test_capture_context_is_forwarded_before_cancel():
    calls = []
    context = TaskContext("session", (1, 2), ((1, 2),))
    request_listening(SimpleNamespace(scope="session", capture_repeat_reference=lambda: None,
                                     cancel=lambda: calls.append("cancel")),
                      SimpleNamespace(request=lambda **fields: calls.append(fields)),
                      source="console", task_context=context)
    assert calls[0] == "cancel" and calls[1]["task_context"] is context


def test_second_button_press_does_not_replace_capture_context():
    voice = VoiceInput(transcribe=lambda *args: None)
    context = TaskContext("session", (1, 1), ((1, 1),))
    voice.task_context = context
    voice._worker = SimpleNamespace(is_alive=lambda: True)
    voice._state = "listening"
    voice.request(source="console", scope="session", task_context=TaskContext("session", (2, 1), ((2, 1),)))
    assert voice.task_context is context


def test_target_pace_extension_preserves_immutable_reference_and_tolerance():
    registry = LapReportRegistry()
    reference = PaceReference(2, 100000, 16, (30000, 35000))
    task = registry.install(QuestionPlan(("self",), ("target_pace",)), (None,), cursor=7, lap=4, laps=3,
                            pace_reference=reference, pace_tolerance_ms=300)
    extended = registry.extend(task.task_id, task.version, additional_laps=2, cursor=20, lap=5)
    assert extended.pace_reference is reference and extended.pace_tolerance_ms == 300


def test_extension_cannot_exceed_lap_255():
    registry = LapReportRegistry()
    task = registry.install(QuestionPlan(("self",), ("position",)), (None,), cursor=7, lap=254, laps=2)
    with pytest.raises(ValueError):
        registry.extend(task.task_id, task.version, additional_laps=1, cursor=8, lap=255)
    assert registry.active() == (task,)


def test_memory_retains_only_bounded_text_and_returns_independent_results():
    memory = ConversationMemory()
    memory.record("instruction", request="long" * 1024, response="long" * 1024)
    turn = memory.previous_request()
    assert len(turn["request"].encode("utf-8")) <= 1024 and len(turn["response"].encode("utf-8")) <= 1024
    turn["response"] = "changed"
    assert memory.previous_request()["response"] != "changed"


def test_completed_report_is_retained_as_completed_and_cannot_be_extended():
    controller, radio, events, value = named_live()
    install(controller, radio, "report at end of next lap my position")
    finish_lap(controller, value)
    assert controller.conversation.previous_tracking_result()["tasks"][0]["state"] == "completed"
    controller.handle_request("extend task 1 by 2 laps", now=11)
    assert not controller.tasks.active() and "Which active task" in radio.pending[-1].text


def test_cancelled_instruction_does_not_restore_an_implicit_reference():
    controller, radio, events, value = named_live()
    install(controller, radio)
    controller.handle_request("cancel task 1", now=10)
    controller.handle_request("track that for another two laps", now=10)
    assert not controller.tasks.active() and "Which active task" in radio.pending[-1].text


def test_sector_followup_preserves_selected_driver_identity():
    controller, radio, events, value = named_live()
    controller.handle_request("Leclerc last lap", now=10)
    controller._drivers[1]["identity"]["tenure_ordinal"] += 1
    controller.handle_request("only sector two", now=10)
    assert "driver unavailable or ambiguous" in radio.pending[-1].text


def test_event_history_gap_clears_conversation():
    controller, radio, events, value = named_live()
    install(controller, radio)
    value["event_floor"] = 10
    value["event_cursor"] = 10
    controller.feed(value, now=10.1)
    assert not controller.conversation.context() and not controller._history


def test_accepted_capture_assigns_context_without_opening_audio(monkeypatch):
    monkeypatch.setattr("f1_engineer.radio.listening.threading.Thread",
                        lambda **fields: SimpleNamespace(start=lambda: None, is_alive=lambda: True))
    voice = VoiceInput(transcribe=lambda *args: None)
    context = TaskContext("session", (1, 1), ((1, 1),))
    voice.request(source="console", scope="session", task_context=context)
    assert voice.task_context is context and voice.status()["state"] == "starting"


def test_rejected_capture_does_not_assign_context():
    voice = VoiceInput(transcribe=lambda *args: None, enabled=False)
    context = TaskContext("session", (1, 1), ((1, 1),))
    voice.request(source="console", scope="session", task_context=context)
    assert voice.task_context is None


def test_active_speech_reference_wins_over_newer_unheard_instruction():
    memory = ConversationMemory()
    memory.record("task_result", response="report", tasks=[{"task_id": 1, "version": 1}], message_key="report:1")
    memory.record("instruction", request="new instruction", response="ack",
                  tasks=[{"task_id": 2, "version": 1}], message_key="answer:2")
    context = memory.capture_tasks("session", (), SimpleNamespace(key="report:1"))
    assert context.selected == (1, 1)


def test_voice_question_keeps_driver_selected_when_listening_started():
    controller, radio, events, value = named_live()
    controller.handle_request("Leclerc last lap", now=10)
    captured = controller.capture_question_context()
    controller.handle_request("my last lap", now=10)
    dispatch_voice_request("his previous lap", controller,
                           SimpleNamespace(task_context=None, question_context=captured), now=10)
    assert "Previously selected Charles Leclerc" in radio.pending[-1].text
    assert "101.000 seconds" in radio.pending[-1].text
    assert controller._history[-1]["plan"].targets == ("name:leclerc",)


def test_voice_without_prior_question_does_not_adopt_a_later_reference():
    controller, radio, events, value = named_live()
    captured = controller.capture_question_context()
    assert captured is None
    controller.handle_request("Leclerc last lap", now=10)
    dispatch_voice_request("his previous lap", controller,
                           SimpleNamespace(task_context=None, question_context=captured), now=10)
    assert "Please specify a driver" in radio.pending[-1].text


@pytest.mark.parametrize("reset", ["forget", "unavailable", "history_gap"])
def test_question_capture_cannot_survive_conversation_reset(reset):
    controller, radio, events, value = named_live()
    controller.handle_request("Leclerc last lap", now=10)
    captured = controller.capture_question_context()
    if reset == "forget":
        controller.forget_conversation()
    elif reset == "unavailable":
        controller.feed({"status": "unavailable", "reason": "no_telemetry"}, now=10.1)
    else:
        value["event_floor"] = value["event_cursor"] = 10
        controller.feed(value, now=10.1)
    controller.feed(value, now=10.2)
    controller.handle_request("Leclerc last lap", now=10.2)
    dispatch_voice_request("only sector two", controller,
                           SimpleNamespace(task_context=None, question_context=captured), now=10.2)
    assert "Which lap and driver" in radio.pending[-1].text


def test_forgetting_invalidates_task_context_even_when_task_is_still_active():
    controller, radio, events, value = named_live()
    install(controller, radio)
    captured = controller.capture_task_context()
    controller.forget_conversation()
    controller.handle_request("extend task 1 by 2 laps", now=10, task_context=captured)
    assert controller.tasks.active()[0].version == 1 and "No same-session" in radio.pending[-1].text


def test_frozen_question_bindings_are_independent_of_live_history():
    controller, radio, events, value = named_live()
    controller.handle_request("Leclerc last lap", now=10)
    captured = controller.capture_question_context()
    controller._history[-1]["bindings"][0]["tenure_ordinal"] += 1
    controller.handle_request("his previous lap", now=10, question_context=captured)
    assert "101.000 seconds" in radio.pending[-1].text


def test_captured_question_still_works_after_raw_history_eviction():
    controller, radio, events, value = named_live()
    controller.handle_request("Leclerc last lap", now=10)
    captured = controller.capture_question_context()
    for index in range(20):
        controller.handle_request("my position", now=10)
    controller.handle_request("his previous lap", now=10, question_context=captured)
    assert "Previously selected Charles Leclerc" in radio.pending[-1].text


def test_cross_scope_question_context_never_supplies_a_pronoun_reference():
    controller, radio, events, value = named_live()
    controller.handle_request("Leclerc last lap", now=10)
    captured = controller.capture_question_context()
    foreign = QuestionContext("different scope", captured.revision, captured.plan, captured.bindings)
    controller.handle_request("his previous lap", now=10, question_context=foreign)
    assert "Please specify a driver" in radio.pending[-1].text


def test_explicit_voice_question_works_without_any_prior_question_context():
    controller, radio, events, value = named_live()
    controller.handle_request("Leclerc last lap", now=10, question_context=None)
    assert "101.000 seconds" in radio.pending[-1].text


def test_second_press_preserves_both_question_and_task_context(monkeypatch):
    monkeypatch.setattr("f1_engineer.radio.listening.threading.Thread",
                        lambda **fields: SimpleNamespace(start=lambda: None, is_alive=lambda: True))
    voice = VoiceInput(transcribe=lambda *args: None)
    task = TaskContext("session", (1, 1), ((1, 1),))
    question = QuestionContext("session", 0, QuestionPlan(("self",), ("last_lap",)), (None,))
    voice.request(source="console", scope="session", task_context=task, question_context=question)
    voice.request(source="console", scope="session", task_context=None, question_context=None)
    assert voice.task_context is task and voice.question_context is question


def test_listening_request_forwards_frozen_question_context():
    calls = []
    question = QuestionContext("session", 0, QuestionPlan(("self",), ("last_lap",)), (None,))
    request_listening(SimpleNamespace(scope="session", capture_repeat_reference=lambda: None, cancel=lambda: None),
                      SimpleNamespace(request=lambda **fields: calls.append(fields)),
                      source="console", question_context=question)
    assert calls[0]["question_context"] is question
