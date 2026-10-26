import { requestApi, type TrackModelRecord } from "@/lib/api";
import { forwardLocalRequest } from "@/lib/local-proxy";
import {
  traceFromEvidence,
  traceFromPreview,
  trackModelLabel,
  unavailableTrace,
  type LapTrace,
} from "./lap-telemetry";

// Server-side readers for Lap Analysis telemetry. Every reader resolves to an
// "unavailable" trace (never throws) so the chart card can explain the gap.

const EVIDENCE_ID = /^[A-Za-z0-9:_-]{1,128}$/;
const TRACE_TIMEOUT_MS = 15_000;
const TRACE_CACHE_LIMIT = 32;
const BUSY_RETRY_MS = 400;

// Published evidence is immutable, so successful reads are kept per revision.
const traceCache = new Map<string, Promise<LapTrace>>();

async function readEvidenceTrace(sessionId: string, revisionId: string): Promise<LapTrace> {
  const path = `/api/v2/session-evidence/sessions/${encodeURIComponent(sessionId)}/attempts/${encodeURIComponent(revisionId)}`;
  // The backend admits one trace read at a time and answers 409 while busy.
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const response = await forwardLocalRequest(path, { signal: AbortSignal.timeout(TRACE_TIMEOUT_MS) });
      const body: unknown = await response.json().catch(() => null);
      if (response.ok) return traceFromEvidence(body);
      const reason = (body as { reason?: unknown } | null)?.reason;
      if (response.status === 409 && attempt === 0) {
        await new Promise((resolve) => setTimeout(resolve, BUSY_RETRY_MS));
        continue;
      }
      return unavailableTrace(typeof reason === "string" ? reason : null);
    } catch {
      return unavailableTrace("unreachable");
    }
  }
  return unavailableTrace("comparison_read_budget_busy");
}

/** Samples of one v2 attempt revision; unavailable when the backend cannot supply them. */
export function fetchEvidenceTrace(sessionId: string, revisionId: string): Promise<LapTrace> {
  if (!EVIDENCE_ID.test(sessionId) || !EVIDENCE_ID.test(revisionId)) {
    return Promise.resolve(unavailableTrace(null));
  }
  const key = `${sessionId}:${revisionId}`;
  const cached = traceCache.get(key);
  if (cached) return cached;
  const value = readEvidenceTrace(sessionId, revisionId);
  if (traceCache.size >= TRACE_CACHE_LIMIT) traceCache.clear();
  traceCache.set(key, value);
  void value.then((trace) => {
    if (trace.state === "unavailable") traceCache.delete(key);
  });
  return value;
}

/** Trace preview of one v1 attempt (`GET /api/v1/attempts/{key}/traces`). */
export async function fetchPreviewTrace(attemptKey: string): Promise<LapTrace> {
  const response = await requestApi<Record<string, unknown>>(`/api/v1/attempts/${encodeURIComponent(attemptKey)}/traces`).catch(
    () => null,
  );
  if (!response) return unavailableTrace("unreachable");
  if (response.status !== "ok" || !response.data) return unavailableTrace(response.reason);
  return traceFromPreview(response);
}

/** Name of the registered track model for this circuit; null when none matches. */
export async function fetchTrackModelLabel(trackName: string | null, trackLengthM: number | null): Promise<string | null> {
  if (!trackName) return null;
  const response = await requestApi<TrackModelRecord[]>("/api/v1/track-models").catch(() => null);
  if (!response || response.status !== "ok" || !Array.isArray(response.data)) return null;
  return trackModelLabel(response.data, trackName, trackLengthM);
}
