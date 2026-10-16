"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { EmptyState, InlineAlert } from "@/features/ui-foundation/states";
import { ChevronRightIcon, PlayIcon, StatusGlyph, StopIcon } from "@/features/ui-foundation/icons";
import { Button, Card, Checkbox, Select } from "@/features/ui-foundation/primitives";
import {
  applyLocalVoicePreferences,
  getLocalVoicePreferenceSnapshot,
  getServerLocalVoicePreferenceSnapshot,
  subscribeToLocalVoicePreferences,
} from "@/lib/local-voice-preferences";
import {
  cancelLocalEvidenceSpeech,
  speakLocalEvidence,
  subscribeToLocalSpeechPlayback,
} from "@/lib/local-speech-controller";
import {
  METER_SEGMENTS,
  RATE_MAX,
  RATE_MIN,
  RATE_STEP,
  SAMPLE_PHRASE,
  TEST_SECONDS,
  formatSeconds,
  heardText,
  levelFraction,
  litSegments,
  pickVoice,
  rateLabel,
  segmentTone,
  snapRate,
  volumeLabel,
} from "./audio";
import {
  getBrowserVoiceSnapshot,
  getServerBrowserVoiceSnapshot,
  saveBrowserVoice,
  subscribeBrowserVoice,
} from "./browser-voice";
import type { SpeechView } from "./connections";
import { deviceLabel, microphoneStage, selectedDevice, type BrowserAudio } from "./devices";
import { openMic, type MicSession } from "./mic-capture";
import { Field } from "./shared";
import { runSpeechTest, type SpeechTestOutcome } from "./speech-test";
import { UnitsCard } from "./UnitsCard";
import styles from "./Settings.module.css";
import tabStyles from "./SettingsTabs.module.css";

const SAMPLE_KEY = "settings-voice-sample";

const SEGMENT_FILL = {
  green: "var(--green)",
  amber: "var(--amber)",
  red: "var(--red)",
} as const;

/** 20-segment input level meter. SVG so it needs no CSS-drawn shapes. */
export function LevelMeter({ rms, label }: { rms: number; label: string }) {
  const lit = litSegments(rms);
  return (
    <svg
      className={tabStyles.meterSvg}
      viewBox="0 0 200 14"
      preserveAspectRatio="none"
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(levelFraction(rms) * 100)}
      focusable="false"
    >
      {Array.from({ length: METER_SEGMENTS }, (_, index) => (
        <rect
          key={index}
          x={index * 10 + 1}
          y={1}
          width={8}
          height={12}
          rx={1.5}
          fill={index < lit ? SEGMENT_FILL[segmentTone(index)] : "var(--grey-soft)"}
        />
      ))}
    </svg>
  );
}

export type VoicePanelProps = {
  audio: BrowserAudio;
  requestAccess: () => Promise<"granted" | "denied" | "no-device" | "failed">;
  micDeviceId: string | null;
  onMicDevice: (deviceId: string) => void;
  speech: SpeechView;
  onShowAssistance: () => void;
};

export function VoicePanel({ audio, requestAccess, micDeviceId, onMicDevice, speech, onShowAssistance }: VoicePanelProps) {
  return (
    <div className={styles.grid}>
      <MicrophoneCard
        audio={audio}
        requestAccess={requestAccess}
        deviceId={micDeviceId}
        onDevice={onMicDevice}
        speech={speech}
      />
      <EngineerVoiceCard audio={audio} onShowAssistance={onShowAssistance} />
      <UnitsCard />
    </div>
  );
}

/* --------------------------------------------------------------- microphone */

