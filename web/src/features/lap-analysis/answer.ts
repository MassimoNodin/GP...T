import { humanReason } from "@/features/review-data/reasons";

// Engineer answers: mapping the v2 ask result (`POST .../sessions/{id}/ask`) and
// the v1 ask result (`POST /api/v1/engineer/ask`) onto one `AnswerView`, plus
// the copy for qualifications, failures and suggested questions. Pure: no I/O.
//
// v2 body:  { data: { status: available|unavailable|unsupported|pending, reason,
//             message, selection: { target, reference, origin, kind? },
//             report: { distance_m[], channels{}, delta_time, braking_zones[],
//             qualifications[] } | null, model?: { model_name, latency_ms } } }
// v1 body:  { status: "ok", data: { status: answered|partial|unavailable|unsupported,
//             message, reason?, selection, report: { facts[{text}], warnings[{text}] },
//             model?: { model_name, latency_ms } } }

export type AnswerStatus = "answered" | "partial" | "unsupported" | "unavailable";

export type AnswerRelatedLap = { role: "Target" | "Reference"; lap: number; time: string };

export type AnswerView = {
  status: AnswerStatus;
  message: string;
  scope: string[];
  evidence: string[];
  relatedLaps: AnswerRelatedLap[];
  /** First four limitations; the rest are in `moreLimitations`. */
  limitations: string[];
  moreLimitations: string[];
  model: { name: string; latencyMs: number | null } | null;
  /** Backend reason code of an unavailable or unsupported answer. */
  reason: string | null;
  /** Follow-up hint shown under an unavailable answer. */
  hint: string | null;
};

/** The laps an answer can refer to, matched by whichever id the backend echoes. */
export type AnswerLap = {
  /** v1 attempt key or v2 revision id (the `selection` ids use one of them). */
  keys: string[];
  lap: number;
  ms: number;
};

export const MAX_LIMITATIONS = 4;

/* ----------------------------------------------------------------- copy */

const QUALIFICATIONS: Record<string, string> = {
  fuel_and_tyre_conditions_not_established: "Fuel load and tyre condition were not established.",
  later_braking_is_not_automatically_better: "Braking later is not automatically better.",
  numeric_distance_regions_not_named_corners: "Regions are distance ranges, not named corners.",
  measurement_not_reference_ranking: "This is a measurement, not a ranking of laps.",
  invalid_or_unknown_game_validity: "One of the laps was invalid or its validity is unknown.",
  pit_encountered: "One of the laps includes the pit lane.",
  track_geometry_not_fully_established: "Track geometry was not fully established.",
  different_lifecycle_epochs: "The laps come from different parts of the session.",
  acquisition_gap: "Telemetry has a gap in one of the laps.",
  fuel_load_uncontrolled: "Fuel, tyres and traffic were not controlled.",
  tyre_condition_uncontrolled: "Fuel, tyres and traffic were not controlled.",
  traffic_uncontrolled: "Fuel, tyres and traffic were not controlled.",
  diagnostic_trace_only: "This is a diagnostic trace summary, not a comparison.",
  no_comparative_measurement: "No comparison with another lap was made.",
};

function sentenceCase(code: string): string {
  const words = code.replace(/[_\s]+/g, " ").trim();
  return words ? `${words[0].toUpperCase()}${words.slice(1).toLowerCase()}.` : "";
}

/** Plain-language limitation for a qualification code; unknown codes are humanised. */
export function qualificationCopy(code: string): string {
  return Object.hasOwn(QUALIFICATIONS, code) ? QUALIFICATIONS[code] : sentenceCase(code.slice(0, 120));
}

const REASON_COPY: Record<string, string> = {
  reference_unavailable: "No eligible reference lap is available.",
  no_eligible_reference: "No eligible reference lap is available.",
  historical_materialization_not_authorized: "Detailed telemetry for one of the laps has not been processed.",
  materialization_busy: "The telemetry reader is busy. Try again in a moment.",
  engineer_ai_busy: "The engineer is busy with another request. Try again in a moment.",
  engineer_request_timeout: "The engineer took too long to answer. Try again.",
  question_invalid: "The question could not be understood.",
  request_target_invalid: "Choose a lap to ask about.",
  question_unsupported: "This question is outside diagnostic lap comparison.",
  focus_measurement_unavailable: "The recording does not provide the requested measurement.",
  lap_time_measurement_unavailable: "A lap time was missing for one of the laps.",
  attempt_not_in_session: "That lap is not part of this session.",
  materializer_unavailable: "The telemetry reader is unavailable.",
  detail_wait_timeout: "The lap did not finish in time.",
};

