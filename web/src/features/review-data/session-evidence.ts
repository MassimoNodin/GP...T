import type { Tone } from "@/features/ui-foundation/primitives";
import type { ProgressionPoint } from "./fixtures";
import { formatGame, formatLapTime, formatSessionType, formatWeather } from "./format";

// Pure helpers for the v2 session-evidence API (`/api/v2/session-evidence/*`).
// Nothing here performs I/O so the mapping can be tested without a backend.
//
// Fields used, all from the v2 payloads:
//   session   id, lifecycle, acquisition, occurrence, context (JSON *string*)
//   attempt   id (revision id), role, published_sequence,
//             payload.lap_number / attempt_number / lap_time_ms / disposition /
//             game_valid / pit_encountered / start_observed / superseded
// The v2 API carries no wall-clock timestamp and no sector times.

export type EvidenceContext = {
  trackName: string | null;
  sessionType: string | null;
  weatherName: string | null;
  airTemperatureC: number | null;
  trackTemperatureC: number | null;
  packetFormat: number | null;
  ruleSet: string | null;
  trackLengthM: number | null;
  totalLaps: number | null;
};

export type EvidenceSession = {
  id: string;
  generation: string;
  uid: string;
  lifecycle: string;
  acquisition: string;
  occurrence: number;
  context: EvidenceContext | null;
};

export type EvidenceAttempt = {
  /** Attempt revision id; the key for `/sessions/{id}/attempts/{revision_id}`. */
  id: string;
  role: string;
  publishedSequence: number | null;
  attemptNumber: number | null;
  lapNumber: number | null;
  lapTimeMs: number | null;
  disposition: string;
  gameValid: boolean | null;
  pitEncountered: boolean;
  startObserved: boolean | null;
  superseded: boolean;
  /** Whether the raw trace is already materialised (`detail.state === "available"`). */
  traceAvailable: boolean;
  /** First non-null context recorded while the attempt ran. */
  context: EvidenceContext | null;
};

export type EvidenceLap = {
  /** Stable per-session lap id used in URLs: the lap number as text. */
  id: string;
  lap: number;
  attempt: number | null;
  ms: number;
  valid: boolean;
  pit: boolean;
  startObserved: boolean | null;
  /** v2 attempts carry no sector times; kept for the shared lap shape. */
  sectors: [number, number, number] | null;
  revisionId: string;
  traceAvailable: boolean;
};

export type EvidenceEvent = {
  lap: number;
  text: string;
  icon: "ring-green" | "alert-red" | "ring-amber" | "check-green";
  highlight?: boolean;
};

