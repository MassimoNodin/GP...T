import type { RecordingCatalogSourceRecord, SessionRecord } from "@/lib/api";
import {
  cachedSummaryDate,
  fetchAcquisition,
  fetchEvidenceAttempts,
  fetchEvidenceSessions,
  fetchRecordingSources,
  fetchSummary,
  fetchSessions,
  telemetryFooter,
  telemetryStatus,
} from "@/features/review-data/backend";
import {
  evidenceLaps,
  evidenceLibraryFields,
  evidenceSessionContext,
  sessionRecency,
  typeTone,
  bestEvidenceLap,
  type EvidenceContext,
  type EvidenceSession,
} from "@/features/review-data/session-evidence";
import { libraryFixture, shellFixture } from "@/features/review-data/fixtures";
import { formatDayTime, formatGame, formatSessionType, formatWeather } from "@/features/review-data/format";
import type { ShellFooter, ShellStatus } from "@/features/ui-foundation/AppShell";
import type { Tone } from "@/features/ui-foundation/primitives";
import { trackKeyFromName } from "@/features/ui-foundation/TrackMap";
import { trackPhotoUrl } from "@/features/ui-foundation/track-photo-url";
import {
  restoreImports,
  type FolderSource,
  type ImportItem,
} from "./imports/import-state";
import {
  ATTEMPT_READ_CAP,
  ATTEMPT_READ_CONCURRENCY,
  comparePair,
  highlightLines,
  mapLimit,
  sessionDate,
  type LibraryRow,
} from "./library-rows";

export type { LibraryRow } from "./library-rows";

export type LibraryImports = {
  /** Queued, running and recently failed imports found on the engine. */
  restored: ImportItem[];
  /** Captures in the recordings folder that were never imported; null when unreadable. */
  folder: FolderSource[] | null;
  /** Total never-imported captures (the list above is one page). */
  folderTotal: number;
};

export type LibraryModel = {
  /**
   * `live`: the engine answered. `reference`: it is entirely unreachable, so the
   * rows are the design's example sessions and the screen says so.
   */
  mode: "live" | "reference";
  /** Importing is possible (the engine answered). */
  live: boolean;
  status: ShellStatus;
  footer: ShellFooter;
  header: { items: string[]; trailing?: string };
  sessions: LibraryRow[];
  /** Readable-but-incomplete notices shown above the list. */
  warnings: string[];
  /** The engine answered but neither session list could be read. */
  sessionsUnreadable: boolean;
  imports: LibraryImports;
};

/** Header pill when the backend cannot be reached or its status cannot be read. */
export const BACKEND_UNAVAILABLE: ShellStatus = { tone: "red", label: "Backend unavailable" };
export const ENGINE_OFFLINE_FOOTER: ShellFooter = {
  tone: "red",
  label: "Engine offline",
  lines: ["Start the local engine", ""],
};

const UNREAD_NOTE = "Lap details load when the session is opened";
const NO_IMPORTS: LibraryImports = { restored: [], folder: null, folderTotal: 0 };

function statusOf(record: SessionRecord): { label: string; tone: Tone } {
  switch (record.run_status) {
    case "complete":
      return { label: "Complete", tone: "green" };
    case "running":
    case "queued":
    case "processing":
      return { label: "Processing", tone: "blue" };
    case "partial":
      return { label: "Partial", tone: "amber" };
    default:
      return { label: "Unavailable", tone: "red" };
  }
}

type CaptureLinks = Map<string, { captureId: string; version: string }>;

function toRow(record: SessionRecord, captures: CaptureLinks): LibraryRow {
  const context = record.context;
  const trackName = context?.track_name ?? null;
  const track = trackKeyFromName(trackName);
  const weather = formatWeather(context?.weather_name);
  const status = statusOf(record);
  return {
    key: record.session_key,
    title: trackName ?? "Unknown circuit",
    track,
    type: { label: formatSessionType(context?.session_type), tone: typeTone(context?.session_type) },
    source: { label: "Game", tone: "blue" },
    date: formatDayTime(record.started_at_utc),
    meta: [formatGame(record.packet_format), ...(weather ? [weather] : [])],
    laps: record.lap_attempts,
    notes: "",
    status,
    href: status.label === "Complete" ? `/review/${encodeURIComponent(record.session_key)}` : null,
    photo: trackPhotoUrl(track),
    kind: "v1",
    groupKey: `v1|${trackName ?? ""}|${record.packet_format}|${record.started_at_utc.slice(0, 10)}`,
    highlights: record.lap_attempts === 0 ? ["No timed laps"] : [],
    dateKnown: true,
    startedAtUtc: record.started_at_utc,
    detailed: true,
    compare: null,
    download: captures.get(record.run_id) ?? null,
  };
}

