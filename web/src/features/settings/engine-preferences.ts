// Assistance preferences backed by the engine (`GET/PUT /api/v1/engineer/preferences`).
// Pure state machine and view mapping for the Assistance tab: the form edits
// label-keyed values, the engine stores a strict document with a revision, and
// every outcome of a save (applied, conflict, rejected, forbidden, unavailable)
// has a distinct view state. Behaviour follows design plan 3.5.1.

import {
  DEFAULT_PREFERENCES,
  docToForm,
  type AssistanceForm,
  type PreferencesDoc,
  type PreferencesRead,
  type PreferencesSaveResult,
  type StoredPreferences,
} from "@/features/review-data/preferences";
import { preferencesEqual } from "./preferences";

export type PrefsAccess = "ready" | "unreadable" | "unreachable";

export type SaveFailure =
  | { kind: "conflict"; currentRevision: number }
  | { kind: "invalid"; field: string | null; reason: string }
  | { kind: "forbidden"; reason: string }
  | { kind: "unavailable" | "unreachable"; reason: string };

export type PrefsState = {
  access: PrefsAccess;
  /** Reason code from a 503 read, shown only in the unreadable state. */
  unreadableReason: string | null;
  /** Saved revision; null when the saved file could not be read. */
  revision: number | null;
  /** Last known stored document (the defaults when nothing is stored). */
  savedDoc: PreferencesDoc;
  saved: AssistanceForm;
  form: AssistanceForm;
  phase: "idle" | "saving" | "saved";
  /** True from a successful save until the next edit; drives the "saved" note. */
  justSaved: boolean;
  failure: SaveFailure | null;
};

export type PrefsAction =
  | { type: "edit"; form: AssistanceForm }
  | { type: "discard" }
  | { type: "saving" }
  | { type: "result"; result: PreferencesSaveResult }
  /** "Load latest": adopt the server document and drop local edits. */
  | { type: "adopt"; stored: StoredPreferences }
  | { type: "settle" };

export function initPrefsState(read: PreferencesRead): PrefsState {
  const defaults = docToForm(DEFAULT_PREFERENCES);
  const base = {
    savedDoc: DEFAULT_PREFERENCES,
    saved: defaults,
    form: defaults,
    phase: "idle" as const,
    justSaved: false,
    failure: null,
  };
  if (read.state === "ok") {
    const form = docToForm(read.stored.doc);
    return {
      ...base,
      access: "ready",
      unreadableReason: null,
      revision: read.stored.revision,
      savedDoc: read.stored.doc,
      saved: form,
      form,
    };
  }
  if (read.state === "unreadable") {
    return { ...base, access: "unreadable", unreadableReason: read.reason, revision: null };
  }
  return { ...base, access: "unreachable", unreadableReason: null, revision: null };
}

export function isDirty(state: PrefsState): boolean {
  return !preferencesEqual(state.form, state.saved);
}

function failureFrom(result: Exclude<PreferencesSaveResult, { ok: true }>): SaveFailure {
  if (result.kind === "conflict") return { kind: "conflict", currentRevision: result.currentRevision };
  if (result.kind === "invalid") return { kind: "invalid", field: result.field, reason: result.reason };
  return { kind: result.kind, reason: result.reason };
}

export function prefsReducer(state: PrefsState, action: PrefsAction): PrefsState {
  switch (action.type) {
    case "edit": {
      // A conflict stays until it is resolved and a forbidden save never recovers
      // by editing; other failures clear as soon as the user changes something.
      const keep = state.failure?.kind === "conflict" || state.failure?.kind === "forbidden";
      return {
        ...state,
        form: action.form,
        justSaved: false,
        phase: state.phase === "saved" ? "idle" : state.phase,
        failure: keep ? state.failure : null,
      };
    }
    case "discard":
      return { ...state, form: state.saved, justSaved: false, failure: null };
    case "saving":
      return { ...state, phase: "saving", failure: null, justSaved: false };
    case "result": {
      if (action.result.ok) {
        const stored = action.result.stored;
        return {
          ...state,
          access: "ready",
          unreadableReason: null,
          revision: stored.revision,
          savedDoc: stored.doc,
          saved: docToForm(stored.doc),
          phase: "saved",
          justSaved: true,
          failure: null,
        };
      }
      return { ...state, phase: "idle", failure: failureFrom(action.result) };
    }
    case "adopt": {
      const form = docToForm(action.stored.doc);
      return {
        ...state,
        access: "ready",
        unreadableReason: null,
        revision: action.stored.revision,
        savedDoc: action.stored.doc,
        saved: form,
        form,
        phase: "idle",
        justSaved: false,
        failure: null,
      };
    }
    case "settle":
      return state.phase === "saved" ? { ...state, phase: "idle" } : state;
  }
}

