import {
  DEFAULT_EVENTS_PAGE,
  MAX_EVENTS_PAGE,
  engineerEventsQuery,
  mergeEngineerEvents,
  parseEngineerEventsRead,
  type EngineerEvent,
  type EngineerEventsRead,
} from "@/features/review-data/engineer-events";

// Framework-free core of the Radio History feed: a reducer that folds polled
// pages into a bounded list, and a poller with the same visibility, deadline
// and stale-response rules as the other live status pollers.

export type FeedStatus = "ok" | "unavailable" | "unreachable";

export type FeedState = {
  /** Oldest first, at most `MAX_FEED_EVENTS`. */
  events: EngineerEvent[];
  /** Cursor for the next poll; null until a first page was read. */
  nextAfter: number | null;
  status: FeedStatus;
  /** Backend reason while `unavailable`. */
  reason: string | null;
  /** Consecutive failed polls. One dropped poll does not change `status`. */
  failures: number;
  /** Counts every page that brought new events; `arrived` holds those events. */
  arrivalSeq: number;
  arrived: EngineerEvent[];
};

export const MAX_FEED_EVENTS = MAX_EVENTS_PAGE;
export const FEED_PAGE_SIZE = DEFAULT_EVENTS_PAGE;
const TOLERATED_FAILURES = 1;

export function initialFeedState(input: {
  status: FeedStatus;
  events?: EngineerEvent[];
  nextAfter?: number | null;
  reason?: string | null;
}): FeedState {
  return {
    events: input.events ?? [],
    nextAfter: input.nextAfter ?? null,
    status: input.status,
    reason: input.reason ?? null,
    failures: 0,
    arrivalSeq: 0,
    arrived: [],
  };
}

/**
 * Folds one poll result into the feed. A page read without a cursor replaces
 * the list (first read or recovery) and reports no arrivals, so nothing old is
 * highlighted or spoken. A page read with a cursor appends and reports the
 * events that were not known before.
 */
export function reduceFeed(state: FeedState, read: EngineerEventsRead, cursor: number | null): FeedState {
  if (read.state === "ok") {
    const { page } = read;
    if (cursor === null) {
      return {
        ...state,
        events: mergeEngineerEvents([], page.events, MAX_FEED_EVENTS),
        nextAfter: page.nextAfter,
        status: "ok",
        reason: null,
        failures: 0,
        arrived: [],
      };
    }
    const known = new Set(state.events.map((event) => event.id));
    const arrived = page.events.filter((event) => !known.has(event.id));
    return {
      ...state,
      events: arrived.length ? mergeEngineerEvents(state.events, arrived, MAX_FEED_EVENTS) : state.events,
      nextAfter: Math.max(page.nextAfter, state.nextAfter ?? 0),
      status: "ok",
      reason: null,
      failures: 0,
      arrivalSeq: arrived.length ? state.arrivalSeq + 1 : state.arrivalSeq,
      arrived: arrived.length ? arrived : state.arrived,
    };
  }
  const failures = state.failures + 1;
  if (failures <= TOLERATED_FAILURES && state.status === "ok") return { ...state, failures };
  return {
    ...state,
    failures,
    status: read.state,
    reason: read.state === "unavailable" ? read.reason : null,
  };
}

/** Fetches one page through the web proxy and types the result. */
export async function fetchEventsPage(
  cursor: number | null,
  signal: AbortSignal,
  fetcher: typeof fetch = (input, init) => fetch(input, init),
): Promise<EngineerEventsRead> {
  const query = engineerEventsQuery({ after: cursor, limit: FEED_PAGE_SIZE });
  const response = await fetcher(`/api/engineer/events?${query}`, { cache: "no-store", signal });
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  return parseEngineerEventsRead(response.status, body);
}

export type FeedPoller = { setHidden(hidden: boolean): void; stop(): void };

/**
 * Polls while visible: one request in flight, a hard deadline per request,
 * stale responses dropped after a hide/show cycle. A full page is followed by
 * an immediate catch-up poll; failures slow the cadence down.
 */
export function createFeedPoller({
  fetchRead,
  getCursor,
  onRead,
  intervalMs = 2000,
  slowIntervalMs = 5000,
  deadlineMs = 8000,
  pageSize = FEED_PAGE_SIZE,
}: {
  fetchRead: (cursor: number | null, signal: AbortSignal) => Promise<EngineerEventsRead>;
  getCursor: () => number | null;
  onRead: (read: EngineerEventsRead, cursor: number | null) => void;
  intervalMs?: number;
  slowIntervalMs?: number;
  deadlineMs?: number;
  pageSize?: number;
}): FeedPoller {
  let disposed = false;
  let hidden = true;
  let generation = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | null = null;

  async function poll() {
    if (disposed || hidden) return;
    const current = ++generation;
    const cursor = getCursor();
    const requestController = new AbortController();
    controller = requestController;
    let deadline: ReturnType<typeof setTimeout> | undefined;
    const expired = new Promise<never>((_resolve, reject) => {
      deadline = setTimeout(() => {
        requestController.abort();
        reject(new Error("feed_deadline"));
      }, deadlineMs);
    });
    let delay = slowIntervalMs;
    try {
      const read = await Promise.race([fetchRead(cursor, requestController.signal), expired]);
      if (!disposed && !hidden && generation === current && !requestController.signal.aborted) {
        onRead(read, cursor);
        if (read.state === "ok") {
          delay = read.page.events.length >= pageSize ? 0 : intervalMs;
        }
      }
    } catch {
      if (!disposed && !hidden && generation === current) onRead({ state: "unreachable" }, cursor);
    } finally {
      clearTimeout(deadline);
      if (controller === requestController) controller = null;
      if (!disposed && !hidden && generation === current) {
        timer = setTimeout(() => void poll(), delay);
      }
    }
  }

  return {
    setHidden(next: boolean) {
      if (disposed || next === hidden) return;
      hidden = next;
      generation += 1;
      clearTimeout(timer);
      controller?.abort();
      if (!hidden) void poll();
    },
    stop() {
      disposed = true;
      generation += 1;
      clearTimeout(timer);
      controller?.abort();
    },
  };
}
