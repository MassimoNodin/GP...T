export interface AcquisitionStatus {
  state:
    | "starting"
    | "listening"
    | "receiving"
    | "stale"
    | "stopped"
    | "failed"
    | "disabled";
  received: number | null;
  journaled: number | null;
  processed: number | null;
  dropped: number | null;
  kernel_dropped: number | null;
  socket_errors: number | null;
  queue_depth: number | null;
  queue_limit: number | null;
  pending_publication: number | null;
  last_publication_delay_s: number | null;
}

const states = new Set([
  "starting",
  "listening",
  "receiving",
  "stale",
  "stopped",
  "failed",
  "disabled",
]);
const counters = [
  "received",
  "journaled",
  "processed",
  "dropped",
  "socket_errors",
  "queue_depth",
  "queue_limit",
  "pending_publication",
] as const;

export function parseAcquisitionStatus(
  value: unknown,
): AcquisitionStatus | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const data = value as Record<string, unknown>;
  if (typeof data.state !== "string" || !states.has(data.state)) return null;
  if (data.state === "disabled") {
    return {
      state: "disabled",
      received: null,
      journaled: null,
      processed: null,
      dropped: null,
      kernel_dropped: null,
      socket_errors: null,
      queue_depth: null,
      queue_limit: null,
      pending_publication: null,
      last_publication_delay_s: null,
    };
  }
  for (const key of counters) {
    if (!Number.isSafeInteger(data[key]) || (data[key] as number) < 0)
      return null;
  }
  if (
    data.kernel_dropped !== null &&
    (!Number.isSafeInteger(data.kernel_dropped) ||
      (data.kernel_dropped as number) < 0)
  )
    return null;
  if (
    typeof data.last_publication_delay_s !== "number" ||
    !Number.isFinite(data.last_publication_delay_s) ||
    data.last_publication_delay_s < 0
  )
    return null;
  if (
    (data.queue_limit as number) < 1 ||
    (data.queue_limit as number) > 4096 ||
    (data.queue_depth as number) > (data.queue_limit as number)
  )
    return null;
  return Object.fromEntries(
    ["state", ...counters, "kernel_dropped", "last_publication_delay_s"].map(
      (key) => [key, data[key]],
    ),
  ) as unknown as AcquisitionStatus;
}

type FetchStatus = (
  input: string,
  init: { cache: "no-store"; signal: AbortSignal },
) => Promise<{ ok: boolean; json: () => Promise<unknown> }>;

export function createAcquisitionPoller({
  onStatus,
  fetchStatus = (input, init) => fetch(input, init),
  intervalMs = 1000,
  deadlineMs = 5000,
}: {
  onStatus: (status: AcquisitionStatus | null) => void;
  fetchStatus?: FetchStatus;
  intervalMs?: number;
  deadlineMs?: number;
}) {
  let disposed = false;
  let hidden = true;
  let generation = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | null = null;

  async function poll() {
    if (disposed || hidden) return;
    const current = ++generation;
    const requestController = new AbortController();
    controller = requestController;
    let deadline: ReturnType<typeof setTimeout> | undefined;
    const expired = new Promise<never>((_resolve, reject) => {
      deadline = setTimeout(() => {
        requestController.abort();
        reject(new Error("acquisition_status_deadline"));
      }, deadlineMs);
    });
    try {
      const read = async () => {
        const response = await fetchStatus("/api/session-evidence/status", {
          cache: "no-store",
          signal: requestController.signal,
        });
        return response.ok ? await response.json() : null;
      };
      const body: unknown = await Promise.race([read(), expired]);
      if (
        !disposed &&
        !hidden &&
        generation === current &&
        !requestController.signal.aborted
      ) {
        onStatus(parseAcquisitionStatus(body));
      }
    } catch {
      if (!disposed && !hidden && generation === current) onStatus(null);
    } finally {
      clearTimeout(deadline);
      if (controller === requestController) controller = null;
      if (!disposed && !hidden && generation === current)
        timer = setTimeout(() => void poll(), intervalMs);
    }
  }

  return {
    setHidden(next: boolean) {
      if (disposed || next === hidden) return;
      hidden = next;
      generation += 1;
      clearTimeout(timer);
      controller?.abort();
      onStatus(null);
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
