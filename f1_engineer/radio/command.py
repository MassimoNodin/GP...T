from __future__ import annotations

import asyncio
import math
import queue
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from .audio import list_audio_devices
from .client import RadioClient
from .controller import RadioController
from .input_settings import DEFAULT_INPUT_SETTINGS
from .listening import VoiceInput
from .region_captures import RadioRegionCaptures
from .opponent_captures import RadioOpponentCaptures
from .track_geometry import RadioTrackGeometry
from .scheduler import RadioMessage, RadioScheduler
from .activity_reporter import ActivityReporter
from .proactive_selection import ProactiveSelector
from .wheel_input import WheelButtonMonitor
from .edge_speech import EdgeSpeech, EdgeSpeechUnavailable
from .windows_speech import WindowsSpeech
from .diagnostics import emit, radio_event_log, trace_event
from .glossary import load_glossary
from .intents import LOCAL_CONTROLS, render_intent
from .live_settings import LivePlayback, RadioLiveSettings
from .presence import PresenceReporter
from .settings import DEFAULT_RADIO_SETTINGS


class DryRunPlayback:
    def start(self, text: str) -> None:
        pass

    def busy(self) -> bool:
        return False

    def cancel(self) -> None:
        pass

    def close(self) -> None:
        pass


def _speech(args):
    if args.tts_engine == "windows":
        return WindowsSpeech(voice=args.voice, rate=args.rate, volume=args.volume)
    fallback = WindowsSpeech(rate=args.rate, volume=args.volume)
    try:
        return EdgeSpeech(voice=args.voice, rate=args.rate, volume=args.volume, fallback=fallback)
    except EdgeSpeechUnavailable:
        return fallback


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
            inbox.put(_command_text(line))


def _command_text(line: str) -> str:
    text = line.strip().removeprefix("/")
    prefix = text[:4].lower()
    return prefix + text[4:] if prefix in ("ask ", "say ") else text.lower()


def _tail_commands(inbox: queue.Queue, path: Path, stop: threading.Event) -> None:
    """Read commands appended to a file; it is opened only briefly so writers are never blocked."""
    offset = path.stat().st_size if path.exists() else 0
    pending = ""
    while not stop.wait(0.2):
        try:
            size = path.stat().st_size
            if size < offset:
                offset, pending = 0, ""
            if size == offset:
                continue
            with path.open("rb") as stream:
                stream.seek(offset)
                data = stream.read(size - offset)
            offset += len(data)
        except OSError:
            continue
        pending += data.decode("utf-8", errors="replace")
        *lines, pending = pending.split("\n")
        for line in lines:
            if line.strip() and len(line.encode("utf-8")) <= 1024:
                inbox.put(_command_text(line))


