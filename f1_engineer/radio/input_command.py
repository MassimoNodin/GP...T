from __future__ import annotations

import math
import time
from dataclasses import asdict
from pathlib import Path

from .command import emit
from .input_settings import ButtonBinding, load_binding, save_binding
from .wheel_input import SDLWheelInput, WheelButtonMonitor, WheelInputUnavailable, bound_device


def _learn(source, index: int, *, timeout: float, notify=emit, clock=time.monotonic, sleep=time.sleep) -> ButtonBinding:
    frame = source.poll()
    if not 0 <= index < len(frame.devices):
        raise ValueError("radio_wheel_device_index_unavailable")
    selected = frame.devices[index]
    if not selected.buttons:
        raise ValueError("radio_wheel_has_no_buttons")
    candidate = ButtonBinding(selected.guid, selected.name, 0)
    bound_device(frame, candidate)
    released = {button for button, pressed in enumerate(selected.buttons) if not pressed}
    notify({"event": "radio_input_learning", "name": selected.name, "guid": selected.guid,
            "instruction": "Release any held buttons, then press the desired listening button."})
    deadline = clock() + timeout
    while clock() < deadline:
        frame = source.poll()
        current = bound_device(frame, candidate)
        if current.instance_id != selected.instance_id or len(current.buttons) != len(selected.buttons):
            raise WheelInputUnavailable("radio_wheel_changed_during_learning")
        for edge in frame.edges:
            if edge.instance_id != selected.instance_id or not 0 <= edge.button < len(current.buttons):
                continue
            if not edge.pressed:
                released.add(edge.button)
            elif edge.button in released:
                return ButtonBinding(selected.guid, selected.name, edge.button)
        released.update(button for button, pressed in enumerate(current.buttons) if not pressed)
        sleep(0.02)
    raise WheelInputUnavailable("radio_wheel_learning_timed_out")


def run_input(args) -> int:
    settings = Path(args.input_settings)
    if args.input_action == "show":
        binding = load_binding(settings)
        emit({"event": "radio_input_binding", "binding": asdict(binding) if binding else None})
        return 0
    if args.input_action == "clear":
        save_binding(settings, None)
        emit({"event": "radio_input_binding", "binding": None})
        return 0
    if args.input_action == "test":
        if load_binding(settings) is None:
            raise ValueError("radio_input_not_bound")
        monitor = WheelButtonMonitor(settings, notify=emit)
        try:
            emit({"event": "radio_input_test", "instruction": "Press the bound button; Ctrl+C stops. No microphone or audio is used."})
            while True:
                if monitor.poll(now=time.monotonic()):
                    emit({"event": "radio_listen_requested", "source": "wheel", "test_only": True})
                time.sleep(0.02)
        finally:
            monitor.close()
    if args.input_action == "bind" and (not math.isfinite(args.timeout) or not 1 <= args.timeout <= 120):
        raise ValueError("radio_input_timeout_invalid")
    source = SDLWheelInput()
    try:
        if args.input_action == "list":
            frame = source.poll()
            emit({"event": "radio_input_devices", "devices": [
                {"index": index, "guid": device.guid, "name": device.name, "buttons": len(device.buttons)}
                for index, device in enumerate(frame.devices)]})
            return 0
        binding = _learn(source, args.device_index, timeout=args.timeout)
        save_binding(settings, binding)
        emit({"event": "radio_input_binding", "binding": asdict(binding)})
        return 0
    finally:
        source.close()
