// Small polling helpers for the live status feeds. The acquisition feed reuses
// createAcquisitionPoller from src/lib/acquisition-status.ts; the engineer
// runtime feed uses createEndpointPoller, which has the same semantics: no
// requests while hidden, one request in flight, a hard deadline per request, and
// stale responses dropped after a hide/show cycle.

type FetchJson = (
  input: string,
  init: { cache: "no-store"; signal: AbortSignal },
) => Promise<{ ok: boolean; json: () => Promise<unknown> }>;

export function createEndpointPoller<T>({
  url,
  parse,
  onResult,
  fetchJson = (input, init) => fetch(input, init),
  intervalMs = 2000,
  deadlineMs = 5000,
}: {
  url: string;
  parse: (body: unknown) => T | null;
  onResult: (value: T | null) => void;
  fetchJson?: FetchJson;
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
        reject(new Error("endpoint_deadline"));
      }, deadlineMs);
    });
    try {
      const read = async () => {
        const response = await fetchJson(url, { cache: "no-store", signal: requestController.signal });
        return response.ok ? await response.json() : null;
      };
      const body: unknown = await Promise.race([read(), expired]);
      if (!disposed && !hidden && generation === current && !requestController.signal.aborted) {
        onResult(parse(body));
      }
    } catch {
      if (!disposed && !hidden && generation === current) onResult(null);
    } finally {
      clearTimeout(deadline);
      if (controller === requestController) controller = null;
      if (!disposed && !hidden && generation === current) {
        timer = setTimeout(() => void poll(), intervalMs);
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

/**
 * Forwards values to `emit`, but swallows up to `tolerated` consecutive
 * failures (null) so a single dropped poll does not flip the UI to
 * "unavailable" and back.
 */
export function createFailureFilter<T>(emit: (value: T | null) => void, tolerated = 1) {
  let failures = 0;
  return (value: T | null) => {
    if (value !== null) {
      failures = 0;
      emit(value);
      return;
    }
    failures += 1;
    if (failures > tolerated) emit(null);
  };
}
