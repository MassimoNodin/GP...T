from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from f1_engineer import cli
from f1_engineer.radio import command, input_command, input_settings, wheel_input
from f1_engineer.radio.input_command import _learn
from f1_engineer.radio.input_settings import ButtonBinding, load_binding, save_binding
from f1_engineer.radio.wheel_input import (ButtonEdge, InputFrame, SDLWheelInput, WheelButtonMonitor,
                                          WheelDevice, WheelInputUnavailable, bound_device)


GUID = "a" * 32
BINDING = ButtonBinding(GUID, "MOZA R3", 1)


def device(*, instance=7, held=False, name="MOZA R3", guid=GUID):
    return WheelDevice(instance, guid, name, (False, held, False))


def frame(*, held=False, edges=(), devices=None, instance=7):
    return InputFrame(devices if devices is not None else (device(instance=instance, held=held),),
                      tuple(ButtonEdge(instance, button, pressed) for button, pressed in edges))


class Source:
    def __init__(self, *frames):
        self.frames = list(frames)
        self.closed = False

    def poll(self):
        assert not self.closed
        result = self.frames.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        self.closed = True


def monitor(tmp_path, source, binding=BINDING):
    settings = tmp_path / "input.json"
    save_binding(settings, binding)
    events = []
    watcher = WheelButtonMonitor(settings, source_factory=lambda: source, notify=events.append)
    return watcher, settings, events


def test_binding_round_trip_clear_and_missing_file(tmp_path):
    settings = tmp_path / "nested" / "input.json"
    assert load_binding(settings) is None
    save_binding(settings, BINDING)
    assert load_binding(settings) == BINDING
    save_binding(settings, None)
    assert load_binding(settings) is None
    assert not list(settings.parent.glob("*.tmp"))


@pytest.mark.parametrize("changes", [
    {"guid": "MOZA"}, {"guid": "A" * 32}, {"name": ""}, {"name": "bad\nname"},
    {"name": "x" * 257}, {"button": -1}, {"button": 512}, {"button": True}, {"button": "1"},
], ids=["bad-guid", "uppercase-guid", "empty-name", "control-name", "long-name",
        "negative-button", "large-button", "boolean-button", "string-button"])
def test_invalid_binding_is_rejected(changes):
    with pytest.raises(ValueError, match="binding_invalid"):
        replace(BINDING, **changes)


@pytest.mark.parametrize("raw", [b"not json", b"x" * 4097, b"\xff",
    b'{"schema_version":true,"listen":null}', b'{"schema_version":2,"listen":null}',
    b'{"schema_version":1,"listen":{},"extra":1}', b'{"schema_version":1,"listen":[]}',
], ids=["invalid-json", "oversized", "invalid-utf8", "boolean-version", "future-version", "extra-key", "bad-binding"])
def test_invalid_settings_are_bounded_and_rejected(tmp_path, raw):
    settings = tmp_path / "input.json"
    settings.write_bytes(raw)
    with pytest.raises(ValueError, match="settings_invalid"):
        load_binding(settings)


def test_atomic_save_failure_preserves_old_binding_and_cleans_temporary_file(tmp_path, monkeypatch):
    settings = tmp_path / "input.json"
    save_binding(settings, BINDING)
    def fail(*args):
        raise OSError("replace failed")
    monkeypatch.setattr(input_settings.os, "replace", fail)
    with pytest.raises(OSError):
        save_binding(settings, replace(BINDING, button=2))
    assert load_binding(settings) == BINDING
    assert list(tmp_path.iterdir()) == [settings]


def test_settings_symlinks_are_not_read_or_replaced(tmp_path, monkeypatch):
    settings = tmp_path / "input.json"
    monkeypatch.setattr(Path, "is_symlink", lambda path: True)
    with pytest.raises(ValueError):
        load_binding(settings)
    with pytest.raises(ValueError):
        save_binding(settings, BINDING)
    assert not settings.exists()


def test_unbound_monitor_never_initializes_sdl(tmp_path):
    watcher = WheelButtonMonitor(tmp_path / "absent", source_factory=lambda: pytest.fail("no device access"))
    assert not watcher.poll(now=0)
    assert watcher.status()["status"] == "unbound"
    watcher.close()


