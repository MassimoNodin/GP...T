import pytest

from f1_engineer.cli import build_parser
from f1_engineer.radio.scenarios import SCENARIOS, play_scenario


@pytest.mark.parametrize("name", SCENARIOS)
def test_synthetic_scenario_uses_the_real_controller_and_scheduler(name):
    events = []
    assert play_scenario(name, notify=events.append)
    result = next(event for event in events if event["event"] == "radio_scenario_complete")
    assert result["success"] and not result["audio"] and not result["backend"] and not result["model"]
    if name == "closing":
        assert any(event.get("event") == "radio_started" and "car behind is closing" in event["text"] for event in events)
    elif name == "pace":
        assert any(event.get("event") == "radio_started" and "getting slower" in event["text"] for event in events)
    elif name in ("mid-lap", "busy-radio"):
        assert any(event.get("event") == "radio_started" and "over 30 seconds" in event["text"] for event in events)
        if name == "busy-radio":
            assert any(event.get("reason") == "radio_busy" for event in events)


def test_cli_exposes_proactive_modes_and_offline_scenarios():
    parser = build_parser()
    assert parser.parse_args(["radio", "--proactive-mode", "off"]).proactive_mode == "off"
    assert parser.parse_args(["radio", "--proactive-mode", "model"]).proactive_mode == "model"
    assert parser.parse_args(["radio-scenario"]).scenario == "all"
