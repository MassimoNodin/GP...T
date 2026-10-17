from __future__ import annotations

import array
import math
import sys

from ..analysis.local_speech import SPEECH_SAMPLE_RATE
from ..errors import F1EngineerError


class RadioAudioUnavailable(F1EngineerError):
    pass


def _sounddevice():
    try:
        import sounddevice
        return sounddevice
    except (ImportError, OSError) as exception:
        raise RadioAudioUnavailable("radio_audio_requires_radio_extra") from exception


def list_audio_devices() -> dict:
    sounddevice = _sounddevice()
    try:
        devices = sounddevice.query_devices()
        defaults = list(sounddevice.default.device)
        return {"event": "radio_audio_devices", "default_input": defaults[0], "default_output": defaults[1],
                "devices": [{"index": index, "name": device["name"],
                             "input_channels": device["max_input_channels"],
                             "output_channels": device["max_output_channels"],
                             "host_api": device["hostapi"]}
                            for index, device in enumerate(devices)]}
    except sounddevice.PortAudioError as exception:
        raise RadioAudioUnavailable("radio_audio_devices_unavailable") from exception


def cue_pcm(kind: str, volume: float) -> bytes:
    if not math.isfinite(volume) or not 0 < volume <= 0.3:
        raise ValueError("radio_cue_volume_invalid")
    tones = {"start": ((900, 0.06), (1300, 0.06)), "end": ((700, 0.1),),
             "pulse": ((1100, 0.025),)}
    if kind not in tones:
        raise ValueError("radio_cue_kind_invalid")
    samples = array.array("h")
    level = volume * (0.4 if kind == "pulse" else 1)
    for frequency, duration in tones[kind]:
        count = round(duration * 48_000)
        for index in range(count):
            envelope = min(1, index / 240, (count - 1 - index) / 240)
            samples.append(round(32767 * level * envelope * math.sin(2 * math.pi * frequency * index / 48_000)))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


class NativeRadioAudio:
    def __init__(self, *, microphone=None, output=None, cue_volume=0.08) -> None:
        self._sounddevice = _sounddevice()
        self.microphone = microphone
        self.output = output
        self.cue_volume = cue_volume
        self._stream = None

    def cue(self, kind: str) -> None:
        sounddevice = self._sounddevice
        try:
            with sounddevice.RawOutputStream(device=self.output, samplerate=48_000,
                                             channels=1, dtype="int16") as stream:
                underflow = stream.write(cue_pcm(kind, self.cue_volume))
                if underflow:
                    raise RadioAudioUnavailable("radio_cue_output_underflow")
        except (sounddevice.PortAudioError, ValueError) as exception:
            raise RadioAudioUnavailable("radio_cue_output_unavailable") from exception

    def start(self) -> None:
        sounddevice = self._sounddevice
        try:
            if self._stream is None:
                self._stream = sounddevice.RawInputStream(device=self.microphone, samplerate=SPEECH_SAMPLE_RATE,
                                                        channels=1, dtype="int16", blocksize=320)
            self._stream.start()
        except (sounddevice.PortAudioError, ValueError) as exception:
            raise RadioAudioUnavailable("radio_microphone_unavailable") from exception

    def read(self) -> bytes:
        try:
            data, overflow = self._stream.read(320)
            if overflow:
                raise RadioAudioUnavailable("radio_microphone_overflow")
            return bytes(data)
        except self._sounddevice.PortAudioError as exception:
            raise RadioAudioUnavailable("radio_microphone_read_failed") from exception

    def pause(self) -> None:
        try:
            self._stream.abort()
        except self._sounddevice.PortAudioError as exception:
            raise RadioAudioUnavailable("radio_microphone_stop_failed") from exception

    def close(self) -> None:
        stream = self._stream
        if stream is not None:
            try:
                stream.abort()
            finally:
                stream.close()
                self._stream = None