const DATE_READ_TIMEOUT_MS = 4_000;

/**
 * Capture start time of each session. The sessions list carries none, so it
 * comes from `/summary` (a small document), read once per session with a few
 * requests in flight and remembered in memory; later loads cost nothing.
 * Sessions beyond `ATTEMPT_READ_CAP` or whose summary cannot be read keep the
 * "Date not recorded" placeholder.
 */
async function captureDates(sessions: EvidenceSession[]): Promise<Map<string, string>> {
  const dates = new Map<string, string>();
  const missing: EvidenceSession[] = [];
  for (const session of sessions.slice(0, ATTEMPT_READ_CAP)) {
    const known = cachedSummaryDate(session.id);
    if (known) dates.set(session.id, known);
    else missing.push(session);
  }
  await mapLimit(missing, ATTEMPT_READ_CONCURRENCY, async (session) => {
    const read = await fetchSummary(session.id, { timeoutMs: DATE_READ_TIMEOUT_MS });
    if (read.state === "ok" && read.value.startedAtUtc) dates.set(session.id, read.value.startedAtUtc);
  });
  return dates;
}

/**
 * v2 sessions the engine acquired from the game, newest journal position first.
 *
 * Recency needs every session's attempts, which on a cold cache is one request
 * per session (up to 12 pages each). Reads are therefore capped at
 * `ATTEMPT_READ_CAP` sessions with a few requests in flight; sessions beyond
 * the cap are listed after the read ones, without laps or highlights, and
 * stay openable.
 */
async function evidenceRows(sessions: EvidenceSession[]): Promise<LibraryRow[]> {
  const dates = await captureDates(sessions);
  const toRead = sessions.slice(0, ATTEMPT_READ_CAP);
  const results = await mapLimit(toRead, ATTEMPT_READ_CONCURRENCY, (session) => fetchEvidenceAttempts(session.id));
  const read = toRead.map((session, index) => {
    const attempts = results[index]?.attempts ?? [];
    const laps = evidenceLaps(attempts);
    const context = evidenceSessionContext(session, attempts);
    const track = trackKeyFromName(context?.trackName);
    const fields = evidenceLibraryFields(session, context, laps.length);
    const date = sessionDate(session.id, dates.get(session.id) ?? null);
    const row: LibraryRow = {
      ...fields,
      date: date.text,
      track,
      photo: trackPhotoUrl(track),
      kind: "v2",
      bestMs: bestEvidenceLap(laps)?.ms ?? null,
      bestLapId: bestEvidenceLap(laps)?.id ?? null,
      groupKey: v2GroupKey(session, context),
      notes: highlightLines(laps).join(" "),
      highlights: highlightLines(laps),
      dateKnown: date.known,
      startedAtUtc: date.startedAtUtc,
      detailed: true,
      compare: comparePair(laps),
      download: null,
    };
    return { recency: sessionRecency(attempts), row };
  });
  read.sort((a, b) => b.recency - a.recency);
  const unread = sessions.slice(ATTEMPT_READ_CAP).map((session): LibraryRow => {
    const context = session.context;
    const track = trackKeyFromName(context?.trackName);
    const fields = evidenceLibraryFields(session, context, 0);
    const date = sessionDate(session.id, dates.get(session.id) ?? null);
    return {
      ...fields,
      date: date.text,
      track,
      photo: trackPhotoUrl(track),
      kind: "v2",
      groupKey: v2GroupKey(session, context),
      notes: "",
      highlights: [UNREAD_NOTE],
      dateKnown: date.known,
      startedAtUtc: date.startedAtUtc,
      detailed: false,
      compare: null,
      download: null,
    };
  });
  return [...read.map((entry) => entry.row), ...unread];
}

/** Live sessions share a weekend when circuit, game and engine run match. */
function v2GroupKey(session: EvidenceSession, context: EvidenceContext | null): string | null {
  if (!context?.trackName || !session.generation) return null;
  return `v2|${context.trackName}|${context.packetFormat ?? ""}|${session.generation}`;
}

function connectionHeader(state: string): LibraryModel["header"] {
  return state === "receiving"
    ? libraryFixture.header
    : { items: ["Game not connected"], trailing: libraryFixture.header.trailing };
}

