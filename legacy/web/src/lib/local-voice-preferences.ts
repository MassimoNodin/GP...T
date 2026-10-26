export interface LocalVoicePreferences {
  rate: number;
  volume: number;
}

export type LocalVoicePreferenceStatus =
  | "checking"
  | "available"
  | "stored"
  | "invalid"
  | "unavailable"
  | "write_failed";

export interface LocalVoicePreferenceSnapshot {
  effective: LocalVoicePreferences;
  status: LocalVoicePreferenceStatus;
  message: string | null;
}

export type ApplyLocalVoicePreferenceResult =
  | { ok: true }
  | { ok: false; reason: string };

export const DEFAULT_LOCAL_VOICE_PREFERENCES: LocalVoicePreferences = {
  rate: 1,
  volume: 1,
};

export const LOCAL_VOICE_PREFERENCE_STORAGE_KEY =
  "gp...t:local-voice-preferences";
export const LOCAL_VOICE_PREFERENCE_MAX_BYTES = 2 * 1024;

const SERVER_SNAPSHOT: LocalVoicePreferenceSnapshot = {
  effective: DEFAULT_LOCAL_VOICE_PREFERENCES,
  status: "checking",
  message: null,
};

let snapshot = SERVER_SNAPSHOT;
let hasLoadedFromStorage = false;
let storageListener: ((event: StorageEvent) => void) | null = null;
const listeners = new Set<() => void>();

export function getLocalVoicePreferenceSnapshot(): LocalVoicePreferenceSnapshot {
  return snapshot;
}

export function getServerLocalVoicePreferenceSnapshot(): LocalVoicePreferenceSnapshot {
  return SERVER_SNAPSHOT;
}

export function subscribeToLocalVoicePreferences(listener: () => void): () => void {
  listeners.add(listener);
  if (typeof window !== "undefined") {
    if (!storageListener) {
      storageListener = handleStorageChange;
      window.addEventListener("storage", storageListener);
    }
    if (!hasLoadedFromStorage) loadFromStorage();
  }

  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && typeof window !== "undefined" && storageListener) {
      window.removeEventListener("storage", storageListener);
      storageListener = null;
      hasLoadedFromStorage = false;
    }
  };
}

export function applyLocalVoicePreferences(
  value: LocalVoicePreferences,
): ApplyLocalVoicePreferenceResult {
  if (!isValidPreferences(value)) {
    const reason = "Rate must be from 0.5 to 2 and volume from 0 to 1.";
    publish({ ...snapshot, status: "write_failed", message: reason });
    return { ok: false, reason };
  }

  const record = {
    schema_version: 1,
    rate: value.rate,
    volume: value.volume,
  };
  const serialized = JSON.stringify(record);
  if (utf8ByteLength(serialized) > LOCAL_VOICE_PREFERENCE_MAX_BYTES) {
    const reason = "The browser preference record exceeds its storage limit.";
    publish({ ...snapshot, status: "write_failed", message: reason });
    return { ok: false, reason };
  }

  try {
    if (typeof window === "undefined") throw new Error("Browser storage is unavailable.");
    window.localStorage.setItem(LOCAL_VOICE_PREFERENCE_STORAGE_KEY, serialized);
    hasLoadedFromStorage = true;
    publish({
      effective: { rate: value.rate, volume: value.volume },
      status: "stored",
      message: "Applied in this browser.",
    });
    return { ok: true };
  } catch {
    const reason = "Browser storage could not save these settings. Effective values were kept.";
    publish({ ...snapshot, status: "write_failed", message: reason });
    return { ok: false, reason };
  }
}

function handleStorageChange(event: StorageEvent) {
  if (event.key !== null && event.key !== LOCAL_VOICE_PREFERENCE_STORAGE_KEY) return;
  loadFromStorage();
}

function loadFromStorage() {
  hasLoadedFromStorage = true;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(LOCAL_VOICE_PREFERENCE_STORAGE_KEY);
  } catch {
    publish({
      effective: DEFAULT_LOCAL_VOICE_PREFERENCES,
      status: "unavailable",
      message: "Browser storage could not be read. Safe defaults are active.",
    });
    return;
  }
  if (raw === null) {
    publish({
      effective: DEFAULT_LOCAL_VOICE_PREFERENCES,
      status: "available",
      message: null,
    });
    return;
  }
  if (utf8ByteLength(raw) > LOCAL_VOICE_PREFERENCE_MAX_BYTES) {
    publish({
      effective: DEFAULT_LOCAL_VOICE_PREFERENCES,
      status: "invalid",
      message: "The saved browser preference record is oversized. Defaults are active; Apply or Reset can replace it explicitly.",
    });
    return;
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    publish({
      effective: DEFAULT_LOCAL_VOICE_PREFERENCES,
      status: "invalid",
      message: "The saved browser preference record is malformed. Defaults are active; Apply or Reset can replace it explicitly.",
    });
    return;
  }
  if (!isPreferenceRecord(parsed)) {
    publish({
      effective: DEFAULT_LOCAL_VOICE_PREFERENCES,
      status: "invalid",
      message: "The saved browser preference record is invalid. Defaults are active; Apply or Reset can replace it explicitly.",
    });
    return;
  }
  if (parsed.schema_version !== 1) {
    publish({
      effective: DEFAULT_LOCAL_VOICE_PREFERENCES,
      status: "invalid",
      message: "The saved browser preference version is not supported. Defaults are active; Apply or Reset can replace it explicitly.",
    });
    return;
  }
  if (!isValidPreferences(parsed)) {
    publish({
      effective: DEFAULT_LOCAL_VOICE_PREFERENCES,
      status: "invalid",
      message: "The saved rate or volume is outside its allowed range. Defaults are active; Apply or Reset can replace it explicitly.",
    });
    return;
  }
  publish({
    effective: { rate: parsed.rate, volume: parsed.volume },
    status: "stored",
    message: "Loaded from this browser.",
  });
}

function utf8ByteLength(value: string): number {
  let bytes = 0;
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit <= 0x7f) {
      bytes += 1;
    } else if (codeUnit <= 0x7ff) {
      bytes += 2;
    } else if (
      codeUnit >= 0xd800 &&
      codeUnit <= 0xdbff &&
      index + 1 < value.length &&
      value.charCodeAt(index + 1) >= 0xdc00 &&
      value.charCodeAt(index + 1) <= 0xdfff
    ) {
      bytes += 4;
      index += 1;
    } else {
      bytes += 3;
    }
    if (bytes > LOCAL_VOICE_PREFERENCE_MAX_BYTES) return bytes;
  }
  return bytes;
}

function isPreferenceRecord(value: unknown): value is {
  schema_version: unknown;
  rate: unknown;
  volume: unknown;
} {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  return (
    keys.length === 3 &&
    keys[0] === "rate" &&
    keys[1] === "schema_version" &&
    keys[2] === "volume"
  );
}

function isValidPreferences(value: unknown): value is LocalVoicePreferences {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.rate === "number" &&
    Number.isFinite(candidate.rate) &&
    candidate.rate >= 0.5 &&
    candidate.rate <= 2 &&
    typeof candidate.volume === "number" &&
    Number.isFinite(candidate.volume) &&
    candidate.volume >= 0 &&
    candidate.volume <= 1
  );
}

function publish(next: LocalVoicePreferenceSnapshot) {
  const changed =
    snapshot.effective.rate !== next.effective.rate ||
    snapshot.effective.volume !== next.effective.volume;
  if (
    !changed &&
    snapshot.status === next.status &&
    snapshot.message === next.message
  ) {
    return;
  }
  snapshot = next;
  for (const listener of listeners) listener();
}
