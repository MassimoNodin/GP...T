import { asRecord, envelopeData, envelopeReason, integer, text } from "./parse-utils";

// Parser and helpers for `GET /api/v2/engineer/events?after=&limit=&session_id=`.
// Verified against `f1_engineer/engineer_activity/store.py`:
//   data.events[]  id, created_at_utc, kind, topic, text, session_id, lap,
//                  source, related_id, priority, detail (object | null)
//   data.next_after  largest returned id, or `after` (0 when absent)
// Without `after` the newest `limit` events come back oldest-first; with it,
// events with a larger id come oldest-first. Entries without a session id are
// included for every `session_id` filter. 503 reason: engineer_activity_unavailable.

export const MAX_EVENTS_PAGE = 200;
export const DEFAULT_EVENTS_PAGE = 50;

export const EVENT_KINDS = ["proactive", "question", "response", "issue"] as const;
export const EVENT_SOURCES = ["v1_ask", "v2_ask", "live_rules", "system"] as const;
export const EVENT_PRIORITIES = ["low", "normal", "high"] as const;

export type EngineerEventKind = (typeof EVENT_KINDS)[number];
export type EngineerEventSource = (typeof EVENT_SOURCES)[number];
export type EngineerEventPriority = (typeof EVENT_PRIORITIES)[number];

export type EngineerEvent = {
  id: number;
  createdAtUtc: string;
  kind: EngineerEventKind;
  topic: string | null;
  text: string;
  sessionId: string | null;
  lap: number | null;
  source: EngineerEventSource;
  relatedId: number | null;
  priority: EngineerEventPriority;
  detail: Record<string, unknown> | null;
};

export type EngineerEventPage = { events: EngineerEvent[]; nextAfter: number };

/** Typed outcome of reading the activity feed. */
export type EngineerEventsRead =
  | { state: "ok"; page: EngineerEventPage }
  /** The engine answered 503: the activity log itself cannot be read. */
  | { state: "unavailable"; reason: string }
  | { state: "unreachable" };

function oneOf<T extends string>(options: readonly T[], value: unknown): T | null {
  return typeof value === "string" && (options as readonly string[]).includes(value) ? (value as T) : null;
}

function parseEvent(value: unknown): EngineerEvent | null {
  const record = asRecord(value);
  const id = integer(record?.id);
  const createdAtUtc = text(record?.created_at_utc, 40);
  const kind = oneOf(EVENT_KINDS, record?.kind);
  const source = oneOf(EVENT_SOURCES, record?.source);
  const body = typeof record?.text === "string" ? record.text.slice(0, 2000) : null;
  if (!record || id === null || id < 0 || !createdAtUtc || !kind || !source || body === null) return null;
  if (!Number.isFinite(Date.parse(createdAtUtc))) return null;
  return {
    id,
    createdAtUtc,
    kind,
    topic: text(record.topic, 32),
    text: body,
    sessionId: text(record.session_id, 128),
    lap: integer(record.lap),
    source,
    relatedId: integer(record.related_id),
    priority: oneOf(EVENT_PRIORITIES, record.priority) ?? "normal",
    detail: asRecord(record.detail),
  };
}

/** Parses an events page body. Duplicate ids keep the first entry. Null when malformed. */
export function parseEngineerEventPage(body: unknown): EngineerEventPage | null {
  const data = asRecord(envelopeData(body));
  if (!data || !Array.isArray(data.events)) return null;
  const seen = new Set<number>();
  const events: EngineerEvent[] = [];
  for (const item of data.events.slice(0, MAX_EVENTS_PAGE)) {
    const event = parseEvent(item);
    if (!event || seen.has(event.id)) continue;
    seen.add(event.id);
    events.push(event);
  }
  events.sort((a, b) => a.id - b.id);
  const reported = integer(data.next_after);
  const newest = events.length ? events[events.length - 1].id : 0;
  return { events, nextAfter: reported !== null && reported >= 0 ? Math.max(reported, newest) : newest };
}

/** Turns a response (status + body) into a typed read. */
export function parseEngineerEventsRead(status: number, body: unknown): EngineerEventsRead {
  if (status === 200) {
    const page = parseEngineerEventPage(body);
    return page ? { state: "ok", page } : { state: "unavailable", reason: "upstream_response_invalid" };
  }
  if (status === 503 && envelopeReason(body) === "engineer_activity_unavailable") {
    return { state: "unavailable", reason: "engineer_activity_unavailable" };
  }
  if (status >= 400 && status < 500) {
    return { state: "unavailable", reason: envelopeReason(body) ?? "request_invalid" };
  }
  return { state: "unreachable" };
}

/**
 * Builds the query string for an events read. `after` and `limit` are clamped
 * to the backend's bounds; an invalid `sessionId` is dropped.
 */
export function engineerEventsQuery(options: {
  after?: number | null;
  limit?: number;
  sessionId?: string | null;
}): string {
  const query = new URLSearchParams();
  if (typeof options.after === "number" && Number.isSafeInteger(options.after) && options.after >= 0) {
    query.set("after", String(options.after));
  }
  const limit = Math.trunc(options.limit ?? DEFAULT_EVENTS_PAGE);
  query.set("limit", String(Math.min(MAX_EVENTS_PAGE, Math.max(1, Number.isFinite(limit) ? limit : DEFAULT_EVENTS_PAGE))));
  if (options.sessionId && /^[A-Za-z0-9:_-]{1,128}$/.test(options.sessionId)) {
    query.set("session_id", options.sessionId);
  }
  return query.toString();
}

/**
 * Appends a polled page to what is already loaded: dedupes by id, keeps the
 * list ordered oldest-first and bounded to the newest `max` events.
 */
export function mergeEngineerEvents(
  existing: readonly EngineerEvent[],
  incoming: readonly EngineerEvent[],
  max = MAX_EVENTS_PAGE,
): EngineerEvent[] {
  const byId = new Map<number, EngineerEvent>();
  for (const event of existing) byId.set(event.id, event);
  for (const event of incoming) if (!byId.has(event.id)) byId.set(event.id, event);
  const merged = [...byId.values()].sort((a, b) => a.id - b.id);
  return merged.length > max ? merged.slice(merged.length - max) : merged;
}
