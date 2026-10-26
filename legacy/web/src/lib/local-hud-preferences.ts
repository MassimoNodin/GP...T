export type HudAlignment =
  "top-left" | "top-right" | "bottom-left" | "bottom-right";
export type HudTheme = "dark" | "light";

export interface HudPreferences {
  alignment: HudAlignment;
  background_opacity: number;
  text_scale: number;
  theme: HudTheme;
}

export type HudPreferenceStatus =
  | "checking"
  | "available"
  | "stored"
  | "invalid"
  | "unavailable"
  | "write_failed";

export interface HudPreferenceSnapshot {
  effective: HudPreferences;
  status: HudPreferenceStatus;
  message: string | null;
}

export type ApplyHudPreferenceResult =
  { ok: true } | { ok: false; reason: string };

export const DEFAULT_HUD_PREFERENCES: HudPreferences = {
  alignment: "top-left",
  background_opacity: 0.9,
  text_scale: 1,
  theme: "dark",
};
export const LOCAL_HUD_PREFERENCE_STORAGE_KEY = "gp...t:local-hud-preferences";
export const LOCAL_HUD_PREFERENCE_MAX_BYTES = 2 * 1024;

const SERVER_SNAPSHOT: HudPreferenceSnapshot = {
  effective: DEFAULT_HUD_PREFERENCES,
  status: "checking",
  message: null,
};

let snapshot = SERVER_SNAPSHOT;
let hasLoadedFromStorage = false;
let storageListener: ((event: StorageEvent) => void) | null = null;
const listeners = new Set<() => void>();

export function getLocalHudPreferenceSnapshot(): HudPreferenceSnapshot {
  return snapshot;
}

export function getServerLocalHudPreferenceSnapshot(): HudPreferenceSnapshot {
  return SERVER_SNAPSHOT;
}

export function subscribeToLocalHudPreferences(
  listener: () => void,
): () => void {
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
    if (
      listeners.size === 0 &&
      typeof window !== "undefined" &&
      storageListener
    ) {
      window.removeEventListener("storage", storageListener);
      storageListener = null;
      hasLoadedFromStorage = false;
    }
  };
}

export function applyLocalHudPreferences(
  value: HudPreferences,
): ApplyHudPreferenceResult {
  if (!isValidPreferences(value)) {
    const reason =
      "Choose a supported corner, opacity from 0.6 to 1, text scale from 0.8 to 1.5, and light or dark theme.";
    publish({ ...snapshot, status: "write_failed", message: reason });
    return { ok: false, reason };
  }

  const record = {
    schema_version: 1,
    alignment: value.alignment,
    background_opacity: value.background_opacity,
    text_scale: value.text_scale,
    theme: value.theme,
  };
  const serialized = JSON.stringify(record);
  if (utf8ByteLength(serialized) > LOCAL_HUD_PREFERENCE_MAX_BYTES) {
    const reason = "The browser preference record exceeds its storage limit.";
    publish({ ...snapshot, status: "write_failed", message: reason });
    return { ok: false, reason };
  }

  try {
    if (typeof window === "undefined")
      throw new Error("Browser storage is unavailable.");
    window.localStorage.setItem(LOCAL_HUD_PREFERENCE_STORAGE_KEY, serialized);
    hasLoadedFromStorage = true;
    publish({
      effective: { ...value },
      status: "stored",
      message: "Applied in this browser.",
    });
    return { ok: true };
  } catch {
    const reason =
      "Browser storage could not save these settings. Effective values were kept.";
    publish({ ...snapshot, status: "write_failed", message: reason });
    return { ok: false, reason };
  }
}

function handleStorageChange(event: StorageEvent) {
  if (event.key !== null && event.key !== LOCAL_HUD_PREFERENCE_STORAGE_KEY)
    return;
  loadFromStorage();
}

