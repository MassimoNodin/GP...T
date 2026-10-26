import { humanReason } from "@/features/review-data/reasons";
import { traceFromEvidence, traceUnavailableReason, type LapTrace } from "./lap-telemetry";

// Opt-in processing of a deferred v2 lap
// (`POST /api/review/sessions/{session}/attempts/{revision}/materialize`).
// The result is request-scoped on the backend: the parsed trace lives only in
// the page's state. Pure apart from `fetch`, which can be injected.

export const PROCESS_BUSY = "The telemetry reader is busy. Try again in a moment.";
export const PROCESS_FORBIDDEN = "The web app is not allowed to process telemetry. Check the engine's control token.";
export const PROCESS_OFFLINE = "The local engine is unavailable.";
export const PROCESS_TIMEOUT = "Processing took too long. Try again.";
/** The proxy gives the engine 45 s. */
export const PROCESS_CLIENT_TIMEOUT_MS = 50_000;

export type ProcessResult =
  | { state: "ok"; trace: LapTrace }
  | { state: "busy"; message: string }
  | { state: "failed"; message: string; retryable: boolean };

export async function processTelemetry(
  sessionId: string,
  revisionId: string,
  options: { fetchImpl?: typeof fetch; timeoutMs?: number } = {},
): Promise<ProcessResult> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const url = `/api/review/sessions/${encodeURIComponent(sessionId)}/attempts/${encodeURIComponent(revisionId)}/materialize`;
  try {
    const response = await fetchImpl(url, {
      method: "POST",
      signal: AbortSignal.timeout(options.timeoutMs ?? PROCESS_CLIENT_TIMEOUT_MS),
    });
    const body: unknown = await response.json().catch(() => null);
    if (response.ok) {
      const trace = traceFromEvidence(body);
      return trace.state === "available"
        ? { state: "ok", trace }
        : { state: "failed", message: trace.reason, retryable: false };
    }
    const reason = typeof (body as { reason?: unknown } | null)?.reason === "string" ? (body as { reason: string }).reason : null;
    if (response.status === 409) return { state: "busy", message: PROCESS_BUSY };
    if (response.status === 403 || response.status === 401) return { state: "failed", message: PROCESS_FORBIDDEN, retryable: false };
    if (response.status === 504) return { state: "failed", message: PROCESS_TIMEOUT, retryable: true };
    if (response.status === 502 || response.status === 503) return { state: "failed", message: PROCESS_OFFLINE, retryable: true };
    return {
      state: "failed",
      message: humanReason(reason) || traceUnavailableReason(reason),
      retryable: false,
    };
  } catch (error) {
    const timedOut = error instanceof DOMException && error.name === "TimeoutError";
    return { state: "failed", message: timedOut ? PROCESS_TIMEOUT : PROCESS_OFFLINE, retryable: true };
  }
}
