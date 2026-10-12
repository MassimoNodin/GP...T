from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from ..errors import F1EngineerError
from .input_settings import ButtonBinding, load_binding


class WheelInputUnavailable(F1EngineerError):
    pass


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
        try:
            pygame.display.init()
            pygame.joystick.init()
        except pygame.error as exception:
            self.close()
            raise WheelInputUnavailable("radio_wheel_initialization_failed") from exception

    def poll(self) -> InputFrame:
        pygame = self._pygame
        try:
            events = pygame.event.get()
            devices = []
            connected = set()
            if pygame.joystick.get_count() > 64:
                raise WheelInputUnavailable("radio_wheel_device_limit")
            for index in range(pygame.joystick.get_count()):
                stick = pygame.joystick.Joystick(index)
                stick.init()
                instance = stick.get_instance_id()
                connected.add(instance)
                self._sticks[instance] = stick
                count = stick.get_numbuttons()
                if not 0 <= count <= 512:
                    raise WheelInputUnavailable("radio_wheel_button_limit")
                devices.append(WheelDevice(instance, stick.get_guid().lower(), stick.get_name(),
                                           tuple(bool(stick.get_button(button)) for button in range(count))))
            for instance in tuple(self._sticks):
                if instance not in connected:
                    self._sticks.pop(instance).quit()
            edges = tuple(ButtonEdge(event.instance_id, event.button, event.type == pygame.JOYBUTTONDOWN)
                          for event in events if event.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP))
            if len(edges) > 256:
                raise WheelInputUnavailable("radio_wheel_event_overflow")
            return InputFrame(tuple(devices), edges)
        except pygame.error as exception:
            raise WheelInputUnavailable("radio_wheel_poll_failed") from exception

    def close(self) -> None:
        for stick in self._sticks.values():
            stick.quit()
        self._sticks.clear()
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

    def _report(self, status: str, reason=None) -> None:
        if (status, reason) != self._status:
            self._status = (status, reason)
            self._notify({"event": "radio_input", **self.status()})

    def _reset(self) -> None:
        self._connection = None
        self._armed = False

    def poll(self, *, now: float) -> bool:
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
