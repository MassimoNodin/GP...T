from types import SimpleNamespace

import pytest

from f1_engineer.radio.command import dispatch_voice_request
from f1_engineer.radio.conversation import TaskContext
from f1_engineer.radio.tasks import parse_task_request
from tests.test_live_driver_names import named_live
from tests.test_radio_tasks import finish_lap, install


@pytest.mark.parametrize("phrase", ["cancel that", "Cancel that!", "cancel that task", "cancel that tracking"])
def test_closed_cancel_reference_intents(phrase):
    assert parse_task_request(phrase).kind == "cancel_reference"


def test_typed_cancel_reference_cancels_only_the_single_selected_task():
    controller, radio, events, value = named_live()
    install(controller, radio)
    install(controller, radio, "coach my consistency")
    controller.handle_request("cancel that", now=10)
    assert [task.task_id for task in controller.tasks.active()] == [1]
    assert "Task 2 cancelled" in radio.pending[-1].text


def test_spoken_cancel_reference_uses_captured_task_not_newer_instruction():
    controller, radio, events, value = named_live()
    install(controller, radio)
    captured = controller.capture_task_context()
    install(controller, radio, "coach my consistency")
    dispatch_voice_request("cancel that", controller,
                           SimpleNamespace(task_context=captured, question_context=None), now=10)
    assert [task.task_id for task in controller.tasks.active()] == [2]
    assert "Task 1 cancelled" in radio.pending[-1].text


def test_cancel_reference_after_version_change_is_refused():
    controller, radio, events, value = named_live()
    install(controller, radio)
    captured = controller.capture_task_context()
    controller.handle_request("update task 1 report at end of each lap my position", now=10)
    controller.handle_request("cancel that", now=10, task_context=captured)
    assert controller.tasks.active()[0].version == 2 and "task changed" in radio.pending[-1].text


@pytest.mark.parametrize("context", [None, TaskContext("different session", (1, 1), ((1, 1),))])
def test_missing_or_cross_session_cancel_reference_clarifies(context):
    controller, radio, events, value = named_live()
    install(controller, radio)
    controller.handle_request("cancel that", now=10, task_context=context)
    assert len(controller.tasks.active()) == 1
    assert "cancel task followed by its number" in radio.pending[-1].text


def test_multi_task_report_is_not_a_single_cancel_reference():
    controller, radio, events, value = named_live()
    install(controller, radio)
    install(controller, radio, "report at end of each lap my position")
    finish_lap(controller, value)
    radio.tick(now=11)
    captured = controller.capture_task_context()
    controller.handle_request("cancel that", now=11, task_context=captured)
    assert len(controller.tasks.active()) == 2 and "No single" in radio.pending[-1].text


def test_completed_reference_cannot_cancel_or_revive_another_task():
    controller, radio, events, value = named_live()
    install(controller, radio, "report at end of next lap my position")
    finish_lap(controller, value)
    radio.tick(now=11)
    captured = controller.capture_task_context()
    install(controller, radio, "coach my consistency")
    controller.handle_request("cancel that", now=11, task_context=captured)
    assert [task.task_id for task in controller.tasks.active()] == [2]
    assert "No single" in radio.pending[-1].text


def test_forgetting_invalidates_implicit_cancel_reference():
    controller, radio, events, value = named_live()
    install(controller, radio)
    captured = controller.capture_task_context()
    controller.forget_conversation()
    controller.handle_request("cancel that", now=10, task_context=captured)
    assert len(controller.tasks.active()) == 1 and "No single" in radio.pending[-1].text


def test_old_selected_version_cannot_be_rebound_to_current_active_version():
    controller, radio, events, value = named_live()
    install(controller, radio)
    controller.handle_request("update task 1 report at end of each lap my position", now=10)
    context = TaskContext(controller.scheduler.scope, (1, 1), ((1, 2),), controller.conversation.revision)
    controller.handle_request("cancel that", now=10, task_context=context)
    assert controller.tasks.active()[0].version == 2 and "No single" in radio.pending[-1].text
