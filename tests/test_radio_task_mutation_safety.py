import pytest

from f1_engineer.radio.conversation import TaskContext
from f1_engineer.radio.questions import QuestionPlan
from f1_engineer.radio.tasks import LapReportRegistry
from tests.test_live_driver_names import named_live
from tests.test_radio_tasks import finish_lap, install


@pytest.mark.parametrize("changes", [{"lap": True}, {"lap": None}, {"lap": 256}, {"cursor": False},
                                    {"cursor": -1}, {"laps": True}, {"laps": 0}, {"laps": 21},
                                    {"lap": 254, "laps": 3}, {"task_id": True}])
def test_invalid_install_is_atomic(changes):
    registry = LapReportRegistry()
    fields = dict(cursor=0, lap=4, laps=3)
    fields.update(changes)
    with pytest.raises(ValueError):
        registry.install(QuestionPlan(("self",), ("position",)), (None,), **fields)
    assert registry.views() == []


def test_completed_registry_task_cannot_be_updated_back_to_active():
    registry = LapReportRegistry()
    task = registry.install(QuestionPlan(("self",), ("position",)), (None,), cursor=0, lap=4, laps=1)
    registry.complete_lap(4)
    before = registry.views()
    with pytest.raises(ValueError, match="ended"):
        registry.install(task.plan, task.fixed_bindings, task_id=task.task_id, cursor=1, lap=5, laps=1)
    assert registry.views() == before


def test_typed_update_of_completed_task_requires_new_request():
    controller, radio, events, value = named_live()
    install(controller, radio, "report at end of next lap my position")
    finish_lap(controller, value)
    controller.handle_request("update task 1 report at end of each lap my last lap", now=11)
    assert not controller.tasks.active() and "Create a new task" in radio.pending[-1].text


@pytest.mark.parametrize("command", ["cancel task 1", "update task 1 report at end of each lap my last lap"])
def test_delayed_voice_command_cannot_mutate_updated_task(command):
    controller, radio, events, value = named_live()
    install(controller, radio)
    captured = controller.capture_task_context()
    controller.handle_request("update task 1 report at end of each lap my position", now=10)
    before = controller.tasks.views()
    controller.handle_request(command, now=10, task_context=captured)
    assert controller.tasks.views() == before and "task changed" in radio.pending[-1].text


@pytest.mark.parametrize("command", ["cancel task 1", "update task 1 report at end of each lap my last lap"])
@pytest.mark.parametrize("context", [None, TaskContext("different session", (1, 1), ((1, 1),))])
def test_missing_or_foreign_voice_context_refuses_task_edits(command, context):
    controller, radio, events, value = named_live()
    install(controller, radio)
    before = controller.tasks.views()
    controller.handle_request(command, now=10, task_context=context)
    assert controller.tasks.views() == before and "No same-session" in radio.pending[-1].text


@pytest.mark.parametrize("command", ["cancel task 1", "update task 1 report at end of each lap my last lap"])
def test_valid_captured_version_allows_task_edit(command):
    controller, radio, events, value = named_live()
    install(controller, radio)
    captured = controller.capture_task_context()
    controller.handle_request(command, now=10, task_context=captured)
    if command.startswith("cancel"):
        assert not controller.tasks.active()
    else:
        assert controller.tasks.active()[0].version == 2


def test_new_task_created_during_capture_cannot_be_cancelled_by_old_command():
    controller, radio, events, value = named_live()
    captured = controller.capture_task_context()
    install(controller, radio)
    controller.handle_request("cancel task 1", now=10, task_context=captured)
    assert controller.tasks.active()[0].version == 1 and "not active when listening began" in radio.pending[-1].text


def test_explicit_new_request_after_expiry_gets_a_new_task_identifier():
    controller, radio, events, value = named_live()
    install(controller, radio, "report at end of next lap my position")
    finish_lap(controller, value)
    controller.handle_request("report at end of each lap my position", now=11)
    assert len(controller.tasks.active()) == 1 and controller.tasks.active()[0].task_id == 2
