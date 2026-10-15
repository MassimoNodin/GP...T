from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from ..errors import F1EngineerError
from ..diagnostics import exception_trace
from .input_settings import ButtonBinding, load_binding


class WheelInputUnavailable(F1EngineerError):
    pass


def input_error_reason(exception: Exception, fallback: str) -> str:
    if "0x80070005" in str(exception).lower():
        return "radio_wheel_access_denied_restart_required"
    return fallback


@dataclass(frozen=True)
class WheelDevice:
    instance_id: int
    guid: str
    name: str
    buttons: tuple[bool, ...]


@dataclass(frozen=True)
class ButtonEdge:
    instance_id: int
    button: int
    pressed: bool


@dataclass(frozen=True)
class InputFrame:
    devices: tuple[WheelDevice, ...]
    edges: tuple[ButtonEdge, ...] = ()


class SDLWheelInput:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise WheelInputUnavailable("radio_wheel_requires_windows")
        os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"
        os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
        try:
            import pygame
        except ImportError as exception:
            raise WheelInputUnavailable("radio_wheel_requires_radio_extra") from exception
        self._pygame = pygame
        self._sticks = {}
        self._enumerated = False
        try:
            pygame.display.init()
            pygame.joystick.init()
        except pygame.error as exception:
            self.close()
            raise WheelInputUnavailable(input_error_reason(exception, "radio_wheel_initialization_failed")) from exception

    def _open(self, index: int) -> None:
        stick = self._pygame.joystick.Joystick(index)
        self._sticks[stick.get_instance_id()] = stick

    def poll(self) -> InputFrame:
        pygame = self._pygame
        try:
            events = pygame.event.get()
            devices = []
            device_count = pygame.joystick.get_count()
            if device_count > 64:
                raise WheelInputUnavailable("radio_wheel_device_limit")
            for event in events:
                if event.type == pygame.JOYDEVICEREMOVED:
                    stick = self._sticks.pop(event.instance_id, None)
                    if stick is not None:
                        stick.quit()
            if not self._enumerated:
                for index in range(device_count):
                    self._open(index)
                self._enumerated = True
            else:
                for index in sorted({event.device_index for event in events if event.type == pygame.JOYDEVICEADDED}):
                    if type(index) is not int or not 0 <= index < device_count:
                        raise WheelInputUnavailable("radio_wheel_device_index_invalid")
                    self._open(index)
            for instance, stick in self._sticks.items():
                count = stick.get_numbuttons()
                if not 0 <= count <= 512:
                    raise WheelInputUnavailable("radio_wheel_button_limit")
                devices.append(WheelDevice(instance, stick.get_guid().lower(), stick.get_name(),
                                           tuple(bool(stick.get_button(button)) for button in range(count))))
            edges = tuple(ButtonEdge(event.instance_id, event.button, event.type == pygame.JOYBUTTONDOWN)
                          for event in events if event.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP))
            if len(edges) > 256:
                raise WheelInputUnavailable("radio_wheel_event_overflow")
            return InputFrame(tuple(devices), edges)
        except pygame.error as exception:
            raise WheelInputUnavailable(input_error_reason(exception, "radio_wheel_poll_failed")) from exception

    def close(self) -> None:
        for stick in self._sticks.values():
            stick.quit()
        self._sticks.clear()
        self._enumerated = False
        self._pygame.joystick.quit()
        self._pygame.display.quit()


def bound_device(frame: InputFrame, binding: ButtonBinding) -> WheelDevice:
    matches = [device for device in frame.devices
               if device.guid == binding.guid and device.name == binding.name]
    if not matches:
        raise WheelInputUnavailable("radio_wheel_disconnected")
    if len(matches) != 1:
        raise WheelInputUnavailable("radio_wheel_identity_ambiguous")
    device = matches[0]
    if binding.button >= len(device.buttons):
        raise WheelInputUnavailable("radio_wheel_button_unavailable")
    return device


class WheelButtonMonitor:
    def __init__(self, settings: Path, *, source_factory=SDLWheelInput, notify=None) -> None:
        self.settings = settings
        self._source_factory = source_factory
        self._notify = notify or (lambda event: None)
        self._source = None
        self._binding = None
        self._connection = None
        self._armed = False
        self._last_press = float("-inf")
        self._next_settings = 0.0
        self._next_source = 0.0
        self._status = None
        self._access_blocked = False

    def _report(self, status: str, reason=None) -> None:
        if (status, reason) != self._status:
            self._status = (status, reason)
            self._notify({"event": "radio_input", **self.status()})

    def _reset(self) -> None:
        self._connection = None
        self._armed = False

    def poll(self, *, now: float) -> bool:
        if self._access_blocked:
            return False
        if now >= self._next_settings:
            self._next_settings = now + 0.5
            try:
                binding = load_binding(self.settings)
            except (OSError, ValueError):
                self._binding = None
                self._reset()
                self._report("unavailable", "radio_input_settings_invalid")
                return False
            if binding != self._binding:
                self._binding = binding
                self._reset()
                self._report("changed")
            elif self._status == ("unavailable", "radio_input_settings_invalid"):
                self._report("unbound" if binding is None else "changed")
        if self._binding is None:
            if self._source is not None:
                self._source.close()
                self._source = None
            if self._status != ("unavailable", "radio_input_settings_invalid"):
                self._report("unbound")
            return False
        if self._source is None and now < self._next_source:
            return False
        try:
            if self._source is None:
                self._source = self._source_factory()
            frame = self._source.poll()
            device = bound_device(frame, self._binding)
            connection = (device.instance_id, self._binding)
            self._report("ready")
            if connection != self._connection:
                self._connection = connection
                self._armed = not device.buttons[self._binding.button]
                return False
            activated = False
            for edge in frame.edges:
                if edge.instance_id != device.instance_id or edge.button != self._binding.button:
                    continue
                if not edge.pressed:
                    self._armed = True
                else:
                    if self._armed and now - self._last_press >= 0.25:
                        activated = True
                        self._last_press = now
                    self._armed = False
            if not device.buttons[self._binding.button]:
                self._armed = True
            return activated
        except WheelInputUnavailable as exception:
            self._notify({"event": "radio_input_failure", "reason": str(exception),
                          "exception": exception_trace(exception)})
            if str(exception) == "radio_wheel_access_denied_restart_required":
                self._access_blocked = True
            self._reset()
            self._report("unavailable", str(exception))
            if self._source is not None:
                self._source.close()
                self._source = None
            self._next_source = now + 1
            return False

    def status(self) -> dict:
        status, reason = self._status or ("unbound", None)
        return {"status": status, "reason": reason,
                "connection_instance": self._connection[0] if self._connection else None,
                "binding": ({"guid": self._binding.guid, "name": self._binding.name,
                             "button": self._binding.button} if self._binding else None)}

    def close(self) -> None:
        if self._source is not None:
            self._source.close()
            self._source = None