def test_one_activation_per_press_no_held_repeat_and_debounce(tmp_path):
    source = Source(frame(), frame(held=True, edges=[(1, True)]),
                    frame(held=True, edges=[(1, True)]), frame(edges=[(1, False)]),
                    frame(held=True, edges=[(1, True)]), frame(edges=[(1, False)]),
                    frame(held=True, edges=[(1, True)]))
    watcher, settings, events = monitor(tmp_path, source)
    assert not watcher.poll(now=0)
    assert watcher.poll(now=0.1)
    assert not watcher.poll(now=0.15)
    assert not watcher.poll(now=0.2)
    assert not watcher.poll(now=0.22)
    assert not watcher.poll(now=0.3)
    assert watcher.poll(now=0.5)
    watcher.close()
    assert source.closed


def test_press_and_release_between_polls_is_not_lost(tmp_path):
    watcher, settings, events = monitor(tmp_path, Source(frame(), frame(edges=[(1, True), (1, False)])))
    assert not watcher.poll(now=0)
    assert watcher.poll(now=0.1)


def test_initial_held_button_and_queued_events_do_not_activate(tmp_path):
    watcher, settings, events = monitor(tmp_path, Source(
        frame(held=True, edges=[(1, True)]), frame(held=True, edges=[(1, True)]),
        frame(edges=[(1, False)]), frame(held=True, edges=[(1, True)])))
    assert not watcher.poll(now=0)
    assert not watcher.poll(now=0.1)
    assert not watcher.poll(now=0.2)
    assert watcher.poll(now=0.3)


def test_other_buttons_and_devices_never_activate(tmp_path):
    other = device(instance=9, guid="b" * 32)
    initial = InputFrame((other, device()))
    next_frame = InputFrame((device(), other), (ButtonEdge(9, 1, True), ButtonEdge(7, 2, True)))
    watcher, settings, events = monitor(tmp_path, Source(initial, next_frame, frame(edges=[(1, True)])))
    assert not watcher.poll(now=0)
    assert not watcher.poll(now=0.1)
    assert watcher.poll(now=0.2)


def test_live_rebind_discards_baseline_and_old_button(tmp_path):
    source = Source(frame(), frame(edges=[(2, True)]), frame(edges=[(1, True)]), frame(edges=[(2, True)]))
    watcher, settings, events = monitor(tmp_path, source)
    assert not watcher.poll(now=0)
    save_binding(settings, replace(BINDING, button=2))
    assert not watcher.poll(now=0.5)
    assert watcher.status()["binding"]["button"] == 2
    assert not watcher.poll(now=0.6)
    assert watcher.poll(now=0.7)


def test_clear_live_binding_closes_input(tmp_path):
    source = Source(frame())
    watcher, settings, events = monitor(tmp_path, source)
    watcher.poll(now=0)
    save_binding(settings, None)
    assert not watcher.poll(now=0.5)
    assert source.closed
    assert watcher.status()["status"] == "unbound"


def test_malformed_settings_fail_closed_then_recover(tmp_path):
    source = Source(frame(), frame())
    watcher, settings, events = monitor(tmp_path, source)
    watcher.poll(now=0)
    settings.write_text("bad", encoding="utf-8")
    assert not watcher.poll(now=0.5)
    assert watcher.status()["reason"] == "radio_input_settings_invalid"
    save_binding(settings, BINDING)
    assert not watcher.poll(now=1)
    assert watcher.status()["status"] == "ready"


def test_invalid_settings_corrected_to_unbound_clear_error(tmp_path):
    settings = tmp_path / "input.json"
    settings.write_text("bad", encoding="utf-8")
    watcher = WheelButtonMonitor(settings)
    watcher.poll(now=0)
    save_binding(settings, None)
    assert not watcher.poll(now=0.5)
    assert watcher.status()["status"] == "unbound"


