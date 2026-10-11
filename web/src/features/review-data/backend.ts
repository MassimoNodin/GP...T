import { parseAcquisitionStatus, type AcquisitionStatus } from "@/lib/acquisition-status";
import { requestApi, type LapRecord, type SessionRecord } from "@/lib/api";
import { forwardLocalRequest } from "@/lib/local-proxy";
import { parseStorageUsageResponse, type StorageUsage } from "@/lib/storage-usage.mjs";
import type { ShellFooter, ShellStatus } from "@/features/ui-foundation/AppShell";
import {
  parseEvidenceAttempts,
  parseEvidenceSessions,
  type EvidenceAttempt,
  type EvidenceSession,
} from "./session-evidence";

// Server-side reads from the local F1 Engineer API. Every reader returns null
// when the API is unreachable or reports the data unavailable, so screens can
// fall back to the reference fixture for that section.

export async function fetchSessions(): Promise<SessionRecord[] | null> {
  const response = await requestApi<SessionRecord[]>("/api/v1/sessions").catch(() => null);
  if (!response || response.status !== "ok" || !Array.isArray(response.data)) return null;
  // One entry per capture/session: the latest completed processing run wins.
  const latest = new Map<string, SessionRecord>();
  for (const item of response.data) {
    const key = `${item.capture_sha256}:${item.session_uid}`;
    const current = latest.get(key);
    const finished = item.finished_at_utc ?? item.started_at_utc;
    const currentFinished = current ? (current.finished_at_utc ?? current.started_at_utc) : "";
    const preferred =
      !current ||
      (item.run_status === "complete" && current.run_status !== "complete") ||
      (item.run_status === current.run_status && finished > currentFinished);
    if (preferred) latest.set(key, item);
  }
  return [...latest.values()].sort((a, b) => b.started_at_utc.localeCompare(a.started_at_utc));
}

export async function fetchSession(sessionKey: string): Promise<SessionRecord | null> {
  const sessions = await fetchSessions();
  return sessions?.find((item) => item.session_key === sessionKey) ?? null;
}

export async function fetchLaps(session: SessionRecord): Promise<LapRecord[] | null> {
  const query = new URLSearchParams({ run_id: session.run_id, session_uid: session.session_uid });
  const response = await requestApi<LapRecord[]>(`/api/v1/laps?${query}`).catch(() => null);
  if (!response || response.status !== "ok" || !Array.isArray(response.data)) return null;
  return response.data;
}

/* ------------------------------------------------- v2 session evidence */

const EVIDENCE_BASE = "/api/v2/session-evidence";
const EVIDENCE_PAGE_LIMIT = 100; // the API rejects larger pages
const EVIDENCE_SESSION_PAGES = 5;
const EVIDENCE_ATTEMPT_PAGES = 12; // up to 1,200 attempts per session
const EVIDENCE_REQUEST_TIMEOUT_MS = 5000;
const EVIDENCE_CACHE_TTL_MS = 10_000;
const EVIDENCE_ID = /^[A-Za-z0-9:_-]{1,128}$/;

type Cached<T> = { at: number; value: Promise<T> };
const sessionCache: { current?: Cached<EvidenceSession[] | null> } = {};
const attemptCache = new Map<string, Cached<EvidenceAttemptsResult | null>>();

export type EvidenceAttemptsResult = {
  attempts: EvidenceAttempt[];
  /** True when the page budget ran out or a later page failed. */
  truncated: boolean;
};

