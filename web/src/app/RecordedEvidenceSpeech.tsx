"use client";

import { useEffect, useState } from "react";
import {
  cancelLocalEvidenceSpeech,
  cancelLocalEvidenceSpeechForPage,
  subscribeToLocalSpeechPlayback,
  speakLocalEvidence,
  type LocalSpeechPlayback,
  watchForLocalSpeechVoice,
} from "@/lib/local-speech-controller";
import type { RecordedSpeechPlanResult } from "@/lib/recorded-speech-plan";

type VoiceAvailability = "checking" | "ready" | "unavailable";

const INITIAL_PLAYBACK: LocalSpeechPlayback = {
  status: "idle",
  sourceKey: null,
  message: null,
};

export default function RecordedEvidenceSpeech({
  planResult,
}: {
  planResult: RecordedSpeechPlanResult;
}) {
  const [availability, setAvailability] =
    useState<VoiceAvailability>("checking");
  const [localVoice, setLocalVoice] = useState<SpeechSynthesisVoice | null>(
    null,
  );
  const [playback, setPlayback] = useState(INITIAL_PLAYBACK);
  const plan = planResult.ok ? planResult.plan : null;
  const sourceKey = plan?.source_key ?? null;

  useEffect(() => {
    const unsubscribe = subscribeToLocalSpeechPlayback(setPlayback);
    const handleVisibility = () => {
      if (document.visibilityState === "hidden")
        cancelLocalEvidenceSpeechForPage();
    };
    const handlePageHide = () => cancelLocalEvidenceSpeechForPage();
    document.addEventListener("visibilitychange", handleVisibility);
    window.addEventListener("pagehide", handlePageHide);
    return () => {
      unsubscribe();
      document.removeEventListener("visibilitychange", handleVisibility);
      window.removeEventListener("pagehide", handlePageHide);
    };
  }, []);

  useEffect(() => {
    if (!sourceKey) return;
    return () => cancelLocalEvidenceSpeech(sourceKey);
  }, [sourceKey]);

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

    setAvailability("checking");
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

  const isThisReportSpeaking =
    playback.status === "speaking" && playback.sourceKey === sourceKey;
  const speechIsActive = playback.status === "speaking";
  const voiceLabel =
    availability === "ready"
      ? `Local voice ready${localVoice?.name ? ` · ${localVoice.name}` : ""}`
      : availability === "checking"
        ? "Checking for a local browser voice…"
        : "Local speech unavailable";

  return (
    <section
      className="recorded-speech"
      aria-label="Read recorded evidence aloud"
    >
      {planResult.ok ? (
        <div className="recorded-speech-preview">
          <strong>Speech preview</strong>
          <p className="recorded-speech-note">
            These exact lines play in order.
          </p>
          <ol>
            {planResult.plan.chunks.map((chunk, index) => (
              <li key={`${index}-${chunk}`}>{chunk}</li>
            ))}
          </ol>
        </div>
      ) : (
        <p className="recorded-speech-error" role="status">
          Speech preview unavailable: {planResult.reason}
        </p>
      )}

      <div className="recorded-speech-controls">
        <div>
          <span className="eyebrow">LOCAL READ-ALOUD</span>
          <p
            className="recorded-speech-availability"
            role="status"
            aria-live="polite"
          >
            {voiceLabel}
          </p>
        </div>
        <div className="recorded-speech-buttons">
          <button
            className="button-secondary"
            type="button"
            disabled={!plan || availability !== "ready" || !localVoice}
            onClick={() => {
              if (plan && localVoice?.localService === true) {
                speakLocalEvidence(plan.chunks, localVoice, plan.source_key);
              }
            }}
          >
            {isThisReportSpeaking ? "Restart read-aloud" : "Read aloud"}
          </button>
          <button
            className="button-tertiary"
            type="button"
            disabled={!speechIsActive}
            onClick={() => cancelLocalEvidenceSpeechForPage()}
          >
            Stop
          </button>
        </div>
      </div>

      {playback.message &&
      (playback.sourceKey === sourceKey || playback.status === "speaking") ? (
        <p
          className="recorded-speech-playback"
          role="status"
          aria-live="polite"
        >
          {playback.status === "speaking" && playback.sourceKey !== sourceKey
            ? "Another selected evidence report is speaking."
            : playback.message}
        </p>
      ) : null}
    </section>
  );
}
