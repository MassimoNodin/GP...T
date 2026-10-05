import type {
  PinnedLiveRead,
  PinnedLiveSnapshot,
  LiveSource,
} from "@/lib/live";

export type PinnedLiveMonitorStatus =
  PinnedLiveRead["status"] | "unselected" | "invalid" | "suspended";

export interface PinnedLiveMonitorState {
  status: PinnedLiveMonitorStatus;
  snapshot: PinnedLiveSnapshot | null;
}

type FetchCurrent = (
  input: string,
  init: { cache: "no-store"; signal: AbortSignal },
) => Promise<{ ok: boolean; json: () => Promise<unknown> }>;

type Schedule = (callback: () => void, delayMs: number) => unknown;
type CancelSchedule = (handle: unknown) => void;

export interface PinnedLivePollerOptions {
  initialStatus: PinnedLiveMonitorStatus;
  initialSnapshot: PinnedLiveSnapshot | null;
  source: LiveSource | null;
  operationId: string | null;
  readCurrent: (
    source: LiveSource,
    operationId: string,
    value: unknown,
  ) => PinnedLiveRead;
  isActive: (snapshot: PinnedLiveSnapshot) => boolean;
  fetchCurrent?: FetchCurrent;
  schedule?: Schedule;
  cancelSchedule?: CancelSchedule;
  requestDeadlineMs?: number;
  activeIntervalMs?: number;
  retryIntervalMs?: number;
}

export interface PinnedLivePoller {
  getState: () => PinnedLiveMonitorState;
  subscribe: (listener: () => void) => () => void;
  start: (hidden?: boolean) => void;
  setHidden: (hidden: boolean) => void;
  stop: () => void;
}

export function createPinnedLivePoller({
  initialStatus,
  initialSnapshot,
  source,
  operationId,
  readCurrent,
  isActive,
  fetchCurrent = (input, init) => fetch(input, init),
  schedule = (callback, delayMs) => setTimeout(callback, delayMs),
  cancelSchedule = (handle) =>
    clearTimeout(handle as ReturnType<typeof setTimeout>),
  requestDeadlineMs = 5000,
  activeIntervalMs = 500,
  retryIntervalMs = 1000,
}: PinnedLivePollerOptions): PinnedLivePoller {
  let state: PinnedLiveMonitorState = {
    status: initialStatus,
    snapshot: initialSnapshot,
  };
  const listeners = new Set<() => void>();
  let started = false;
  let monitoring = false;
  let disposed = false;
  let suspended = false;
  let stopped = false;
  let requestGeneration = 0;
  let timer: unknown = null;
  let controller: AbortController | null = null;

  const publish = (
    status: PinnedLiveMonitorStatus,
    snapshot: PinnedLiveSnapshot | null,
  ) => {
    if (disposed) return;
    state = { status, snapshot };
    for (const listener of listeners) listener();
  };

  const schedulePoll = (delayMs: number) => {
    timer = schedule(() => {
      timer = null;
      void poll();
    }, delayMs);
  };

  const poll = async () => {
    if (
      !started ||
      disposed ||
      suspended ||
      stopped ||
      !source ||
      !operationId
    ) {
      return;
    }
    const generation = ++requestGeneration;
    const requestController = new AbortController();
    controller = requestController;
    const isCurrentRequest = () =>
      !disposed && !suspended && !stopped && requestGeneration === generation;
    const shouldIgnoreRequest = () =>
      !isCurrentRequest() ||
      (requestController.signal.aborted && !deadlineExpired);
    const rejectExpiredRequest = () => {
      if (!isCurrentRequest()) return true;
      if (!requestController.signal.aborted) return false;
      if (deadlineExpired) throw new Error("live_source_deadline_expired");
      return true;
    };
    let deadlineExpired = false;
    const deadline = schedule(() => {
      deadlineExpired = true;
      requestController.abort();
    }, requestDeadlineMs);
    try {
      const endpoint =
        source === "recording"
          ? "/api/recordings/current"
          : "/api/replays/current";
      const response = await fetchCurrent(endpoint, {
        cache: "no-store",
        signal: requestController.signal,
      });
      if (shouldIgnoreRequest() || rejectExpiredRequest()) return;
      const body: unknown = await response.json();
      if (shouldIgnoreRequest() || rejectExpiredRequest()) return;
      if (!response.ok) throw new Error("live_source_unavailable");
      const read = readCurrent(source, operationId, body);
      if (shouldIgnoreRequest() || rejectExpiredRequest()) return;
      if (read.status === "ready") {
        const active = isActive(read.snapshot);
        if (!active) stopped = true;
        publish(
          "ready",
          active ? read.snapshot : clearPinnedLiveValues(read.snapshot),
        );
        if (active && isCurrentRequest()) schedulePoll(activeIntervalMs);
        return;
      }
      if (read.status === "error") {
        publish("error", null);
        if (isCurrentRequest()) schedulePoll(retryIntervalMs);
        return;
      }
      stopped = true;
      publish(read.status, null);
    } catch {
      if (
        disposed ||
        suspended ||
        stopped ||
        requestGeneration !== generation ||
        (requestController.signal.aborted && !deadlineExpired)
      ) {
        return;
      }
      publish("error", null);
      if (isCurrentRequest()) schedulePoll(retryIntervalMs);
    } finally {
      cancelSchedule(deadline);
      if (controller === requestController) controller = null;
    }
  };

  return {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    start(hidden = false) {
      if (started || disposed) return;
      started = true;
      if (
        !source ||
        !operationId ||
        (initialStatus !== "ready" && initialStatus !== "error") ||
        (initialSnapshot !== null && !isActive(initialSnapshot))
      ) {
        return;
      }
      monitoring = true;
      suspended = hidden;
      if (hidden) {
        publish("suspended", null);
      } else {
        schedulePoll(activeIntervalMs);
      }
    },
    setHidden(hidden) {
      if (!monitoring || disposed || stopped || suspended === hidden) return;
      suspended = hidden;
      if (hidden) {
        requestGeneration += 1;
        if (timer !== null) cancelSchedule(timer);
        timer = null;
        controller?.abort();
        controller = null;
        publish("suspended", null);
        return;
      }
      publish("ready", null);
      schedulePoll(0);
    },
    stop() {
      if (disposed) return;
      disposed = true;
      requestGeneration += 1;
      if (timer !== null) cancelSchedule(timer);
      timer = null;
      controller?.abort();
      controller = null;
      listeners.clear();
    },
  };
}

function clearPinnedLiveValues(
  snapshot: PinnedLiveSnapshot,
): PinnedLiveSnapshot {
  return {
    ...snapshot,
    live_telemetry: null,
    live_car_status: null,
    live_lap_timing: null,
    live_car_damage: null,
    live_car_setup: null,
    live_session_conditions: null,
    live_motion: null,
    live_session_history: null,
  };
}