@pytest.mark.parametrize("devices,reason", [
    ((), "disconnected"), ((device(), device(instance=9)), "identity_ambiguous"),
    ((replace(device(), buttons=(False,)),), "button_unavailable"),
    ((device(name="other"),), "disconnected"),
], ids=["unplugged", "duplicate-identity", "missing-button", "wrong-name"])
def test_missing_ambiguous_or_changed_devices_do_not_activate(devices, reason):
    with pytest.raises(WheelInputUnavailable, match=reason):
        bound_device(InputFrame(devices), BINDING)


def test_disconnect_reconnect_reseeds_held_button_and_retries_with_backoff(tmp_path):
    first = Source(frame(), frame(devices=()))
    second = Source(frame(instance=9, held=True, edges=[(1, True)]),
                    frame(instance=9, edges=[(1, False)]), frame(instance=9, held=True, edges=[(1, True)]))
    sources = iter((first, second))
    watcher, settings, events = monitor(tmp_path, first)
    watcher._source_factory = lambda: next(sources)
    assert not watcher.poll(now=0)
    assert not watcher.poll(now=0.1)
    assert first.closed
    assert not watcher.poll(now=0.5)
    assert not watcher.poll(now=1.1)
    assert not watcher.poll(now=1.2)
    assert watcher.poll(now=1.3)


def test_optional_dependency_failure_does_not_break_radio(tmp_path):
    watcher, settings, events = monitor(tmp_path, None)
    def unavailable():
        raise WheelInputUnavailable("radio_wheel_requires_radio_extra")
    watcher._source_factory = unavailable
    assert not watcher.poll(now=0)
    assert watcher.status()["reason"] == "radio_wheel_requires_radio_extra"
    assert not watcher.poll(now=0.1)


def test_learning_ignores_held_buttons_and_other_devices():
    source = Source(frame(held=True), frame(held=True, edges=[(1, True)]),
                    frame(edges=[(1, False)]), frame(edges=[(1, True)]))
    assert _learn(source, 0, timeout=30, notify=lambda event: None, sleep=lambda seconds: None) == BINDING


def test_learning_accepts_a_short_press_between_polls():
    source = Source(frame(), frame(edges=[(2, True), (2, False)]))
    result = _learn(source, 0, timeout=30, notify=lambda event: None, sleep=lambda seconds: None)
    assert result == replace(BINDING, button=2)


def test_learning_timeout_and_invalid_device_do_not_save():
    times = iter((0, 2))
    with pytest.raises(WheelInputUnavailable, match="timed_out"):
        _learn(Source(frame()), 0, timeout=1, notify=lambda event: None, clock=lambda: next(times))
    with pytest.raises(ValueError, match="index_unavailable"):
        _learn(Source(frame()), -1, timeout=1)


def test_learning_aborts_on_reconnection():
    with pytest.raises(WheelInputUnavailable, match="changed_during_learning"):
        _learn(Source(frame(), frame(instance=9)), 0, timeout=30,
               notify=lambda event: None, sleep=lambda seconds: None)


def test_cli_show_and_clear_need_no_device_backend_or_audio(tmp_path, monkeypatch, capsys):
    settings = tmp_path / "input.json"
    save_binding(settings, BINDING)
    monkeypatch.setattr(input_command, "SDLWheelInput", lambda: pytest.fail("no device access"))
    args = cli.build_parser().parse_args(["radio-input", "--input-settings", str(settings), "show"])
    assert args.handler(args) == 0
    assert json.loads(capsys.readouterr().out)["binding"]["button"] == 1
    args.input_action = "clear"
    assert args.handler(args) == 0
    assert load_binding(settings) is None


def test_cli_binding_is_saved_only_after_successful_learning(tmp_path, monkeypatch, capsys):
    settings = tmp_path / "input.json"
    save_binding(settings, BINDING)
    source = Source(frame(), frame(edges=[(2, True)]))
    monkeypatch.setattr(input_command, "SDLWheelInput", lambda: source)
    args = cli.build_parser().parse_args(["radio-input", "--input-settings", str(settings),
                                          "bind", "--device-index", "0"])
    assert args.handler(args) == 0
    assert load_binding(settings).button == 2
    assert source.closed


