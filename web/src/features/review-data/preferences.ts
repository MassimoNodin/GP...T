import { asRecord, envelopeData, integer, text } from "./parse-utils";

// Engineer preferences: strict document, form mapping and `If-Match` helpers
// for `GET/PUT /api/v1/engineer/preferences`. Verified against
// `f1_engineer/analysis/engineer_preferences.py` and
// `f1_engineer/api/engineer_preferences_routes.py`:
//   GET 200  { api_version, status:"ok", data: { schema_version, revision, topics,
//              verbosity, interruption, depth, interpretations, focus_areas } }
//            with `ETag: "<revision>"`. Revision 0 means defaults, never saved.
//   GET 503  reason preferences_file_unreadable | _too_large | _invalid
//   PUT      body = exactly the six document keys (no schema_version/revision);
//            `If-Match: "7"` (also `7`, `W/"7"`, or `*`); 409 data.revision +
//            ETag with the current revision; 422 data.field + reason;
//            403 preferences_control_not_authorized; 503 preferences_store_unavailable.

export const TOPIC_KEYS = [
  "tyre_management",
  "track_conditions",
  "race_strategy",
  "driving_technique",
  "gap_information",
  "rule_reminders",
  "setup_suggestions",
  "general_information",
] as const;
export const FOCUS_KEYS = [
  "driving_consistency",
  "comparative_analysis",
  "corner_analysis",
  "setup_impact",
  "tyre_performance",
  "data_quality_notes",
] as const;
export const VERBOSITY_VALUES = ["concise", "balanced", "detailed"] as const;
export const INTERRUPTION_VALUES = ["allow", "queue", "never"] as const;
export const DEPTH_VALUES = ["quick", "standard", "in_depth"] as const;

export type TopicKey = (typeof TOPIC_KEYS)[number];
export type FocusKey = (typeof FOCUS_KEYS)[number];
export type Verbosity = (typeof VERBOSITY_VALUES)[number];
export type Interruption = (typeof INTERRUPTION_VALUES)[number];
export type Depth = (typeof DEPTH_VALUES)[number];

export type PreferencesDoc = {
  topics: Record<TopicKey, boolean>;
  verbosity: Verbosity;
  interruption: Interruption;
  depth: Depth;
  interpretations: boolean;
  focus_areas: Record<FocusKey, boolean>;
};

export type StoredPreferences = {
  doc: PreferencesDoc;
  /** 0 means "defaults, never saved". */
  revision: number;
  schemaVersion: number;
};

export type PreferencesSaveResult =
  | { ok: true; stored: StoredPreferences }
  | { ok: false; kind: "conflict"; currentRevision: number }
  | { ok: false; kind: "invalid"; field: string | null; reason: string }
  | { ok: false; kind: "forbidden" | "unavailable" | "unreachable"; reason: string };

/** Result of reading preferences on the server or in the client. */
export type PreferencesRead =
  | { state: "ok"; stored: StoredPreferences }
  /** The engine answered but the saved file cannot be trusted (GET 503). */
  | { state: "unreadable"; reason: string }
  | { state: "unreachable" };

export const DEFAULT_PREFERENCES: PreferencesDoc = {
  topics: {
    tyre_management: true,
    track_conditions: true,
    race_strategy: true,
    driving_technique: true,
    gap_information: true,
    rule_reminders: true,
    setup_suggestions: false,
    general_information: false,
  },
  verbosity: "balanced",
  interruption: "allow",
  depth: "standard",
  interpretations: true,
  focus_areas: {
    driving_consistency: true,
    comparative_analysis: true,
    corner_analysis: true,
    setup_impact: true,
    tyre_performance: true,
    data_quality_notes: false,
  },
};

/* ------------------------------------------------------------------ labels */

