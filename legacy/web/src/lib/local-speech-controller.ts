export type LocalSpeechStatus =
  "idle" | "speaking" | "complete" | "stopped" | "failed";

export interface LocalSpeechPlayback {
  status: LocalSpeechStatus;
  sourceKey: string | null;
  message: string | null;
}

export interface LocalSpeechSettings {
  rate: number;
  volume: number;
}

export type LocalSpeechVoiceDiscovery =
  { status: "ready"; voice: SpeechSynthesisVoice } | { status: "unavailable" };

export function watchForLocalSpeechVoice(
  synthesis: SpeechSynthesis,
  preferredLanguage: string | undefined,
  listener: (result: LocalSpeechVoiceDiscovery) => void,
  timeoutMs = 2_500,
): () => void {
  let finished = false;
  let discoveredLocalVoice = false;
  const readVoices = () => {
    if (finished) return;
    try {
      const voices = synthesis.getVoices();
      const localVoices = voices.filter((voice) => voice.localService === true);
      const preferred = preferredLanguage?.toLowerCase();
      localVoices.sort((left, right) => {
        const leftMatches =
          preferred && left.lang.toLowerCase().startsWith(preferred);
        const rightMatches =
          preferred && right.lang.toLowerCase().startsWith(preferred);
        return Number(Boolean(rightMatches)) - Number(Boolean(leftMatches));
      });
      if (localVoices.length > 0) {
        discoveredLocalVoice = true;
        listener({ status: "ready", voice: localVoices[0] });
      } else {
        const wasReady = discoveredLocalVoice;
        discoveredLocalVoice = false;
        if (wasReady || voices.length > 0) listener({ status: "unavailable" });
      }
    } catch {
      const wasReady = discoveredLocalVoice;
      discoveredLocalVoice = false;
      if (wasReady) listener({ status: "unavailable" });
    }
  };

  readVoices();
  synthesis.addEventListener("voiceschanged", readVoices);
  const timeout = window.setTimeout(() => {
    if (!finished && !discoveredLocalVoice) listener({ status: "unavailable" });
  }, timeoutMs);
  return () => {
    finished = true;
    window.clearTimeout(timeout);
    synthesis.removeEventListener("voiceschanged", readVoices);
  };
}

const PLAYBACK_EVENT = "f1-engineer:local-speech-playback";

let generation = 0;
let activeSourceKey: string | null = null;

export function subscribeToLocalSpeechPlayback(
  listener: (playback: LocalSpeechPlayback) => void,
): () => void {
  const handle = (event: Event) => {
    const detail = (event as CustomEvent<LocalSpeechPlayback>).detail;
    if (detail) listener(detail);
  };
  window.addEventListener(PLAYBACK_EVENT, handle);
  return () => window.removeEventListener(PLAYBACK_EVENT, handle);
}

export function speakLocalEvidence(
  chunks: readonly string[],
  voice: SpeechSynthesisVoice,
  sourceKey: string,
  settings: LocalSpeechSettings = { rate: 1, volume: 1 },
): void {
  if (typeof window === "undefined" || !window.speechSynthesis) {
    publish({
      status: "failed",
      sourceKey,
      message: "Local speech is unavailable in this browser.",
    });
    return;
  }
  const requestGeneration = ++generation;
  activeSourceKey = null;
  try {
    window.speechSynthesis.cancel();
  } catch {
    publish({
      status: "failed",
      sourceKey,
      message: "The current browser speech could not be replaced.",
    });
    return;
  }
  if (voice.localService !== true) {
    publish({
      status: "failed",
      sourceKey,
      message: "Only a local browser voice can be used.",
    });
    return;
  }
  if (
    !Number.isFinite(settings.rate) ||
    settings.rate < 0.5 ||
    settings.rate > 2 ||
    !Number.isFinite(settings.volume) ||
    settings.volume < 0 ||
    settings.volume > 1
  ) {
    publish({
      status: "failed",
      sourceKey,
      message: "Local speech settings are outside the supported range.",
    });
    return;
  }

  activeSourceKey = sourceKey;
  try {
    publish({
      status: "speaking",
      sourceKey,
      message: "Speaking with a local browser voice.",
    });
    speakChunk(0);
  } catch {
    if (generation === requestGeneration) {
      activeSourceKey = null;
      publish({
        status: "failed",
        sourceKey,
        message: "The local browser voice could not start.",
      });
    }
  }

  function speakChunk(index: number): void {
    if (generation !== requestGeneration || activeSourceKey !== sourceKey)
      return;
    if (index >= chunks.length) {
      activeSourceKey = null;
      publish({
        status: "complete",
        sourceKey,
        message: "Read aloud complete.",
      });
      return;
    }
    const chunk = chunks[index];
    const utterance = new SpeechSynthesisUtterance(chunk);
    utterance.voice = voice;
    utterance.rate = settings.rate;
    utterance.volume = settings.volume;
    if (voice.lang) utterance.lang = voice.lang;
    utterance.onend = () => {
      if (generation === requestGeneration && activeSourceKey === sourceKey) {
        speakChunk(index + 1);
      }
    };
    utterance.onerror = () => {
      if (generation === requestGeneration && activeSourceKey === sourceKey) {
        activeSourceKey = null;
        publish({
          status: "failed",
          sourceKey,
          message: "The local browser voice stopped with an error.",
        });
      }
    };
    try {
      window.speechSynthesis.speak(utterance);
    } catch {
      if (generation === requestGeneration && activeSourceKey === sourceKey) {
        activeSourceKey = null;
        publish({
          status: "failed",
          sourceKey,
          message: "The local browser voice could not continue.",
        });
      }
    }
  }
}

export function cancelLocalEvidenceSpeech(sourceKey?: string): void {
  if (sourceKey !== undefined && activeSourceKey !== sourceKey) return;
  const canceledSource = activeSourceKey;
  if (canceledSource === null) return;
  generation += 1;
  activeSourceKey = null;
  try {
    if (typeof window !== "undefined" && window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
  } catch {
    // The stopped state remains authoritative even if the browser API throws.
  }
  publish({
    status: "stopped",
    sourceKey: canceledSource,
    message: "Speech stopped.",
  });
}

export function cancelLocalEvidenceSpeechForPage(): void {
  cancelLocalEvidenceSpeech();
}

function publish(playback: LocalSpeechPlayback): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(PLAYBACK_EVENT, { detail: playback }));
}
