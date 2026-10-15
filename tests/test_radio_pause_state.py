from copy import deepcopy

from tests.test_radio_target_pace import REQUEST, ready
from tests.test_radio_reported_history import connected


def test_same_session_pause_preserves_history_tasks_and_conversation_until_resume():
    controller, radio, events, value = ready()
    controller.handle_request(REQUEST, now=12)
    bests = deepcopy(controller.pace.best)
    turns = deepcopy(controller.conversation.turns)
    controller.check_freshness(now=42)
    assert controller._player is None
    assert radio.scope is None
    assert controller.pace.best == bests
    assert controller.tasks.active()
    assert controller.conversation.turns == turns
    value["events"] = []
    value["event_cursor"] += 5
    value["event_floor"] = value["event_cursor"]
    controller.feed(value, now=4000)
    assert controller.tasks.active()
    assert controller.pace.best == bests
    assert not controller.pace.started
    controller.handle_request(REQUEST, now=4000)
    assert "Fixed reference" in radio.pending[-1].text


def test_session_change_after_pause_clears_retained_state():
    controller, radio, events, value = ready()
    controller.handle_request(REQUEST, now=12)
    controller.check_freshness(now=42)
    value["scope"]["session_uid"] += 1
    controller.feed(value, now=43)
    assert not controller.tasks.active()
    assert not controller.pace.best
    assert not controller.conversation.turns


def test_game_best_can_supply_lap_only_pace_after_pause():
    controller, radio, value = connected()
    value["drivers"][0]["lap"]["current_lap_invalid_id"] = 0
    value["drivers"][0]["tyre"] = {"status": "available", "value": {"actual_compound": 16}}
    controller.check_freshness(now=40)
    controller.feed(value, now=41)
    controller.handle_request(REQUEST, now=41)
    assert controller.tasks.active()
    assert controller.tasks.active()[0].pace_reference.sector_times_ms is None
    assert "game-reported session-best" in radio.pending[-1].text
    assert "Sector feedback unavailable" in radio.pending[-1].text