function MicrophoneCard({
  audio,
  requestAccess,
  deviceId,
  onDevice,
  speech,
}: {
  audio: BrowserAudio;
  requestAccess: VoicePanelProps["requestAccess"];
  deviceId: string | null;
  onDevice: (deviceId: string) => void;
  speech: SpeechView;
}) {
  const stage = microphoneStage(audio);
  const device = selectedDevice(audio.devices, deviceId);
  const activeId = device?.deviceId ?? null;

  const [rms, setRms] = useState(0);
  const [micReady, setMicReady] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);
  const [accessMessage, setAccessMessage] = useState<string | null>(null);
  const [phase, setPhase] = useState<"idle" | "recording" | "transcribing">("idle");
  const [elapsed, setElapsed] = useState(0);
  const [outcome, setOutcome] = useState<SpeechTestOutcome | null>(null);
  const session = useRef<MicSession | null>(null);
  const tickTimer = useRef<number | null>(null);
  const stopTimer = useRef<number | null>(null);
  const request = useRef<AbortController | null>(null);

  function clearTimers() {
    if (tickTimer.current !== null) window.clearInterval(tickTimer.current);
    if (stopTimer.current !== null) window.clearTimeout(stopTimer.current);
    tickTimer.current = null;
    stopTimer.current = null;
  }

  // The meter listens only while this tab shows a ready microphone.
  useEffect(() => {
    if (stage !== "ready") return;
    let cancelled = false;
    openMic(activeId, setRms)
      .then((opened) => {
        if (cancelled) {
          opened.close();
          return;
        }
        session.current = opened;
        setOpenError(null);
        setMicReady(true);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const name = error instanceof DOMException ? error.name : "";
        setOpenError(
          name === "NotAllowedError"
            ? "Microphone access was withdrawn. Allow it in the browser's site settings, then reload."
            : "The microphone could not be opened. It may be in use by another app.",
        );
      });
    return () => {
      cancelled = true;
      clearTimers();
      request.current?.abort();
      session.current?.close();
      session.current = null;
      setMicReady(false);
      setRms(0);
      setPhase("idle");
    };
  }, [stage, activeId]);

  async function finishTest() {
    clearTimers();
    const opened = session.current;
    if (!opened) return;
    const samples = opened.finishRecording(TEST_SECONDS);
    setPhase("transcribing");
    const controller = new AbortController();
    request.current = controller;
    const result = await runSpeechTest(samples, opened.sampleRate, controller.signal);
    if (controller.signal.aborted) return;
    setOutcome(result);
    setPhase("idle");
  }

  function startTest() {
    const opened = session.current;
    if (!opened || phase !== "idle") return;
    setOutcome(null);
    setElapsed(0);
    opened.startRecording();
    setPhase("recording");
    const began = performance.now();
    tickTimer.current = window.setInterval(() => setElapsed(Math.floor((performance.now() - began) / 1000)), 250);
    stopTimer.current = window.setTimeout(() => void finishTest(), TEST_SECONDS * 1000);
  }

  async function allow() {
    setAccessMessage(null);
    const result = await requestAccess();
    if (result === "failed") setAccessMessage("The microphone could not be opened. Try again.");
  }

  const speechLine =
    speech.state === "ready"
      ? "Speech recognition: Ready · Local CPU"
      : speech.state === "unavailable"
        ? "Speech recognition: Not set up on the engine"
        : speech.state === "unreachable"
          ? "Speech recognition: Local engine unavailable"
          : "Speech recognition: Status not recognised";

  return (
    <Card title="Microphone" labelledBy="voice-microphone">
      <p className={styles.cardDesc}>Check which input the browser hears and whether speech recognition understands it.</p>

      {stage === "checking" && <p className={`${tabStyles.text} ${tabStyles.muted}`}>Checking the microphone…</p>}

      {stage === "unsupported" && (
        <EmptyState size="inline" title="This browser does not support microphone access." />
      )}

      {stage === "blocked" && (
        <InlineAlert tone="error">
          Microphone access is blocked. Allow it in the browser&apos;s site settings, then reload.
        </InlineAlert>
      )}

      {stage === "needs-access" && (
        <>
          <p className={tabStyles.text}>The browser asks for permission once.</p>
          <div className={tabStyles.row}>
            <Button variant="outline-blue" onClick={() => void allow()}>
              Allow microphone access
            </Button>
          </div>
          {accessMessage && (
            <p className={`${tabStyles.text} ${tabStyles.err}`} role="alert">
              {accessMessage}
            </p>
          )}
        </>
      )}

      {stage === "no-device" && (
        <>
          <p className={tabStyles.text}>No microphone was found.</p>
          <div className={tabStyles.row}>
            <Button onClick={() => void allow()}>Check again</Button>
          </div>
        </>
      )}

      {stage === "ready" && (
        <>
          <Field label="Input device" help="Used for the level meter and the test below.">
            <Select
              value={activeId ?? ""}
              onChange={(event) => onDevice(event.target.value)}
              aria-label="Input device"
              disabled={phase !== "idle"}
            >
              {audio.devices.map((candidate, index) => (
                <option key={candidate.deviceId || index} value={candidate.deviceId}>
                  {deviceLabel(candidate, index)}
                </option>
              ))}
            </Select>
          </Field>

          <div className={tabStyles.meterRow}>
            <span className={tabStyles.meterLabel}>Level</span>
            <LevelMeter rms={rms} label="Microphone level" />
          </div>

          {openError ? (
            <p className={`${tabStyles.text} ${tabStyles.err}`} role="alert">
              {openError}
            </p>
          ) : (
            <div className={tabStyles.row}>
              {phase === "recording" ? (
                <>
                  <span className={tabStyles.inline} role="status">
                    <StatusGlyph tone="red" glyph="dot" size={14} />
                    Recording… {elapsed} s
                  </span>
                  <Button onClick={() => void finishTest()}>
                    <StopIcon size={13} className={tabStyles.buttonIcon} />
                    Stop
                  </Button>
                </>
              ) : (
                <>
                  <Button
                    variant="outline-blue"
                    onClick={startTest}
                    disabled={phase !== "idle" || !micReady}
                    aria-busy={phase === "transcribing" || undefined}
                  >
                    {phase === "transcribing" ? "Transcribing…" : "Test microphone"}
                  </Button>
                  <span className={tabStyles.hint}>Speak for up to {TEST_SECONDS} s</span>
                </>
              )}
            </div>
          )}

          {outcome && (
            <p className={tabStyles.heard} data-tone={outcome.kind === "error" ? "error" : undefined} role="status">
              {outcome.kind === "heard" ? (
                <>
                  {heardText(outcome.transcript)}
                  <span className={tabStyles.heardTime}>({formatSeconds(outcome.ms)})</span>
                </>
              ) : (
                outcome.text
              )}
            </p>
          )}
          <p className={styles.footnote}>{speechLine}</p>
        </>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------ engineer voice */

function EngineerVoiceCard({ audio, onShowAssistance }: { audio: BrowserAudio; onShowAssistance: () => void }) {
  const stored = useSyncExternalStore(
    subscribeToLocalVoicePreferences,
    getLocalVoicePreferenceSnapshot,
    getServerLocalVoicePreferenceSnapshot,
  );
  const choice = useSyncExternalStore(subscribeBrowserVoice, getBrowserVoiceSnapshot, getServerBrowserVoiceSnapshot);
  const [speaking, setSpeaking] = useState(false);
  const [sampleError, setSampleError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState(false);

  useEffect(() => {
    const unsubscribe = subscribeToLocalSpeechPlayback((playback) => {
      if (playback.sourceKey !== SAMPLE_KEY) return;
      setSpeaking(playback.status === "speaking");
      setSampleError(playback.status === "failed" ? playback.message : null);
    });
    return () => {
      unsubscribe();
      cancelLocalEvidenceSpeech(SAMPLE_KEY);
    };
  }, []);

  if (audio.checked && !audio.speechSupported) {
    return (
      <Card title="Engineer Voice" labelledBy="voice-output">
        <p className={styles.cardDesc}>How spoken responses sound in this browser.</p>
        <EmptyState size="inline" title="This browser does not support speech output." />
      </Card>
    );
  }

  const picked = pickVoice(audio.voices, choice.voiceURI);
  const rate = snapRate(stored.effective.rate);
  const volume = stored.effective.volume;

  function setRate(value: number) {
    applyLocalVoicePreferences({ rate: snapRate(value), volume });
  }
  function setVolume(percent: number) {
    applyLocalVoicePreferences({ rate, volume: Math.min(1, Math.max(0, percent / 100)) });
  }
  function saveChoice(next: Partial<typeof choice>) {
    setSaveError(!saveBrowserVoice({ ...choice, ...next }));
  }

  function play() {
    if (speaking) {
      cancelLocalEvidenceSpeech(SAMPLE_KEY);
      return;
    }
    setSampleError(null);
    const voice = picked
      ? window.speechSynthesis.getVoices().find((candidate) => candidate.voiceURI === picked.voiceURI && candidate.localService)
      : undefined;
    if (!voice) {
      setSampleError("No local voice is available in this browser.");
      return;
    }
    speakLocalEvidence([SAMPLE_PHRASE], voice, SAMPLE_KEY, { rate: stored.effective.rate, volume });
  }

  const message =
    stored.status === "invalid" || stored.status === "unavailable" || stored.status === "write_failed"
      ? stored.message
      : null;

  return (
    <Card title="Engineer Voice" labelledBy="voice-output">
      <p className={styles.cardDesc}>How spoken responses sound in this browser.</p>

      <Field label="Voice" help="Only voices installed on this computer are offered. Nothing is sent to a cloud service.">
        <Select
          value={picked?.voiceURI ?? ""}
          onChange={(event) => saveChoice({ voiceURI: event.target.value || null })}
          aria-label="Voice"
          disabled={audio.voices.length === 0}
        >
          {audio.voices.length === 0 && <option value="">{audio.checked ? "No local voice found" : "Checking…"}</option>}
          {audio.voices.map((voice) => (
            <option key={voice.voiceURI} value={voice.voiceURI}>
              {voice.name}
            </option>
          ))}
        </Select>
      </Field>

      <div className={tabStyles.slider}>
        <label className={tabStyles.sliderLabel} htmlFor="voice-rate">
          Rate
        </label>
        <input
          id="voice-rate"
          className={tabStyles.range}
          type="range"
          min={RATE_MIN}
          max={RATE_MAX}
          step={RATE_STEP}
          value={rate}
          onChange={(event) => setRate(Number(event.target.value))}
          aria-valuetext={rateLabel(rate)}
        />
        <output htmlFor="voice-rate">{rateLabel(rate)}</output>
      </div>
      <div className={tabStyles.slider}>
        <label className={tabStyles.sliderLabel} htmlFor="voice-volume">
          Volume
        </label>
        <input
          id="voice-volume"
          className={tabStyles.range}
          type="range"
          min={0}
          max={100}
          step={5}
          value={Math.round(volume * 100)}
          onChange={(event) => setVolume(Number(event.target.value))}
          aria-valuetext={volumeLabel(volume)}
        />
        <output htmlFor="voice-volume">{volumeLabel(volume)}</output>
      </div>

      <div className={tabStyles.row}>
        <Button onClick={play} disabled={!picked} aria-pressed={speaking || undefined}>
          {speaking ? (
            <StopIcon size={13} className={tabStyles.buttonIcon} />
          ) : (
            <PlayIcon size={13} className={tabStyles.buttonIcon} />
          )}
          {speaking ? "Stop sample" : "Play sample"}
        </Button>
      </div>
      {sampleError && (
        <p className={`${tabStyles.text} ${tabStyles.err}`} role="alert">
          {sampleError}
        </p>
      )}

      <div className={tabStyles.mt}>
        <Checkbox
          label="Read engineer calls aloud on Drive"
          checked={choice.readAloud}
          onChange={(readAloud) => saveChoice({ readAloud })}
        />
        <p className={styles.help}>Off by default. Calls are spoken only while the Drive screen is open.</p>
      </div>
      <p className={tabStyles.text}>
        <button type="button" className={tabStyles.linkButton} onClick={onShowAssistance}>
          Interruptions follow Assistance
          <ChevronRightIcon size={13} />
        </button>
      </p>

      <p className={styles.footnote} role="status">
        {saveError
          ? "Browser storage could not save the voice choice."
          : (message ?? "Saved in this browser. Nothing here is sent to the engine.")}
      </p>
    </Card>
  );
}
