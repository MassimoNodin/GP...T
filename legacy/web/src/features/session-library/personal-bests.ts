import type { LibraryRow } from "./library-rows";

// Personal bests per circuit from the library rows: the fastest lap overall,
// per kind of session, and the session-by-session trend. Pure; no I/O.

export type SessionFamily = "Practice" | "Qualifying" | "Sprint" | "Race" | "Time Trial" | "Other";

export const FAMILY_ORDER: SessionFamily[] = ["Practice", "Qualifying", "Sprint", "Race", "Time Trial", "Other"];

export type LapRef = {
  ms: number;
  row: LibraryRow;
  /** Lap Analysis for that lap, or the session overview when its lap id is unknown. */
  href: string | null;
};

export type TrackBests = {
  key: string;
  title: string;
  track: string;
  photo: string | null;
  game: string;
  sessions: number;
  best: LapRef;
  byFamily: Array<{ family: SessionFamily; best: LapRef; sessions: number }>;
  /** Each session's best, oldest first (library order is newest first). */
  trend: LapRef[];
};

export function sessionFamily(label: string): SessionFamily {
  if (label.startsWith("Practice") || label === "Short Practice") return "Practice";
  if (label.includes("Shootout")) return "Qualifying";
  if (label.includes("Qualifying")) return "Qualifying";
  if (label === "Sprint") return "Sprint";
  if (label.startsWith("Race")) return "Race";
  if (label === "Time Trial") return "Time Trial";
  return "Other";
}

function lapRef(row: LibraryRow): LapRef | null {
  if (typeof row.bestMs !== "number") return null;
  const href = row.href
    ? row.bestLapId
      ? `${row.href}/laps?target=${encodeURIComponent(row.bestLapId)}`
      : row.href
    : null;
  return { ms: row.bestMs, row, href };
}

const faster = (a: LapRef | null, b: LapRef) => (a === null || b.ms < a.ms ? b : a);

/** One entry per circuit and game with at least one timed lap, fastest circuits' order kept from the library. */
export function personalBests(rows: readonly LibraryRow[]): TrackBests[] {
  const groups = new Map<string, LibraryRow[]>();
  for (const row of rows) {
    if (row.kind === "reference" || row.title === "Unknown circuit") continue;
    const key = `${row.title}|${row.meta[0] ?? ""}`;
    const members = groups.get(key);
    if (members) members.push(row);
    else groups.set(key, [row]);
  }
  const result: TrackBests[] = [];
  for (const [key, members] of groups) {
    const refs = members.map(lapRef).filter((ref): ref is LapRef => ref !== null);
    if (refs.length === 0) continue;
    const best = refs.reduce<LapRef | null>(faster, null) as LapRef;
    const families = new Map<SessionFamily, { best: LapRef | null; sessions: number }>();
    for (const ref of refs) {
      const family = sessionFamily(ref.row.type.label);
      const entry = families.get(family) ?? { best: null, sessions: 0 };
      families.set(family, { best: faster(entry.best, ref), sessions: entry.sessions + 1 });
    }
    const first = members[0];
    result.push({
      key,
      title: first.title,
      track: first.track,
      photo: first.photo,
      game: first.meta[0] ?? "",
      sessions: refs.length,
      best,
      byFamily: FAMILY_ORDER.flatMap((family) => {
        const entry = families.get(family);
        return entry?.best ? [{ family, best: entry.best, sessions: entry.sessions }] : [];
      }),
      trend: [...refs].reverse(),
    });
  }
  return result;
}

/** The personal best after each session: a line that only ever moves down. */
export function runningBest(trend: readonly LapRef[]): number[] {
  let best = Number.POSITIVE_INFINITY;
  return trend.map((ref) => (best = Math.min(best, ref.ms)));
}

/** Points for a small trend chart, x and y in 0..1 (y = 0 is the fastest). */
export function trendPoints(values: readonly number[], scale: readonly number[] = values): Array<[number, number]> {
  if (values.length === 0) return [];
  const min = Math.min(...scale);
  const max = Math.max(...scale);
  const span = max - min;
  return values.map((ms, index) => [
    values.length === 1 ? 0.5 : index / (values.length - 1),
    span === 0 ? 0.5 : (ms - min) / span,
  ]);
}
