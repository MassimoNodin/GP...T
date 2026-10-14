from __future__ import annotations

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
from .region_captures import RadioRegionCaptures
from .opponent_captures import RadioOpponentCaptures
from .track_geometry import RadioTrackGeometry
from .scheduler import RadioScheduler
from .wheel_input import WheelButtonMonitor
from .windows_speech import WindowsSpeech
from .diagnostics import emit, radio_event_log, trace_event


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
            text = line.strip().removeprefix("/")
            inbox.put("ask " + text[4:] if text[:4].lower() == "ask " else text.lower())


def _poll(client: RadioClient, inbox: queue.Queue, stop: threading.Event, interval: float, captures=None, opponent_captures=None, geometry=None) -> None:
    try:
        while not stop.is_set():
            started = time.monotonic()
            snapshot = client.fetch()
            results = captures.poll() if captures is not None else []
            if opponent_captures is not None:
                results += opponent_captures.poll(snapshot)
            if geometry is not None:
                results += geometry.poll(snapshot)
            trace_event({"event": "radio_poll", "poll_elapsed_s": time.monotonic() - started,
                         "status": snapshot.get("status"), "reason": snapshot.get("reason"),
                         "generation": snapshot.get("generation"), "session_id": snapshot.get("session_id"),
                         "scope": snapshot.get("scope"), "age_s": snapshot.get("age_s"),
                         "frame_ordinal": snapshot.get("frame_ordinal"),
                         "received_monotonic_ns": snapshot.get("received_monotonic_ns"),
                         "event_cursor": snapshot.get("event_cursor"), "event_floor": snapshot.get("event_floor"),
                         "player_car_index": snapshot.get("player_car_index"),
                         "driver_count": len(snapshot["drivers"]) if isinstance(snapshot.get("drivers"), list) else None,
                         "capture_results": [{key: value for key, value in item.items()
                                              if key in ("task_id", "version", "lease_id", "kind", "request_age_s", "received_at")}
                                             for item in results if isinstance(item, dict)]})
            try:
                inbox.get_nowait()
            except queue.Empty:
                pass
            inbox.put((snapshot, started, results))
            stop.wait(interval)
    finally:
        try:
            if captures is not None:
                captures.close()
        finally:
            try:
                if opponent_captures is not None:
                    opponent_captures.close()
            finally:
                client.close()


def run_radio(args) -> int:
    with radio_event_log(getattr(args, "log_file", None)):
        return _run_radio(args)


def _run_radio(args) -> int:
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
    captures = RadioRegionCaptures(client, notify=emit) if client is not None else None
    opponent_captures = RadioOpponentCaptures(client, notify=emit) if client is not None else None
    geometry = RadioTrackGeometry(client) if client is not None else None
    try:
        playback = DryRunPlayback() if args.dry_run or args.once or args.capture_test else WindowsSpeech(
            voice=args.voice, rate=args.rate, volume=args.volume)
        scheduler = RadioScheduler(playback, notify=emit)
        controller = RadioController(scheduler, notify=emit, backend_regions=client is not None)
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
              "microphone": args.microphone, "cue_output": args.cue_output,
              "poll_interval_s": args.poll_interval, "capture_seconds": args.capture_seconds,
              "voice_submission": ("disabled" if args.capture_test else
                                   "manual" if getattr(args, "manual_submit", False) else "automatic"),
              "commands": ["lap", "listen", "finish", "ask <question>", "submit", "tasks", "forget",
                           "quiet", "resume", "cancel", "repeat", "status", "quit"]})
        snapshots: queue.Queue = queue.Queue(maxsize=1)
        commands: queue.Queue = queue.Queue(maxsize=16)
        if client is not None:
            worker = threading.Thread(target=_poll, args=(client, snapshots, stop, args.poll_interval, captures, opponent_captures, geometry),
                                      name="radio-backend", daemon=True)
            worker.start()
        threading.Thread(target=_commands, args=(commands, sys.stdin), name="radio-commands", daemon=True).start()
        while not stop.is_set():
            now = time.monotonic()
            scheduler.suspended = voice.blocks_playback
            try:
                snapshot, started, results = snapshots.get_nowait()
                now = time.monotonic()
                controller.feed(snapshot, now=now, transport_age_s=now - started, region_captures=results)
            except queue.Empty:
                pass
            controller.check_freshness(now=now)
            pressed = wheel.poll(now=now)
            voice.tick(scope=scheduler.scope, input_status=wheel.status())
            if pressed:
                request_listening(scheduler, voice, source="wheel", binding=wheel.status()["binding"],
                                  connection=wheel.status().get("connection_instance"),
                                  task_context=controller.capture_task_context(),
                                  question_context=controller.capture_question_context())
            try:
                command = commands.get_nowait()
                if command == "quit":
                    stop.set()
                elif command == "lap":
                    voice.cancel()
                    controller.answer_last_lap(now=now)
                elif command == "listen":
                    request_listening(scheduler, voice, source="console",
                                      task_context=controller.capture_task_context(),
                                      question_context=controller.capture_question_context())
                elif command == "finish":
                    voice.finish()
                elif command == "tasks":
                    voice.cancel()
                    scheduler.cancel()
                    controller.handle_request("list tasks", now=now)
                elif command == "submit" or command.startswith("ask "):
                    if args.capture_test:
                        emit({"event": "radio_question_unavailable", "reason": "capture_test"})
                    else:
                        submit_question(command, controller, voice, now=now)
                elif command == "forget":
                    voice.cancel()
                    scheduler.cancel()
                    controller.forget_conversation()
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
                now = time.monotonic()
                if not args.capture_test and not getattr(args, "manual_submit", False):
                    submit_ready_question(controller, voice, now=now, input_status=wheel.status())
                if captures is not None:
                    captures.replace(controller.region_capture_requests())
                if opponent_captures is not None:
                    opponent_captures.replace(controller.opponent_capture_requests())
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
            if client is not None and worker is None:
                client.close()


