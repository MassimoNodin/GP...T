from __future__ import annotations

import ctypes
import pytest

from f1_engineer.radio.directinput import (BUFFER_SIZE, SHARED_BACKGROUND, BufferProperty,
    ControllerDescriptor, DataFormat, DeviceEvent, DeviceInstance, Guid, NativeController, NativeDirectInput,
    ObjectFormat, ObjectInstance, SharedWheelInput, check_result, compatible_guid)
from f1_engineer.radio.input_settings import ButtonBinding, save_binding
from f1_engineer.radio.wheel_input import WheelButtonMonitor, WheelInputUnavailable


DESCRIPTOR = ControllerDescriptor("a" * 32, "R3 Racing Wheel and Pedals", 0x346E, 5)
BINDING = ButtonBinding("030007036e3400000500000000000000", DESCRIPTOR.name, 32)


class NativeOwner:
    def __init__(self, *, fail=None, events=(), overflow=False):
        self.callback = ctypes.CFUNCTYPE
        self.pointer = ctypes.c_void_p(1)
        self.window = 7
        self.fail = fail
        self.events = events
        self.overflow = overflow
        self.calls = []
        self.state = [False] * 64
        self.flags = None
        self.offsets = None
        self.buffer = None

    def call(self, pointer, slot, types=(), arguments=()):
        self.calls.append(slot)
        if slot == self.fail:
            return -2147467259
        if slot == 3:
            ctypes.cast(arguments[1], ctypes.POINTER(ctypes.c_void_p)).contents.value = 2
        elif slot == 4:
            for index in range(64):
                value = ObjectInstance()
                value.type = 12 | (index << 8)
                if not arguments[0](ctypes.pointer(value), None):
                    break
        elif slot == 11:
            value = ctypes.cast(arguments[0], ctypes.POINTER(DataFormat)).contents
            self.offsets = [value.objects[index].offset for index in range(value.object_count)]
            assert value.data_size == 64
        elif slot == 13:
            self.flags = arguments[1]
        elif slot == 6:
            value = ctypes.cast(arguments[1], ctypes.POINTER(BufferProperty)).contents
            self.buffer = value.value
            assert value.header_size == 16 and value.how == 0
        elif slot == 10:
            events = ctypes.cast(arguments[1], ctypes.POINTER(DeviceEvent))
            for index, (button, pressed) in enumerate(self.events):
                events[index].offset = button
                events[index].data = 0x80 if pressed else 0
            ctypes.cast(arguments[2], ctypes.POINTER(ctypes.c_uint32)).contents.value = len(self.events)
            if self.overflow:
                return 1
        elif slot == 9:
            state = ctypes.cast(arguments[1], ctypes.POINTER(ctypes.c_uint8))
            for index, pressed in enumerate(self.state):
                state[index] = 0x80 if pressed else 0
        return 0


class Controller:
    def __init__(self):
        self.buttons = (False,) * 64
        self.transitions = ()
        self.closed = 0
        self.error = None

    def read(self):
        if self.error:
            raise self.error
        return self.buttons, self.transitions

    def close(self):
        self.closed += 1


class Backend:
    def __init__(self, *descriptors):
        self.descriptors = descriptors or (DESCRIPTOR,)
        self.opened = []
        self.controllers = []
        self.enumerated = 0
        self.closed = 0

    def enumerate_devices(self):
        self.enumerated += 1
        return self.descriptors

    def open_device(self, descriptor):
        self.opened.append(descriptor)
        controller = Controller()
        self.controllers.append(controller)
        return controller

    def close(self):
        self.closed += 1


def test_legacy_r3_identity_is_preserved_without_opening_sdl():
    assert DESCRIPTOR.guid == BINDING.guid
    assert compatible_guid("no vendor", 0, 0, "b" * 32) == "b" * 32


def test_native_structures_match_windows_fixed_width_layout():
    assert ctypes.sizeof(Guid) == 16
    assert ctypes.sizeof(DeviceInstance) == 1100
    assert ctypes.sizeof(ObjectInstance) == 576
    assert ctypes.sizeof(BufferProperty) == 20
    assert ctypes.sizeof(DataFormat) == (32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 24)
    assert ctypes.sizeof(ObjectFormat) == (24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)
    assert ctypes.sizeof(DeviceEvent) == (24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 20)


