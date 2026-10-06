"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  buildCanonicalPcmWav,
  LOCAL_SPEECH_MAX_SAMPLES,
  LOCAL_SPEECH_MAX_TRANSCRIPT_BYTES,
  LOCAL_SPEECH_SAMPLE_RATE,
  parseLocalSpeechStatus,
  parseLocalTranscription,
  sha256Hex,
  type LocalSpeechStatus,
} from "@/lib/local-speech-draft";
import { cancelLocalEvidenceSpeechForPage } from "@/lib/local-speech-controller";

type Capture = {
  generation: number;
  stream: MediaStream;
  context: AudioContext;
  source: MediaStreamAudioSourceNode;
  processor: ScriptProcessorNode;
  mute: GainNode;
  sampleRate: number;
  chunks: Float32Array[];
  sampleCount: number;
  timer: number;
};

type Props = {
  question: string;
  onQuestionChange: (value: string) => void;
  selectionKey: string;
  disabled: boolean;
  resetToken: number;
  onWorkingChange: (working: boolean) => void;
};

type SpeechPhase = "idle" | "starting" | "recording" | "transcribing";

export default function EngineerSpeechDraft({
  question,
  onQuestionChange,
  selectionKey,
  disabled,
  resetToken,
  onWorkingChange,
}: Props) {
  const [availability, setAvailability] = useState<LocalSpeechStatus | null>(
    null,
  );
  const [phase, setPhase] = useState<SpeechPhase>("idle");
  const [transcript, setTranscript] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [transcriptBytes, setTranscriptBytes] = useState(0);
  const captureRef = useRef<Capture | null>(null);
  const activeRequestRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);
  const questionRef = useRef(question);
  const selectionRef = useRef(selectionKey);
  const phaseRef = useRef(phase);
  const cancelAllRef = useRef<() => void>(() => undefined);

  questionRef.current = question;
  selectionRef.current = selectionKey;
  phaseRef.current = phase;

  const setCurrentPhase = useCallback(
    (next: SpeechPhase) => {
      phaseRef.current = next;
      setPhase(next);
      onWorkingChange(next !== "idle");
    },
    [onWorkingChange],
  );

  const releaseCapture = useCallback((capture: Capture) => {
    window.clearTimeout(capture.timer);
    capture.processor.onaudioprocess = null;
    try {
      capture.processor.disconnect();
      capture.mute.disconnect();
      capture.source.disconnect();
    } catch {
      // Nodes may already have been disconnected by a browser device error.
    }
    for (const track of capture.stream.getTracks()) track.stop();
    void capture.context.close().catch(() => undefined);
  }, []);

  const cancelAll = useCallback(() => {
    generationRef.current += 1;
    activeRequestRef.current?.abort();
    activeRequestRef.current = null;
    const capture = captureRef.current;
    captureRef.current = null;
    if (capture) releaseCapture(capture);
    setTranscript(null);
    setTranscriptBytes(0);
    setCurrentPhase("idle");
  }, [releaseCapture, setCurrentPhase]);

  cancelAllRef.current = cancelAll;

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    const deadline = window.setTimeout(() => controller.abort(), 5_000);
    void (async () => {
      try {
        const response = await fetch("/api/engineer/transcribe", {
          cache: "no-store",
          signal: controller.signal,
        });
        const body: unknown = await response.json();
        if (current) {
          setAvailability(
            parseLocalSpeechStatus(body) ?? {
              status: "unavailable",
              reason: "runtime_unavailable",
            },
          );
        }
      } catch {
        if (current) {
          setAvailability({
            status: "unavailable",
            reason: "runtime_unavailable",
          });
        }
      } finally {
        window.clearTimeout(deadline);
      }
    })();
    return () => {
      current = false;
      window.clearTimeout(deadline);
      controller.abort();
    };
  }, []);

  useEffect(() => {
    cancelAll();
    return () => cancelAll();
  }, [selectionKey, resetToken, cancelAll]);

  useEffect(() => {
    const stopWhenHidden = () => {
      if (document.visibilityState === "hidden") {
        cancelAllRef.current();
        setMessage(
          "Recording or transcription was cancelled because the page was hidden.",
        );
      }
    };
    const stopOnPageHide = () => {
      cancelAllRef.current();
    };
    document.addEventListener("visibilitychange", stopWhenHidden);
    window.addEventListener("pagehide", stopOnPageHide);
    return () => {
      document.removeEventListener("visibilitychange", stopWhenHidden);
      window.removeEventListener("pagehide", stopOnPageHide);
    };
  }, []);

  const transcribe = useCallback(
    async (capture: Capture) => {
      const expectedQuestion = questionRef.current;
      const expectedSelection = selectionRef.current;
      const operationGeneration = capture.generation;
      const current = () =>
        generationRef.current === operationGeneration &&
        selectionRef.current === expectedSelection;
      const source = new Float32Array(capture.sampleCount);
      let offset = 0;
      for (const chunk of capture.chunks) {
        source.set(chunk, offset);
        offset += chunk.length;
      }
      const targetLength = Math.min(
        LOCAL_SPEECH_MAX_SAMPLES,
        Math.max(
          1,
          Math.round(
            (source.length * LOCAL_SPEECH_SAMPLE_RATE) / capture.sampleRate,
          ),
        ),
      );
      const OfflineAudioContextConstructor = window.OfflineAudioContext;
      if (!OfflineAudioContextConstructor) {
        throw new Error("This browser cannot prepare local microphone audio.");
      }
      const offline = new OfflineAudioContextConstructor(
        1,
        targetLength,
        LOCAL_SPEECH_SAMPLE_RATE,
      );
      const inputBuffer = offline.createBuffer(
        1,
        source.length,
        capture.sampleRate,
      );
      inputBuffer.copyToChannel(source, 0);
      const input = offline.createBufferSource();
      input.buffer = inputBuffer;
      input.connect(offline.destination);
      input.start();
      const rendered = await offline.startRendering();
      if (!current()) return;
      const wav = buildCanonicalPcmWav(rendered.getChannelData(0));
      if (!wav || wav.byteLength > 512 * 1_024) {
        throw new Error(
          "The captured clip exceeded the supported audio limit.",
        );
      }
      const selectedRuntime = availability;
      if (!selectedRuntime || selectedRuntime.status !== "ready") {
        throw new Error("The pinned local speech runtime is unavailable.");
      }
      const requestId = crypto.randomUUID();
      const audioSha256 = await sha256Hex(wav);
      if (!current()) return;
      const controller = new AbortController();
      activeRequestRef.current = controller;
      const timeout = window.setTimeout(() => controller.abort(), 60_000);
      try {
        const response = await fetch("/api/engineer/transcribe", {
          method: "POST",
          cache: "no-store",
          headers: {
            "content-type": "audio/wav",
            "x-request-id": requestId,
            "x-audio-sha256": audioSha256,
          },
          body: wav.buffer.slice(
            wav.byteOffset,
            wav.byteOffset + wav.byteLength,
          ) as ArrayBuffer,
          signal: controller.signal,
        });
        const body: unknown = await response.json();
        if (!current()) return;
        if (!response.ok) {
          throw new Error(readSpeechFailure(body));
        }
        const result = parseLocalTranscription(
          body,
          requestId,
          audioSha256,
          selectedRuntime.runtime_id,
        );
        if (!result) {
          throw new Error(
            "The transcript could not be matched to this clip and pinned runtime.",
          );
        }
        if (questionRef.current !== expectedQuestion) {
          setTranscript(null);
          setMessage(
            "Your question changed while the clip was processing. The transcript was discarded; your typed question is unchanged.",
          );
          return;
        }
        setTranscript(result.transcript);
        setTranscriptBytes(
          new TextEncoder().encode(result.transcript).byteLength,
        );
        setMessage(
          "Transcript ready. Review it before choosing Use transcript.",
        );
      } finally {
        window.clearTimeout(timeout);
        if (activeRequestRef.current === controller)
          activeRequestRef.current = null;
      }
    },
    [availability],
  );

  const finishCapture = useCallback(
    async (expectedGeneration: number) => {
      const capture = captureRef.current;
      if (!capture || capture.generation !== expectedGeneration) return;
      captureRef.current = null;
      releaseCapture(capture);
      if (
        capture.sampleCount < Math.max(1, Math.floor(capture.sampleRate * 0.2))
      ) {
        setCurrentPhase("idle");
        setMessage(
          "The clip was too short. Record a little longer or type your question.",
        );
        return;
      }
      setCurrentPhase("transcribing");
      setMessage("Transcribing locally. You can cancel at any time.");
      try {
        await transcribe(capture);
      } catch (error) {
        if (generationRef.current === expectedGeneration) {
          setMessage(
            error instanceof DOMException && error.name === "AbortError"
              ? "Transcription was cancelled. Your typed question is unchanged."
              : error instanceof Error
                ? error.message
                : "Local transcription failed. Your typed question is unchanged.",
          );
        }
      } finally {
        if (generationRef.current === expectedGeneration)
          setCurrentPhase("idle");
      }
    },
    [releaseCapture, setCurrentPhase, transcribe],
  );

  const startCapture = useCallback(async () => {
    if (
      disabled ||
      phaseRef.current !== "idle" ||
      availability?.status !== "ready"
    ) {
      return;
    }
    setMessage(null);
    setTranscript(null);
    cancelLocalEvidenceSpeechForPage();
    const generation = ++generationRef.current;
    setCurrentPhase("starting");
    let pendingStream: MediaStream | null = null;
    let pendingContext: AudioContext | null = null;
    try {
      if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) {
        throw new Error("Microphone recording is unavailable in this browser.");
      }
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: { ideal: 1 },
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });
      pendingStream = stream;
      if (generationRef.current !== generation) {
        for (const track of stream.getTracks()) track.stop();
        pendingStream = null;
        return;
      }
      const context = new window.AudioContext();
      pendingContext = context;
      const sampleRate = context.sampleRate;
      if (
        !Number.isFinite(sampleRate) ||
        sampleRate < 8_000 ||
        sampleRate > 192_000
      ) {
        for (const track of stream.getTracks()) track.stop();
        pendingStream = null;
        await context.close().catch(() => undefined);
        pendingContext = null;
        throw new Error(
          "This microphone sample rate is outside the supported range.",
        );
      }
      const source = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor(4_096, 1, 1);
      const mute = context.createGain();
      mute.gain.value = 0;
      const capture = {
        generation,
        stream,
        context,
        source,
        processor,
        mute,
        sampleRate,
        chunks: [] as Float32Array[],
        sampleCount: 0,
        timer: 0,
      } satisfies Capture;
      const maximumSourceSamples = Math.ceil(
        (sampleRate * LOCAL_SPEECH_MAX_SAMPLES) / LOCAL_SPEECH_SAMPLE_RATE,
      );
      processor.onaudioprocess = (event) => {
        if (
          generationRef.current !== generation ||
          captureRef.current !== capture
        )
          return;
        const remaining = maximumSourceSamples - capture.sampleCount;
        if (remaining <= 0) {
          void finishCapture(generation);
          return;
        }
        const input = event.inputBuffer;
        const count = Math.min(input.length, remaining);
        const channels = input.numberOfChannels;
        const mono = new Float32Array(count);
        const inputChannels = Array.from(
          { length: channels },
          (_unused, index) => input.getChannelData(index),
        );
        for (let frame = 0; frame < count; frame += 1) {
          let sum = 0;
          for (const channel of inputChannels) sum += channel[frame];
          mono[frame] = channels > 0 ? sum / channels : 0;
        }
        if (count > 0) {
          capture.chunks.push(mono);
          capture.sampleCount += count;
        }
        if (capture.sampleCount >= maximumSourceSamples)
          void finishCapture(generation);
      };
      for (const track of stream.getAudioTracks()) {
        track.addEventListener(
          "ended",
          () => {
            if (
              generationRef.current === generation &&
              captureRef.current === capture
            ) {
              cancelAll();
              setMessage(
                "The microphone disconnected. Your typed question is unchanged.",
              );
            }
          },
          { once: true },
        );
      }
      source.connect(processor);
      processor.connect(mute);
      mute.connect(context.destination);
      captureRef.current = capture;
      pendingStream = null;
      pendingContext = null;
      capture.timer = window.setTimeout(
        () => void finishCapture(generation),
        12_000,
      );
      await context.resume();
      if (
        generationRef.current !== generation ||
        captureRef.current !== capture
      ) {
        const stale = captureRef.current;
        if (stale?.generation === generation) {
          captureRef.current = null;
          releaseCapture(stale);
        }
        return;
      }
      setCurrentPhase("recording");
      setMessage(
        "Recording for up to 12 seconds. Stop to create a draft; Cancel discards the clip.",
      );
    } catch (error) {
      const failedCapture = captureRef.current;
      if (failedCapture?.generation === generation) {
        captureRef.current = null;
        releaseCapture(failedCapture);
      }
      if (pendingStream) {
        for (const track of pendingStream.getTracks()) track.stop();
      }
      if (pendingContext) void pendingContext.close().catch(() => undefined);
      if (generationRef.current !== generation) return;
      const permissionDenied =
        error instanceof DOMException &&
        (error.name === "NotAllowedError" ||
          error.name === "PermissionDeniedError");
      setMessage(
        permissionDenied
          ? "Microphone permission was denied. You can type your question instead."
          : error instanceof Error
            ? error.message
            : "The microphone could not be opened. You can type your question instead.",
      );
      setCurrentPhase("idle");
    }
  }, [
    availability,
    cancelAll,
    disabled,
    finishCapture,
    releaseCapture,
    setCurrentPhase,
  ]);

  const stopCapture = () => {
    const capture = captureRef.current;
    if (capture) void finishCapture(capture.generation);
  };

  const cancelSpeech = () => {
    cancelAll();
    setMessage(
      "Recording or transcription was cancelled. Your typed question is unchanged.",
    );
  };

  const applyTranscript = () => {
    if (transcript === null || transcriptBytes > 1_024) return;
    onQuestionChange(transcript.trim());
    setTranscript(null);
    setMessage("Transcript copied into the question. Review it before asking.");
  };

  const statusText =
    availability === null
      ? "Checking local speech runtime…"
      : availability.status === "ready"
        ? "Local English speech input is ready. Clips are transcribed on this computer."
        : speechUnavailableText(availability.reason);

  return (
    <div className="engineer-speech-draft">
      <div className="engineer-speech-controls">
        {phase === "idle" ? (
          <button
            className="button-secondary"
            type="button"
            onClick={() => void startCapture()}
            disabled={disabled || availability?.status !== "ready"}
          >
            Record question
          </button>
        ) : (
          <>
            {phase === "starting" ? (
              <span role="status">Waiting for microphone…</span>
            ) : null}
            {phase === "recording" ? (
              <>
                <span className="engineer-speech-recording" role="status">
                  RECORDING · MAX 12 SEC
                </span>
                <button
                  className="button-secondary"
                  type="button"
                  onClick={stopCapture}
                >
                  Stop and transcribe
                </button>
              </>
            ) : null}
            {phase === "transcribing" ? (
              <span role="status">Transcribing locally…</span>
            ) : null}
            <button
              className="button-tertiary"
              type="button"
              onClick={cancelSpeech}
            >
              Cancel
            </button>
          </>
        )}
        <span className="engineer-speech-availability">{statusText}</span>
      </div>
      {transcript !== null ? (
        <div className="engineer-speech-transcript">
          <label htmlFor="engineer-speech-transcript">
            Transcribed draft · review before asking
          </label>
          <textarea
            id="engineer-speech-transcript"
            value={transcript}
            maxLength={LOCAL_SPEECH_MAX_TRANSCRIPT_BYTES}
            onChange={(event) => {
              const next = event.target.value;
              setTranscript(next);
              setTranscriptBytes(new TextEncoder().encode(next).byteLength);
            }}
            rows={3}
          />
          <div className="engineer-speech-draft-footer">
            <span>
              {transcriptBytes.toLocaleString()} UTF-8 bytes · unverified local
              transcript
            </span>
            <button
              className="button-secondary"
              type="button"
              onClick={applyTranscript}
              disabled={transcriptBytes > 1_024 || !transcript.trim()}
            >
              Use transcript
            </button>
          </div>
          {transcriptBytes > 1_024 ? (
            <p role="status">
              Edit this draft to 1 KiB or less before using it.
            </p>
          ) : null}
        </div>
      ) : null}
      {message ? (
        <p className="engineer-speech-message" role="status">
          {message}
        </p>
      ) : null}
    </div>
  );
}

function readSpeechFailure(value: unknown): string {
  if (
    value &&
    typeof value === "object" &&
    "reason" in value &&
    typeof (value as { reason?: unknown }).reason === "string"
  ) {
    const reason = (value as { reason: string }).reason;
    if (reason === "engineer_ask_busy")
      return "GP...T is busy with another local AI task. Try again shortly.";
    if (
      reason === "transcription_timeout" ||
      reason === "engineer_speech_deadline_exceeded"
    )
      return "Local transcription timed out. Your typed question is unchanged.";
    if (reason === "transcript_empty")
      return "No speech was recognized. Try again or type your question.";
    if (reason === "runtime_not_configured" || reason.startsWith("runtime_"))
      return "The pinned local speech runtime is unavailable or changed. Typing questions remains available.";
  }
  return "Local transcription failed. Your typed question is unchanged.";
}

function speechUnavailableText(reason: string): string {
  if (reason === "runtime_not_configured")
    return "Local speech is not installed yet. You can type your question.";
  if (reason.startsWith("runtime_"))
    return "The pinned local speech runtime is unavailable or changed. You can type your question.";
  return "Local speech is unavailable. You can type your question.";
}