function loadFromStorage() {
  hasLoadedFromStorage = true;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(LOCAL_HUD_PREFERENCE_STORAGE_KEY);
  } catch {
    publish({
      effective: DEFAULT_HUD_PREFERENCES,
      status: "unavailable",
      message: "Browser storage could not be read. Safe defaults are active.",
    });
    return;
  }
  if (raw === null) {
    publish({
      effective: DEFAULT_HUD_PREFERENCES,
      status: "available",
      message: null,
    });
    return;
  }
  if (utf8ByteLength(raw) > LOCAL_HUD_PREFERENCE_MAX_BYTES) {
    publish({
      effective: DEFAULT_HUD_PREFERENCES,
      status: "invalid",
      message:
        "The saved browser preference record is oversized. Defaults are active; Apply or Reset can replace it explicitly.",
    });
    return;
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    publish({
      effective: DEFAULT_HUD_PREFERENCES,
      status: "invalid",
      message:
        "The saved browser preference record is malformed. Defaults are active; Apply or Reset can replace it explicitly.",
    });
    return;
  }
  if (!isPreferenceRecord(parsed)) {
    publish({
      effective: DEFAULT_HUD_PREFERENCES,
      status: "invalid",
      message:
        "The saved browser preference record is invalid. Defaults are active; Apply or Reset can replace it explicitly.",
    });
    return;
  }
  if (parsed.schema_version !== 1) {
    publish({
      effective: DEFAULT_HUD_PREFERENCES,
      status: "invalid",
      message:
        "The saved browser preference version is not supported. Defaults are active; Apply or Reset can replace it explicitly.",
    });
    return;
  }
  const preferences = {
    alignment: parsed.alignment,
    background_opacity: parsed.background_opacity,
    text_scale: parsed.text_scale,
    theme: parsed.theme,
  };
  if (!isValidPreferences(preferences)) {
    publish({
      effective: DEFAULT_HUD_PREFERENCES,
      status: "invalid",
      message:
        "The saved HUD values are outside their allowed ranges. Defaults are active; Apply or Reset can replace them explicitly.",
    });
    return;
  }
  publish({
    effective: preferences,
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
    if (bytes > LOCAL_HUD_PREFERENCE_MAX_BYTES) return bytes;
  }
  return bytes;
}

function isPreferenceRecord(value: unknown): value is {
  schema_version: unknown;
  alignment: unknown;
  background_opacity: unknown;
  text_scale: unknown;
  theme: unknown;
} {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  return (
    keys.length === 5 &&
    keys[0] === "alignment" &&
    keys[1] === "background_opacity" &&
    keys[2] === "schema_version" &&
    keys[3] === "text_scale" &&
    keys[4] === "theme"
  );
}

function isValidPreferences(value: unknown): value is HudPreferences {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const candidate = value as Record<string, unknown>;
  const keys = Object.keys(candidate).sort();
  return (
    keys.length === 4 &&
    keys[0] === "alignment" &&
    keys[1] === "background_opacity" &&
    keys[2] === "text_scale" &&
    keys[3] === "theme" &&
    (candidate.alignment === "top-left" ||
      candidate.alignment === "top-right" ||
      candidate.alignment === "bottom-left" ||
      candidate.alignment === "bottom-right") &&
    typeof candidate.background_opacity === "number" &&
    Number.isFinite(candidate.background_opacity) &&
    candidate.background_opacity >= 0.6 &&
    candidate.background_opacity <= 1 &&
    typeof candidate.text_scale === "number" &&
    Number.isFinite(candidate.text_scale) &&
    candidate.text_scale >= 0.8 &&
    candidate.text_scale <= 1.5 &&
    (candidate.theme === "dark" || candidate.theme === "light")
  );
}

function publish(next: HudPreferenceSnapshot) {
  const changed =
    snapshot.effective.alignment !== next.effective.alignment ||
    snapshot.effective.background_opacity !==
      next.effective.background_opacity ||
    snapshot.effective.text_scale !== next.effective.text_scale ||
    snapshot.effective.theme !== next.effective.theme;
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
