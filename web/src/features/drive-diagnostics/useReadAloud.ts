"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  cancelLocalEvidenceSpeech,
  speakLocalEvidence,
  subscribeToLocalSpeechPlayback,
  watchForLocalSpeechVoice,
} from "@/lib/local-speech-controller";
import {
  getLocalVoicePreferenceSnapshot,
  getServerLocalVoicePreferenceSnapshot,
  subscribeToLocalVoicePreferences,
} from "@/lib/local-voice-preferences";
import type { EngineerEvent } from "@/features/review-data/engineer-events";
import {
  getBrowserVoiceSnapshot,
  getServerBrowserVoiceSnapshot,
  subscribeBrowserVoice,
} from "@/features/settings/browser-voice";
import { isSpeakable, speakingPreview, speechChunks, speechDecision, type Interruption, type PlaybackOutcome } from "./radio";
import type { AudioState } from "./system-rows";
import { useRadioPresence } from "@/features/settings/useRadioPresence";
import { browserMaySpeak } from "@/features/settings/radio-settings";

// Optional browser read-aloud of engineer calls. It is a browser-only setting
// (localStorage), off by default, and uses the existing local speech
// controller and the voice (browser-voice.ts), rate and volume saved from
// Settings. Without a saved voice, or when it is no longer installed, the first
// local voice is used. Nothing is sent
// anywhere and no new audio code is involved.

const STORAGE_KEY = "f1-engineer:drive-read-aloud";
const SOURCE_PREFIX = "radio-";
const MAX_QUEUE = 5;

const listeners = new Set<() => void>();

function readEnabled(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function writeEnabled(enabled: boolean): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, enabled ? "1" : "0");
  } catch {
    // Storage may be blocked; the in-memory notification below still applies.
  }
  for (const listener of listeners) listener();
}

