"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import {
  cancelLocalEvidenceSpeech,
  cancelLocalEvidenceSpeechForPage,
  subscribeToLocalSpeechPlayback,
  speakLocalEvidence,
  type LocalSpeechPlayback,
  watchForLocalSpeechVoice,
} from "@/lib/local-speech-controller";
import {
  applyLocalVoicePreferences,
  DEFAULT_LOCAL_VOICE_PREFERENCES,
  getLocalVoicePreferenceSnapshot,
  getServerLocalVoicePreferenceSnapshot,
  subscribeToLocalVoicePreferences,
  type LocalVoicePreferences,
} from "@/lib/local-voice-preferences";

type VoiceAvailability = "checking" | "ready" | "unavailable";

const PREVIEW_SOURCE_KEY = "settings:local-voice-preview";
const PREVIEW_UTTERANCE = "GP dot dot dot T local voice preview. Audio settings are ready.";
const INITIAL_PLAYBACK: LocalSpeechPlayback = {
  status: "idle",
  sourceKey: null,
  message: null,
};

export default function SettingsVoicePreferences() {
  const preferenceState = useSyncExternalStore(
    subscribeToLocalVoicePreferences,
    getLocalVoicePreferenceSnapshot,
    getServerLocalVoicePreferenceSnapshot,
  );
  const [draftOverride, setDraftOverride] = useState<LocalVoicePreferences | null>(null);
  const [availability, setAvailability] = useState<VoiceAvailability>("checking");
  const [localVoice, setLocalVoice] = useState<SpeechSynthesisVoice | null>(null);
  const [playback, setPlayback] = useState(INITIAL_PLAYBACK);

  useEffect(() => {
    const unsubscribe = subscribeToLocalSpeechPlayback(setPlayback);
    const handleVisibility = () => {
      if (document.visibilityState === "hidden") cancelLocalEvidenceSpeechForPage();
    };
    const handlePageHide = () => cancelLocalEvidenceSpeechForPage();
    document.addEventListener("visibilitychange", handleVisibility);
    window.addEventListener("pagehide", handlePageHide);
    return () => {
      unsubscribe();
      document.removeEventListener("visibilitychange", handleVisibility);
      window.removeEventListener("pagehide", handlePageHide);
      cancelLocalEvidenceSpeech(PREVIEW_SOURCE_KEY);
    };
  }, []);

  useEffect(() => {
    if (
      typeof window === "undefined" ||
      !("speechSynthesis" in window) ||
      typeof window.speechSynthesis.getVoices !== "function" ||
      typeof SpeechSynthesisUtterance === "undefined"
    ) {
      setAvailability("unavailable");
      return;
    }
    return watchForLocalSpeechVoice(
      window.speechSynthesis,
      navigator.language,
      (result) => {
        if (result.status === "ready") {
          setLocalVoice(result.voice);
          setAvailability("ready");
        } else {
          setLocalVoice(null);
          setAvailability("unavailable");
        }
      },
    );
  }, []);

  useEffect(() => {
    cancelLocalEvidenceSpeechForPage();
  }, [preferenceState.effective.rate, preferenceState.effective.volume]);

  const draft = draftOverride ?? preferenceState.effective;
  const pending =
    draftOverride !== null && (
      draft.rate !== preferenceState.effective.rate ||
      draft.volume !== preferenceState.effective.volume
    );
  const previewSpeaking =
    playback.status === "speaking" && playback.sourceKey === PREVIEW_SOURCE_KEY;
  const localVoiceReady = availability === "ready" && localVoice?.localService === true;

  function updateDraft(next: LocalVoicePreferences) {
    cancelLocalEvidenceSpeechForPage();
    setDraftOverride(
      next.rate === preferenceState.effective.rate &&
        next.volume === preferenceState.effective.volume
        ? null
        : next,
    );
  }

  function applyDraft() {
    const result = applyLocalVoicePreferences(draft);
    if (result.ok) setDraftOverride(null);
  }

  function resetToDefaults() {
    const result = applyLocalVoicePreferences(DEFAULT_LOCAL_VOICE_PREFERENCES);
    if (result.ok) setDraftOverride(null);
  }

  function cancelDraft() {
    cancelLocalEvidenceSpeechForPage();
    setDraftOverride(null);
  }

  const statusText = preferenceState.message ?? preferenceStatusText(preferenceState.status);
  const playbackText =
    playback.status === "speaking" && playback.sourceKey !== PREVIEW_SOURCE_KEY
      ? "Another selected evidence report is speaking."
      : playback.sourceKey === PREVIEW_SOURCE_KEY
        ? playback.message
        : null;

  return (
    <section className="settings-section settings-voice-section panel" id="voice-audio" aria-labelledby="settings-voice-title">
      <div className="settings-section-heading">
        <div>
          <span className="eyebrow">VOICE &amp; AUDIO · BROWSER ONLY</span>
          <h2 id="settings-voice-title">Local read-aloud</h2>
        </div>
        <span className={`settings-state settings-state-${preferenceState.status}`}>
          {preferenceState.status === "stored" ? "SAVED HERE" : preferenceState.status.replaceAll("_", " ").toUpperCase()}
        </span>
      </div>
      <p className="settings-copy">
        These values affect user-triggered read-aloud for recorded Dashboard and Engineer evidence. They stay in this browser; no speech or telemetry is sent to the local service.
      </p>
      <p className="settings-copy settings-preview-note">
        Preview uses a fixed sample phrase and the current sliders, including pending changes. It never reads a session or telemetry.
      </p>
      <div className="settings-voice-grid">
        <label className="settings-range-control">
          <span>READING RATE <output>{draft.rate.toFixed(1)}×</output></span>
          <input
            type="range"
            min="0.5"
            max="2"
            step="0.1"
            value={draft.rate}
            onChange={(event) => updateDraft({ ...draft, rate: Number(event.currentTarget.value) })}
            aria-label="Read-aloud rate"
          />
          <small>0.5× slower · 2× faster</small>
        </label>
        <label className="settings-range-control">
          <span>VOICE VOLUME <output>{Math.round(draft.volume * 100)}%</output></span>
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={draft.volume}
            onChange={(event) => updateDraft({ ...draft, volume: Number(event.currentTarget.value) })}
            aria-label="Read-aloud volume"
          />
          <small>0% muted · 100% full volume</small>
        </label>
      </div>
      <div className="settings-voice-status">
        <span className="eyebrow">LOCAL VOICE</span>
        <p role="status" aria-live="polite">
          {availability === "ready"
            ? localVoiceReady
              ? `${localVoice?.name ?? "Local browser voice"} · ${localVoice?.lang ?? navigator.language} · local`
              : "No local browser voice is available. Remote voices are not used."
            : availability === "checking"
              ? "Checking for a local browser voice…"
              : "Local speech is unavailable in this browser. No remote fallback is used."}
        </p>
      </div>
      <div className="settings-voice-actions">
        <div className="settings-transaction-actions">
          <button className="button-secondary" type="button" disabled={preferenceState.status === "checking" || !pending} onClick={applyDraft}>
            Apply
          </button>
          <button className="button-tertiary" type="button" disabled={preferenceState.status === "checking" || !pending} onClick={cancelDraft}>
            Cancel
          </button>
          <button className="button-tertiary" type="button" disabled={preferenceState.status === "checking"} onClick={resetToDefaults}>
            Reset to defaults
          </button>
        </div>
        <div className="settings-preview-actions">
          <button
            className="button-secondary"
            type="button"
            disabled={!localVoiceReady}
            onClick={() => {
              if (localVoice?.localService === true) {
                speakLocalEvidence([PREVIEW_UTTERANCE], localVoice, PREVIEW_SOURCE_KEY, draft);
              }
            }}
          >
            {previewSpeaking ? "Restart preview" : "Preview current values"}
          </button>
          <button className="button-tertiary" type="button" disabled={playback.status !== "speaking"} onClick={cancelLocalEvidenceSpeechForPage}>
            Stop
          </button>
        </div>
      </div>
      <p className="settings-persistence-status" role="status" aria-live="polite">{statusText}</p>
      {pending ? <p className="settings-pending-status" role="status">Pending values are not active until applied.</p> : null}
      {playbackText ? <p className="settings-persistence-status" role="status" aria-live="polite">{playbackText}</p> : null}
    </section>
  );
}

function preferenceStatusText(status: string) {
  if (status === "checking") return "Checking this browser’s saved preferences…";
  if (status === "available") return "Using defaults. Changes are saved only after Apply or Reset.";
  if (status === "invalid") return "The saved record was left untouched. Defaults are active until you explicitly Apply or Reset.";
  if (status === "unavailable") return "Storage could not be read. Safe defaults are active.";
  if (status === "write_failed") return "The last write failed. Effective values were kept; you can retry after checking browser storage access.";
  return "Applied in this browser.";
}