const PROCESS_HINT = "Tick ‘Process unprocessed telemetry’ to include this lap.";
const PROCESS_REASONS = new Set(["historical_materialization_not_authorized", "not_processed", "trace_not_selected"]);

/** Text for a reason code on an ask result: ask-specific copy first, then the shared table. */
export function askReasonCopy(code: string | null | undefined): string {
  if (!code) return "";
  return Object.hasOwn(REASON_COPY, code) ? REASON_COPY[code] : humanReason(code);
}

/* -------------------------------------------------------------- parsing */

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function str(value: unknown, max = 2000): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.slice(0, max) : null;
}

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatTime(ms: number): string {
  const minutes = Math.floor(ms / 60000);
  return `${minutes}:${((ms - minutes * 60000) / 1000).toFixed(3).padStart(6, "0")}`;
}

function metres(value: number): string {
  return `${Math.round(value).toLocaleString("en-US")} m`;
}

function findLap(laps: readonly AnswerLap[], id: unknown): AnswerLap | null {
  return typeof id === "string" ? (laps.find((lap) => lap.keys.includes(id)) ?? null) : null;
}

const CHANNEL_NAMES: Record<string, string> = {
  speed_mps: "speed trace",
  throttle: "throttle input",
  brake: "brake input",
  steering: "steering input",
  gear: "gear",
  drs_active: "DRS state",
};

function capitalise(text: string): string {
  return text ? `${text[0].toUpperCase()}${text.slice(1)}` : text;
}

function modelOf(value: unknown): AnswerView["model"] {
  const model = record(value);
  const name = str(model?.model_name, 80);
  if (!model || !name) return null;
  return { name, latencyMs: number(model.latency_ms) };
}

function splitLimitations(texts: string[]): Pick<AnswerView, "limitations" | "moreLimitations"> {
  const unique = [...new Set(texts)];
  return { limitations: unique.slice(0, MAX_LIMITATIONS), moreLimitations: unique.slice(MAX_LIMITATIONS) };
}

function related(selection: Record<string, unknown> | null, laps: readonly AnswerLap[]): AnswerRelatedLap[] {
  const items: AnswerRelatedLap[] = [];
  const target = findLap(laps, selection?.target ?? selection?.target_attempt_key);
  const reference = findLap(laps, selection?.reference ?? selection?.reference_attempt_key);
  if (target) items.push({ role: "Target", lap: target.lap, time: formatTime(target.ms) });
  if (reference) items.push({ role: "Reference", lap: reference.lap, time: formatTime(reference.ms) });
  return items;
}

function statusOf(raw: unknown, source: "v1" | "v2"): AnswerStatus | null {
  if (source === "v2") {
    if (raw === "available") return "answered";
    if (raw === "unsupported") return "unsupported";
    if (raw === "unavailable" || raw === "pending") return "unavailable";
    return null;
  }
  return raw === "answered" || raw === "partial" || raw === "unsupported" || raw === "unavailable" ? raw : null;
}

/**
 * Maps an ask result body onto the answer anatomy. Null when the body is not an
 * answer (an error envelope or an unexpected shape): the caller then maps the
 * HTTP status with `askFailure`.
 */