/* ----------------------------------------------------------------- parsing */

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function nonEmpty(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

/** Accepts the JSON string the API stores, or an already-decoded object. */
export function parseEvidenceContext(raw: unknown): EvidenceContext | null {
  let value: unknown = raw;
  if (typeof raw === "string") {
    try {
      value = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  const record = asRecord(value);
  if (!record) return null;
  return {
    trackName: nonEmpty(record.track_name),
    sessionType: nonEmpty(record.session_type),
    weatherName: nonEmpty(record.weather_name),
    airTemperatureC: finite(record.air_temperature_c),
    trackTemperatureC: finite(record.track_temperature_c),
    packetFormat: finite(record.packet_format),
    ruleSet: nonEmpty(record.rule_set),
    trackLengthM: finite(record.track_length_m),
    totalLaps: finite(record.total_laps),
  };
}

function dataArray(body: unknown): unknown[] {
  const data = asRecord(body)?.data;
  return Array.isArray(data) ? data : [];
}

export function parseEvidenceSessions(body: unknown): EvidenceSession[] {
  const sessions: EvidenceSession[] = [];
  for (const item of dataArray(body)) {
    const record = asRecord(item);
    const id = nonEmpty(record?.id);
    if (!record || !id) continue;
    sessions.push({
      id,
      generation: typeof record.generation === "string" ? record.generation : "",
      uid: record.uid === undefined || record.uid === null ? "" : String(record.uid),
      lifecycle: typeof record.lifecycle === "string" ? record.lifecycle : "",
      acquisition: typeof record.acquisition === "string" ? record.acquisition : "",
      occurrence: finite(record.occurrence) ?? 1,
      context: parseEvidenceContext(record.context),
    });
  }
  return sessions;
}

export function parseEvidenceAttempts(body: unknown): EvidenceAttempt[] {
  const attempts: EvidenceAttempt[] = [];
  for (const item of dataArray(body)) {
    const record = asRecord(item);
    const payload = asRecord(record?.payload);
    const id = nonEmpty(record?.id);
    if (!record || !payload || !id) continue;
    let context: EvidenceContext | null = null;
    if (Array.isArray(payload.context_segments)) {
      for (const segment of payload.context_segments) {
        const parsed = parseEvidenceContext(asRecord(segment)?.context);
        if (parsed) {
          context = parsed;
          break;
        }
      }
    }
    attempts.push({
      id,
      role: typeof record.role === "string" ? record.role : "",
      publishedSequence: finite(record.published_sequence),
      attemptNumber: finite(payload.attempt_number),
      lapNumber: finite(payload.lap_number),
      lapTimeMs: finite(payload.lap_time_ms),
      disposition: typeof payload.disposition === "string" ? payload.disposition : "",
      gameValid: typeof payload.game_valid === "boolean" ? payload.game_valid : null,
      pitEncountered: payload.pit_encountered === true,
      startObserved: typeof payload.start_observed === "boolean" ? payload.start_observed : null,
      superseded: payload.superseded !== null && payload.superseded !== undefined,
      traceAvailable: asRecord(record.detail)?.state === "available",
      context,
    });
  }
  return attempts;
}

/* -------------------------------------------------------------------- laps */

/**
 * Completed, timed, non-superseded player laps ordered by lap number. When the
 * game re-times a lap number the highest attempt number wins.
 */
export function evidenceLaps(attempts: EvidenceAttempt[]): EvidenceLap[] {
  const byLap = new Map<number, EvidenceAttempt>();
  for (const attempt of attempts) {
    if (attempt.role !== "player" || attempt.superseded) continue;
    if (attempt.disposition !== "completed") continue;
    if (attempt.lapNumber === null || attempt.lapTimeMs === null || attempt.lapTimeMs <= 0) continue;
    const current = byLap.get(attempt.lapNumber);
    if (!current || (attempt.attemptNumber ?? 0) > (current.attemptNumber ?? 0)) {
      byLap.set(attempt.lapNumber, attempt);
    }
  }
  return [...byLap.values()]
    .sort((a, b) => (a.lapNumber as number) - (b.lapNumber as number))
    .map((attempt) => ({
      id: String(attempt.lapNumber),
      lap: attempt.lapNumber as number,
      attempt: attempt.attemptNumber,
      ms: attempt.lapTimeMs as number,
      valid: attempt.gameValid !== false,
      pit: attempt.pitEncountered,
      startObserved: attempt.startObserved,
      sectors: null,
      revisionId: attempt.id,
      traceAvailable: attempt.traceAvailable,
    }));
}

/** Fastest lap the game reported valid; null when there is none. */
export function bestEvidenceLap(laps: EvidenceLap[]): EvidenceLap | null {
  let best: EvidenceLap | null = null;
  for (const lap of laps) {
    if (!lap.valid) continue;
    if (!best || lap.ms < best.ms) best = lap;
  }
  return best;
}

/** Milliseconds a lap is slower (+) or faster (-) than the reference lap. */
export function lapDeltaMs(lap: Pick<EvidenceLap, "ms">, reference: Pick<EvidenceLap, "ms"> | null): number | null {
  return reference ? lap.ms - reference.ms : null;
}

export function timeDrivenMs(laps: EvidenceLap[]): number | null {
  return laps.length ? laps.reduce((sum, lap) => sum + lap.ms, 0) : null;
}

/**
 * Notable events derivable from player attempts alone: the session best, pit
 * laps, invalid laps and laps that never completed.
 */
export function evidenceEvents(attempts: EvidenceAttempt[], laps: EvidenceLap[]): EvidenceEvent[] {
  const events: EvidenceEvent[] = [];
  const best = bestEvidenceLap(laps);
  for (const lap of laps) {
    if (best && lap.id === best.id) {
      events.push({ lap: lap.lap, text: `Session best (${formatLapTime(lap.ms)})`, icon: "check-green", highlight: true });
    }
    if (lap.pit) events.push({ lap: lap.lap, text: "Pit lane in or out lap", icon: "ring-amber" });
    if (!lap.valid) events.push({ lap: lap.lap, text: "Lap invalidated by the game", icon: "alert-red" });
  }
  for (const attempt of attempts) {
    if (attempt.role !== "player" || attempt.superseded || attempt.disposition === "completed") continue;
    if (attempt.lapNumber === null) continue;
    events.push({ lap: attempt.lapNumber, text: "Lap not completed", icon: "alert-red" });
  }
  return events.sort((a, b) => a.lap - b.lap);
}

/** Journal position of the newest published attempt; -1 without attempts. */
export function sessionRecency(attempts: EvidenceAttempt[]): number {
  let newest = -1;
  for (const attempt of attempts) {
    if (attempt.publishedSequence !== null && attempt.publishedSequence > newest) newest = attempt.publishedSequence;
  }
  return newest;
}

/* ----------------------------------------------------------- presentation */

export function typeTone(sessionType: string | null | undefined): Tone {
  if (!sessionType) return "grey";
  if (sessionType.startsWith("race") || sessionType.startsWith("sprint")) return "green";
  if (sessionType.startsWith("practice")) return "amber";
  if (sessionType === "time_trial") return "purple";
  return "grey";
}

/** Session lifecycle as a library/overview status badge. */
export function lifecycleStatus(lifecycle: string): { label: string; tone: Tone } {
  switch (lifecycle) {
    case "ended":
      return { label: "Complete", tone: "green" };
    case "interrupted":
      return { label: "Partial", tone: "amber" };
    case "active":
    case "live":
      return { label: "Live", tone: "blue" };
    default:
      return { label: lifecycle ? formatSessionType(lifecycle) : "Unknown", tone: "grey" };
  }
}

/** "Clear 26°C"; either part may be missing. */
export function conditionsLabel(context: EvidenceContext | null): string | null {
  if (!context) return null;
  const weather = formatWeather(context.weatherName);
  const air = context.airTemperatureC !== null ? `${Math.round(context.airTemperatureC)}°C` : null;
  const parts = [weather, air].filter((part): part is string => part !== null);
  return parts.length ? parts.join(" ") : null;
}

export function formatTrackLength(lengthM: number | null | undefined): string {
  return typeof lengthM === "number" && Number.isFinite(lengthM) && lengthM > 0
    ? `${Math.round(lengthM).toLocaleString("en-US")} m`
    : "—";
}

/** The v2 API has no wall-clock time, so rows are labelled with a short id. */
export function evidenceDateLabel(sessionId: string): string {
  return `Date not recorded • #${sessionId.slice(0, 8)}`;
}

export function evidenceSessionContext(session: EvidenceSession, attempts: EvidenceAttempt[]): EvidenceContext | null {
  return session.context ?? attempts.find((attempt) => attempt.context)?.context ?? null;
}

export type EvidenceLibraryFields = {
  key: string;
  title: string;
  type: { label: string; tone: Tone };
  source: { label: string; tone: Tone };
  date: string;
  meta: string[];
  laps: number;
  notes: string;
  status: { label: string; tone: Tone };
  href: string;
};

/** Library row fields for a v2 session; the loader adds track artwork. */
export function evidenceLibraryFields(
  session: EvidenceSession,
  context: EvidenceContext | null,
  lapCount: number,
): EvidenceLibraryFields {
  const conditions = conditionsLabel(context);
  return {
    key: session.id,
    title: context?.trackName ?? "Unknown circuit",
    type: { label: formatSessionType(context?.sessionType), tone: typeTone(context?.sessionType) },
    source: { label: "Game", tone: "blue" },
    date: evidenceDateLabel(session.id),
    meta: [formatGame(context?.packetFormat), ...(conditions ? [conditions] : [])],
    laps: lapCount,
    notes: "",
    status: lifecycleStatus(session.lifecycle),
    href: `/review/${encodeURIComponent(session.id)}`,
  };
}

/* ------------------------------------------------------------ progression */

export type ProgressionLap = { id: string; lap: number; ms: number; valid: boolean };

export type Progression = {
  points: ProgressionPoint[];
  yMin: number;
  yMax: number;
  yStep: number;
  xMax: number;
  /** Fixture charts follow the reference drawing rather than the lap list. */
  illustrative: boolean;
};

export const EMPTY_PROGRESSION: Progression = {
  points: [],
  yMin: 0,
  yMax: 4000,
  yStep: 1000,
  xMax: 10,
  illustrative: false,
};

/** Lap time progression: valid laps, slow neutralised/pit laps left off the chart. */
export function progressionFrom(
  laps: ProgressionLap[],
  selectedId: string,
  bestId: string | null,
): Progression {
  const timed = laps.filter((lap) => lap.valid);
  if (timed.length === 0) return EMPTY_PROGRESSION;
  const sorted = timed.map((lap) => lap.ms).sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)] ?? 0;
  // Exclude neutralised/pit laps that would flatten the racing laps.
  const plotted = timed.filter((lap) => lap.ms <= median + 6000);
  const min = Math.min(...plotted.map((lap) => lap.ms));
  const max = Math.max(...plotted.map((lap) => lap.ms));
  const yStep = max - min > 6000 ? 2000 : 1000;
  const yMin = Math.floor(min / yStep) * yStep;
  const yMax = Math.max(yMin + 4 * yStep, Math.ceil(max / yStep) * yStep);
  let personalBest = Number.POSITIVE_INFINITY;
  const points: ProgressionPoint[] = plotted.map((lap) => {
    let tone: ProgressionPoint["tone"] = "orange";
    if (lap.ms < personalBest) {
      personalBest = lap.ms;
      tone = "green";
    }
    if (lap.id === bestId) tone = "purple";
    if (lap.id === selectedId) tone = "blue";
    return { lap: lap.lap, ms: lap.ms, tone };
  });
  return {
    points,
    yMin,
    yMax,
    yStep,
    xMax: Math.max(10, ...plotted.map((lap) => lap.lap)),
    illustrative: false,
  };
}
