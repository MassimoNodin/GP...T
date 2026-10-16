import type { RadioEvent } from "@/features/review-data/fixtures";
import type { EngineerEvent, EngineerEventKind } from "@/features/review-data/engineer-events";
import { humanReason } from "@/features/review-data/reasons";

// Pure Radio History logic for the Drive screen: maps engineer activity events
// (`GET /api/v2/engineer/events`) to rows and Event Details, pairs questions
// with their responses, counts, unread tracking, follow mode and the read-aloud
// decisions. No React, no browser APIs, so it is tested directly.

export type RadioKind = EngineerEventKind;
export type OutcomeTone = "green" | "amber" | "red" | "grey";
export type PlaybackOutcome = "played" | "not_played";
export type Interruption = "allow" | "queue" | "never";

/** A question with no response or failure younger than this still shows "Waiting". */
export const QUESTION_WAIT_MS = 30_000;

export const KIND_LABEL: Record<RadioKind, string> = {
  proactive: "Proactive",
  question: "You",
  response: "Engineer",
  issue: "Issue",
};

export const KIND_TITLE: Record<RadioKind, string> = {
  proactive: "Proactive Message",
  question: "Driver Question",
  response: "Engineer Response",
  issue: "Issue",
};

export type RadioRow = {
  /** Stable React key: `event-12` for live events, the fixture id for reference rows. */
  id: string;
  /** Backend event id; null for reference (fixture) rows. */
  seq: number | null;
  kind: RadioKind;
  title: string;
  /** `HH:MM` in the display time zone. */
  time: string;
  /** `HH:MM:SS` in the display time zone. */
  timestamp: string;
  text: string;
  outcome: string;
  outcomeTone: OutcomeTone;
  priority: "low" | "normal" | "high" | null;
  trigger: string;
  evidence: string[];
  output: string;
  timings: string;
  limitations: string | null;
};

export type RadioContext = {
  /** Epoch milliseconds the rows are evaluated at (drives "Waiting"). */
  now: number;
  /** IANA zone for the clock columns; the runtime zone when omitted. */
  timeZone?: string;
  /** Read-aloud results by event id, recorded in this browser tab. */
  playback?: Readonly<Record<number, PlaybackOutcome>>;
};

/* ------------------------------------------------------------------ format */

export function formatClock(iso: string, options: { seconds?: boolean; timeZone?: string } = {}): string {
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return options.seconds ? "--:--:--" : "--:--";
  const format = (timeZone: string | undefined) =>
    new Intl.DateTimeFormat("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      ...(options.seconds ? { second: "2-digit" as const } : {}),
      hourCycle: "h23",
      ...(timeZone ? { timeZone } : {}),
    }).format(date);
  try {
    return format(options.timeZone);
  } catch {
    return format("UTC");
  }
}

export function formatLapTime(milliseconds: number): string {
  const minutes = Math.floor(milliseconds / 60_000);
  const seconds = (milliseconds - minutes * 60_000) / 1000;
  return `${minutes}:${seconds.toFixed(3).padStart(6, "0")}`;
}

export function formatDelta(milliseconds: number): string {
  return `${milliseconds < 0 ? "-" : "+"}${(Math.abs(milliseconds) / 1000).toFixed(3)}`;
}