def test_native_controller_uses_only_shared_background_access_and_button_calls():
    owner = NativeOwner(events=((32, True), (32, False)))
    controller = NativeController(owner, DESCRIPTOR)
    buttons, transitions = controller.read()
    assert owner.flags == SHARED_BACKGROUND == 10
    assert owner.flags & 1 == 0 and owner.flags & 4 == 0
    assert owner.buffer == BUFFER_SIZE
    assert owner.offsets == list(range(64))
    assert not any(buttons) and transitions == ((32, True), (32, False))
    controller.close()
    controller.close()
    assert owner.calls.count(7) == 1 and owner.calls.count(8) == 1 and owner.calls.count(2) == 1
    assert set(owner.calls) <= {2, 3, 4, 6, 7, 8, 9, 10, 11, 13, 25}


@pytest.mark.parametrize("slot", [3, 4, 11, 13, 6, 7])
def test_native_initialization_failure_releases_partial_handle(slot):
    owner = NativeOwner(fail=slot)
    with pytest.raises(WheelInputUnavailable):
        NativeController(owner, DESCRIPTOR)
    assert owner.calls.count(2) == (0 if slot == 3 else 1)
    assert owner.calls.count(7) <= 1


@pytest.mark.parametrize("slot", [25, 10, 9])
def test_native_read_failure_does_not_reacquire(slot):
    owner = NativeOwner()
    controller = NativeController(owner, DESCRIPTOR)
    owner.fail = slot
    with pytest.raises(WheelInputUnavailable, match="restart_required"):
        controller.read()
    controller.close()
    assert owner.calls.count(7) == 1 and owner.calls.count(2) == 1


def test_native_read_vtable_slots_match_sdk_including_get_object_info_slot():
    calls = []
    callbacks = []
    table = (ctypes.c_void_p * 33)()
    def retain(slot, types, handler):
        callback = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, *types)(handler)
        callbacks.append(callback)
        table[slot] = ctypes.cast(callback, ctypes.c_void_p).value
    def poll(pointer):
        calls.append("Poll")
        return 0
    def read_events(pointer, size, events, count, flags):
        calls.append("GetDeviceData")
        count.contents.value = 0
        return 0
    def read_state(pointer, size, state):
        calls.append("GetDeviceState")
        ctypes.memset(state, 0, size)
        return 0
    retain(25, (), poll)
    retain(10, (ctypes.c_uint32, ctypes.POINTER(DeviceEvent), ctypes.POINTER(ctypes.c_uint32),
               ctypes.c_uint32), read_events)
    retain(9, (ctypes.c_uint32, ctypes.c_void_p), read_state)
    owner = NativeDirectInput.__new__(NativeDirectInput)
    owner.callback = ctypes.CFUNCTYPE
    controller = NativeController.__new__(NativeController)
    controller.owner = owner
    controller.pointer = ctypes.cast(ctypes.pointer(ctypes.cast(table, ctypes.POINTER(ctypes.c_void_p))),
                                     ctypes.c_void_p)
    controller.button_count = 64
    controller.data_size = 64
    assert controller.read() == ((False,) * 64, ())
    assert calls == ["Poll", "GetDeviceData", "GetDeviceState"]


def test_buffer_overflow_is_rejected_not_interpreted_as_button_press():
    owner = NativeOwner(overflow=True, events=((32, True),))
    controller = NativeController(owner, DESCRIPTOR)
    with pytest.raises(WheelInputUnavailable, match="event_overflow"):
        controller.read()
    controller.close()


def test_invalid_buffered_button_offset_is_rejected():
    owner = NativeOwner(events=((128, True),))
    controller = NativeController(owner, DESCRIPTOR)
    with pytest.raises(WheelInputUnavailable, match="event_invalid"):
        controller.read()
    controller.close()


@pytest.mark.parametrize("result,reason", [(-2147024891, "access_denied_restart_required"),
                                         (-2147024866, "disconnected")])
def test_specific_hresult_has_safe_recovery_classification(result, reason):
    with pytest.raises(WheelInputUnavailable, match=reason):
        check_result(result, "fallback")


def test_shared_provider_opens_only_selected_wheel_and_reuses_it():
    other = ControllerDescriptor("b" * 32, "Other controller", 123, 45)
    backend = Backend(DESCRIPTOR, other)
    source = SharedWheelInput(backend=backend, clock=lambda: 0)
    source.select_binding(BINDING)
    first = source.poll()
    for iteration in range(100):
        assert source.poll() == first
    assert backend.opened == [DESCRIPTOR] and backend.enumerated == 1
    source.close()
    source.close()
    assert backend.controllers[0].closed == 1 and backend.closed == 1
    with pytest.raises(WheelInputUnavailable, match="source_closed"):
        source.poll()


