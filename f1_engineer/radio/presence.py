"""A separate heartbeat worker keeps network/device discovery off the radio loop."""
from __future__ import annotations

import threading
import uuid
from importlib.metadata import PackageNotFoundError, version

from .audio import list_audio_devices


class PresenceReporter:
    def __init__(self, client, stop):
        self.client, self.stop = client, stop
        self.instance_id = uuid.uuid4().hex
        self.state = None
        self._lock = threading.Lock()
        self._refresh = threading.Event()

    def request_refresh(self):
        self._refresh.set()

    def publish(self, *, revision, engine, speaking, listening, wheel_status):
        with self._lock:
            self.state = {"applied_revision": revision, "tts_engine": engine, "speaking": speaking,
                          "listening": listening, "wheel_status": str(wheel_status)[:128]}

    def devices(self):
        try:
            return [{"index": item["index"], "name": item["name"][:256],
                        "input": item["input_channels"] > 0, "output": item["output_channels"] > 0}
                       for item in list_audio_devices()["devices"][:64]]
        except Exception:
            return []

    def run(self):
        devices = self.devices()
        try:
            package_version = version("f1-race-engineer")
        except PackageNotFoundError:
            package_version = "development"
        voices = {"edge": ["en-AU-WilliamNeural", "en-AU-NatashaNeural"], "windows": []}
        try:
            from .windows_speech import WindowsSpeech
            speech = WindowsSpeech()
            voices["windows"] = speech.voices[:64]
            speech.close()
        except Exception:
            pass
        while not self.stop.is_set():
            if self._refresh.is_set():
                self._refresh.clear()
                devices = self.devices()
            with self._lock:
                state = self.state.copy() if self.state else None
            if state:
                self.client.report_presence({**state, "instance_id": self.instance_id,
                                             "version": package_version, "devices": devices,
                                             "voices": voices[state["tts_engine"]]})
            self.stop.wait(2)