def request_listening(scheduler: RadioScheduler, voice: VoiceInput, *, source: str, binding=None, connection=None,
                      task_context=None, question_context=None) -> None:
    reference = scheduler.capture_repeat_reference()
    scheduler.cancel()
    voice.request(source=source, scope=scheduler.scope, binding=binding, connection=connection,
                  repeat_reference=reference, task_context=task_context, question_context=question_context)


def dispatch_voice_request(question: str, controller: RadioController, voice: VoiceInput, *, now: float) -> None:
    control = " ".join(question.lower().split()).rstrip(".!?").rstrip()
    scheduler = controller.scheduler
    action = {
        "quiet": "quiet", "quiet radio": "quiet", "pause coaching": "quiet",
        "resume coaching": "resume", "resume radio": "resume",
        "cancel speech": "cancel", "stop speaking": "cancel",
        "repeat that": "repeat", "repeat message": "repeat",
    }.get(control)
    if action == "quiet":
        scheduler.quiet()
        controller.answer_radio_control("Proactive coaching paused. Tracking continues.", now=now)
    elif action == "resume":
        scheduler.muted = False
        scheduler.cancel()
        controller.answer_radio_control("Proactive coaching resumed.", now=now)
    elif action == "cancel":
        scheduler.cancel()
    elif action == "repeat":
        if not scheduler.repeat_captured(voice.repeat_reference, now=now):
            scheduler.cancel()
            controller.answer_radio_control("The message from when you started listening is no longer available.", now=now)
            emit({"event": "radio_repeat_unavailable", "reason": "captured_message_unavailable"})
            return
    elif control in ("cancel", "stop", "resume", "repeat"):
        scheduler.cancel()
        controller.answer_radio_control(
            "Say cancel speech, repeat that, resume coaching, or cancel task followed by its number.", now=now)
        emit({"event": "radio_voice_control_unavailable", "reason": "ambiguous_control",
              "guidance": "Say cancel speech, repeat that, resume coaching, or cancel task with its number."})
        return
    else:
        scheduler.cancel()
        controller.handle_request(question, now=now, task_context=voice.task_context,
                                  question_context=voice.question_context)
        return
    emit({"event": "radio_voice_control", "action": action, "tracking_unchanged": True})


def submit_ready_question(controller: RadioController, voice: VoiceInput, *, now: float, input_status=None) -> bool:
    controller.check_freshness(now=now)
    submission = voice.take_ready_draft(scope=controller.scheduler.scope, input_status=input_status)
    if submission is None:
        return False
    dispatch_voice_request(submission["question"], controller, voice, now=now)
    emit({"event": "radio_voice_submitted", "capture_id": submission["capture_id"],
          "source": submission["source"], "submitted": True, "verified": False,
          "submission_wait_s": submission["submission_wait_s"]})
    return True


def submit_question(command: str, controller: RadioController, voice: VoiceInput, *, now: float) -> None:
    controller.check_freshness(now=now)
    if command == "submit":
        question = voice.take_draft(scope=controller.scheduler.scope)
        if question is None:
            emit({"event": "radio_question_unavailable", "reason": "no_current_draft"})
            return
    else:
        question = command[4:]
        voice.cancel()
    if command == "submit":
        dispatch_voice_request(question, controller, voice, now=now)
    else:
        controller.scheduler.cancel()
        controller.handle_request(question, now=now)