/** `lap_time` -> `Lap time`. */
export function humanize(code: string): string {
  const words = code.replace(/[_\s]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "";
}

function lowerFirst(value: string): string {
  return value ? value[0].toLowerCase() + value.slice(1) : value;
}

function stripPeriod(value: string): string {
  return value.replace(/\.+$/, "");
}

function detailString(event: EngineerEvent, key: string): string | null {
  const value = event.detail?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function detailInteger(event: EngineerEvent, key: string): number | null {
  const value = event.detail?.[key];
  return typeof value === "number" && Number.isSafeInteger(value) ? value : null;
}

function createdAtMs(event: EngineerEvent): number {
  return Date.parse(event.createdAtUtc);
}

/* ----------------------------------------------------------------- pairing */

type Pairing = {
  byId: Map<number, EngineerEvent>;
  responseFor: Map<number, EngineerEvent>;
  failureFor: Map<number, EngineerEvent>;
};

function pair(events: readonly EngineerEvent[]): Pairing {
  const byId = new Map<number, EngineerEvent>();
  const responseFor = new Map<number, EngineerEvent>();
  const failureFor = new Map<number, EngineerEvent>();
  for (const event of events) {
    byId.set(event.id, event);
    if (event.relatedId === null) continue;
    if (event.kind === "response" && !responseFor.has(event.relatedId)) responseFor.set(event.relatedId, event);
    if (event.kind === "issue" && !failureFor.has(event.relatedId)) failureFor.set(event.relatedId, event);
  }
  return { byId, responseFor, failureFor };
}

function isWaiting(question: EngineerEvent, now: number): boolean {
  const created = createdAtMs(question);
  return Number.isFinite(created) && now - created < QUESTION_WAIT_MS;
}

/** The newest question that is still within its wait window and has neither a response nor a failure. */
export function pendingQuestion(events: readonly EngineerEvent[], now: number): EngineerEvent | null {
  const { responseFor, failureFor } = pair(events);
  let pending: EngineerEvent | null = null;
  for (const event of events) {
    if (event.kind !== "question" || responseFor.has(event.id) || failureFor.has(event.id)) continue;
    if (isWaiting(event, now) && (pending === null || event.id > pending.id)) pending = event;
  }
  return pending;
}

/* ------------------------------------------------------------ outcome copy */

type Outcome = { label: string; tone: OutcomeTone };

function outcomeFor(event: EngineerEvent, pairing: Pairing, context: RadioContext): Outcome {
  switch (event.kind) {
    case "proactive": {
      const playback = context.playback?.[event.id];
      if (playback === "played") return { label: "Played", tone: "green" };
      if (playback === "not_played") return { label: "Not played", tone: "grey" };
      return { label: "Logged", tone: "grey" };
    }
    case "question":
      if (pairing.responseFor.has(event.id)) return { label: "Responded", tone: "green" };
      if (pairing.failureFor.has(event.id)) return { label: "No answer", tone: "red" };
      if (isWaiting(event, context.now)) return { label: "Waiting", tone: "amber" };
      return { label: "No answer", tone: "grey" };
    case "response": {
      const status = detailString(event, "status");
      if (status === "answered") return { label: "Answered", tone: "green" };
      if (status === "partial") return { label: "Partial", tone: "amber" };
      if (status === "unavailable" || status === "unsupported") return { label: "Unavailable", tone: "grey" };
      return { label: "Answered", tone: "green" };
    }
    default:
      return { label: "Logged", tone: "grey" };
  }
}

/* ------------------------------------------------------------------ copy */

const RULE_TRIGGER: Record<string, string> = {
  lap_invalidated: "Lap invalidated by the game",
  pit_lap: "Lap included a pit stop",
  first_timed_lap: "First timed lap of the session",
  personal_best: "New personal best",
  lap_completed: "Lap completed",
  connected: "Telemetry connected",
  recovery: "Telemetry recovered",
  failed: "Telemetry listener stopped",
  gap: "Telemetry went quiet",
};

const LAP_RULES = new Set(["lap_invalidated", "pit_lap", "first_timed_lap", "personal_best", "lap_completed"]);

export const LAP_RULE_LIMITATION =
  "Lap results come from published lap attempts; pit messages describe the completed lap, not the moment of pit entry.";

function triggerFor(event: EngineerEvent, pairing: Pairing): string {
  const rule = detailString(event, "rule");
  if (event.source === "live_rules") {
    if (rule && Object.hasOwn(RULE_TRIGGER, rule)) return RULE_TRIGGER[rule];
    return event.topic === "telemetry" ? "Telemetry state changed" : "Engineer rule";
  }
  if (event.kind === "question") return "Asked from Lap Analysis";
  if (event.kind === "response") {
    const question = event.relatedId === null ? undefined : pairing.byId.get(event.relatedId);
    return question ? `Answer to: ${question.text}` : "Answer to a driver question";
  }
  if (event.kind === "issue") {
    const reason = detailString(event, "reason");
    return reason ? stripPeriod(humanReason(reason)) : "Issue logged by the engine";
  }
  return "Reported by the engine";
}

function evidenceFor(event: EngineerEvent): string[] {
  const lines: string[] = [];
  if (event.kind === "proactive") {
    if (event.lap !== null) lines.push(`Lap ${event.lap}`);
    const lapMs = detailInteger(event, "lap_time_ms");
    if (lapMs !== null && lapMs > 0) lines.push(`Lap time ${formatLapTime(lapMs)}`);
    const rule = detailString(event, "rule");
    const delta = detailInteger(event, "delta_ms");
    const previous = detailInteger(event, "previous_best_ms");
    if (rule === "personal_best") {
      if (previous !== null && previous > 0) lines.push(`Previous best ${formatLapTime(previous)}`);
      if (delta !== null) lines.push(`Delta to previous best ${formatDelta(delta)}`);
    } else if (delta !== null) {
      lines.push(`Delta to best ${formatDelta(delta)}`);
    }
    const state = detailString(event, "state");
    const before = detailString(event, "previous_state");
    if (event.topic === "telemetry" && state) {
      lines.push(before ? `Receiver state: ${humanize(before)} to ${humanize(state)}` : `Receiver state: ${humanize(state)}`);
    }
    if (event.topic) lines.push(`Topic: ${humanize(event.topic)}`);
    lines.push(`Priority: ${humanize(event.priority)}`);
  } else if (event.kind === "question") {
    if (event.sessionId) lines.push(`Session #${event.sessionId}`);
    if (event.lap !== null) lines.push(`Lap ${event.lap}`);
  } else if (event.kind === "response") {
    const route = detailString(event, "route");
    const focus = detailString(event, "focus");
    const status = detailString(event, "status");
    if (route) lines.push(`Route: ${humanize(route)}`);
    if (focus) lines.push(`Focus: ${humanize(focus)}`);
    if (status) lines.push(`Status: ${humanize(status)}`);
  } else {
    const http = detailInteger(event, "http_status");
    if (http !== null) lines.push(`HTTP ${http}`);
  }
  return lines;
}

function outputFor(event: EngineerEvent, playback: PlaybackOutcome | undefined): string {
  if (event.kind === "question") return "Sent to the engineer";
  if (event.kind === "issue") return "Logged for review";
  return playback === "played" ? "Spoken on this computer" : "Shown in Radio History";
}

function timingsFor(event: EngineerEvent, pairing: Pairing, timeZone: string | undefined): string {
  const logged = `Logged at ${formatClock(event.createdAtUtc, { seconds: true, timeZone })}`;
  if (event.kind !== "response" || event.relatedId === null) return logged;
  const question = pairing.byId.get(event.relatedId);
  if (!question) return logged;
  const elapsed = createdAtMs(event) - createdAtMs(question);
  return Number.isFinite(elapsed) && elapsed >= 0
    ? `Answered ${(elapsed / 1000).toFixed(1)} s after the question`
    : logged;
}

function limitationsFor(event: EngineerEvent): string | null {
  if (event.kind === "proactive") {
    const rule = detailString(event, "rule");
    return event.source === "live_rules" && rule && LAP_RULES.has(rule) ? LAP_RULE_LIMITATION : null;
  }
  if (event.kind === "response" && detailString(event, "status") === "partial") {
    const reason = detailString(event, "reason");
    return reason ? `The answer was partial: ${lowerFirst(stripPeriod(humanReason(reason)))}.` : "The answer was partial.";
  }
  return null;
}

/* --------------------------------------------------------------------- rows */

function toRadioRow(event: EngineerEvent, pairing: Pairing, context: RadioContext): RadioRow {
  const outcome = outcomeFor(event, pairing, context);
  return {
    id: `event-${event.id}`,
    seq: event.id,
    kind: event.kind,
    title: KIND_TITLE[event.kind],
    time: formatClock(event.createdAtUtc, { timeZone: context.timeZone }),
    timestamp: formatClock(event.createdAtUtc, { seconds: true, timeZone: context.timeZone }),
    text: event.text,
    outcome: outcome.label,
    outcomeTone: outcome.tone,
    priority: event.priority,
    trigger: triggerFor(event, pairing),
    evidence: evidenceFor(event),
    output: outputFor(event, context.playback?.[event.id]),
    timings: timingsFor(event, pairing, context.timeZone),
    limitations: limitationsFor(event),
  };
}

/** Rows for Radio History, newest first. `events` may be in any order. */
export function buildRadioRows(events: readonly EngineerEvent[], context: RadioContext): RadioRow[] {
  const pairing = pair(events);
  return [...events].sort((a, b) => b.id - a.id).map((event) => toRadioRow(event, pairing, context));
}

const OUTCOME_TONES: readonly OutcomeTone[] = ["green", "amber", "red", "grey"];

/** Reference (fixture) rows in the same shape. Used only on the explicit reference path. */
export function fixtureRadioRows(fixture: readonly RadioEvent[]): RadioRow[] {
  return fixture.map((item) => ({
    id: item.id,
    seq: null,
    kind: item.kind,
    title: KIND_TITLE[item.kind],
    time: item.time,
    timestamp: item.timestamp,
    text: item.text,
    outcome: item.outcome,
    outcomeTone: (OUTCOME_TONES as readonly string[]).includes(item.outcomeTone)
      ? (item.outcomeTone as OutcomeTone)
      : "grey",
    priority: null,
    trigger: item.trigger,
    evidence: item.evidence,
    output: item.output,
    timings: item.timings,
    limitations: item.limitations ?? null,
  }));
}

/* ------------------------------------------------------- filters and counts */

export type RadioFilter = "all" | RadioKind;

export const FILTER_ITEMS: ReadonlyArray<{ key: RadioFilter; label: string }> = [
  { key: "all", label: "All" },
  { key: "question", label: "Questions" },
  { key: "response", label: "Responses" },
  { key: "proactive", label: "Proactive" },
  { key: "issue", label: "Issues" },
];

export const FILTER_EMPTY: Record<RadioKind, string> = {
  question: "No questions yet.",
  response: "No responses yet.",
  proactive: "No proactive messages yet.",
  issue: "No issues yet.",
};

export function radioCounts(rows: ReadonlyArray<{ kind: RadioKind }>): Record<RadioFilter, number> {
  const counts: Record<RadioFilter, number> = { all: rows.length, question: 0, response: 0, proactive: 0, issue: 0 };
  for (const row of rows) counts[row.kind] += 1;
  return counts;
}

/* ------------------------------------------------------------------ unread */

/** Backend ids that are newer than the last-seen id and have not been selected since. */
export function unreadIds(
  events: ReadonlyArray<{ id: number }>,
  seenThrough: number,
  readIds: ReadonlySet<number>,
): Set<number> {
  const unread = new Set<number>();
  for (const event of events) if (event.id > seenThrough && !readIds.has(event.id)) unread.add(event.id);
  return unread;
}

export function newestId(events: ReadonlyArray<{ id: number }>): number {
  let newest = 0;
  for (const event of events) if (event.id > newest) newest = event.id;
  return newest;
}

/**
 * Follow mode: selection moves to the newest event only when the newest event
 * was selected before the new events arrived (or nothing was selected).
 */
export function followSelection(
  selectedId: number | null,
  previousNewest: number | null,
  nextNewest: number | null,
): number | null {
  if (nextNewest === null) return selectedId;
  if (selectedId === null) return nextNewest;
  return selectedId === previousNewest ? nextNewest : selectedId;
}

/* -------------------------------------------------------------- read aloud */

export type SpeechAction = "speak" | "cancel_and_speak" | "queue" | "drop";

/**
 * What to do with a new call given the backend interruption preference:
 * `allow` lets a high priority call cancel current speech, `queue` always
 * waits, `never` drops the call while something is being spoken.
 */
export function speechDecision(input: {
  interruption: Interruption;
  priority: "low" | "normal" | "high";
  speaking: boolean;
}): SpeechAction {
  if (!input.speaking) return "speak";
  if (input.interruption === "never") return "drop";
  if (input.interruption === "allow" && input.priority === "high") return "cancel_and_speak";
  return "queue";
}

/** Only proactive calls and engineer responses are read aloud. */
export function isSpeakable(event: Pick<EngineerEvent, "kind" | "text">): boolean {
  return (event.kind === "proactive" || event.kind === "response") && event.text.trim() !== "";
}

/** Splits text into utterance-sized chunks at sentence boundaries. */
export function speechChunks(text: string, limit = 180): string[] {
  const sentences = text.replace(/\s+/g, " ").trim().match(/[^.!?]+[.!?]*\s*/g) ?? [];
  const chunks: string[] = [];
  let current = "";
  for (const sentence of sentences) {
    if (current && current.length + sentence.length > limit) {
      chunks.push(current.trim());
      current = "";
    }
    current += sentence;
    while (current.length > limit) {
      const cut = current.lastIndexOf(" ", limit);
      const at = cut > 0 ? cut : limit;
      chunks.push(current.slice(0, at).trim());
      current = current.slice(at);
    }
  }
  if (current.trim()) chunks.push(current.trim());
  return chunks;
}

export const SPEAKING_PREVIEW_LENGTH = 40;

export function speakingPreview(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > SPEAKING_PREVIEW_LENGTH ? `${flat.slice(0, SPEAKING_PREVIEW_LENGTH).trimEnd()}…` : flat;
}