export const TOPIC_LABELS: Record<TopicKey, string> = {
  tyre_management: "Tyre management",
  track_conditions: "Track conditions",
  race_strategy: "Race strategy",
  driving_technique: "Driving technique",
  gap_information: "Gap information",
  rule_reminders: "Rule reminders",
  setup_suggestions: "Setup suggestions",
  general_information: "General information",
};
export const FOCUS_LABELS: Record<FocusKey, string> = {
  driving_consistency: "Driving consistency",
  comparative_analysis: "Comparative analysis",
  corner_analysis: "Corner analysis",
  setup_impact: "Setup impact",
  tyre_performance: "Tyre performance",
  data_quality_notes: "Data quality notes",
};
export const VERBOSITY_LABELS: Record<Verbosity, string> = {
  concise: "Concise",
  balanced: "Balanced",
  detailed: "Detailed",
};
export const INTERRUPTION_LABELS: Record<Interruption, string> = {
  allow: "Allow interruptions",
  queue: "Queue until finished",
  never: "Never interrupt",
};
export const DEPTH_LABELS: Record<Depth, string> = {
  quick: "Quick",
  standard: "Standard",
  in_depth: "In-depth",
};

/** The label-keyed shape the Settings form works with (a subset of `settings/preferences.ts`). */
export type AssistanceForm = {
  topics: Record<string, boolean>;
  focusAreas: Record<string, boolean>;
  verbosity: string;
  interruption: string;
  depth: string;
  interpretations: boolean;
};

export function docToForm(doc: PreferencesDoc): AssistanceForm {
  return {
    topics: Object.fromEntries(TOPIC_KEYS.map((key) => [TOPIC_LABELS[key], doc.topics[key]])),
    focusAreas: Object.fromEntries(FOCUS_KEYS.map((key) => [FOCUS_LABELS[key], doc.focus_areas[key]])),
    verbosity: VERBOSITY_LABELS[doc.verbosity],
    interruption: INTERRUPTION_LABELS[doc.interruption],
    depth: DEPTH_LABELS[doc.depth],
    interpretations: doc.interpretations,
  };
}

function keyForLabel<K extends string>(labels: Record<K, string>, label: string, fallback: K): K {
  for (const [key, value] of Object.entries(labels) as Array<[K, string]>) {
    if (value === label) return key;
  }
  return fallback;
}

/**
 * Maps the form back to the document. Labels the form does not carry fall back
 * to `base` (default: the defaults), so the result is always a complete document.
 */
export function formToDoc(form: AssistanceForm, base: PreferencesDoc = DEFAULT_PREFERENCES): PreferencesDoc {
  const topics = { ...base.topics };
  for (const key of TOPIC_KEYS) {
    const value = form.topics[TOPIC_LABELS[key]];
    if (typeof value === "boolean") topics[key] = value;
  }
  const focus = { ...base.focus_areas };
  for (const key of FOCUS_KEYS) {
    const value = form.focusAreas[FOCUS_LABELS[key]];
    if (typeof value === "boolean") focus[key] = value;
  }
  return {
    topics,
    verbosity: keyForLabel(VERBOSITY_LABELS, form.verbosity, base.verbosity),
    interruption: keyForLabel(INTERRUPTION_LABELS, form.interruption, base.interruption),
    depth: keyForLabel(DEPTH_LABELS, form.depth, base.depth),
    interpretations: typeof form.interpretations === "boolean" ? form.interpretations : base.interpretations,
    focus_areas: focus,
  };
}

/* ----------------------------------------------------------------- parsing */

function flags<K extends string>(keys: readonly K[], value: unknown): Record<K, boolean> | null {
  const record = asRecord(value);
  if (!record) return null;
  const result = {} as Record<K, boolean>;
  for (const key of keys) {
    if (typeof record[key] !== "boolean") return null;
    result[key] = record[key] as boolean;
  }
  return result;
}

function choice<T extends string>(options: readonly T[], value: unknown): T | null {
  return typeof value === "string" && (options as readonly string[]).includes(value) ? (value as T) : null;
}

/** Strict parse of the six document keys. Null when anything is missing or invalid. */
export function parsePreferencesDoc(value: unknown): PreferencesDoc | null {
  const record = asRecord(value);
  if (!record) return null;
  const topics = flags(TOPIC_KEYS, record.topics);
  const focus = flags(FOCUS_KEYS, record.focus_areas);
  const verbosity = choice(VERBOSITY_VALUES, record.verbosity);
  const interruption = choice(INTERRUPTION_VALUES, record.interruption);
  const depth = choice(DEPTH_VALUES, record.depth);
  if (!topics || !focus || !verbosity || !interruption || !depth) return null;
  if (typeof record.interpretations !== "boolean") return null;
  return { topics, verbosity, interruption, depth, interpretations: record.interpretations, focus_areas: focus };
}