/* ------------------------------------------------------------------- view */

export const NOTE_DEFAULT = "Most settings apply immediately. Some connection settings require a restart.";
export const NOTE_DIRTY = "You have unsaved changes.";
export const NOTE_SAVING = "Saving to the engine…";
export const NOTE_SAVED = "Saved to the engine. The next engineer call uses these settings.";

export type AlertAction = "load-latest" | "overwrite";

export type AssistanceView = {
  dirty: boolean;
  /** Form controls cannot be used (engine unreachable). */
  controlsDisabled: boolean;
  applyLabel: string;
  applyDisabled: boolean;
  busy: boolean;
  showDiscard: boolean;
  note: string;
  noteTone: "red" | null;
  alert: {
    tone: "info" | "warning" | "error";
    title?: string;
    text: string;
    actions: AlertAction[];
  } | null;
};

function words(code: string): string {
  return code.replaceAll("_", " ").trim();
}

/** Distinct view for every state of the Apply row (plan 3.5.1). */
export function assistanceView(state: PrefsState): AssistanceView {
  const dirty = isDirty(state);
  const failure = state.failure;
  const unreachable = state.access === "unreachable";
  let alert: AssistanceView["alert"] = null;
  if (unreachable) {
    alert = {
      tone: "info",
      text: "Settings cannot be loaded because the local engine is not reachable.",
      actions: [],
    };
  } else if (failure?.kind === "conflict") {
    alert = {
      tone: "warning",
      title: "Settings changed elsewhere",
      text: `Another window saved revision ${failure.currentRevision} while you were editing.`,
      actions: ["load-latest", "overwrite"],
    };
  } else if (state.access === "unreadable") {
    alert = {
      tone: "error",
      text: `Saved settings could not be read (${words(state.unreadableReason ?? "preferences_file_unreadable")}). Defaults are shown; applying replaces the saved file.`,
      actions: [],
    };
  }

  let note = NOTE_DEFAULT;
  let noteTone: "red" | null = null;
  if (state.phase === "saving") note = NOTE_SAVING;
  else if (failure?.kind === "invalid") {
    note = `The engine rejected these settings${failure.field ? ` (${failure.field})` : ""}. Reload the page and try again.`;
    noteTone = "red";
  } else if (failure?.kind === "forbidden") {
    note = "The web app is not allowed to change engine settings. Check the engine's control token.";
    noteTone = "red";
  } else if (failure?.kind === "unavailable") {
    note = `The engine could not save these settings (${words(failure.reason)}). Try again.`;
    noteTone = "red";
  } else if (failure?.kind === "unreachable") {
    note = "The local engine is not reachable. Your changes are kept; try again.";
    noteTone = "red";
  } else if (failure?.kind === "conflict") note = NOTE_DIRTY;
  else if (state.justSaved && !dirty) note = NOTE_SAVED;
  else if (dirty) note = NOTE_DIRTY;

  const saving = state.phase === "saving";
  const label = saving ? "Applying…" : state.phase === "saved" && !dirty ? "Changes Applied" : "Apply Changes";
  // An unreadable file can be replaced with the defaults, so Apply stays available there.
  const canApply = dirty || state.access === "unreadable";
  const applyDisabled =
    unreachable || saving || failure?.kind === "forbidden" || state.phase === "saved" || !canApply;

  return {
    dirty,
    controlsDisabled: unreachable,
    applyLabel: label,
    applyDisabled,
    busy: saving,
    showDiscard: dirty && !saving && !unreachable,
    note,
    noteTone,
    alert,
  };
}

/* -------------------------------------------------------------- migration */

export type LegacyMigration =
  | { state: "none" }
  /** The browser copy equals the engine copy; the stale key can be removed. */
  | { state: "identical" }
  | { state: "offer"; form: AssistanceForm };

/**
 * One-time migration of the retired browser-local preferences. `legacy` is the
 * parsed browser copy (null when there is none or it is unreadable) and `saved`
 * the engine copy the form starts from.
 */
export function planMigration(legacy: AssistanceForm | null, saved: AssistanceForm, access: PrefsAccess): LegacyMigration {
  if (!legacy || access === "unreachable") return { state: "none" };
  if (preferencesEqual(legacy, saved)) return { state: "identical" };
  return { state: "offer", form: legacy };
}

/** `If-Match` revision for a save: the saved revision, or an overwrite target. */
export function saveRevision(state: PrefsState, overwriteRevision: number | null): number | null {
  return overwriteRevision ?? state.revision;
}
