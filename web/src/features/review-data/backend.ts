import { parseAcquisitionStatus, type AcquisitionStatus } from "@/lib/acquisition-status";
import {
  requestApi,
  type LapRecord,
  type RecordingSourcePageRecord,
  type SessionRecord,
} from "@/lib/api";
import { forwardLocalRequest } from "@/lib/local-proxy";
import { readBoundedBytes } from "@/lib/proxy-json";
import { readRecordingSourcePage } from "@/lib/recording-catalog";
import { parseStorageUsageResponse, type StorageUsage } from "@/lib/storage-usage.mjs";
import type { ShellFooter, ShellStatus } from "@/features/ui-foundation/AppShell";
import {
  parseEvidenceAttempts,
  parseEvidenceSessions,
  type EvidenceAttempt,
  type EvidenceSession,
} from "./session-evidence";
import { parseLapEnrichment, type LapEnrichment } from "./enrichment";
import {
  engineerEventsQuery,
  parseEngineerEventsRead,
  type EngineerEventsRead,
} from "./engineer-events";
import { envelopeReason } from "./parse-utils";
import { parsePreferencesRead, type PreferencesRead } from "./preferences";
import {
  parseRegionsResponse,
  parseSessionTrackModels,
  parseTrackModelList,
  type RegionModel,
  type SessionRegionModels,
  type TrackModelSummary,
} from "./regions";
import { parseSessionSummary, type SessionSummary } from "./summary";

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
const EVIDENCE_MAX_BYTES = 8 * 1024 * 1024;
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
  const read = await readLocalJson(path, {
    timeoutMs: EVIDENCE_REQUEST_TIMEOUT_MS,
    maxBytes: EVIDENCE_MAX_BYTES,
  });
  return read.kind === "response" && read.status >= 200 && read.status < 300 ? read.body : null;
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

/** Header pill when the acquisition status itself cannot be read (plan 3.0.3). */
export const TELEMETRY_OFFLINE: ShellStatus = { tone: "red", label: "Backend unavailable" };

/** Telemetry acquisition state expressed as a header pill. */

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

/* ------------------------------------------------- bounded local reads */

/** Outcome of one bounded read of the local API. */
export type LocalRead =
  | { kind: "response"; status: number; body: unknown; etag: string | null }
  /** The time budget ran out before the engine answered. */
  | { kind: "timeout" }
  /** Network failure, oversized body or a body that is not JSON. */
  | { kind: "unreachable" };

type LocalReadOptions = { timeoutMs: number; maxBytes: number };

/**
 * One GET against the local API, bounded in time and size. Never throws. Error
 * statuses are returned (not collapsed) so callers can tell busy from missing.
 */
export async function readLocalJson(path: string, options: LocalReadOptions): Promise<LocalRead> {
  const signal = AbortSignal.timeout(options.timeoutMs);
  try {
    const response = await forwardLocalRequest(path, { signal });
    const declared = response.headers.get("content-length");
    if (declared !== null && /^[0-9]{1,12}$/.test(declared) && Number(declared) > options.maxBytes) {
      await response.body?.cancel().catch(() => undefined);
      return { kind: "unreachable" };
    }
    const read = await readBoundedBytes(response.body, options.maxBytes, signal);
    if (!read.ok) return signal.aborted ? { kind: "timeout" } : { kind: "unreachable" };
    return {
      kind: "response",
      status: response.status,
      body: JSON.parse(new TextDecoder().decode(read.bytes)) as unknown,
      etag: response.headers.get("etag"),
    };
  } catch {
    return signal.aborted ? { kind: "timeout" } : { kind: "unreachable" };
  }
}

/**
 * Typed outcome of a reader. `busy` is the engine's 409 admission response;
 * `timeout` means the caller's budget ran out (the data may still be computing);
 * `unavailable` is the engine answering that the data does not exist.
 */
export type ReadResult<T> =
  | { state: "ok"; value: T }
  | { state: "busy"; reason: string }
  | { state: "timeout" }
  | { state: "unavailable"; reason: string; status: number }
  | { state: "unreachable" };

export function toReadResult<T>(read: LocalRead, parse: (body: unknown) => T | null): ReadResult<T> {
  if (read.kind === "timeout") return { state: "timeout" };
  if (read.kind === "unreachable") return { state: "unreachable" };
  const reason = envelopeReason(read.body);
  if (read.status === 200) {
    const value = parse(read.body);
    return value === null
      ? { state: "unavailable", reason: "upstream_response_invalid", status: 200 }
      : { state: "ok", value };
  }
  if (read.status === 409 && reason !== null && reason.includes("busy")) {
    return { state: "busy", reason };
  }
  return { state: "unavailable", reason: reason ?? "unavailable", status: read.status };
}

type ReaderOptions = { timeoutMs?: number };

