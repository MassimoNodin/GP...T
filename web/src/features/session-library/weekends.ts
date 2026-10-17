import { formatDay, formatLapTime } from "@/features/review-data/format";
import type { LibraryRow } from "./library-rows";

// Groups library rows into race weekends. The game links a weekend's sessions
// with an identifier the engine does not keep, so rows share a weekend when
// they have the same circuit, game and either engine run (live sessions) or
// calendar day (imports). Pure; no I/O.

export type Weekend = {
  key: string;
  title: string;
  track: string;
  photo: string | null;
  game: string;
  /** Sessions in weekend order: practice, qualifying, sprint, race. */
  rows: LibraryRow[];
  laps: number;
  best: { row: LibraryRow; ms: number } | null;
};

export type LibraryEntry = { kind: "row"; row: LibraryRow } | { kind: "weekend"; weekend: Weekend };

const SESSION_ORDER = [
  "Practice 1",
  "Practice 2",
  "Practice 3",
  "Short Practice",
  "Qualifying 1",
  "Qualifying 2",
  "Qualifying 3",
  "Short Qualifying",
  "One Shot Qualifying",
  "Sprint Shootout 1",
  "Sprint Shootout 2",
  "Sprint Shootout 3",
  "Short Sprint Shootout",
  "One Shot Sprint Shootout",
  "Sprint",
  "Race",
  "Race 2",
  "Race 3",
];

const SHORT_LABELS: Record<string, string> = {
  "Practice 1": "P1",
  "Practice 2": "P2",
  "Practice 3": "P3",
  "Short Practice": "SP",
  "Qualifying 1": "Q1",
  "Qualifying 2": "Q2",
  "Qualifying 3": "Q3",
  "Short Qualifying": "SQ",
  "One Shot Qualifying": "OSQ",
  "Sprint Shootout 1": "SQ1",
  "Sprint Shootout 2": "SQ2",
  "Sprint Shootout 3": "SQ3",
  Sprint: "Sprint",
  Race: "Race",
  "Race 2": "Race 2",
  "Race 3": "Race 3",
  "Time Trial": "TT",
};

/** "Qualifying 2" -> "Q2"; unknown labels keep their initials. */
export function shortSessionLabel(label: string): string {
  return (
    SHORT_LABELS[label] ??
    label
      .split(/\s+/)
      .map((word) => (/^\d+$/.test(word) ? word : word[0]?.toUpperCase() ?? ""))
      .join("")
  );
}

function orderOf(label: string): number {
  const index = SESSION_ORDER.indexOf(label);
  return index === -1 ? SESSION_ORDER.length : index;
}

/** Rows without a group key (the reference design, time trials) are never grouped. */
export function weekendKey(row: LibraryRow): string | null {
  if (!row.groupKey || row.type.label === "Time Trial") return null;
  return row.groupKey;
}

/**
 * Library entries in list order: a weekend appears where its first session
 * did, and a weekend of one session stays a plain row.
 */
export function groupWeekends(rows: readonly LibraryRow[]): LibraryEntry[] {
  const groups = new Map<string, LibraryRow[]>();
  for (const row of rows) {
    const key = weekendKey(row);
    if (!key) continue;
    const members = groups.get(key);
    if (members) members.push(row);
    else groups.set(key, [row]);
  }
  const emitted = new Set<string>();
  const entries: LibraryEntry[] = [];
  for (const row of rows) {
    const key = weekendKey(row);
    const members = key ? groups.get(key) : undefined;
    if (!key || !members || members.length < 2) {
      entries.push({ kind: "row", row });
      continue;
    }
    if (emitted.has(key)) continue;
    emitted.add(key);
    entries.push({ kind: "weekend", weekend: buildWeekend(key, members) });
  }
  return entries;
}

function buildWeekend(key: string, members: LibraryRow[]): Weekend {
  const ordered = members
    .map((row, index) => ({ row, index }))
    .sort((a, b) => orderOf(a.row.type.label) - orderOf(b.row.type.label) || a.index - b.index)
    .map((entry) => entry.row);
  let best: Weekend["best"] = null;
  for (const row of ordered) {
    if (typeof row.bestMs === "number" && (!best || row.bestMs < best.ms)) best = { row, ms: row.bestMs };
  }
  const first = ordered[0];
  return {
    key,
    title: first.title,
    track: first.track,
    photo: first.photo,
    game: first.meta[0] ?? "",
    rows: ordered,
    laps: ordered.reduce((sum, row) => sum + (row.detailed ? row.laps : 0), 0),
    best,
  };
}

/** "Best 1:29.049 in Q2" for the weekend summary. */
export function weekendBestLine(weekend: Weekend): string | null {
  if (!weekend.best) return null;
  return `Best ${formatLapTime(weekend.best.ms)} in ${shortSessionLabel(weekend.best.row.type.label)}`;
}

/** Dates of a weekend's sessions when known: one day, or "first – last". */
export function weekendDateLabel(weekend: Weekend, local = false): string {
  const known = weekend.rows
    .filter((row) => row.dateKnown)
    .map((row) => (row.startedAtUtc ? formatDay(row.startedAtUtc, local) : row.date.split(",")[0]));
  const days = [...new Set(known)];
  if (days.length === 0) return "Dates not recorded";
  return days.length === 1 ? days[0] : `${days[0]} – ${days[days.length - 1]}`;
}

