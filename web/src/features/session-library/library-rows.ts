import {
  bestEvidenceLap,
  evidenceDateLabel,
  type EvidenceLap,
} from "@/features/review-data/session-evidence";
import { formatDayTime, formatLapTime } from "@/features/review-data/format";
import type { LibrarySession } from "@/features/review-data/fixtures";

// Pure helpers for the Session Library rows: highlights, dates, lap pairs,
// bounded concurrency, filtering and pagination. No I/O.

export const PAGE_SIZE = 20;

/** Sessions beyond this many are not read for attempts on a cold cache (see `loadLibrary`). */
export const ATTEMPT_READ_CAP = 80;
export const ATTEMPT_READ_CONCURRENCY = 6;

export type LibraryRow = LibrarySession & {
  photo: string | null;
  /** Where the row comes from; reference rows are the design fixture. */
  kind: "reference" | "v1" | "v2";
  /** Derived notes, at most two lines. Empty for rows without attempts. */
  highlights: string[];
  /** False when the date is the "Date not recorded" placeholder. */
  dateKnown: boolean;
  /** Attempts were read, so laps and highlights are real. */
  detailed: boolean;
  /** Lap Analysis pair for `Compare best laps`: reference = fastest, target = second fastest. */
  compare: { target: string; reference: string } | null;
  /** Capture file behind an imported row, when the recordings catalog knows it. */
  download: { captureId: string; version: string } | null;
};

/** `Best 1:32.104 (lap 7)` then `3 invalid · 2 pit laps`; `No timed laps` without timed laps. */
export function highlightLines(laps: EvidenceLap[]): string[] {
  if (laps.length === 0) return ["No timed laps"];
  const best = bestEvidenceLap(laps);
  const lines = [best ? `Best ${formatLapTime(best.ms)} (lap ${best.lap})` : "No valid timed lap"];
  const invalid = laps.filter((lap) => !lap.valid).length;
  const pit = laps.filter((lap) => lap.pit).length;
  const parts: string[] = [];
  if (invalid > 0) parts.push(`${invalid} invalid`);
  if (pit > 0) parts.push(`${pit} pit ${pit === 1 ? "lap" : "laps"}`);
  if (parts.length > 0) lines.push(parts.join(" · "));
  return lines;
}

/** The two fastest valid laps, or null when fewer than two exist. */
export function comparePair(laps: EvidenceLap[]): { target: string; reference: string } | null {
  const valid = laps.filter((lap) => lap.valid).sort((a, b) => a.ms - b.ms);
  if (valid.length < 2) return null;
  return { target: valid[1].id, reference: valid[0].id };
}

/** Row date: the cached `/summary` start time when known, else the placeholder with the short id. */
export function sessionDate(sessionId: string, startedAtUtc: string | null): { text: string; known: boolean } {
  if (startedAtUtc) {
    const text = formatDayTime(startedAtUtc);
    if (text !== "—") return { text, known: true };
  }
  return { text: evidenceDateLabel(sessionId), known: false };
}

/**
 * Maps `items` with at most `limit` calls in flight, keeping input order.
 * Keeps a cold-cache Library load from fanning out one request per session.
 */
export async function mapLimit<T, R>(items: readonly T[], limit: number, task: (item: T, index: number) => Promise<R>): Promise<R[]> {
  const results = new Array<R>(items.length);
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const index = next;
      next += 1;
      results[index] = await task(items[index], index);
    }
  }
  const workers = Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, worker);
  await Promise.all(workers);
  return results;
}

/* --------------------------------------------------------------- filtering */

export type RowFilters = {
  query: string;
  track: string;
  type: string;
  source: string;
  status: string;
  oldestFirst: boolean;
  /** Include sessions with no timed laps (see `isEmptySession`). */
  showEmpty: boolean;
};

export const NO_FILTERS: RowFilters = {
  query: "",
  track: "",
  type: "",
  source: "",
  status: "",
  oldestFirst: false,
  showEmpty: false,
};

/**
 * Sessions with nothing to review: attempts were read and none is a timed lap,
 * or the circuit is unknown and no lap was recorded. Rows still processing are
 * never empty, since their laps are not counted yet.
 */
export function isEmptySession(row: LibraryRow): boolean {
  if (row.kind === "reference" || !row.detailed) return false;
  if (row.status.label.startsWith("Processing")) return false;
  return row.laps === 0;
}

export function filtersActive(filters: RowFilters): boolean {
  return Boolean(filters.query.trim() || filters.track || filters.type || filters.source || filters.status);
}

export function searchText(row: LibraryRow): string {
  return [row.title, row.type.label, row.source.label, row.date, row.notes, ...row.highlights, ...row.meta]
    .join(" ")
    .toLowerCase();
}

export function filterRows(rows: LibraryRow[], filters: RowFilters): LibraryRow[] {
  const needle = filters.query.trim().toLowerCase();
  const filtered = rows.filter((row) => {
    if (!filters.showEmpty && isEmptySession(row)) return false;
    if (filters.track && row.title !== filters.track) return false;
    if (filters.type && row.type.label !== filters.type) return false;
    if (filters.source && row.source.label !== filters.source) return false;
    if (filters.status && !row.status.label.startsWith(filters.status)) return false;
    return needle === "" || searchText(row).includes(needle);
  });
  return filters.oldestFirst ? [...filtered].reverse() : filtered;
}

/* -------------------------------------------------------------- pagination */

/** Rows to render for `shown` (a multiple of the page size) and the count line under the list. */
export function paginate<T>(rows: readonly T[], shown: number): { visible: T[]; remaining: number; countLine: string; more: string } {
  const visibleCount = Math.min(rows.length, Math.max(PAGE_SIZE, shown));
  const remaining = rows.length - visibleCount;
  return {
    visible: rows.slice(0, visibleCount),
    remaining,
    countLine: `Showing ${visibleCount} of ${rows.length} ${rows.length === 1 ? "session" : "sessions"}`,
    more: `Show ${Math.min(PAGE_SIZE, remaining)} more`,
  };
}
