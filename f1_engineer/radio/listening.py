from __future__ import annotations

import asyncio
import array
import io
import math
import queue
import sys
import threading
import time
import uuid
import wave

from ..analysis.local_speech import SPEECH_MAX_SECONDS, SPEECH_MAX_SAMPLES, SPEECH_SAMPLE_RATE, SPEECH_MAX_TRANSCRIPT_BYTES
from .audio import NativeRadioAudio, RadioAudioUnavailable
from .diagnostics import exception_trace
from .intents import LOCAL_CONTROLS


def capture_levels(pcm: bytes) -> dict:
    samples = array.array("h", pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return {"audio_rms_dbfs": None, "audio_peak": 0.0, "audio_clipped_fraction": 0.0}
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768
    return {"audio_rms_dbfs": 20 * math.log10(rms) if rms else None,
            "audio_peak": max(abs(sample) for sample in samples) / 32768,
            "audio_clipped_fraction": sum(abs(sample) >= 32767 for sample in samples) / len(samples)}


def canonical_wav(pcm: bytes) -> bytes:
    if not pcm or len(pcm) % 2 or len(pcm) > SPEECH_MAX_SAMPLES * 2:
        raise ValueError("radio_capture_audio_invalid")
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(SPEECH_SAMPLE_RATE)
        stream.writeframes(pcm)
    return output.getvalue()


class VoiceInput:
    def __init__(self, *, transcribe=None, interpret=None, notify=None, enabled=True, microphone=None, output=None,
                 cue_volume=0.08, maximum_seconds=SPEECH_MAX_SECONDS, audio_factory=NativeRadioAudio,
                 clock=time.monotonic) -> None:
        if (not math.isfinite(maximum_seconds) or not 1 <= maximum_seconds <= SPEECH_MAX_SECONDS
                or not math.isfinite(cue_volume) or not 0 < cue_volume <= 0.3):
            raise ValueError("radio_capture_settings_invalid")
        self._transcribe = transcribe
        self._interpret = interpret
        self._notify = notify or (lambda event: None)
        self._enabled = enabled
        self._audio_factory = audio_factory
        self._audio_settings = {"microphone": microphone, "output": output, "cue_volume": cue_volume}
        self._maximum_seconds = maximum_seconds
        self._clock = clock
        self._events = queue.Queue(maxsize=16)
        self._worker = None
        self._finish = threading.Event()
        self._cancelled = threading.Event()
        self._capture_id = None
        self._scope = None
        self._binding = None
        self._connection = None
        self._source = None
        self._state = "idle"
        self._discard = False
        self._cancel_at = None
        self._watchdog_reported = False
        self._draft = None
        self._draft_interpretation = None
        self._requested_at = None
        self._capture_started_at = None
        self._stop_at = None
        self._cleanup_failed = False
        self._transcript_seen = False
        self._consumed_capture_id = None
        self._draft_ready_at = None
        self.repeat_reference = None
        self.task_context = None
        self.question_context = None
        self.intent_context = None

    @property
    def blocks_playback(self) -> bool:
        return self._cleanup_failed or self._state in ("starting", "listening", "stopping")

    def status(self) -> dict:
        return {"state": self._state, "capture_id": self._capture_id,
                "maximum_seconds": self._maximum_seconds, "draft_available": self._draft is not None}

    def request(self, *, source: str, scope: str | None, binding=None, connection=None,
                repeat_reference=None, task_context=None, question_context=None, intent_context=None) -> None:
        self._notify({"event": "radio_listen_requested", "source": source})
        if not self._enabled:
            self._notify({"event": "radio_listen_unavailable", "reason": "dry_run"})
            return
        if self._cleanup_failed:
            self._notify({"event": "radio_listen_unavailable", "reason": "audio_cleanup_failed_restart_required"})
            return
        if self._worker is not None and self._worker.is_alive():
            if self._state in ("starting", "listening") and not self._finish.is_set() and not self._discard:
                self.finish()
            else:
                self._notify({"event": "radio_listen_unavailable", "reason": "capture_busy"})
            return
        self._capture_id = str(uuid.uuid4())
        self._scope, self._binding, self._source = scope, binding, source
        self._connection = connection
        self.repeat_reference = repeat_reference
        self.task_context = task_context
        self.question_context = question_context
        self.intent_context = intent_context
        self._draft_interpretation = None
        self._finish = threading.Event()
        self._cancelled = threading.Event()
        self._discard = False
        self._cancel_at = None
        self._watchdog_reported = False
        self._draft = None
        self._state = "starting"
        self._transcript_seen = False
        self._draft_ready_at = None
        requested_at = self._clock()
        self._requested_at = requested_at
        self._capture_started_at = None
        self._stop_at = None
        self._notify({"event": "radio_capture_starting", "capture_id": self._capture_id})
        self._worker = threading.Thread(target=self._run, args=(self._capture_id, requested_at,
                                       self._finish, self._cancelled), name="radio-capture", daemon=True)
        self._worker.start()

    def finish(self) -> None:
        if self._state in ("starting", "listening"):
            self._state = "stopping"
            self._stop_at = self._clock()
            self._finish.set()

    def take_draft(self, *, scope: str | None) -> str | None:
        if scope is None or scope != self._scope or self._discard:
            self._draft = None
            return None
        if self._capture_id is not None and self._capture_id == self._consumed_capture_id:
            self._draft = None
            return None
        draft, self._draft = self._draft, None
        if draft is not None and self._capture_id is not None:
            self._consumed_capture_id = self._capture_id
        return draft

    def take_ready_draft(self, *, scope: str | None, input_status=None) -> dict | None:
        if (self._draft is None or self.blocks_playback or self._state != "idle"
                or self._worker is None or self._worker.is_alive()):
            return None
        if self._source not in ("wheel", "console") or self._capture_id is None:
            self._draft = None
            self._notify({"event": "radio_voice_submission_unavailable", "reason": "capture_origin_unavailable"})
            return None
        if self._source == "wheel" and (not isinstance(input_status, dict)
                or not isinstance(self._binding, dict) or not self._binding or self._connection is None
                or input_status.get("status") != "ready" or input_status.get("binding") != self._binding
                or input_status.get("connection_instance") != self._connection):
            self.cancel("wheel_binding_changed_or_unavailable")
            return None
        if scope is None and self._scope is None and not self._discard:
            self._draft = None
            self._consumed_capture_id = self._capture_id
            return {"unavailable_reason": "session_unavailable", "capture_id": self._capture_id,
                    "source": self._source}
        interpretation = self._draft_interpretation
        question = self.take_draft(scope=scope)
        if question is None:
            self._notify({"event": "radio_voice_submission_unavailable", "reason": "no_current_draft"})
            return None
        wait = self._clock() - self._draft_ready_at if self._draft_ready_at is not None else None
        return {"question": question, "capture_id": self._capture_id, "source": self._source,
                "submission_wait_s": max(0, wait) if wait is not None else None,
                "interpretation": interpretation}

    def cancel(self, reason="cancelled") -> None:
        self._draft = None
        self._draft_interpretation = None
        if self._capture_id is not None and not self._discard:
            self._discard = True
            self._cancel_at = self._clock()
            self._cancelled.set()
            self._finish.set()
            if self._state in ("starting", "listening"):
                self._state = "stopping"
            self._notify({"event": "radio_capture_cancelled", "reason": reason,
                          "capture_id": self._capture_id})

    def tick(self, *, scope: str | None, input_status=None) -> None:
        if self._capture_id is not None:
            if scope != self._scope:
                self.cancel("session_changed")
            elif self._source == "wheel" and input_status is not None and (
                    input_status.get("status") != "ready" or input_status.get("binding") != self._binding
                    or input_status.get("connection_instance") != self._connection):
                self.cancel("wheel_binding_changed_or_unavailable")
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            if event["capture_id"] != self._capture_id:
                continue
            state = event.pop("state", None)
            if state is not None:
                self._state = state
            if self._discard:
                continue
            if event["event"] == "radio_transcript":
                if self._transcript_seen or (self._capture_id is not None
                                             and self._capture_id == self._consumed_capture_id):
                    continue
                self._transcript_seen = True
                transcript = event.get("transcript")
                try:
                    valid = (isinstance(transcript, str) and bool(transcript.strip())
                             and len(transcript.encode("utf-8")) <= SPEECH_MAX_TRANSCRIPT_BYTES)
                except UnicodeError:
                    valid = False
                if not valid:
                    self._notify({"event": "radio_transcription_unavailable", "capture_id": self._capture_id,
                                  "reason": "transcription_response_invalid"})
                    continue
                self._draft = transcript
                self._draft_interpretation = event.get("interpretation")
                self._draft_ready_at = self._clock()
            self._notify(event)
        if self._worker is not None and not self._worker.is_alive():
            self._state = "unavailable" if self._cleanup_failed else "idle"
        if self._worker is not None and self._worker.is_alive() and not self._discard:
            now = self._clock()
            if (self._capture_started_at is None and self._requested_at is not None
                    and now - self._requested_at >= 5):
                self.cancel("audio_start_timeout")
            elif (self._capture_started_at is not None and self._state in ("starting", "listening")
                    and now - self._capture_started_at >= self._maximum_seconds + 1):
                self.cancel("capture_deadline_exceeded")
            elif self._state == "stopping" and self._stop_at is not None and now - self._stop_at >= 2:
                self.cancel("capture_stop_timeout")
        if (self._cancel_at is not None and self._worker is not None and self._worker.is_alive()
                and not self._watchdog_reported and self._clock() - self._cancel_at >= 2):
            self._watchdog_reported = True
            self._notify({"event": "radio_capture_stop_pending", "reason": "audio_or_transport_cleanup_pending"})

    def _put(self, capture_id: str, event: str, **fields) -> None:
        try:
            self._events.put_nowait({"event": event, "capture_id": capture_id, **fields})
        except queue.Full:
            self._cancelled.set()

    def _interpretation(self, capture_id: str, transcript: str, stopped_at: float, cancelled) -> dict:
        """Interpret the transcript on the capture worker thread; falls back to the plain parser on any failure."""
        control = " ".join(transcript.lower().split()).rstrip(".!?").rstrip()
        if control in LOCAL_CONTROLS:
            return {"status": "skipped", "reason": "local_control", "intent": None}
        self._put(capture_id, "radio_transcribed", transcript=transcript,
                  end_to_transcript_s=self._clock() - stopped_at)
        started = self._clock()
        try:
            result = asyncio.run(self._interpret(transcript, self.intent_context, capture_id, cancelled))
        except (OSError, ValueError, RuntimeError):
            result = {"status": "unavailable", "reason": "intent_failed"}
        if not isinstance(result, dict) or result.get("status") != "available":
            reason = result.get("reason") if isinstance(result, dict) else None
            return {"status": "unavailable", "reason": reason or "intent_unavailable", "intent": None,
                    "latency_s": self._clock() - started}
        return {"status": "available", "reason": None, "intent": result["intent"],
                "latency_s": self._clock() - started, "model_latency_ms": result.get("model_latency_ms")}

    def _run(self, capture_id: str, requested_at: float, finish, cancelled) -> None:
        audio = None
        pcm = bytearray()
        wav = None
        started_at = None
        cue_started = False
        cue_ended = False
        try:
            audio = self._audio_factory(**self._audio_settings)
            if cancelled.is_set() or finish.is_set():
                return
            audio.cue("start")
            cue_started = True
            if cancelled.wait(0.2) or finish.is_set():
                return
            audio.start()
            started_at = self._clock()
            self._capture_started_at = started_at
            self._put(capture_id, "radio_listening", state="listening",
                      request_to_listening_s=started_at - requested_at)
            next_pulse = started_at + 2
            while not finish.is_set() and not cancelled.is_set():
                now = self._clock()
                if now - started_at >= self._maximum_seconds or len(pcm) >= SPEECH_MAX_SAMPLES * 2:
                    break
                if now >= next_pulse:
                    audio.pause()
                    audio.cue("pulse")
                    if cancelled.wait(0.2) or finish.is_set():
                        break
                    audio.start()
                    next_pulse = self._clock() + 2
                    if next_pulse - 2 - started_at >= self._maximum_seconds:
                        break
                chunk = audio.read()
                if not chunk or len(chunk) % 2 or len(chunk) > 640:
                    raise RadioAudioUnavailable("radio_microphone_data_invalid")
                remaining = SPEECH_MAX_SAMPLES * 2 - len(pcm)
                pcm.extend(chunk[:remaining])
            audio.close()
            stopped_at = self._clock()
            self._put(capture_id, "radio_capture_stopping", state="stopping")
            audio.cue("end")
            cue_ended = True
            if cancelled.is_set():
                return
            elapsed = stopped_at - started_at
            self._put(capture_id, "radio_capture_complete", state="transcribing" if self._transcribe else "idle",
                      capture_elapsed_s=elapsed, capture_audio_s=len(pcm) / (SPEECH_SAMPLE_RATE * 2),
                      capture_samples=len(pcm) // 2,
                      capture_gap_s=max(0, elapsed - len(pcm) / (SPEECH_SAMPLE_RATE * 2)),
                      automatic_stop=not finish.is_set(), audio_persisted=False, **capture_levels(pcm))
            if not pcm:
                raise RadioAudioUnavailable("radio_capture_empty")
            if self._transcribe is not None:
                wav = canonical_wav(bytes(pcm))
                result = asyncio.run(self._transcribe(wav, capture_id, cancelled))
                if cancelled.is_set():
                    return
                if result.get("status") != "available":
                    self._put(capture_id, "radio_transcription_unavailable", state="idle",
                              reason=result.get("reason", "transcription_unavailable"))
                else:
                    fields = {}
                    if self._interpret is not None:
                        fields["interpretation"] = self._interpretation(capture_id, result["transcript"],
                                                                        stopped_at, cancelled)
                        if cancelled.is_set():
                            return
                    self._put(capture_id, "radio_transcript", state="idle", transcript=result["transcript"],
                              verified=False, submitted=False, end_to_transcript_s=self._clock() - stopped_at,
                              **fields)
        except (RadioAudioUnavailable, OSError, ValueError, RuntimeError) as exception:
            safe_reasons = {"radio_audio_requires_radio_extra", "radio_microphone_unavailable",
                            "radio_microphone_overflow", "radio_microphone_read_failed",
                            "radio_microphone_stop_failed", "radio_microphone_data_invalid",
                            "radio_cue_output_underflow", "radio_cue_output_unavailable", "radio_capture_empty"}
            reason = str(exception) if isinstance(exception, RadioAudioUnavailable) and str(exception) in safe_reasons else "capture_or_transcription_failed"
            self._put(capture_id, "radio_listen_unavailable", reason=reason,
                      exception_chain=exception_trace(exception))
        finally:
            if audio is not None:
                try:
                    audio.close()
                except Exception:
                    self._cleanup_failed = True
                    self._put(capture_id, "radio_listen_unavailable", reason="audio_cleanup_failed")
                if cue_started and not cue_ended and not self._cleanup_failed:
                    try:
                        audio.cue("end")
                    except Exception:
                        self._put(capture_id, "radio_listen_unavailable", reason="end_cue_failed")
            pcm.clear()
            wav = None
            self._put(capture_id, "radio_capture_idle", state="idle")

    def close(self) -> None:
        self.cancel("shutdown")
        if self._worker is not None:
            self._worker.join(timeout=2)