async function evidenceJson(path: string): Promise<unknown | null> {
  try {
    const response = await forwardLocalRequest(path, {
      signal: AbortSignal.timeout(EVIDENCE_REQUEST_TIMEOUT_MS),
    });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

function fresh<T>(entry: Cached<T> | undefined): Promise<T> | null {
  return entry && Date.now() - entry.at < EVIDENCE_CACHE_TTL_MS ? entry.value : null;
}

async function readEvidenceSessions(): Promise<EvidenceSession[] | null> {
  const sessions: EvidenceSession[] = [];
  let after = "";
  for (let page = 0; page < EVIDENCE_SESSION_PAGES; page += 1) {
    const query = new URLSearchParams({ limit: String(EVIDENCE_PAGE_LIMIT), after });
    const body = await evidenceJson(`${EVIDENCE_BASE}/sessions?${query}`);
    if (body === null) return page === 0 ? null : sessions;
    const batch = parseEvidenceSessions(body);
    sessions.push(...batch);
    if (batch.length < EVIDENCE_PAGE_LIMIT) break;
    after = batch[batch.length - 1].id;
  }
  return sessions;
}

/** Every v2 session, ordered by id (the API has no timestamps). Null when unreachable. */
export function fetchEvidenceSessions(): Promise<EvidenceSession[] | null> {
  const cached = fresh(sessionCache.current);
  if (cached) return cached;
  const value = readEvidenceSessions();
  sessionCache.current = { at: Date.now(), value };
  void value.then((result) => {
    if (result === null) delete sessionCache.current;
  });
  return value;
}

async function readEvidenceAttempts(sessionId: string): Promise<EvidenceAttemptsResult | null> {
  const attempts: EvidenceAttempt[] = [];
  let after = "";
  for (let page = 0; page < EVIDENCE_ATTEMPT_PAGES; page += 1) {
    const query = new URLSearchParams({ limit: String(EVIDENCE_PAGE_LIMIT), after });
    const body = await evidenceJson(`${EVIDENCE_BASE}/sessions/${encodeURIComponent(sessionId)}/attempts?${query}`);
    if (body === null) return page === 0 ? null : { attempts, truncated: true };
    const batch = parseEvidenceAttempts(body);
    attempts.push(...batch);
    if (batch.length < EVIDENCE_PAGE_LIMIT) return { attempts, truncated: false };
    after = batch[batch.length - 1].id;
  }
  return { attempts, truncated: true };
}

/**
 * All attempts of one v2 session (player and opponents; the API has no role
 * filter), paged within a bounded budget. Null when unreachable.
 */
export function fetchEvidenceAttempts(sessionId: string): Promise<EvidenceAttemptsResult | null> {
  if (!EVIDENCE_ID.test(sessionId)) return Promise.resolve(null);
  const cached = fresh(attemptCache.get(sessionId));
  if (cached) return cached;
  const value = readEvidenceAttempts(sessionId);
  if (attemptCache.size >= 64) attemptCache.clear();
  attemptCache.set(sessionId, { at: Date.now(), value });
  void value.then((result) => {
    if (result === null) attemptCache.delete(sessionId);
  });
  return value;
}

export async function fetchEvidenceSession(sessionId: string): Promise<EvidenceSession | null> {
  if (!EVIDENCE_ID.test(sessionId)) return null;
  const sessions = await fetchEvidenceSessions();
  return sessions?.find((item) => item.id === sessionId) ?? null;
}

export async function fetchAcquisition(): Promise<AcquisitionStatus | null> {
  try {
    const response = await forwardLocalRequest("/api/v2/session-evidence/status", {
      signal: AbortSignal.timeout(2500),
    });
    if (!response.ok) return null;
    return parseAcquisitionStatus(await response.json());
  } catch {
    return null;
  }
}

export async function fetchStorage(): Promise<StorageUsage | null> {
  try {
    const response = await forwardLocalRequest("/api/v1/storage/usage", {
      signal: AbortSignal.timeout(2500),
    });
    if (!response.ok) return null;
    return parseStorageUsageResponse(await response.json());
  } catch {
    return null;
  }
}

/** Telemetry acquisition state expressed as a header pill. */
/** Header pill when the acquisition status itself cannot be read. */
export const TELEMETRY_OFFLINE: ShellStatus = { tone: "red", label: "Telemetry offline" };

export function telemetryStatus(status: AcquisitionStatus): ShellStatus {
  switch (status.state) {
    case "receiving":
      return { tone: "green", label: "Telemetry live" };
    case "starting":
    case "listening":
      return { tone: "amber", label: "Waiting for game" };
    case "stale":
      return { tone: "amber", label: "Telemetry stale" };
    case "disabled":
      return { tone: "grey", label: "Telemetry disabled" };
    default:
      return { tone: "red", label: "Telemetry offline" };
  }
}

export function telemetryFooter(status: AcquisitionStatus, fallback: ShellFooter): ShellFooter {
  const pill = telemetryStatus(status);
  const detail =
    status.state === "receiving"
      ? "Ready"
      : status.state === "listening" || status.state === "starting"
        ? "Listening on UDP 20777"
        : status.state === "stale"
          ? "No recent packets"
          : "Not receiving";
  return { tone: pill.tone, label: pill.label, lines: [fallback.lines[0] ?? "", detail] };
}

export function completedLaps(laps: LapRecord[]): LapRecord[] {
  return laps
    .filter((lap) => lap.disposition === "completed" && typeof lap.lap_time_ms === "number")
    .sort((a, b) => a.lap_number - b.lap_number || a.attempt_number - b.attempt_number);
}

/** Fastest lap that the game reported valid and the pipeline allows as a reference. */
export function sessionBestLap(laps: LapRecord[]): LapRecord | null {
  let best: LapRecord | null = null;
  for (const lap of completedLaps(laps)) {
    if (lap.game_valid === false || !lap.reference_eligible) continue;
    if (!best || (lap.lap_time_ms as number) < (best.lap_time_ms as number)) best = lap;
  }
  return best;
}