const SCAN_TIMEOUT_MS = 12_000; // the engine's scan budget is 10 s
const ENRICHMENT_MAX_BYTES = 2 * 1024 * 1024;
const SUMMARY_MAX_BYTES = 2 * 1024 * 1024;
const REGIONS_MAX_BYTES = 1024 * 1024;
const MODELS_TIMEOUT_MS = 5000;

const enrichmentCache = new Map<string, Cached<ReadResult<LapEnrichment>>>();
const summaryCache = new Map<string, Cached<ReadResult<SessionSummary>>>();
const sessionModelsCache = new Map<string, Cached<ReadResult<SessionRegionModels>>>();
const trackModelListCache: { current?: Cached<ReadResult<TrackModelSummary[]>> } = {};
const regionsCache = new Map<string, Cached<ReadResult<RegionModel>>>();

function cachedRead<T>(
  cache: Map<string, Cached<ReadResult<T>>>,
  key: string,
  load: () => Promise<ReadResult<T>>,
): Promise<ReadResult<T>> {
  const hit = fresh(cache.get(key));
  if (hit) return hit;
  const value = load();
  if (cache.size >= 64) cache.clear();
  cache.set(key, { at: Date.now(), value });
  // Only successes are reused; a busy or timed-out read must be retried.
  void value.then((result) => {
    if (result.state !== "ok" && cache.get(key)?.value === value) cache.delete(key);
  });
  return value;
}

const NOT_FOUND = { state: "unavailable", reason: "session_not_found", status: 404 } as const;

/**
 * Sector and tyre enrichment of a v2 session (`/laps`). Reused for 10 s per
 * session so tab switches do not rescan the journal. Pass a small
 * `timeoutMs` to get `{ state: "timeout" }` quickly and defer to the client.
 */
export function fetchLapEnrichment(
  sessionId: string,
  options: ReaderOptions = {},
): Promise<ReadResult<LapEnrichment>> {
  if (!EVIDENCE_ID.test(sessionId)) return Promise.resolve(NOT_FOUND);
  const timeoutMs = options.timeoutMs ?? SCAN_TIMEOUT_MS;
  return cachedRead(enrichmentCache, `${sessionId}|${timeoutMs}`, async () =>
    toReadResult(
      await readLocalJson(`${EVIDENCE_BASE}/sessions/${encodeURIComponent(sessionId)}/laps`, {
        timeoutMs,
        maxBytes: ENRICHMENT_MAX_BYTES,
      }),
      parseLapEnrichment,
    ),
  );
}

/* ---- summary date cache: started_at_utc of every summary read, in memory ---- */

const SUMMARY_DATE_LIMIT = 256;
type DateStore = { dates: Map<string, string> };
// Route Handlers and pages may be bundled separately; share one store.
const dateStore: DateStore = ((globalThis as { __f1SummaryDates?: DateStore }).__f1SummaryDates ??= {
  dates: new Map(),
});

export function rememberSummaryDate(sessionId: string, startedAtUtc: string | null) {
  if (startedAtUtc === null) return;
  dateStore.dates.delete(sessionId); // refresh insertion order
  dateStore.dates.set(sessionId, startedAtUtc);
  while (dateStore.dates.size > SUMMARY_DATE_LIMIT) {
    const oldest = dateStore.dates.keys().next().value;
    if (oldest === undefined) break;
    dateStore.dates.delete(oldest);
  }
}

/** Capture start time of a session whose `/summary` was read earlier (in memory, 256 sessions). */
export function cachedSummaryDate(sessionId: string): string | null {
  return dateStore.dates.get(sessionId) ?? null;
}

/** Snapshot of every remembered session start time. */
export function cachedSummaryDates(): ReadonlyMap<string, string> {
  return new Map(dateStore.dates);
}

/**
 * Timeline and summary of a v2 session (`/summary`). Reused for 10 s per
 * session; every successful read also remembers the session's start time for
 * the Library (which must not call `/summary` once per row).
 */
export function fetchSummary(
  sessionId: string,
  options: ReaderOptions = {},
): Promise<ReadResult<SessionSummary>> {
  if (!EVIDENCE_ID.test(sessionId)) return Promise.resolve(NOT_FOUND);
  const timeoutMs = options.timeoutMs ?? SCAN_TIMEOUT_MS;
  return cachedRead(summaryCache, `${sessionId}|${timeoutMs}`, async () => {
    const result = toReadResult(
      await readLocalJson(`${EVIDENCE_BASE}/sessions/${encodeURIComponent(sessionId)}/summary`, {
        timeoutMs,
        maxBytes: SUMMARY_MAX_BYTES,
      }),
      parseSessionSummary,
    );
    if (result.state === "ok") rememberSummaryDate(sessionId, result.value.startedAtUtc);
    return result;
  });
}

