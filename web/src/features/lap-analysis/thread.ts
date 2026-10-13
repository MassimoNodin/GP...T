import type { AnswerView, AskFailure } from "./answer";

// The Engineer Questions thread: turn types, the session-storage key, trimming
// and defensive parsing of what comes back from storage. Pure.

export const THREAD_LIMIT = 20;
export const THREAD_KEY_PREFIX = "race-engineer.thread";

export type TurnOutcome =
  | { kind: "answer"; view: AnswerView }
  | { kind: "failure"; failure: AskFailure }
  | { kind: "cancelled" };

export type StoredTurn = {
  id: string;
  question: string;
  /** Local clock time label, `18:45`. */
  askedAt: string;
  targetLap: number;
  referenceLap: number | null;
  outcome: TurnOutcome;
};

/** `race-engineer.thread.{session}.{target}.{reference}` */
export function threadKey(session: string, targetId: string, referenceId: string | null): string {
  return `${THREAD_KEY_PREFIX}.${session}.${targetId}.${referenceId ?? "none"}`;
}

export function trimThread<T>(turns: readonly T[]): T[] {
  return turns.length > THREAD_LIMIT ? turns.slice(turns.length - THREAD_LIMIT) : [...turns];
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function strings(value: unknown): string[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? (value as string[]) : null;
}

const STATUSES = new Set(["answered", "partial", "unsupported", "unavailable"]);
const KINDS = new Set(["busy", "timeout", "forbidden", "offline", "invalid", "unavailable"]);

function parseView(value: unknown): AnswerView | null {
  const view = record(value);
  if (!view || typeof view.status !== "string" || !STATUSES.has(view.status) || typeof view.message !== "string") return null;
  const scope = strings(view.scope);
  const evidence = strings(view.evidence);
  const limitations = strings(view.limitations);
  const more = strings(view.moreLimitations);
  if (!scope || !evidence || !limitations || !more || !Array.isArray(view.relatedLaps)) return null;
  const relatedLaps: AnswerView["relatedLaps"] = [];
  for (const item of view.relatedLaps) {
    const lap = record(item);
    if (!lap || (lap.role !== "Target" && lap.role !== "Reference") || typeof lap.lap !== "number" || typeof lap.time !== "string") {
      return null;
    }
    relatedLaps.push({ role: lap.role, lap: lap.lap, time: lap.time });
  }
  const model = record(view.model);
  return {
    status: view.status as AnswerView["status"],
    message: view.message,
    scope,
    evidence,
    relatedLaps,
    limitations,
    moreLimitations: more,
    model:
      model && typeof model.name === "string"
        ? { name: model.name, latencyMs: typeof model.latencyMs === "number" ? model.latencyMs : null }
        : null,
    reason: typeof view.reason === "string" ? view.reason : null,
    hint: typeof view.hint === "string" ? view.hint : null,
  };
}

function parseOutcome(value: unknown): TurnOutcome | null {
  const outcome = record(value);
  if (!outcome) return null;
  if (outcome.kind === "cancelled") return { kind: "cancelled" };
  if (outcome.kind === "answer") {
    const view = parseView(outcome.view);
    return view ? { kind: "answer", view } : null;
  }
  if (outcome.kind === "failure") {
    const failure = record(outcome.failure);
    if (!failure || typeof failure.kind !== "string" || !KINDS.has(failure.kind) || typeof failure.message !== "string") return null;
    return {
      kind: "failure",
      failure: {
        kind: failure.kind as AskFailure["kind"],
        message: failure.message,
        retryable: failure.retryable === true,
        reason: typeof failure.reason === "string" ? failure.reason : null,
      },
    };
  }
  return null;
}

/** Turns stored under a thread key; anything malformed is dropped. */
export function parseThread(raw: string | null | undefined): StoredTurn[] {
  if (!raw) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  const turns: StoredTurn[] = [];
  for (const item of parsed) {
    const turn = record(item);
    const outcome = parseOutcome(turn?.outcome);
    if (
      !turn ||
      !outcome ||
      typeof turn.id !== "string" ||
      typeof turn.question !== "string" ||
      typeof turn.askedAt !== "string" ||
      typeof turn.targetLap !== "number"
    ) {
      continue;
    }
    turns.push({
      id: turn.id,
      question: turn.question,
      askedAt: turn.askedAt,
      targetLap: turn.targetLap,
      referenceLap: typeof turn.referenceLap === "number" ? turn.referenceLap : null,
      outcome,
    });
  }
  return trimThread(turns);
}

export function serializeThread(turns: readonly StoredTurn[]): string {
  return JSON.stringify(trimThread(turns));
}
