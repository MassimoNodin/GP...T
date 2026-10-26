// Reader for the retired browser-local Assistance preferences
// (`race-engineer.settings.v1`). Engineer behaviour settings now live in the
// engine; this module only supports the one-time migration banner and the
// key-order independent form comparison. Pure so it can be tested without a browser.

import type { AssistanceForm } from "@/features/review-data/preferences";

export const PREFERENCES_KEY = "race-engineer.settings.v1";

/** The label-keyed form the Assistance tab edits (same shape the engine mapping uses). */
export type Preferences = AssistanceForm;

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
 * Merge stored JSON over the defaults, ignoring unknown keys, retired keys
 * (activation, voice, speech rate, retention) and values that are no longer
 * valid options. Unreadable input yields the defaults.
 */
export function restorePreferences(source: PreferenceSource, raw: string | null): Preferences {
  return parseLegacyPreferences(source, raw) ?? defaultPreferences(source);
}

/** Like `restorePreferences`, but null when the stored text is not a JSON object. */
export function parseLegacyPreferences(source: PreferenceSource, raw: string | null): Preferences | null {
  if (!raw) return null;
  let stored: Record<string, unknown>;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    stored = parsed as Record<string, unknown>;
  } catch {
    return null;
  }
  const base = defaultPreferences(source);
  return {
    topics: flags(base.topics, stored.topics),
    focusAreas: flags(base.focusAreas, stored.focusAreas),
    verbosity: choice(base.verbosity, stored.verbosity, source.verbosity),
    interruption: choice(base.interruption, stored.interruption, source.interruption),
    depth: choice(base.depth, stored.depth, source.depth),
    interpretations: typeof stored.interpretations === "boolean" ? stored.interpretations : base.interpretations,
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
