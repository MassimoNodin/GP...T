from __future__ import annotations

import json
import math
import queue
import sys
import threading
import time
from pathlib import Path

from .audio import list_audio_devices
from .client import RadioClient
from .controller import RadioController
from .input_settings import DEFAULT_INPUT_SETTINGS
from .listening import VoiceInput
from .scheduler import RadioScheduler
from .wheel_input import WheelButtonMonitor
from .windows_speech import WindowsSpeech


def emit(event: dict) -> None:
    print(json.dumps(event, separators=(",", ":")), flush=True)


class DryRunPlayback:
    def start(self, text: str) -> None:
        pass

    def busy(self) -> bool:
        return False

    def cancel(self) -> None:
        pass

    def close(self) -> None:
        pass


def _commands(inbox: queue.Queue, stream) -> None:
    while True:
        line = stream.readline(1025)
        if not line:
            inbox.put("quit")
            return
        if len(line) > 1024:
            while line and not line.endswith("\n"):
                line = stream.readline(1025)
            inbox.put("invalid")
        else:
            inbox.put(line.strip().lower().removeprefix("/"))


def _poll(client: RadioClient, inbox: queue.Queue, stop: threading.Event, interval: float) -> None:
    while not stop.is_set():
        started = time.monotonic()
        snapshot = client.fetch()
        try:
            inbox.get_nowait()
        except queue.Empty:
            pass
        inbox.put((snapshot, started))
        stop.wait(interval)


def run_radio(args) -> int:
    if (not math.isfinite(args.poll_interval) or not 0.1 <= args.poll_interval <= 1
            or not -10 <= args.rate <= 10 or not 0 <= args.volume <= 100):
        raise ValueError("invalid radio settings")
    if (not math.isfinite(args.capture_seconds) or not 1 <= args.capture_seconds <= 12
            or not math.isfinite(args.cue_volume) or not 0 < args.cue_volume <= 0.3
            or any(value is not None and value < 0 for value in (args.microphone, args.cue_output))):
        raise ValueError("invalid capture settings")
    if args.list_audio_devices:
        emit(list_audio_devices())
        return 0
    if args.capture_test and (args.once or args.list_voices):
        raise ValueError("capture-test cannot be combined with once or list-voices")
    if args.list_voices:
        playback = WindowsSpeech(voice=args.voice, rate=args.rate, volume=args.volume)
        try:
            emit({"event": "radio_voices", "voices": playback.voices})
        finally:
            playback.close()
        return 0
    client = None if args.capture_test else RadioClient(args.api_url, Path(args.token_file))
    scheduler = None
    wheel = None
    voice = None
    stop = threading.Event()
    worker = None
    try:
        playback = DryRunPlayback() if args.dry_run or args.once or args.capture_test else WindowsSpeech(
            voice=args.voice, rate=args.rate, volume=args.volume)
        scheduler = RadioScheduler(playback, notify=emit)
        controller = RadioController(scheduler, notify=emit)
        started = time.monotonic()
        snapshot = client.fetch() if client else {"status": "unavailable", "reason": "capture_test"}
        controller.feed(snapshot, now=time.monotonic(), transport_age_s=time.monotonic() - started)
        if args.once:
            return 0 if controller.status()["backend"] == "available" else 2
        wheel = WheelButtonMonitor(Path(getattr(args, "input_settings", DEFAULT_INPUT_SETTINGS)), notify=emit)
        voice = VoiceInput(transcribe=client.transcribe if client and not args.dry_run else None, notify=emit,
                           enabled=not args.dry_run, microphone=args.microphone, output=args.cue_output,
                           cue_volume=args.cue_volume, maximum_seconds=args.capture_seconds)
        emit({"event": "radio_ready", "dry_run": args.dry_run, "voice": getattr(playback, "voice", None),
              "rate": args.rate, "volume": args.volume,
              "capture_test": args.capture_test,
              "commands": ["lap", "listen", "finish", "quiet", "resume", "cancel", "repeat", "status", "quit"]})
        snapshots: queue.Queue = queue.Queue(maxsize=1)
        commands: queue.Queue = queue.Queue(maxsize=16)
        if client is not None:
            worker = threading.Thread(target=_poll, args=(client, snapshots, stop, args.poll_interval),
                                      name="radio-backend", daemon=True)
            worker.start()
        threading.Thread(target=_commands, args=(commands, sys.stdin), name="radio-commands", daemon=True).start()
        while not stop.is_set():
            now = time.monotonic()
            scheduler.suspended = voice.blocks_playback
            try:
                snapshot, started = snapshots.get_nowait()
                controller.feed(snapshot, now=now, transport_age_s=now - started)
            except queue.Empty:
                pass
            controller.check_freshness(now=now)
            pressed = wheel.poll(now=now)
            voice.tick(scope=scheduler.scope, input_status=wheel.status())
            if pressed:
                request_listening(scheduler, voice, source="wheel", binding=wheel.status()["binding"],
                                  connection=wheel.status().get("connection_instance"))
            try:
                command = commands.get_nowait()
                if command == "quit":
                    stop.set()
                elif command == "lap":
                    voice.cancel()
                    controller.answer_last_lap(now=now)
                elif command == "listen":
                    request_listening(scheduler, voice, source="console")
                elif command == "finish":
                    voice.finish()
                elif command == "quiet":
                    voice.cancel()
                    scheduler.quiet()
                elif command == "resume":
                    scheduler.muted = False
                elif command == "cancel":
                    voice.cancel()
                    scheduler.cancel()
                elif command == "repeat":
                    voice.cancel()
                    if not scheduler.repeat(now=now):
                        emit({"event": "radio_repeat_unavailable"})
                elif command == "status":
                    emit({"event": "radio_status", **controller.status(), "input": wheel.status(),
                          "voice_input": voice.status()})
                else:
                    emit({"event": "radio_command_unavailable"})
            except queue.Empty:
                pass
            if not stop.is_set():
                scheduler.suspended = voice.blocks_playback
                scheduler.tick(now=now)
            stop.wait(0.05)
        return 0
    finally:
        stop.set()
        try:
            try:
                try:
                    if voice is not None:
                        voice.close()
                finally:
                    if wheel is not None:
                        wheel.close()
            finally:
                if scheduler is not None:
                    scheduler.close()
        finally:
            if worker is not None:
                worker.join(timeout=7)
            if client is not None:
                client.close()


def request_listening(scheduler: RadioScheduler, voice: VoiceInput, *, source: str, binding=None, connection=None) -> None:
    scheduler.cancel()
    voice.request(source=source, scope=scheduler.scope, binding=binding, connection=connection)
