// Browser-only voice output choices that the engine never sees: which local
// voice is preferred and whether engineer calls are read aloud on the Drive
// screen (the switch is the one Drive itself stores).
// Rate and volume stay in `lib/local-voice-preferences.ts`. Stored as one small
// JSON record; exposed as an external store so it can be read with
// `useSyncExternalStore` (server render uses the defaults).

export const BROWSER_VOICE_KEY = "race-engineer.voice-output.v1";
/** The Drive screen's own read-aloud switch ("1" on, "0" off); shared so both screens agree. */
export const READ_ALOUD_KEY = "f1-engineer:drive-read-aloud";
export const BROWSER_VOICE_MAX_BYTES = 1024;

export type BrowserVoiceChoice = {
  /** `SpeechSynthesisVoice.voiceURI`; null means the default local voice. */
  voiceURI: string | null;
  /** Read engineer calls aloud on Drive. Off unless the user turns it on. */
  readAloud: boolean;
};

export const DEFAULT_BROWSER_VOICE: BrowserVoiceChoice = { voiceURI: null, readAloud: false };

/** Reads the stored voice record and the Drive read-aloud flag; anything unreadable yields the defaults. */
export function parseBrowserVoice(raw: string | null, readAloudRaw: string | null = null): BrowserVoiceChoice {
  const readAloud = readAloudRaw === "1";
  if (!raw || raw.length > BROWSER_VOICE_MAX_BYTES) return { voiceURI: null, readAloud };
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return { voiceURI: null, readAloud };
    const uri = (parsed as Record<string, unknown>).voiceURI;
    return {
      voiceURI: typeof uri === "string" && uri.length > 0 && uri.length <= 256 ? uri : null,
      readAloud,
    };
  } catch {
    return { voiceURI: null, readAloud };
  }
}

export function serializeBrowserVoice(choice: BrowserVoiceChoice): string {
  return JSON.stringify({ schema_version: 1, voiceURI: choice.voiceURI });
}

/* ------------------------------------------------------------ external store */

const listeners = new Set<() => void>();
let cachedRaw: string | null | undefined;
let cachedReadAloudRaw: string | null | undefined;
let cachedChoice: BrowserVoiceChoice = DEFAULT_BROWSER_VOICE;
let storageListener: ((event: StorageEvent) => void) | null = null;

function readItem(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function getBrowserVoiceSnapshot(): BrowserVoiceChoice {
  const raw = readItem(BROWSER_VOICE_KEY);
  const readAloudRaw = readItem(READ_ALOUD_KEY);
  if (raw !== cachedRaw || readAloudRaw !== cachedReadAloudRaw) {
    cachedRaw = raw;
    cachedReadAloudRaw = readAloudRaw;
    cachedChoice = parseBrowserVoice(raw, readAloudRaw);
  }
  return cachedChoice;
}

export function getServerBrowserVoiceSnapshot(): BrowserVoiceChoice {
  return DEFAULT_BROWSER_VOICE;
}

export function subscribeBrowserVoice(listener: () => void): () => void {
  listeners.add(listener);
  if (!storageListener) {
    storageListener = (event) => {
      if (event.key === null || event.key === BROWSER_VOICE_KEY || event.key === READ_ALOUD_KEY) for (const notify of listeners) notify();
    };
    window.addEventListener("storage", storageListener);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && storageListener) {
      window.removeEventListener("storage", storageListener);
      storageListener = null;
    }
  };
}

/** Saves the choice. Returns false when browser storage refuses the write. */
export function saveBrowserVoice(choice: BrowserVoiceChoice): boolean {
  try {
    window.localStorage.setItem(BROWSER_VOICE_KEY, serializeBrowserVoice(choice));
    window.localStorage.setItem(READ_ALOUD_KEY, choice.readAloud ? "1" : "0");
  } catch {
    return false;
  }
  for (const notify of listeners) notify();
  return true;
}