/** Track models compatible with a v2 session, regions included. An empty list is normal. */
export function fetchSessionTrackModels(
  sessionId: string,
  options: ReaderOptions = {},
): Promise<ReadResult<SessionRegionModels>> {
  if (!EVIDENCE_ID.test(sessionId)) return Promise.resolve(NOT_FOUND);
  const timeoutMs = options.timeoutMs ?? MODELS_TIMEOUT_MS;
  return cachedRead(sessionModelsCache, `${sessionId}|${timeoutMs}`, async () =>
    toReadResult(
      await readLocalJson(
        `${EVIDENCE_BASE}/sessions/${encodeURIComponent(sessionId)}/track-models`,
        { timeoutMs, maxBytes: REGIONS_MAX_BYTES * 2 },
      ),
      parseSessionTrackModels,
    ),
  );
}

/** Every registered track model (`/api/v1/track-models`), without regions. */
export function fetchTrackModels(options: ReaderOptions = {}): Promise<ReadResult<TrackModelSummary[]>> {
  const timeoutMs = options.timeoutMs ?? MODELS_TIMEOUT_MS;
  const hit = fresh(trackModelListCache.current);
  if (hit) return hit;
  const value = (async () =>
    toReadResult(
      await readLocalJson("/api/v1/track-models", { timeoutMs, maxBytes: REGIONS_MAX_BYTES }),
      parseTrackModelList,
    ))();
  trackModelListCache.current = { at: Date.now(), value };
  void value.then((result) => {
    if (result.state !== "ok" && trackModelListCache.current?.value === value) {
      delete trackModelListCache.current;
    }
  });
  return value;
}

const MODEL_ID = /^[A-Za-z0-9._:-]{1,128}$/;

/** Regions of one exact catalog revision (`/api/v1/track-models/{id}/regions`). */
export function fetchRegions(
  modelId: string,
  revision: number,
  options: ReaderOptions = {},
): Promise<ReadResult<RegionModel>> {
  if (!MODEL_ID.test(modelId) || !Number.isSafeInteger(revision) || revision < 1) {
    return Promise.resolve({ state: "unavailable", reason: "unknown_track_model_revision", status: 404 });
  }
  const timeoutMs = options.timeoutMs ?? MODELS_TIMEOUT_MS;
  return cachedRead(regionsCache, `${modelId}|${revision}|${timeoutMs}`, async () =>
    toReadResult(
      await readLocalJson(
        `/api/v1/track-models/${encodeURIComponent(modelId)}/regions?revision=${revision}`,
        { timeoutMs, maxBytes: REGIONS_MAX_BYTES },
      ),
      parseRegionsResponse,
    ),
  );
}

/**
 * One page of the engineer activity feed. Never cached: callers poll with
 * `after`. `unavailable` is the engine reporting that the log cannot be read.
 */
export async function fetchEngineerEvents(
  options: { after?: number | null; limit?: number; sessionId?: string | null } = {},
): Promise<EngineerEventsRead> {
  const read = await readLocalJson(`/api/v2/engineer/events?${engineerEventsQuery(options)}`, {
    timeoutMs: 4000,
    maxBytes: 2 * 1024 * 1024,
  });
  if (read.kind !== "response") return { state: "unreachable" };
  return parseEngineerEventsRead(read.status, read.body);
}

/** Saved engineer preferences with their revision. Never cached. */
export async function fetchPreferences(): Promise<PreferencesRead> {
  const read = await readLocalJson("/api/v1/engineer/preferences", {
    timeoutMs: 3000,
    maxBytes: 64 * 1024,
  });
  if (read.kind !== "response") return { state: "unreachable" };
  return parsePreferencesRead(read.status, read.body);
}

export type RecordingSourcesQuery = {
  limit?: number;
  offset?: number;
  q?: string;
  latestJobStatus?: string;
  availability?: string;
  selectedCaptureId?: string;
};

/** One page of capture files and their latest import state (`/recording-sources/page`). Null on failure. */
export async function fetchRecordingSources(
  query: RecordingSourcesQuery = {},
): Promise<RecordingSourcePageRecord | null> {
  const params = new URLSearchParams();
  if (query.limit !== undefined) params.set("limit", String(Math.min(50, Math.max(1, Math.trunc(query.limit)))));
  if (query.offset !== undefined) params.set("offset", String(Math.max(0, Math.trunc(query.offset))));
  if (query.q) params.set("q", query.q.slice(0, 128));
  if (query.latestJobStatus) params.set("latest_job_status", query.latestJobStatus);
  if (query.availability) params.set("availability", query.availability);
  if (query.selectedCaptureId) params.set("selected_capture_id", query.selectedCaptureId);
  const suffix = params.size ? `?${params}` : "";
  const read = await readLocalJson(`/api/v1/recording-sources/page${suffix}`, {
    timeoutMs: 4000,
    maxBytes: 1024 * 1024,
  });
  if (read.kind !== "response" || read.status !== 200) return null;
  const body = read.body as { status?: unknown; data?: unknown } | null;
  if (!body || body.status !== "ok") return null;
  return readRecordingSourcePage(body.data);
}
