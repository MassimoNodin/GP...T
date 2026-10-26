from tests.test_radio_tasks import finish_lap, install
from tests.test_radio_questions import live


def test_task_report_starts_with_completed_lap_time():
    controller, radio, events, value = live()
    install(controller, radio, "tell me at the end of each lap my position")
    finish_lap(controller, value)
    assert len(radio.pending) == 1
    assert radio.pending[0].text.startswith("Lap 3: 1 minute 39.000 seconds.")
    assert "Task 1" in radio.pending[0].text


def test_session_reset_speaks_one_clear_notice_on_new_scope():
    controller, radio, events, value = live()
    install(controller, radio)
    controller.check_freshness(now=40)
    assert controller.tasks.active()
    assert not radio.pending
    value["scope"]["session_uid"] += 1
    controller.feed(value, now=41)
    assert not controller.tasks.active()
    assert [message.text for message in radio.pending] == ["Tracking cleared: session changed."]
    controller.feed(value, now=41.1)
    assert len(radio.pending) == 1


def test_lap_rewind_explains_why_tracking_stopped():
    controller, radio, events, value = live()
    install(controller, radio)
    value["drivers"][0]["lap"]["current_lap_number"] -= 1
    controller.feed(value, now=11)
    assert not controller.tasks.active()
    assert any(message.text == "Tracking cleared: lap number changed." for message in radio.pending)
