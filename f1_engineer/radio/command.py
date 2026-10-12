from __future__ import annotations

import json
import math
import queue
import sys
import threading
import time
from pathlib import Path

from .client import RadioClient
from .controller import RadioController
from .scheduler import RadioScheduler
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
    if args.list_voices:
        playback = WindowsSpeech(voice=args.voice, rate=args.rate, volume=args.volume)
        try:
            emit({"event": "radio_voices", "voices": playback.voices})
        finally:
            playback.close()
        return 0
    client = RadioClient(args.api_url, Path(args.token_file))
    scheduler = None
    stop = threading.Event()
    worker = None
    try:
        playback = DryRunPlayback() if args.dry_run or args.once else WindowsSpeech(
            voice=args.voice, rate=args.rate, volume=args.volume)
        scheduler = RadioScheduler(playback, notify=emit)
        controller = RadioController(scheduler, notify=emit)
        started = time.monotonic()
        snapshot = client.fetch()
        controller.feed(snapshot, now=time.monotonic(), transport_age_s=time.monotonic() - started)
        if args.once:
            return 0 if controller.status()["backend"] == "available" else 2
        emit({"event": "radio_ready", "dry_run": args.dry_run, "voice": getattr(playback, "voice", None),
              "rate": args.rate, "volume": args.volume,
              "commands": ["lap", "quiet", "resume", "cancel", "repeat", "status", "quit"]})
        snapshots: queue.Queue = queue.Queue(maxsize=1)
        commands: queue.Queue = queue.Queue(maxsize=16)
        worker = threading.Thread(target=_poll, args=(client, snapshots, stop, args.poll_interval),
                                  name="radio-backend", daemon=True)
        worker.start()
        threading.Thread(target=_commands, args=(commands, sys.stdin), name="radio-commands", daemon=True).start()
        while not stop.is_set():
            now = time.monotonic()
            try:
                snapshot, started = snapshots.get_nowait()
                controller.feed(snapshot, now=now, transport_age_s=now - started)
            except queue.Empty:
                pass
            controller.check_freshness(now=now)
            try:
                command = commands.get_nowait()
                if command == "quit":
                    stop.set()
                elif command == "lap":
                    controller.answer_last_lap(now=now)
                elif command == "quiet":
                    scheduler.quiet()
                elif command == "resume":
                    scheduler.muted = False
                elif command == "cancel":
                    scheduler.cancel()
                elif command == "repeat":
                    if not scheduler.repeat(now=now):
                        emit({"event": "radio_repeat_unavailable"})
                elif command == "status":
                    emit({"event": "radio_status", **controller.status()})
                else:
                    emit({"event": "radio_command_unavailable"})
            except queue.Empty:
                pass
            if not stop.is_set():
                scheduler.tick(now=now)
            stop.wait(0.05)
        return 0
    finally:
        stop.set()
        try:
            if scheduler is not None:
                scheduler.close()
        finally:
            if worker is not None:
                worker.join(timeout=7)
            client.close()
