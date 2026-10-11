// Browser-local Settings preferences. Pure helpers so restore and change
// detection can be tested without a browser.

export const PREFERENCES_KEY = "race-engineer.settings.v1";

export type Preferences = {
  topics: Record<string, boolean>;
  focusAreas: Record<string, boolean>;
  verbosity: string;
  interruption: string;
  depth: string;
  interpretations: boolean;
  activation: string;
  voice: string;
  speechRate: string;
};

export const ACTIVATION_OPTIONS = ["Push to talk", "Voice activation"];
export const VOICE_OPTIONS = ["Engineer (default)", "Calm", "Energetic"];
export const SPEECH_RATE_OPTIONS = ["Slow", "Normal", "Fast"];

export type PreferenceSource = {
  topics: Array<[string, boolean]>;
  focusAreas: Array<[string, boolean]>;
  verbosity: string[];
  interruption: string[];
  depth: string[];
};

export function defaultPreferences(source: PreferenceSource): Preferences {
  return {
    topics: Object.fromEntries(source.topics),
    focusAreas: Object.fromEntries(source.focusAreas),
    verbosity: "Balanced",
    interruption: "Allow interruptions",
    depth: "Standard",
    interpretations: true,
    activation: ACTIVATION_OPTIONS[0],
    voice: VOICE_OPTIONS[0],
    speechRate: "Normal",
  };
}

function flags(base: Record<string, boolean>, stored: unknown): Record<string, boolean> {
  const result = { ...base };
  if (stored && typeof stored === "object" && !Array.isArray(stored)) {
    for (const key of Object.keys(base)) {
      const value = (stored as Record<string, unknown>)[key];
      if (typeof value === "boolean") result[key] = value;
    }
  }
  return result;
}

function choice(fallback: string, stored: unknown, options: string[]): string {
  return typeof stored === "string" && options.includes(stored) ? stored : fallback;
}

/**
 * Merge stored JSON over the defaults, ignoring unknown keys, retired keys and
 * values that are no longer valid options. Unreadable input yields the defaults.
 */
export function restorePreferences(source: PreferenceSource, raw: string | null): Preferences {
  const base = defaultPreferences(source);
  if (!raw) return base;
  let stored: Record<string, unknown>;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return base;
    stored = parsed as Record<string, unknown>;
  } catch {
    return base;
  }
  return {
    topics: flags(base.topics, stored.topics),
    focusAreas: flags(base.focusAreas, stored.focusAreas),
    verbosity: choice(base.verbosity, stored.verbosity, source.verbosity),
    interruption: choice(base.interruption, stored.interruption, source.interruption),
    depth: choice(base.depth, stored.depth, source.depth),
    interpretations: typeof stored.interpretations === "boolean" ? stored.interpretations : base.interpretations,
    activation: choice(base.activation, stored.activation, ACTIVATION_OPTIONS),
    voice: choice(base.voice, stored.voice, VOICE_OPTIONS),
    speechRate: choice(base.speechRate, stored.speechRate, SPEECH_RATE_OPTIONS),
  };
}

/** Key-order independent comparison, so a restore never reads as an unsaved change. */
export function preferencesEqual(a: Preferences, b: Preferences): boolean {
  return JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
        .map(([key, entry]) => [key, canonical(entry)]),
    );
  }
  return value;
}