export function answerView(body: unknown, laps: readonly AnswerLap[], source: "v1" | "v2" = "v2"): AnswerView | null {
  const envelope = record(body);
  const data = record(envelope?.data);
  if (!envelope || !data) return null;
  if (source === "v1" && envelope.status !== "ok") return null;
  const status = statusOf(data.status, source);
  const message = str(data.message);
  if (!status || !message) return null;

  const selection = record(data.selection);
  const report = record(data.report);
  const reason = str(data.reason, 128);
  const view: AnswerView = {
    status,
    message,
    scope: [],
    evidence: [],
    relatedLaps: related(selection, laps),
    limitations: [],
    moreLimitations: [],
    model: modelOf(data.model),
    reason,
    hint: null,
  };

  if (source === "v2") {
    if (report) {
      const distance = Array.isArray(report.distance_m) ? report.distance_m.map(number).filter((v): v is number => v !== null) : [];
      const target = view.relatedLaps.find((item) => item.role === "Target");
      if (selection?.kind === "race_lap") {
        view.scope = [`Lap ${target?.lap ?? "?"} (race lap diagnostic)`];
      } else if (distance.length >= 2) {
        view.scope = [`Full lap (${metres(distance[0])} – ${metres(distance[distance.length - 1])})`];
      }
      const names: string[] = [];
      const channels = record(report.channels);
      if (channels) {
        for (const key of Object.keys(channels)) {
          const name = CHANNEL_NAMES[key];
          if (name && !names.includes(name)) names.push(name);
        }
      }
      if (record(report.delta_time) || Array.isArray(report.delta_time)) names.push("time delta");
      if (Array.isArray(report.braking_zones) && report.braking_zones.length > 0) names.push("braking zones");
      if (names.length) view.evidence = [capitalise(names.join(", "))];
      const qualifications = Array.isArray(report.qualifications)
        ? report.qualifications.map((item) => str(item, 120)).filter((item): item is string => item !== null)
        : [];
      Object.assign(view, splitLimitations(qualifications.map(qualificationCopy)));
    }
  } else {
    const target = view.relatedLaps.find((item) => item.role === "Target");
    const region = str(selection?.region_identifier, 64);
    if (target) view.scope = [region ? `Lap ${target.lap} (${region})` : `Lap ${target.lap} (${target.time})`];
    const facts = Array.isArray(report?.facts) ? report.facts : [];
    view.evidence = facts
      .map((fact) => str(record(fact)?.text, 240))
      .filter((text): text is string => text !== null)
      .slice(0, 6);
    const warnings = Array.isArray(report?.warnings) ? report.warnings : [];
    Object.assign(
      view,
      splitLimitations(warnings.map((warning) => str(record(warning)?.text, 240)).filter((text): text is string => text !== null)),
    );
  }

  if (status === "unavailable") {
    const copy = askReasonCopy(reason);
    if (copy) view.message = copy;
    if (reason && PROCESS_REASONS.has(reason)) view.hint = PROCESS_HINT;
  }
  if (status === "unsupported") view.hint = "Try one of the suggested questions.";
  return view;
}

/* ------------------------------------------------------------- failures */

export type AskFailureKind = "busy" | "timeout" | "forbidden" | "offline" | "invalid" | "unavailable";

export type AskFailure = {
  kind: AskFailureKind;
  message: string;
  retryable: boolean;
  reason: string | null;
};

export const FAILURE_COPY = {
  busy: "The engineer is busy with another request. Try again in a moment.",
  timeout: "The engineer took too long to answer. Try again.",
  forbidden: "The web app is not allowed to ask questions. Check the engine's control token.",
  offline: "The local engine is unavailable.",
  cancelled: "Question cancelled.",
} as const;

/**
 * Maps a failed ask (HTTP status and reason code) onto a turn state.
 * `status` is null for network errors and client-side timeouts (`timedOut`).
 */
export function askFailure(status: number | null, reason: string | null, timedOut = false): AskFailure {
  if (timedOut || status === 504) return { kind: "timeout", message: FAILURE_COPY.timeout, retryable: true, reason };
  if (status === 409) return { kind: "busy", message: FAILURE_COPY.busy, retryable: true, reason };
  if (status === 403 || status === 401) return { kind: "forbidden", message: FAILURE_COPY.forbidden, retryable: false, reason };
  if (status === null || status === 502 || status === 503) {
    return { kind: "offline", message: FAILURE_COPY.offline, retryable: true, reason };
  }
  const copy = askReasonCopy(reason);
  if (status === 413 || status === 422) {
    return { kind: "invalid", message: copy || "The question could not be sent.", retryable: false, reason };
  }
  return { kind: "unavailable", message: copy || "The engineer could not answer.", retryable: true, reason };
}

/* ------------------------------------------------------- suggested chips */

export type SuggestionInput = { targetLap: number | null; referenceLap: number | null };

/** At most three one-click questions for the current selection. */
export function suggestedQuestions({ targetLap, referenceLap }: SuggestionInput): string[] {
  if (targetLap === null) return [];
  return referenceLap !== null
    ? [`Where did I lose time to Lap ${referenceLap}?`, "How did my braking compare?", `Summarise Lap ${targetLap}`]
    : [`Summarise Lap ${targetLap}`, `Was Lap ${targetLap} valid?`, "What limits this lap's data?"];
}

/** Pending-turn status text. */
export function pendingCopy(targetLap: number, referenceLap: number | null): string {
  return referenceLap !== null ? `Measuring Lap ${targetLap} against Lap ${referenceLap}…` : `Reading Lap ${targetLap}…`;
}

export const V2_QUESTION_LIMIT = 2000;
export const V1_QUESTION_LIMIT = 1024;
/** The character counter appears this far into the limit. */
export const COUNTER_FROM = 1800;