def start_typed_request(text: str, client, controller: RadioController, glossary: dict, results: queue.Queue,
                        *, interpret: bool, grounded=False) -> None:
    """Treat typed text exactly like a finished voice transcript: same contexts, same interpreter, same dispatch."""
    request_id = str(uuid.uuid4())
    voice_like = SimpleNamespace(repeat_reference=controller.scheduler.capture_repeat_reference(),
                                 task_context=controller.capture_task_context(),
                                 question_context=controller.capture_question_context())
    context = capture_intent_context(controller, glossary, grounded=grounded)
    interpreter = (getattr(controller, "answer_interpreter", None) or client.answer) if grounded else client.interpret
    emit({"event": "radio_typed_request", "capture_id": request_id, "text": text, "interpreter": interpret})
    control = " ".join(text.lower().split()).rstrip(".!?").rstrip()
    if control in LOCAL_CONTROLS:
        results.put(({"question": text, "capture_id": request_id, "source": "typed",
                      "interpretation": None}, voice_like))
        return

    def run():
        started = time.monotonic()
        interpretation = None
        if interpret:
            result = asyncio.run(interpreter(text, context, request_id, threading.Event()))
            interpretation = ({"status": "available", "reason": None, "intent": result["intent"],
                               **({"question_event_id": result["question_event_id"]} if "question_event_id" in result else {}),
                               **({"grounded": result["grounded"]} if "grounded" in result else {}),
                               "latency_s": time.monotonic() - started}
                              if result.get("status") == "available" else
                              {"status": "unavailable", "reason": result.get("reason"), "intent": None,
                               **({"answer_mode": result["answer_mode"]} if "answer_mode" in result else {}),
                               "latency_s": time.monotonic() - started})
            emit({"event": "radio_transcript", "capture_id": request_id, "transcript": text, "source": "typed",
                  "interpretation": interpretation})
        results.put(({"question": text, "capture_id": request_id, "source": "typed",
                      "interpretation": interpretation}, voice_like))

    threading.Thread(target=run, name="radio-typed-request", daemon=True).start()


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
    if getattr(args, "answer_mode", "intent") == "grounded" and getattr(args, "intent_mode", "luna") != "luna":
        raise ValueError("grounded answers require luna intent mode")
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
    report_activity = getattr(client, "report_activity", None)
    activity_reporter = ActivityReporter(report_activity, notify=emit) if callable(report_activity) else None
    scheduler = None
    wheel = None
    voice = None
    stop = threading.Event()
    worker = None
    select_proactive = getattr(client, "select_proactive", None)
    selector = (ProactiveSelector(select_proactive, notify=emit)
                if callable(select_proactive) and not args.dry_run and not args.once else None)
    captures = RadioRegionCaptures(client, notify=emit) if client is not None else None
    opponent_captures = RadioOpponentCaptures(client, notify=emit) if client is not None else None
    geometry = RadioTrackGeometry(client) if client is not None else None
    try:
        playback = DryRunPlayback() if args.dry_run or args.once or args.capture_test else _speech(args)
        def scheduler_event(event):
            emit(event)
            if activity_reporter is not None:
                lap = getattr(controller, "_last_lap", None)
                activity_reporter.observe({**event, "lap": lap if type(lap) is int else None})

        live_playback = LivePlayback(playback, lambda settings: DryRunPlayback() if args.dry_run else _speech(settings),
                                     {}, emit)
        scheduler = RadioScheduler(live_playback, notify=scheduler_event)
        controller = RadioController(scheduler, notify=emit, backend_regions=client is not None,
                                     proactive_mode=getattr(args, "proactive_mode", "rules"), selector=selector)
        started = time.monotonic()
        snapshot = client.fetch() if client else {"status": "unavailable", "reason": "capture_test"}
        controller.feed(snapshot, now=time.monotonic(), transport_age_s=time.monotonic() - started)
        if args.once:
            return 0 if controller.status()["backend"] == "available" else 2
        wheel = WheelButtonMonitor(Path(getattr(args, "input_settings", DEFAULT_INPUT_SETTINGS)), notify=emit)
        grounded = getattr(args, "answer_mode", "intent") == "grounded"
        interpret = ((client.answer if grounded else client.interpret) if client and not args.dry_run
                     and getattr(args, "intent_mode", "luna") == "luna" else None)
        voice = VoiceInput(transcribe=client.transcribe if client and not args.dry_run else None, notify=emit,
                           enabled=not args.dry_run, microphone=args.microphone, output=args.cue_output,
                           cue_volume=args.cue_volume, maximum_seconds=args.capture_seconds,
                           interpret=interpret, speech_hints=not getattr(args, "no_speech_hint", False))
        glossary, glossary_problem = load_glossary(getattr(args, "glossary_file", None))
        live_settings = RadioLiveSettings(args, scheduler, controller, voice, client, emit)
        live_settings.apply(snapshot.get("settings"), received_at=started)
        presence = PresenceReporter(client, stop) if client is not None else None
        if presence is not None:
            live_settings.refresh_devices = presence.request_refresh
            threading.Thread(target=presence.run, name="radio-presence", daemon=True).start()
        if glossary_problem is not None:
            emit({"event": "radio_glossary_unavailable", "reason": glossary_problem})
        emit({"event": "radio_ready", "intent_mode": "luna" if interpret is not None else "parser",
              "answer_mode": "grounded" if grounded else "intent",
              "glossary_terms": len(glossary["terms"]), "glossary_mishearings": len(glossary["mishearings"]), "dry_run": args.dry_run, "voice": getattr(playback, "voice", None),
              "rate": args.rate, "volume": args.volume,
              "capture_test": args.capture_test,
              "microphone": args.microphone, "cue_output": args.cue_output,
              "poll_interval_s": args.poll_interval, "capture_seconds": args.capture_seconds,
              "voice_submission": ("disabled" if args.capture_test else
                                   "manual" if getattr(args, "manual_submit", False) else "automatic"),
              "commands": ["lap", "listen", "finish", "ask <question>", "say <words>", "submit", "tasks", "forget",
                           "quiet", "resume", "proactive off", "proactive rules", "proactive model",
                           "cancel", "repeat", "status", "quit"]})
        snapshots: queue.Queue = queue.Queue(maxsize=1)
        commands: queue.Queue = queue.Queue(maxsize=16)
        if client is not None:
            worker = threading.Thread(target=_poll, args=(client, snapshots, stop, args.poll_interval, captures, opponent_captures, geometry),
                                      name="radio-backend", daemon=True)
            worker.start()
        threading.Thread(target=_commands, args=(commands, sys.stdin), name="radio-commands", daemon=True).start()
        typed_results: queue.Queue = queue.Queue()
        if getattr(args, "command_file", None):
            threading.Thread(target=_tail_commands, args=(commands, Path(args.command_file), stop),
                             name="radio-command-file", daemon=True).start()
        while not stop.is_set():
            now = time.monotonic()
            scheduler.suspended = voice.blocks_playback
            try:
                snapshot, started, results = snapshots.get_nowait()
                now = time.monotonic()
                live_settings.apply(snapshot.get("settings"), received_at=started)
                controller.feed(snapshot, now=now, transport_age_s=now - started, region_captures=results)
            except queue.Empty:
                pass
            controller.check_freshness(now=now)
            live_settings.tick()
            grounded = getattr(args, "answer_mode", "intent") == "grounded"
            if presence is not None:
                presence.publish(revision=live_settings.revision, engine=args.tts_engine,
                                 speaking=scheduler.active is not None, listening=voice.blocks_playback,
                                 wheel_status=wheel.status().get("status", "unknown"),
                                 settings={key: getattr(args, key) for key in DEFAULT_RADIO_SETTINGS})
            pressed = wheel.poll(now=now)
            voice.tick(scope=scheduler.scope, input_status=wheel.status())
            if pressed:
                if selector is not None:
                    selector.cancel()
                request_listening(scheduler, voice, source="wheel", binding=wheel.status()["binding"],
                                  connection=wheel.status().get("connection_instance"),
                                  task_context=controller.capture_task_context(),
                                  question_context=controller.capture_question_context(),
                                  intent_context=capture_intent_context(controller, glossary, grounded=grounded))
            try:
                command = commands.get_nowait()
                if selector is not None and command not in ("status", "resume"):
                    selector.cancel()
                if command == "quit":
                    stop.set()
                elif command == "lap":
                    voice.cancel()
                    controller.answer_last_lap(now=now)
                elif command == "listen":
                    request_listening(scheduler, voice, source="console",
                                      task_context=controller.capture_task_context(),
                                      question_context=controller.capture_question_context(),
                                      intent_context=capture_intent_context(controller, glossary, grounded=grounded))
                elif command == "finish":
                    voice.finish()
                elif command == "tasks":
                    voice.cancel()
                    scheduler.cancel()
                    controller.handle_request("list tasks", now=now)
                elif command.startswith("say "):
                    if client is None or args.capture_test:
                        emit({"event": "radio_question_unavailable", "reason": "backend_unavailable"})
                    else:
                        start_typed_request(command[4:].strip(), client, controller, glossary, typed_results,
                                            interpret=getattr(args, "intent_mode", "luna") == "luna", grounded=grounded)
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
                elif command in ("proactive off", "proactive rules", "proactive model"):
                    controller.set_proactive_mode(command.split()[1])
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
            while not typed_results.empty():
                submission, voice_like = typed_results.get_nowait()
                now = time.monotonic()
                controller.check_freshness(now=now)
                dispatch_submission(submission, controller, voice_like, now=now)
            if not stop.is_set():
                now = time.monotonic()
                if not args.capture_test and not getattr(args, "manual_submit", False):
                    submit_ready_question(controller, voice, now=now, input_status=wheel.status())
                if captures is not None:
                    captures.replace(controller.region_capture_requests())
                if opponent_captures is not None:
                    opponent_captures.replace(controller.opponent_capture_requests())
                scheduler.suspended = voice.blocks_playback
                controller.poll_proactive(now=now)
                scheduler.tick(now=now)
            stop.wait(0.05)
        return 0
    finally:
        stop.set()
        if activity_reporter is not None:
            activity_reporter.close()
        if selector is not None:
            selector.close()
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
                      task_context=None, question_context=None, intent_context=None) -> None:
    reference = scheduler.capture_repeat_reference()
    scheduler.cancel()
    voice.request(source=source, scope=scheduler.scope, binding=binding, connection=connection,
                  repeat_reference=reference, task_context=task_context, question_context=question_context,
                  intent_context=intent_context)


