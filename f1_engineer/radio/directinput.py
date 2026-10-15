from __future__ import annotations

import ctypes
import itertools
import struct
import sys
import time
import uuid
from dataclasses import dataclass

from .wheel_input import ButtonEdge, InputFrame, WheelDevice, WheelInputUnavailable


SHARED_BACKGROUND = 0x00000002 | 0x00000008
BUTTON_TYPE = 0x0000000C
BUFFER_SIZE = 256
_instances = itertools.count(1)


def compatible_guid(name: str, vendor: int, product: int, instance: str) -> str:
    if not vendor:
        return instance
    checksum = 0
    for value in name.encode("utf-8"):
        checksum ^= value
        for bit in range(8):
            checksum = (checksum >> 1) ^ (0xA001 if checksum & 1 else 0)
    return struct.pack("<7H2B", 3, checksum, vendor, 0, product, 0, 0, 0, 0).hex()


@dataclass(frozen=True)
class ControllerDescriptor:
    instance: str
    name: str
    vendor: int
    product: int

    @property
    def guid(self) -> str:
        return compatible_guid(self.name, self.vendor, self.product, self.instance)


class Guid(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                ("data3", ctypes.c_uint16), ("data4", ctypes.c_uint8 * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class DeviceInstance(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("instance", Guid), ("product", Guid),
                ("device_type", ctypes.c_uint32), ("instance_name", ctypes.c_uint16 * 260),
                ("product_name", ctypes.c_uint16 * 260), ("force_driver", Guid),
                ("usage_page", ctypes.c_uint16), ("usage", ctypes.c_uint16)]


class ObjectInstance(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("guid", Guid), ("offset", ctypes.c_uint32),
                ("type", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("name", ctypes.c_uint16 * 260), ("force_max", ctypes.c_uint32),
                ("force_resolution", ctypes.c_uint32), ("collection", ctypes.c_uint16),
                ("designator", ctypes.c_uint16), ("usage_page", ctypes.c_uint16),
                ("usage", ctypes.c_uint16), ("dimension", ctypes.c_uint32),
                ("exponent", ctypes.c_uint16), ("reserved", ctypes.c_uint16)]


class ObjectFormat(ctypes.Structure):
    _fields_ = [("guid", ctypes.c_void_p), ("offset", ctypes.c_uint32),
                ("type", ctypes.c_uint32), ("flags", ctypes.c_uint32)]


class DataFormat(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("object_size", ctypes.c_uint32),
                ("flags", ctypes.c_uint32), ("data_size", ctypes.c_uint32),
                ("object_count", ctypes.c_uint32), ("objects", ctypes.POINTER(ObjectFormat))]


class BufferProperty(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("header_size", ctypes.c_uint32),
                ("object", ctypes.c_uint32), ("how", ctypes.c_uint32), ("value", ctypes.c_uint32)]


class DeviceEvent(ctypes.Structure):
    _fields_ = [("offset", ctypes.c_uint32), ("data", ctypes.c_uint32),
                ("timestamp", ctypes.c_uint32), ("sequence", ctypes.c_uint32),
                ("application_data", ctypes.c_size_t)]


class NativeInputError(WheelInputUnavailable):
    def __init__(self, reason, result):
        super().__init__(reason)
        self.hresult = result & 0xFFFFFFFF


def check_result(result: int, reason: str) -> None:
    if result < 0:
        if result & 0xFFFFFFFF == 0x80070005:
            reason = "radio_wheel_access_denied_restart_required"
        elif result & 0xFFFFFFFF == 0x8007001E:
            reason = "radio_wheel_disconnected"
        raise NativeInputError(reason, result)


class NativeDirectInput:
    def __init__(self):
        if sys.platform != "win32":
            raise WheelInputUnavailable("radio_wheel_requires_windows")
        self.pointer = ctypes.c_void_p()
        self.window = None
        self.callback = ctypes.WINFUNCTYPE
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        library = ctypes.WinDLL("dinput8", use_last_error=True)
        kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        kernel.GetModuleHandleW.restype = ctypes.c_void_p
        self.user.CreateWindowExW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p,
            ctypes.c_uint32, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        self.user.CreateWindowExW.restype = ctypes.c_void_p
        self.user.DestroyWindow.argtypes = [ctypes.c_void_p]
        self.user.DestroyWindow.restype = ctypes.c_int
        library.DirectInput8Create.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Guid),
                                             ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        library.DirectInput8Create.restype = ctypes.c_int32
        self.library = library
        try:
            module = kernel.GetModuleHandleW(None)
            self.window = self.user.CreateWindowExW(0, "STATIC", "F1 Engineer shared input",
                0x80000000, 0, 0, 0, 0, None, None, module, None)
            if not self.window:
                raise WheelInputUnavailable("radio_wheel_window_unavailable")
            interface = Guid.parse("bf798031-483a-4da2-aa99-5d64ed369700")
            check_result(library.DirectInput8Create(module, 0x0800, ctypes.byref(interface),
                ctypes.byref(self.pointer), None), "radio_wheel_initialization_failed")
        except BaseException:
            self.close()
            raise

    def call(self, pointer, slot, types=(), arguments=()):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        method = self.callback(ctypes.c_int32, ctypes.c_void_p, *types)(table[slot])
        return method(pointer, *arguments)

    def enumerate_devices(self):
        devices = []
        errors = []
        callback_type = self.callback(ctypes.c_int, ctypes.POINTER(DeviceInstance), ctypes.c_void_p)
        def collect(info, context):
            try:
                if len(devices) >= 64:
                    raise WheelInputUnavailable("radio_wheel_device_limit")
                value = info.contents
                name = bytes(value.product_name).decode("utf-16-le").split("\0", 1)[0]
                if not 1 <= len(name) <= 256 or any(ord(character) < 32 for character in name):
                    raise WheelInputUnavailable("radio_wheel_identity_invalid")
                devices.append(ControllerDescriptor(uuid.UUID(bytes_le=bytes(value.instance)).hex, name,
                    value.product.data1 & 0xFFFF, value.product.data1 >> 16))
                return 1
            except Exception as exception:
                errors.append(exception)
                return 0
        callback = callback_type(collect)
        result = self.call(self.pointer, 4, (ctypes.c_uint32, callback_type, ctypes.c_void_p, ctypes.c_uint32),
                           (4, callback, None, 1))
        check_result(result, "radio_wheel_enumeration_failed")
        if errors:
            raise WheelInputUnavailable("radio_wheel_enumeration_failed") from errors[0]
        return tuple(devices)

    def open_device(self, descriptor):
        return NativeController(self, descriptor)

    def close(self):
        try:
            if self.pointer.value:
                pointer = self.pointer
                self.pointer = ctypes.c_void_p()
                self.call(pointer, 2)
        finally:
            if self.window:
                window = self.window
                self.window = None
                self.user.DestroyWindow(window)


class NativeController:
    def __init__(self, owner, descriptor):
        self.owner = owner
        self.pointer = ctypes.c_void_p()
        try:
            instance = Guid.parse(descriptor.instance)
            check_result(owner.call(owner.pointer, 3, (ctypes.POINTER(Guid),
                ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p),
                (ctypes.byref(instance), ctypes.byref(self.pointer), None)), "radio_wheel_open_failed_restart_required")
            object_types = []
            callback_type = owner.callback(ctypes.c_int, ctypes.POINTER(ObjectInstance), ctypes.c_void_p)
            def collect(info, context):
                object_types.append(info.contents.type)
                return int(len(object_types) <= 128)
            callback = callback_type(collect)
            check_result(owner.call(self.pointer, 4, (callback_type, ctypes.c_void_p, ctypes.c_uint32),
                (callback, None, BUTTON_TYPE)), "radio_wheel_button_enumeration_failed")
            self.button_count = len(object_types)
            if not 1 <= self.button_count <= 128:
                raise WheelInputUnavailable("radio_wheel_button_limit")
            self.objects = (ObjectFormat * self.button_count)(
                *(ObjectFormat(None, index, object_type, 0) for index, object_type in enumerate(object_types)))
            self.data_size = (self.button_count + 3) & ~3
            self.format = DataFormat(ctypes.sizeof(DataFormat), ctypes.sizeof(ObjectFormat),
                                     2, self.data_size, self.button_count, self.objects)
            check_result(owner.call(self.pointer, 11, (ctypes.POINTER(DataFormat),),
                (ctypes.byref(self.format),)), "radio_wheel_format_failed_restart_required")
            check_result(owner.call(self.pointer, 13, (ctypes.c_void_p, ctypes.c_uint32),
                (owner.window, SHARED_BACKGROUND)), "radio_wheel_shared_access_failed_restart_required")
            buffer = BufferProperty(ctypes.sizeof(BufferProperty), 16, 0, 0, BUFFER_SIZE)
            check_result(owner.call(self.pointer, 6, (ctypes.c_void_p, ctypes.POINTER(BufferProperty)),
                (ctypes.c_void_p(1), ctypes.byref(buffer))), "radio_wheel_buffer_failed_restart_required")
            check_result(owner.call(self.pointer, 7), "radio_wheel_acquire_failed_restart_required")
        except BaseException:
            self.close()
            raise

    def read(self):
        owner = self.owner
        check_result(owner.call(self.pointer, 25), "radio_wheel_poll_failed_restart_required")
        events = (DeviceEvent * BUFFER_SIZE)()
        count = ctypes.c_uint32(BUFFER_SIZE)
        result = owner.call(self.pointer, 10, (ctypes.c_uint32, ctypes.POINTER(DeviceEvent),
            ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32),
            (ctypes.sizeof(DeviceEvent), events, ctypes.byref(count), 0))
        check_result(result, "radio_wheel_read_failed_restart_required")
        if result == 1 or count.value > BUFFER_SIZE:
            raise WheelInputUnavailable("radio_wheel_event_overflow")
        transitions = []
        for event in events[:count.value]:
            if event.offset >= self.button_count:
                raise WheelInputUnavailable("radio_wheel_event_invalid")
            transitions.append((event.offset, bool(event.data & 0x80)))
        state = (ctypes.c_uint8 * self.data_size)()
        check_result(owner.call(self.pointer, 9, (ctypes.c_uint32, ctypes.c_void_p),
            (self.data_size, state)), "radio_wheel_state_failed_restart_required")
        return tuple(bool(value & 0x80) for value in state[:self.button_count]), tuple(transitions)

    def close(self):
        if self.pointer.value:
            pointer = self.pointer
            self.pointer = ctypes.c_void_p()
            try:
                self.owner.call(pointer, 8)
            finally:
                self.owner.call(pointer, 2)


class SharedWheelInput:
    def __init__(self, *, backend=None, clock=time.monotonic, scan_interval=5):
        if type(scan_interval) not in (int, float) or not 0.25 <= scan_interval <= 10:
            raise ValueError("radio_wheel_scan_interval_invalid")
        self.backend = backend if backend is not None else NativeDirectInput()
        self.clock = clock
        self.scan_interval = scan_interval
        self.binding = None
        self.handles = {}
        self.descriptors = ()
        self.next_scan = 0.0
        self.closed = False

    def select_binding(self, binding):
        if binding != self.binding:
            if self.binding is None or binding is None or (binding.guid, binding.name) != (self.binding.guid, self.binding.name):
                self._release_handles()
                self.next_scan = 0.0
            self.binding = binding

    def poll(self):
        if self.closed:
            raise WheelInputUnavailable("radio_wheel_source_closed")
        now = self.clock()
        if now >= self.next_scan:
            descriptors = self.backend.enumerate_devices()
            if len(descriptors) > 64 or len({value.instance for value in descriptors}) != len(descriptors):
                raise WheelInputUnavailable("radio_wheel_identity_ambiguous")
            self.descriptors = descriptors
            self.next_scan = now + self.scan_interval
        selected = self.descriptors
        if self.binding is not None:
            selected = tuple(value for value in selected if (value.guid, value.name) == (self.binding.guid, self.binding.name))
            if len(selected) > 1:
                raise WheelInputUnavailable("radio_wheel_identity_ambiguous")
        connected = {value.instance for value in selected}
        for identity in tuple(self.handles):
            if identity not in connected:
                controller, instance = self.handles.pop(identity)
                controller.close()
        devices = []
        edges = []
        for descriptor in selected:
            if descriptor.instance not in self.handles:
                self.handles[descriptor.instance] = self.backend.open_device(descriptor), next(_instances)
            controller, instance = self.handles[descriptor.instance]
            buttons, transitions = controller.read()
            if not 1 <= len(buttons) <= 128 or len(transitions) > BUFFER_SIZE:
                raise WheelInputUnavailable("radio_wheel_button_limit")
            devices.append(WheelDevice(instance, descriptor.guid, descriptor.name, buttons))
            for button, pressed in transitions:
                if type(button) is not int or not 0 <= button < len(buttons) or type(pressed) is not bool:
                    raise WheelInputUnavailable("radio_wheel_event_invalid")
                edges.append(ButtonEdge(instance, button, pressed))
        if len(edges) > BUFFER_SIZE:
            raise WheelInputUnavailable("radio_wheel_event_overflow")
        return InputFrame(tuple(devices), tuple(edges))

    def _release_handles(self):
        handles = tuple(self.handles.values())
        self.handles.clear()
        errors = []
        for controller, instance in handles:
            try:
                controller.close()
            except Exception as exception:
                errors.append(exception)
        if errors:
            raise WheelInputUnavailable("radio_wheel_cleanup_failed_restart_required") from errors[0]

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self._release_handles()
            finally:
                self.backend.close()