/** The design's example sessions, used only while the engine is unreachable. */
function referenceRows(): LibraryRow[] {
  return libraryFixture.sessions.map((session) => ({
    ...session,
    photo: trackPhotoUrl(session.track),
    kind: "reference",
    highlights: [],
    dateKnown: true,
    startedAtUtc: null,
    detailed: true,
    compare: null,
    download: null,
  }));
}

const FOLDER_PAGE = 50;
const RESTORE_PAGE = 20;
const CATALOG_COMPLETE_PAGE = 50;

/**
 * Reads the recordings catalog once for the import flow: in-flight and recently
 * failed jobs, never-imported captures, and the capture behind each imported
 * session (matched by run id) for `Download capture`.
 */
async function readImports(now: number): Promise<{ imports: LibraryImports; captures: CaptureLinks }> {
  const [queued, running, failed, interrupted, never, complete] = await Promise.all([
    fetchRecordingSources({ limit: RESTORE_PAGE, latestJobStatus: "queued" }),
    fetchRecordingSources({ limit: RESTORE_PAGE, latestJobStatus: "running" }),
    fetchRecordingSources({ limit: RESTORE_PAGE, latestJobStatus: "failed" }),
    fetchRecordingSources({ limit: RESTORE_PAGE, latestJobStatus: "interrupted" }),
    fetchRecordingSources({ limit: FOLDER_PAGE, latestJobStatus: "none", availability: "available" }),
    fetchRecordingSources({ limit: CATALOG_COMPLETE_PAGE, latestJobStatus: "complete", availability: "available" }),
  ]);
  const restoredSources: RecordingCatalogSourceRecord[] = [queued, running, failed, interrupted].flatMap(
    (page) => page?.items ?? [],
  );
  const restored = restoreImports(restoredSources, now).items;
  const folder = never ? restoreImports(never.items, now).folder : null;
  const captures: CaptureLinks = new Map();
  for (const source of complete?.items ?? []) {
    if (source.latest_job_run_id && source.available) {
      captures.set(source.latest_job_run_id, { captureId: source.capture_id, version: source.download_version });
    }
  }
  return { imports: { restored, folder, folderTotal: never?.total_count ?? 0 }, captures };
}

export async function loadLibrary(now: number = Date.now()): Promise<LibraryModel> {
  const [sessions, evidence, acquisition] = await Promise.all([
    fetchSessions(),
    fetchEvidenceSessions(),
    fetchAcquisition(),
  ]);

  // Nothing answered: the engine is not running. Only now are the design's
  // example sessions shown, and the screen discloses it.
  if (sessions === null && evidence === null && acquisition === null) {
    return {
      mode: "reference",
      live: false,
      status: BACKEND_UNAVAILABLE,
      footer: ENGINE_OFFLINE_FOOTER,
      header: { items: ["Local engine offline"] },
      sessions: referenceRows(),
      warnings: [],
      sessionsUnreadable: false,
      imports: NO_IMPORTS,
    };
  }

  const status = acquisition ? telemetryStatus(acquisition) : BACKEND_UNAVAILABLE;
  const baseFooter = acquisition
    ? telemetryFooter(acquisition, shellFixture.telemetryFooter)
    : { tone: "red" as const, label: BACKEND_UNAVAILABLE.label, lines: ["F1", ""] };
  const acquired = evidence ?? [];
  const imported = sessions ?? [];
  const warnings: string[] = [];
  if (evidence === null && sessions !== null) warnings.push("Sessions recorded from the game could not be read.");
  if (sessions === null && evidence !== null) warnings.push("Imported captures could not be read.");

  const { imports, captures } = await readImports(now);
  // The v2 API records no wall-clock time, so acquired sessions (ordered by
  // journal position) come first and dated imports follow, newest first.
  // Dates come from each session's `/summary` (see `captureDates`).
  const rows = [...(await evidenceRows(acquired)), ...imported.map((record) => toRow(record, captures))];
  // The footer names the game of the newest session instead of the reference platform.
  const game = rows[0]?.meta[0] ?? "F1";
  return {
    mode: "live",
    live: true,
    status,
    footer: { ...baseFooter, lines: [game, baseFooter.lines[1] ?? ""] },
    header: acquisition ? connectionHeader(acquisition.state) : { items: ["Local engine connected"] },
    sessions: rows,
    warnings,
    sessionsUnreadable: sessions === null && evidence === null,
    imports,
  };
}