def capture_intent_context(controller: RadioController, glossary: dict, *, grounded=False) -> dict:
    """Snapshot of what the interpreter needs, taken when listening starts like the task and question contexts."""
    context = controller.intent_context()
    context["glossary"] = glossary
    if grounded:
        context["answer_scope"] = controller.scheduler.scope
    return context


def dispatch_submission(submission: dict, controller: RadioController, voice: VoiceInput, *, now: float) -> None:
    interpretation = submission.get("interpretation")
    related = interpretation.get("question_event_id") if isinstance(interpretation, dict) else None
    controller.scheduler.related_question_id = related if type(related) is int and related > 0 else None
    try:
        _dispatch_submission(submission, controller, voice, now=now)
    finally:
        controller.scheduler.related_question_id = None


def _dispatch_submission(submission: dict, controller: RadioController, voice: VoiceInput, *, now: float) -> None:
    """Act on a transcript, using the interpreted request when there is one and the raw words otherwise."""
    question = submission["question"]
    if " ".join(question.lower().split()).rstrip(".!?").rstrip() in LOCAL_CONTROLS:
        dispatch_voice_request(question, controller, voice, now=now)
        return
    interpretation = submission.get("interpretation")
    grounded = interpretation.get("grounded") if isinstance(interpretation, dict) else None
    if grounded is not None and (grounded.get("scope") != controller.scheduler.scope
                                or grounded.get("dispatch_before", 0) <= now):
        emit({"event": "radio_answer_dropped", "reason": "grounded_response_expired"})
        controller.answer_radio_control("That answer expired. Please ask again.", now=now)
        return
    if grounded is not None and grounded["reply"]["kind"] != "command":
        controller.scheduler.cancel()
        if not controller.answer_grounded(question, grounded, now=now):
            controller.answer_radio_control("I couldn't verify a fresh answer. Please try again.", now=now)
        return
    intent = interpretation.get("intent") if isinstance(interpretation, dict) else None
    if intent is not None:
        try:
            rendered = render_intent(intent)
        except (KeyError, TypeError, ValueError, AttributeError):
            rendered = None
            emit({"event": "radio_intent_fallback", "capture_id": submission.get("capture_id"),
                  "reason": "render_failed"})
            controller.scheduler.cancel()
            controller.answer_radio_control("Say again.", now=now)
            return
        if rendered is not None:
            emit({"event": "radio_intent_dispatch", "capture_id": submission.get("capture_id"),
                  "heard": question, "action": intent["action"], "kind": rendered.kind, "text": rendered.text})
            if rendered.kind == "reply":
                controller.scheduler.cancel()
                controller.answer_radio_control(rendered.text, now=now)
                return
            question = rendered.text
    elif isinstance(interpretation, dict) and interpretation.get("status") == "unavailable":
        emit({"event": "radio_intent_fallback", "capture_id": submission.get("capture_id"),
              "reason": interpretation.get("reason")})
        controller.scheduler.cancel()
        text = ("I couldn't verify a fresh answer. Please try again."
                if interpretation.get("answer_mode") == "grounded" else "Say again.")
        controller.answer_radio_control(text, now=now)
        return
    dispatch_voice_request(question, controller, voice, now=now)


def dispatch_voice_request(question: str, controller: RadioController, voice: VoiceInput, *, now: float) -> None:
    control = " ".join(question.lower().split()).rstrip(".!?").rstrip()
    scheduler = controller.scheduler
    action = LOCAL_CONTROLS.get(control)
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
    if submission.get("unavailable_reason") == "session_unavailable":
        text = "No live F1 telemetry. Try again once you're on track."
        controller.scheduler.cancel()
        queued = controller.scheduler.submit(RadioMessage(
            f"voice-unavailable:{submission['capture_id']}", None, text, "answer", now + 15), now=now)
        emit({"event": "radio_voice_submission_unavailable", "reason": "session_unavailable",
              "capture_id": submission["capture_id"], "source": submission["source"],
              "response_queued": queued, "text": text})
        return False
    dispatch_submission(submission, controller, voice, now=now)
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