function subscribeEnabled(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

type Voice =
  | { kind: "checking" }
  | { kind: "ready"; voice: SpeechSynthesisVoice }
  | { kind: "no_voice" }
  | { kind: "unsupported" };

/** The voice chosen in Settings when it is still an installed local voice, else the discovered default. */
function chosenVoice(fallback: SpeechSynthesisVoice, voiceURI: string | null): SpeechSynthesisVoice {
  if (!voiceURI || typeof window === "undefined" || !window.speechSynthesis) return fallback;
  try {
    const saved = window.speechSynthesis
      .getVoices()
      .find((candidate) => candidate.voiceURI === voiceURI && candidate.localService === true);
    return saved ?? fallback;
  } catch {
    return fallback;
  }
}

type QueueItem = { id: number; text: string; priority: "low" | "normal" | "high" };

export type ReadAloud = {
  radioConnected: boolean;
  enabled: boolean;
  setEnabled: (enabled: boolean) => void;
  /** Result of discovering a local voice; also feeds the Audio Output row. */
  audio: AudioState;
  /** Preview of the call being spoken, or null. */
  speaking: string | null;
  /** Results by event id for rows spoken or skipped in this tab. */
  playback: Readonly<Record<number, PlaybackOutcome>>;
  /** Offers newly arrived events to the speaker. Ignored while read-aloud is off. */
  offer: (events: readonly EngineerEvent[]) => void;
};

export function useReadAloud(interruption: Interruption): ReadAloud {
  const radioConnected = useRadioPresence().connected;
  const enabled = useSyncExternalStore(subscribeEnabled, readEnabled, () => false);
  const voicePreferences = useSyncExternalStore(
    subscribeToLocalVoicePreferences,
    getLocalVoicePreferenceSnapshot,
    getServerLocalVoicePreferenceSnapshot,
  ).effective;
  const savedVoiceURI = useSyncExternalStore(
    subscribeBrowserVoice,
    () => getBrowserVoiceSnapshot().voiceURI,
    () => getServerBrowserVoiceSnapshot().voiceURI,
  );
  const [voice, setVoice] = useState<Voice>({ kind: "checking" });
  const [speaking, setSpeaking] = useState<{ id: number; preview: string } | null>(null);
  const [playback, setPlayback] = useState<Record<number, PlaybackOutcome>>({});

  const current = useRef<QueueItem | null>(null);
  const queue = useRef<QueueItem[]>([]);
  const suppressDrain = useRef(false);
  const latest = useRef({ enabled: browserMaySpeak(enabled, radioConnected), interruption, voice, voicePreferences, savedVoiceURI });
  // Declared before every other effect so callers' effects see current values.
  useEffect(() => {
    latest.current = { enabled: browserMaySpeak(enabled, radioConnected), interruption, voice, voicePreferences, savedVoiceURI };
  }, [enabled, radioConnected, interruption, voice, voicePreferences, savedVoiceURI]);

  const record = useCallback((id: number, outcome: PlaybackOutcome) => {
    setPlayback((previous) => (previous[id] === outcome || previous[id] === "played" ? previous : { ...previous, [id]: outcome }));
  }, []);

  const start = useCallback((item: QueueItem) => {
    const { voice: found, voicePreferences: settings, savedVoiceURI: voiceURI } = latest.current;
    if (found.kind !== "ready") {
      record(item.id, "not_played");
      return;
    }
    current.current = item;
    setSpeaking({ id: item.id, preview: speakingPreview(item.text) });
    speakLocalEvidence(speechChunks(item.text), chosenVoice(found.voice, voiceURI), `${SOURCE_PREFIX}${item.id}`, {
      rate: settings.rate,
      volume: settings.volume,
    });
  }, [record]);

  const drain = useCallback(() => {
    const next = queue.current.shift();
    if (next) start(next);
  }, [start]);

  // Voice discovery runs always: the Audio Output row needs it even when off.
  useEffect(() => {
    if (typeof window === "undefined" || !window.speechSynthesis) {
      setVoice({ kind: "unsupported" });
      return;
    }
    return watchForLocalSpeechVoice(window.speechSynthesis, navigator.language, (result) => {
      setVoice(result.status === "ready" ? { kind: "ready", voice: result.voice } : { kind: "no_voice" });
    });
  }, []);

  // Playback events from the shared controller; only this feature's keys.
  useEffect(
    () =>
      subscribeToLocalSpeechPlayback((event) => {
        if (!event.sourceKey?.startsWith(SOURCE_PREFIX)) return;
        const id = Number(event.sourceKey.slice(SOURCE_PREFIX.length));
        if (!Number.isSafeInteger(id)) return;
        if (event.status === "speaking") return;
        record(id, event.status === "complete" ? "played" : "not_played");
        if (current.current?.id !== id) return;
        current.current = null;
        setSpeaking(null);
        if (!suppressDrain.current) drain();
      }),
    [drain, record],
  );

  const stopAll = useCallback(() => {
    for (const item of queue.current) record(item.id, "not_played");
    queue.current = [];
    if (current.current) {
      suppressDrain.current = true;
      cancelLocalEvidenceSpeech(`${SOURCE_PREFIX}${current.current.id}`);
      suppressDrain.current = false;
    }
  }, [record]);

  useEffect(() => () => stopAll(), [stopAll]);
  useEffect(() => { if (radioConnected) stopAll(); }, [radioConnected, stopAll]);

  const setEnabled = useCallback(
    (next: boolean) => {
      if (!next) stopAll();
      writeEnabled(next);
    },
    [stopAll],
  );

  const offer = useCallback(
    (events: readonly EngineerEvent[]) => {
      const { enabled: on, interruption: policy, voice: found } = latest.current;
      if (!on || found.kind !== "ready") return;
      for (const event of events) {
        if (!isSpeakable(event)) continue;
        const item: QueueItem = { id: event.id, text: event.text, priority: event.priority };
        const action = speechDecision({ interruption: policy, priority: event.priority, speaking: current.current !== null });
        if (action === "speak") {
          start(item);
        } else if (action === "cancel_and_speak") {
          const interrupted = current.current;
          suppressDrain.current = true;
          if (interrupted) cancelLocalEvidenceSpeech(`${SOURCE_PREFIX}${interrupted.id}`);
          suppressDrain.current = false;
          start(item);
        } else if (action === "queue") {
          queue.current.push(item);
          while (queue.current.length > MAX_QUEUE) {
            const dropped = queue.current.shift();
            if (dropped) record(dropped.id, "not_played");
          }
        } else {
          record(item.id, "not_played");
        }
      }
    },
    [record, start],
  );

  const audio: AudioState =
    voice.kind === "ready"
      ? { kind: "available", voice: chosenVoice(voice.voice, savedVoiceURI).name, speaking: speaking !== null }
      : voice.kind === "no_voice"
        ? { kind: "no_voice" }
        : voice.kind === "unsupported"
          ? { kind: "unsupported" }
          : { kind: "checking" };

  return { enabled, setEnabled, audio, speaking: speaking?.preview ?? null, playback, offer, radioConnected };
}