/** Parses the stored document of a 200 GET/PUT response body. Null when malformed. */
export function parseStoredPreferences(body: unknown): StoredPreferences | null {
  const data = asRecord(envelopeData(body));
  const doc = parsePreferencesDoc(data);
  const revision = integer(data?.revision);
  if (!data || !doc || revision === null || revision < 0) return null;
  return { doc, revision, schemaVersion: integer(data.schema_version) ?? 1 };
}

/** Turns a GET response (status + body) into a typed read. */
export function parsePreferencesRead(status: number, body: unknown): PreferencesRead {
  if (status === 200) {
    const stored = parseStoredPreferences(body);
    return stored ? { state: "ok", stored } : { state: "unreadable", reason: "preferences_file_invalid" };
  }
  if (status === 503) {
    return { state: "unreadable", reason: text(asRecord(body)?.reason, 128) ?? "preferences_file_unreadable" };
  }
  return { state: "unreachable" };
}

/* ----------------------------------------------------------------- saving */

/** The request body: exactly the six document keys, in the backend's order. */
export function preferencesRequestBody(doc: PreferencesDoc): string {
  return JSON.stringify({
    topics: Object.fromEntries(TOPIC_KEYS.map((key) => [key, doc.topics[key]])),
    verbosity: doc.verbosity,
    interruption: doc.interruption,
    depth: doc.depth,
    interpretations: doc.interpretations,
    focus_areas: Object.fromEntries(FOCUS_KEYS.map((key) => [key, doc.focus_areas[key]])),
  });
}

/** `7` -> `"7"` (quoted entity tag). */
export function ifMatchHeader(revision: number): string {
  return `"${Math.max(0, Math.trunc(revision))}"`;
}

/** Reads the revision from an `ETag`/`If-Match` value such as `"7"`, `W/"7"` or `7`. */
export function revisionFromEtag(value: string | null | undefined): number | null {
  const match = /^\s*(?:W\/)?"?([0-9]{1,9})"?\s*$/.exec(value ?? "");
  return match ? Number(match[1]) : null;
}

/**
 * Maps a PUT response to a save result. `etag` is the response `ETag` header,
 * used as a fallback for the current revision on a conflict.
 */
export function parsePreferencesSaveResult(
  status: number,
  body: unknown,
  etag: string | null = null,
): PreferencesSaveResult {
  const record = asRecord(body);
  const reason = text(record?.reason, 128) ?? "unavailable";
  if (status === 200) {
    const stored = parseStoredPreferences(body);
    return stored ? { ok: true, stored } : { ok: false, kind: "unavailable", reason: "upstream_response_invalid" };
  }
  if (status === 409) {
    const current = integer(asRecord(record?.data)?.revision) ?? revisionFromEtag(etag);
    return current === null
      ? { ok: false, kind: "unavailable", reason }
      : { ok: false, kind: "conflict", currentRevision: current };
  }
  if (status === 422) {
    return { ok: false, kind: "invalid", field: text(asRecord(record?.data)?.field, 128), reason };
  }
  if (status === 403 || status === 401) return { ok: false, kind: "forbidden", reason };
  if (status === 502 || status === 503 || status === 504) {
    // 503 from the web proxy means the engine was unreachable; from the engine, the store failed.
    return {
      ok: false,
      kind: reason === "local_engine_unavailable" || reason === "upstream_deadline_exceeded" ? "unreachable" : "unavailable",
      reason,
    };
  }
  return { ok: false, kind: "unavailable", reason };
}

/** True when two documents differ (key-order independent). */
export function preferencesDiffer(a: PreferencesDoc, b: PreferencesDoc): boolean {
  return preferencesRequestBody(a) !== preferencesRequestBody(b);
}