def test_dynamic_button_change_reuses_handle_and_preserves_short_press(tmp_path):
    backend = Backend()
    source = SharedWheelInput(backend=backend, clock=lambda: 0)
    settings = tmp_path / "input.json"
    save_binding(settings, BINDING)
    watcher = WheelButtonMonitor(settings, source_factory=lambda: source)
    assert not watcher.poll(now=0)
    backend.controllers[0].transitions = ((32, True), (32, False))
    assert watcher.poll(now=0.1)
    backend.controllers[0].transitions = ()
    replacement = ButtonBinding(BINDING.guid, BINDING.name, 3)
    save_binding(settings, replacement)
    assert not watcher.poll(now=0.5)
    backend.controllers[0].transitions = ((3, True), (3, False))
    assert watcher.poll(now=0.8)
    assert len(backend.opened) == 1
    watcher.close()


def test_hotplug_releases_old_handle_and_changes_connection_identity():
    now = [0.0]
    backend = Backend()
    source = SharedWheelInput(backend=backend, clock=lambda: now[0], scan_interval=1)
    source.select_binding(BINDING)
    initial = source.poll().devices[0].instance_id
    backend.descriptors = ()
    now[0] = 1
    assert not source.poll().devices and backend.controllers[0].closed == 1
    backend.descriptors = (DESCRIPTOR,)
    now[0] = 2
    assert source.poll().devices[0].instance_id != initial
    assert len(backend.opened) == 2
    source.close()


def test_ambiguous_identical_wheels_are_rejected_before_acquisition():
    other = ControllerDescriptor("b" * 32, DESCRIPTOR.name, DESCRIPTOR.vendor, DESCRIPTOR.product)
    backend = Backend(DESCRIPTOR, other)
    source = SharedWheelInput(backend=backend)
    source.select_binding(BINDING)
    with pytest.raises(WheelInputUnavailable, match="identity_ambiguous"):
        source.poll()
    assert not backend.opened
    source.close()


def test_native_failure_latches_monitor_without_retry_and_closes_backend(tmp_path):
    backend = Backend()
    source = SharedWheelInput(backend=backend)
    settings = tmp_path / "input.json"
    save_binding(settings, BINDING)
    watcher = WheelButtonMonitor(settings, source_factory=lambda: source)
    assert not watcher.poll(now=0)
    backend.controllers[0].error = WheelInputUnavailable("radio_wheel_state_failed_restart_required")
    assert not watcher.poll(now=0.1)
    for now in (1, 10, 100):
        assert not watcher.poll(now=now)
    assert backend.controllers[0].closed == 1 and backend.closed == 1 and len(backend.opened) == 1
    watcher.close()


def test_default_input_provider_is_shared_and_never_opens_sdl(tmp_path, monkeypatch):
    from f1_engineer.radio import directinput, wheel_input
    backend = Backend()
    source = SharedWheelInput(backend=backend)
    monkeypatch.setattr(directinput, "SharedWheelInput", lambda: source)
    monkeypatch.setattr(wheel_input, "SDLWheelInput", lambda: pytest.fail("exclusive backend forbidden"))
    settings = tmp_path / "input.json"
    save_binding(settings, BINDING)
    watcher = WheelButtonMonitor(settings)
    assert not watcher.poll(now=0)
    assert watcher.status()["status"] == "ready"
    assert backend.opened == [DESCRIPTOR]
    watcher.close()


def test_cleanup_attempts_all_handles_and_root_even_if_one_release_fails():
    other = ControllerDescriptor("b" * 32, "Other controller", 123, 45)
    backend = Backend(DESCRIPTOR, other)
    source = SharedWheelInput(backend=backend)
    source.poll()
    def fail():
        raise RuntimeError("private driver detail")
    backend.controllers[0].close = fail
    with pytest.raises(WheelInputUnavailable, match="cleanup_failed_restart_required"):
        source.close()
    assert backend.controllers[1].closed == 1 and backend.closed == 1
    assert not source.handles and source.closed
    source.close()


@pytest.mark.parametrize("value", [0, -1, 11, float("nan"), float("inf"), True, "5"])
def test_invalid_scan_interval_never_initializes_native_input(value, monkeypatch):
    from f1_engineer.radio import directinput
    monkeypatch.setattr(directinput, "NativeDirectInput", lambda: pytest.fail("no hardware access"))
    with pytest.raises(ValueError, match="scan_interval_invalid"):
        SharedWheelInput(scan_interval=value)