def test_failed_learning_preserves_previous_binding_and_closes_device(tmp_path, monkeypatch):
    settings = tmp_path / "input.json"
    save_binding(settings, BINDING)
    source = Source(frame(), frame(devices=()))
    monkeypatch.setattr(input_command, "SDLWheelInput", lambda: source)
    args = cli.build_parser().parse_args(["radio-input", "--input-settings", str(settings),
                                          "bind", "--device-index", "0"])
    with pytest.raises(WheelInputUnavailable):
        args.handler(args)
    assert load_binding(settings) == BINDING
    assert source.closed


def test_listening_request_cancels_radio_and_forwards_session_binding():
    calls = []
    voice = SimpleNamespace(request=lambda **fields: calls.append(fields))
    command.request_listening(SimpleNamespace(cancel=lambda: calls.append("cancel"), scope="session",
                                             capture_repeat_reference=lambda: "frozen"),
                              voice, source="wheel", binding="button", connection=7)
    assert calls == ["cancel", {"source": "wheel", "scope": "session", "binding": "button", "connection": 7,
                               "repeat_reference": "frozen", "task_context": None}]


def test_radio_loop_routes_bound_button_request_and_closes_input(tmp_path, monkeypatch, capsys):
    settings = tmp_path / "input.json"
    calls = []
    class Client:
        def __init__(self, *args):
            pass

        def fetch(self):
            return {"status": "unavailable", "reason": "no_telemetry"}

        def close(self):
            calls.append("client-close")

    class Wheel:
        def __init__(self, path, **kwargs):
            assert path == settings
            self.first = True

        def poll(self, **kwargs):
            pressed, self.first = self.first, False
            return pressed

        def close(self):
            calls.append("wheel-close")

        def status(self):
            return {"status": "ready", "binding": None, "connection_instance": 7}

    monkeypatch.setattr(command, "RadioClient", Client)
    monkeypatch.setattr(command, "WheelButtonMonitor", Wheel)
    monkeypatch.setattr(command, "_commands", lambda inbox, stream: inbox.put("quit"))
    monkeypatch.setattr(command, "WindowsSpeech", lambda **kwargs: pytest.fail("no audio"))
    args = cli.build_parser().parse_args(["radio", "--dry-run", "--input-settings", str(settings)])
    assert command.run_radio(args) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {"event": "radio_listen_requested", "source": "wheel"} in events
    assert calls == ["wheel-close", "client-close"]


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), 0, -1, 121])
def test_invalid_learning_timeout_never_opens_device(timeout, monkeypatch):
    monkeypatch.setattr(input_command, "SDLWheelInput", lambda: pytest.fail("no device access"))
    args = SimpleNamespace(input_action="bind", input_settings="unused", timeout=timeout)
    with pytest.raises(ValueError, match="timeout_invalid"):
        input_command.run_input(args)


def test_sdl_adapter_initializes_background_input_without_window_or_audio(monkeypatch):
    calls = []
    stick = SimpleNamespace(init=lambda: None, get_instance_id=lambda: 7, get_guid=lambda: GUID,
                            get_name=lambda: "MOZA R3", get_numbuttons=lambda: 3,
                            get_button=lambda button: button == 1, quit=lambda: calls.append("stick-close"))
    pygame = SimpleNamespace(error=RuntimeError, JOYBUTTONDOWN=1, JOYBUTTONUP=2,
        display=SimpleNamespace(init=lambda: calls.append("display"), quit=lambda: calls.append("display-close")),
        joystick=SimpleNamespace(init=lambda: calls.append("joystick"), get_count=lambda: 1,
                                 Joystick=lambda index: stick, quit=lambda: calls.append("joystick-close")),
        event=SimpleNamespace(get=lambda: [SimpleNamespace(type=1, instance_id=7, button=1)]))
    monkeypatch.setattr(wheel_input.sys, "platform", "win32")
    monkeypatch.setitem(wheel_input.sys.modules, "pygame", pygame)
    source = SDLWheelInput()
    assert wheel_input.os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] == "1"
    assert source.poll() == frame(held=True, edges=[(1, True)])
    source.close()
    assert calls == ["display", "joystick", "stick-close", "joystick-close", "display-close"]
