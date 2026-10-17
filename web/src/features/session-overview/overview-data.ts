import type { PlayerCarSetupContext, PlayerCarSetupSnapshot } from "@/lib/api";
import type { ReadResult } from "@/features/review-data/backend";
import type { EnrichedStint, LapEnrichment } from "@/features/review-data/enrichment";
import type { ProgressionPoint } from "@/features/review-data/fixtures";
import {
  formatDayTime,
  formatDelta,
  formatDuration,
  formatGame,
  formatLapTime,
  formatSessionType,
  formatWeather,
} from "@/features/review-data/format";
import { humanReason } from "@/features/review-data/reasons";
import { formatTrackLength, progressionFrom } from "@/features/review-data/session-evidence";
import type { SessionSummary, SummaryEvent, WeatherPoint } from "@/features/review-data/summary";
import type { WeatherKind } from "@/features/ui-foundation/icons";
import type {
  Compound,
  DeferredSection,
  EventCategory,
  EventIcon,
  HeroField,
  HeroPart,
  OverviewContext,
  OverviewEvent,
  OverviewLap,
  OverviewModel,
  OverviewStint,
  SetupView,
  SummaryItem,
} from "./types";

// Pure helpers behind the Session Overview. Nothing here performs I/O or
// touches React, so every rule (stint statistics, event merging, derived
// summary, hero fields, lap filters, compare defaults, URL state) is covered
// by `scripts/session-overview.test.mjs`.

/* ------------------------------------------------------------- compounds */

const COMPOUND_NAMES: Record<Compound, string> = {
  soft: "Soft",
  medium: "Medium",
  hard: "Hard",
  inter: "Intermediate",
  wet: "Wet",
  super_soft: "Super Soft",
  dry: "Dry",
  unknown: "Unknown",
};

const COMPOUND_COLORS: Record<Compound, string> = {
  soft: "var(--stint-soft)",
  super_soft: "var(--stint-soft)",
  medium: "var(--stint-medium)",
  hard: "var(--stint-hard)",
  dry: "var(--stint-hard)",
  inter: "var(--stint-inter)",
  wet: "var(--stint-wet)",
  unknown: "var(--grey-soft)",
};

export function compoundName(compound: Compound): string {
  return COMPOUND_NAMES[compound];
}

export function compoundColor(compound: Compound): string {
  return COMPOUND_COLORS[compound];
}

// Visual compound ids from the game's car status packet.
const VISUAL_IDS: Record<number, Compound> = { 16: "soft", 17: "medium", 18: "hard", 7: "inter", 8: "wet" };

/** Maps the game's visual compound label (or id) to the colour family. */
export function compoundFromTyre(label: string | null, visualId: number | null = null): Compound {
  const text = (label ?? "").toLowerCase();
  if (text.includes("super") && text.includes("soft")) return "super_soft";
  if (text.includes("soft")) return "soft";
  if (text.includes("medium")) return "medium";
  if (text.includes("hard")) return "hard";
  if (text.includes("inter")) return "inter";
  if (text.includes("wet")) return "wet";
  if (text.includes("dry")) return "dry";
  if (visualId !== null && VISUAL_IDS[visualId]) return VISUAL_IDS[visualId];
  return "unknown";
}

/* ------------------------------------------------------------ enrichment */

const NO_SECTORS = "Sector times were not reported for this lap.";
const NO_TYRE = "Tyre data was not recorded for this lap.";

function stintFrom(stint: EnrichedStint): OverviewStint {
  const compound = compoundFromTyre(stint.visualLabel, stint.visualCompound);
  return {
    index: stint.index,
    compound,
    label: stint.visualLabel ?? compoundName(compound),
    actual: stint.actualLabel,
    startLap: stint.startLap,
    endLap: stint.endLap,
    lapCount: stint.lapCount,
    startAge: stint.startAge,
    endAge: stint.endAge,
  };
}

export type AppliedEnrichment = {
  laps: OverviewLap[];
  stints: OverviewStint[];
  /** Plain-language reason the scan stopped early, or null when it read everything. */
  partialReason: string | null;
};

/** Merges `/laps` sectors and tyres into the lap list (joined on attempt revision id). */
export function applyEnrichment(laps: OverviewLap[], enrichment: LapEnrichment): AppliedEnrichment {
  const byRevision = new Map(enrichment.laps.map((lap) => [lap.revisionId, lap]));
  const partialReason = enrichment.truncated
    ? humanReason(enrichment.truncation) || "Only part of this session could be read."
    : null;
  const missing = partialReason ?? "";
  const merged = laps.map((lap) => {
    const found = lap.revisionId ? byRevision.get(lap.revisionId) : undefined;
    if (!found) {
      return {
        ...lap,
        sectorsReason: lap.sectors ? null : missing || NO_SECTORS,
        tyreReason: lap.tyre ? null : missing || NO_TYRE,
      };
    }
    const compound = found.tyre ? compoundFromTyre(found.tyre.visualLabel, found.tyre.visualCompound) : null;
    return {
      ...lap,
      sectors: found.sectorsMs ?? lap.sectors,
      sectorsReason: found.sectorsMs || lap.sectors ? null : humanReason(found.sectorsReason) || NO_SECTORS,
      tyre:
        found.tyre && compound
          ? {
              compound,
              label: found.tyre.visualLabel ?? compoundName(compound),
              actual: found.tyre.actualLabel,
              ageLaps: found.tyre.ageLaps,
            }
          : lap.tyre,
      tyreReason: found.tyre || lap.tyre ? null : humanReason(found.tyreReason) || NO_TYRE,
      pit: lap.pit || found.pit,
      startObserved: lap.startObserved ?? found.startObserved,
    };
  });
  return {
    laps: merged,
    stints: [...enrichment.stints].sort((a, b) => a.index - b.index).map(stintFrom),
    partialReason,
  };
}

/** Result of a section read by the server loader, as a section the page can render. */
export function sectionFromRead<T>(read: ReadResult<T>): DeferredSection<T> {
  switch (read.state) {
    case "ok":
      return { state: "loaded", value: read.value, partialReason: null };
    case "busy":
    case "timeout":
      return { state: "deferred" };
    case "unavailable":
      return { state: "unavailable", reason: humanReason(read.reason) || "This section is not available." };
    default:
      return { state: "unavailable", reason: humanReason("local_engine_unavailable") };
  }
}

/* ---------------------------------------------------------------- stints */

export type StintStats = {
  best: number | null;
  average: number | null;
  /** Seconds per lap; null with fewer than four clean laps. */
  degradation: number | null;
  cleanLaps: number;
  /** Lap-time spread of the clean laps; null with fewer than three. */
  consistency: Consistency | null;
};

export type ConsistencyRating = "Very consistent" | "Consistent" | "Variable" | "Inconsistent";

export type Consistency = {
  /** Standard deviation of the counted laps, in milliseconds. */
  spreadMs: number;
  laps: number;
  rating: ConsistencyRating;
};

/** Laps more than this much slower than the median are mistakes or traffic, not spread. */
const CONSISTENCY_OUTLIER = 1.03;

/**
 * How evenly the clean laps were driven: the standard deviation of their
 * times, ignoring laps over 3% off the median (a mistake is not inconsistency).
 * Needs at least three laps.
 */
export function consistencyOf(laps: readonly OverviewLap[]): Consistency | null {
  const clean = laps.filter(isCleanLap).map((lap) => lap.ms);
  if (clean.length < 3) return null;
  const limit = median(clean) * CONSISTENCY_OUTLIER;
  const counted = clean.filter((ms) => ms <= limit);
  if (counted.length < 3) return null;
  const mean = counted.reduce((sum, ms) => sum + ms, 0) / counted.length;
  const spreadMs = Math.sqrt(counted.reduce((sum, ms) => sum + (ms - mean) ** 2, 0) / counted.length);
  const rating: ConsistencyRating =
    spreadMs <= 200 ? "Very consistent" : spreadMs <= 500 ? "Consistent" : spreadMs <= 1000 ? "Variable" : "Inconsistent";
  return { spreadMs, laps: counted.length, rating };
}

/** "±0.214 s" */
export function formatSpread(consistency: Consistency): string {
  return `±${(consistency.spreadMs / 1000).toFixed(3)} s`;
}

/** Valid, non-pit laps that began at the start line: the laps pace is judged on. */
export function isCleanLap(lap: OverviewLap): boolean {
  return lap.valid && !lap.pit && lap.startObserved !== false && lap.ms > 0;
}

/** Least-squares slope of y against x; null when x has no spread. */
export function linearSlope(points: Array<[number, number]>): number | null {
  const n = points.length;
  if (n < 2) return null;
  const meanX = points.reduce((sum, [x]) => sum + x, 0) / n;
  const meanY = points.reduce((sum, [, y]) => sum + y, 0) / n;
  let numerator = 0;
  let denominator = 0;
  for (const [x, y] of points) {
    numerator += (x - meanX) * (y - meanY);
    denominator += (x - meanX) ** 2;
  }
  return denominator === 0 ? null : numerator / denominator;
}

export function lapsInStint(stint: OverviewStint, laps: OverviewLap[]): OverviewLap[] {
  return laps.filter((lap) => lap.lap >= stint.startLap && lap.lap <= stint.endLap);
}

export function stintStats(stint: OverviewStint, laps: OverviewLap[]): StintStats {
  const clean = lapsInStint(stint, laps).filter(isCleanLap);
  if (clean.length === 0) return { best: null, average: null, degradation: null, cleanLaps: 0, consistency: null };
  const slope = clean.length >= 4 ? linearSlope(clean.map((lap) => [lap.lap, lap.ms])) : null;
  return {
    best: Math.min(...clean.map((lap) => lap.ms)),
    average: clean.reduce((sum, lap) => sum + lap.ms, 0) / clean.length,
    degradation: slope === null ? null : slope / 1000,
    cleanLaps: clean.length,
    consistency: consistencyOf(clean),
  };
}

export function degradationTone(secondsPerLap: number): "red" | "green" | "neutral" {
  if (secondsPerLap > 0.05) return "red";
  if (secondsPerLap < 0) return "green";
  return "neutral";
}

/** "+0.021 s/lap" */
export function formatDegradation(secondsPerLap: number): string {
  const sign = secondsPerLap > 0 ? "+" : secondsPerLap < 0 ? "-" : "";
  return `${sign}${Math.abs(secondsPerLap).toFixed(3)} s/lap`;
}

/** [12, 13, 14, 20] -> "12–14, 20" */
export function formatLapRanges(numbers: number[]): string {
  const sorted = [...new Set(numbers)].sort((a, b) => a - b);
  const parts: string[] = [];
  for (let i = 0; i < sorted.length; ) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j += 1;
    parts.push(j === i ? String(sorted[i]) : `${sorted[i]}–${sorted[j]}`);
    i = j + 1;
  }
  return parts.join(", ");
}

/** Timed laps no stint covers: "Tyre data is missing for laps …". */
export function lapsWithoutStint(laps: OverviewLap[], stints: OverviewStint[]): number[] {
  return laps
    .filter((lap) => !stints.some((stint) => lap.lap >= stint.startLap && lap.lap <= stint.endLap))
    .map((lap) => lap.lap);
}

export type StintSegment =
  | { kind: "stint"; stint: OverviewStint; laps: number }
  | { kind: "gap"; from: number; to: number; laps: number };

export function totalLapsOf(laps: OverviewLap[], stints: OverviewStint[]): number {
  return Math.max(0, ...laps.map((lap) => lap.lap), ...stints.map((stint) => stint.endLap));
}

/**
 * Bar segments over lap positions 1..total; `laps` is the width in lap units, matching the axis ticks.
 * Laps between two stints are absorbed: the stints meet at the pit stop, or halfway across the gap when
 * no pit lap is known. Only laps before the first stint or after the last one stay grey gaps.
 */
export function stintSegments(stints: OverviewStint[], total: number, pits: PitMarker[] = []): StintSegment[] {
  const ordered = stints.filter((stint) => stint.endLap >= 1 && stint.startLap <= total).sort((a, b) => a.startLap - b.startLap);
  const segments: StintSegment[] = [];
  let cursor = 1;
  ordered.forEach((stint, i) => {
    if (i === 0 && stint.startLap > 1) {
      segments.push({ kind: "gap", from: 1, to: stint.startLap - 1, laps: stint.startLap - 1 });
      cursor = stint.startLap;
    }
    const next = ordered[i + 1];
    let end = Math.min(stint.endLap, total);
    if (next) {
      const pit = pits.find((marker) => marker.lap >= stint.endLap && marker.lap <= next.startLap);
      end = pit ? pit.lap : (stint.endLap + next.startLap) / 2;
    }
    segments.push({ kind: "stint", stint, laps: Math.max(end - cursor, 0) });
    cursor = end;
  });
  if (cursor < total) segments.push({ kind: "gap", from: Math.ceil(cursor), to: total, laps: total - cursor });
  return segments;
}

/** Axis ticks of the stint bar: lap 1, every tenth lap, and the last lap. */
export function stintTicks(total: number): number[] {
  if (total <= 1) return [1];
  return [1, ...Array.from({ length: Math.floor(total / 10) }, (_, i) => (i + 1) * 10).filter((t) => t < total - 4), total];
}

export type PitMarker = { lap: number };

/** One marker per stint boundary that has a pit lap among the end/start laps. */
export function pitMarkers(stints: OverviewStint[], laps: OverviewLap[]): PitMarker[] {
  const ordered = [...stints].sort((a, b) => a.startLap - b.startLap);
  const markers: PitMarker[] = [];
  for (let i = 0; i + 1 < ordered.length; i += 1) {
    const from = ordered[i].endLap;
    const to = ordered[i + 1].startLap;
    const pit = laps.filter((lap) => lap.pit && lap.lap >= from && lap.lap <= to).sort((a, b) => a.lap - b.lap)[0];
    if (pit) markers.push({ lap: pit.lap });
  }
  return markers;
}

/** "Medium 42 laps" description of the stint bar for assistive technology. */
export function stintBarLabel(stints: OverviewStint[], pits: PitMarker[]): string {
  const parts = stints.map((stint) => `${stint.label} ${stint.lapCount} ${stint.lapCount === 1 ? "lap" : "laps"}`);
  const pitText = pits.length ? `. Pit stops on ${pits.length === 1 ? "lap" : "laps"} ${pits.map((pit) => pit.lap).join(", ")}` : "";
  return `${parts.join(", ")}${pitText}`;
}

/* ------------------------------------------------------------------ laps */

export type LapFilter = "all" | "valid" | "invalid" | "pit";

export function lapCounts(laps: OverviewLap[]): Record<LapFilter, number> {
  return {
    all: laps.length,
    valid: laps.filter((lap) => lap.valid).length,
    invalid: laps.filter((lap) => !lap.valid).length,
    pit: laps.filter((lap) => lap.pit).length,
  };
}

/** Laps matching the chip, optionally without in/out laps (pit laps and laps that did not start on the line). */
export function filterLaps(laps: OverviewLap[], filter: LapFilter, hideInOut: boolean): OverviewLap[] {
  return laps.filter((lap) => {
    if (hideInOut && (lap.pit || lap.startObserved === false)) return false;
    if (filter === "valid") return lap.valid;
    if (filter === "invalid") return !lap.valid;
    if (filter === "pit") return lap.pit;
    return true;
  });
}

export const compareLapNumber = (a: OverviewLap, b: OverviewLap) => a.lap - b.lap;
export const compareLapTime = (a: OverviewLap, b: OverviewLap) => a.ms - b.ms;

/** Ascending comparator for one sector; laps without the sector sort last. */
export function compareSector(index: 0 | 1 | 2) {
  return (a: OverviewLap, b: OverviewLap) =>
    (a.sectors?.[index] ?? Number.POSITIVE_INFINITY) - (b.sectors?.[index] ?? Number.POSITIVE_INFINITY) || 0;
}

export function compareDeltaToBest(bestMs: number | null) {
  return (a: OverviewLap, b: OverviewLap) => {
    const rank = (lap: OverviewLap) => (bestMs === null || !lap.valid ? Number.POSITIVE_INFINITY : lap.ms - bestMs);
    return rank(a) - rank(b) || 0;
  };
}

/** Fastest valid value per sector, for the "session best" highlight. */
export function sessionBestSectors(laps: OverviewLap[]): [number | null, number | null, number | null] {
  const best: [number | null, number | null, number | null] = [null, null, null];
  for (const lap of laps) {
    if (!lap.valid || !lap.sectors) continue;
    lap.sectors.forEach((value, index) => {
      const current = best[index];
      if (current === null || value < current) best[index as 0 | 1 | 2] = value;
    });
  }
  return best;
}

export type TheoreticalBest = {
  /** Sum of the best valid sector times. */
  ms: number;
  /** How much faster than the best lap, or null without a valid best lap. */
  gainMs: number | null;
  sectors: [number, number, number];
};

/**
 * The lap the driver's best sectors add up to. Null unless all three sectors
 * have a valid time, so a missing sector never makes it look faster.
 */
export function theoreticalBest(laps: OverviewLap[], bestMs: number | null): TheoreticalBest | null {
  const sectors = sessionBestSectors(laps);
  if (sectors.some((value) => value === null)) return null;
  const [s1, s2, s3] = sectors as [number, number, number];
  const ms = s1 + s2 + s3;
  return { ms, gainMs: bestMs === null ? null : Math.max(0, bestMs - ms), sectors: [s1, s2, s3] };
}

/** "1:32.981, 0.322 s quicker than your best lap" */
export function theoreticalBestText(theory: TheoreticalBest): string {
  const gain = theory.gainMs;
  if (gain === null) return formatLapTime(theory.ms);
  if (gain < 1) return `${formatLapTime(theory.ms)}, matching your best lap`;
  return `${formatLapTime(theory.ms)}, ${(gain / 1000).toFixed(3)} s quicker than your best lap`;
}

/** One footnote line per distinct reason: "— Sector times were not … (laps 1–3)." */
export function reasonFootnote(
  laps: OverviewLap[],
  pick: (lap: OverviewLap) => string | null,
  missing: (lap: OverviewLap) => boolean,
): string | undefined {
  const byReason = new Map<string, number[]>();
  for (const lap of laps) {
    if (!missing(lap)) continue;
    const reason = pick(lap);
    if (!reason) continue;
    byReason.set(reason, [...(byReason.get(reason) ?? []), lap.lap]);
  }
  if (byReason.size === 0) return undefined;
  return [...byReason.entries()]
    .map(([reason, numbers]) => `— ${reason.replace(/\.$/, "")} (laps ${formatLapRanges(numbers)}).`)
    .join(" ");
}

export function tyreLabel(tyre: NonNullable<OverviewLap["tyre"]>): string {
  return tyre.ageLaps === null ? tyre.label : `${tyre.label} · ${tyre.ageLaps} ${tyre.ageLaps === 1 ? "lap" : "laps"}`;
}

/* ----------------------------------------------------------- chart series */

export type ChartMetric = "lap" | "delta" | "s1" | "s2" | "s3";

export const METRIC_LABELS: Record<ChartMetric, string> = {
  lap: "Lap Time",
  delta: "Delta to Best",
  s1: "Sector 1",
  s2: "Sector 2",
  s3: "Sector 3",
};

/** Metrics the chart can offer for these laps; sectors only when some lap has them. */
export function metricOptions(laps: OverviewLap[], illustrative: boolean): ChartMetric[] {
  if (illustrative) return ["lap"];
  const metrics: ChartMetric[] = ["lap", "delta"];
  (["s1", "s2", "s3"] as const).forEach((metric, index) => {
    if (laps.some((lap) => lap.sectors?.[index])) metrics.push(metric);
  });
  return metrics;
}

export type ChartSeries = {
  points: ProgressionPoint[];
  yMin: number;
  yMax: number;
  yStep: number;
  xMax: number;
  illustrative: boolean;
  /** Plotted value per lap number, for the selected and reference markers. */
  values: Record<number, number>;
};

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)] ?? 0;
}

const STEPS = [100, 200, 500, 1000, 2000, 5000, 10000];

/** Axis range with at most six steps from a nice step size. */
export function niceScale(min: number, max: number): { yMin: number; yMax: number; yStep: number } {
  const range = Math.max(max - min, 1);
  const yStep = STEPS.find((step) => range / step <= 6) ?? STEPS[STEPS.length - 1];
  const yMin = Math.max(0, Math.floor(min / yStep) * yStep);
  const yMax = Math.max(Math.ceil(max / yStep) * yStep, yMin + 3 * yStep);
  return { yMin, yMax, yStep };
}

export function metricValue(lap: OverviewLap, metric: ChartMetric, bestMs: number | null): number | null {
  if (metric === "lap") return lap.ms;
  if (metric === "delta") return bestMs === null ? null : lap.ms - bestMs;
  const sector = lap.sectors?.[metric === "s1" ? 0 : metric === "s2" ? 1 : 2];
  return sector ?? null;
}

export const EMPTY_SERIES: ChartSeries = {
  points: [],
  yMin: 0,
  yMax: 4000,
  yStep: 1000,
  xMax: 10,
  illustrative: false,
  values: {},
};

/* ------------------------------------------------------------ lap markers */

export type LapMarkerKind = "pit" | "sc" | "vsc";
export type LapMarker = { lap: number; kind: LapMarkerKind };

const SC_KINDS: Record<string, LapMarkerKind> = { safety_car: "sc", virtual_safety_car: "vsc" };

/**
 * The lap a session time fell in, from the player's own events that carry
 * both. Safety car messages have a time but no lap.
 */
export function lapAtTime(events: readonly SummaryEvent[], timeS: number): number | null {
  let lap: number | null = null;
  let bestTime = Number.NEGATIVE_INFINITY;
  for (const event of events) {
    if (!event.isPlayer || event.lap === null || event.sessionTimeS === null) continue;
    if (event.sessionTimeS <= timeS && event.sessionTimeS >= bestTime) {
      bestTime = event.sessionTimeS;
      lap = event.lap;
    }
  }
  return lap;
}

/**
 * Laps run under a safety car or virtual safety car, from "deployed" to
 * "returned" (or to the end of the session). Periods whose lap cannot be
 * placed are left out rather than guessed.
 */
export function neutralisedLaps(events: readonly SummaryEvent[] | null, lastLap: number): Map<number, LapMarkerKind> {
  const result = new Map<number, LapMarkerKind>();
  if (!events) return result;
  const placed = (event: SummaryEvent) =>
    event.lap ?? (event.sessionTimeS === null ? null : lapAtTime(events, event.sessionTimeS));
  let open: { kind: LapMarkerKind; from: number } | null = null;
  const close = (to: number) => {
    if (!open) return;
    for (let lap = open.from; lap <= to; lap += 1) if (!result.has(lap)) result.set(lap, open.kind);
    open = null;
  };
  const ordered = events
    .filter((event) => SC_KINDS[event.kind])
    .sort((a, b) => (a.sessionTimeS ?? 0) - (b.sessionTimeS ?? 0));
  for (const event of ordered) {
    const lap = placed(event);
    if (lap === null) continue;
    const phase = typeof event.detail.event === "string" ? event.detail.event : "";
    if (phase === "deployed") {
      close(lap);
      open = { kind: SC_KINDS[event.kind], from: lap };
    } else if (phase === "returned" || phase === "race resumes") {
      close(lap);
    }
  }
  close(lastLap);
  return result;
}

/** Pit, safety car and virtual safety car markers for the lap chart. */
export function lapMarkers(laps: readonly OverviewLap[], events: readonly SummaryEvent[] | null): LapMarker[] {
  const lastLap = laps.reduce((max, lap) => Math.max(max, lap.lap), 0);
  const neutralised = neutralisedLaps(events, lastLap);
  const markers: LapMarker[] = [];
  for (const lap of laps) {
    if (lap.pit) markers.push({ lap: lap.lap, kind: "pit" });
    const sc = neutralised.get(lap.lap);
    if (sc) markers.push({ lap: lap.lap, kind: sc });
  }
  return markers;
}

/**
 * Laps that are not representative of pace: pit laps, laps under a safety car
 * and anything more than 3% slower than the median valid lap.
 */
export function outlierLapIds(laps: readonly OverviewLap[], markers: readonly LapMarker[]): Set<string> {
  const flagged = new Set(markers.map((marker) => marker.lap));
  const valid = laps.filter((lap) => lap.valid);
  const limit = median(valid.map((lap) => lap.ms)) * 1.03;
  return new Set(valid.filter((lap) => flagged.has(lap.lap) || lap.ms > limit).map((lap) => lap.id));
}

/**
 * Chart points for the chosen metric over the real laps. `excluded` laps
 * (outliers) are left off so the racing laps keep their scale; with nothing
 * excluded every valid lap is drawn.
 */
export function buildSeries(
  laps: OverviewLap[],
  metric: ChartMetric,
  selectedId: string,
  bestId: string | null,
  excluded?: ReadonlySet<string>,
): ChartSeries {
  const best = laps.find((lap) => lap.id === bestId)?.ms ?? null;
  if (metric === "lap" && excluded === undefined) {
    const progression = progressionFrom(laps, selectedId, bestId);
    const values: Record<number, number> = {};
    for (const lap of laps) values[lap.lap] = lap.ms;
    return { ...progression, values };
  }
  if (metric === "lap") return lapTimeSeries(laps, selectedId, bestId, excluded ?? new Set());
  const timed = laps.filter((lap) => lap.valid && (metric !== "delta" || !lap.pit) && !excluded?.has(lap.id));
  const measured = timed.flatMap((lap) => {
    const value = metricValue(lap, metric, best);
    return value === null ? [] : [{ lap, value }];
  });
  if (measured.length === 0) return EMPTY_SERIES;
  // Slow neutralised and pit laps would flatten the racing laps.
  const cutoff = metric === "delta" ? median(timed.map((lap) => lap.ms)) + 6000 - (best ?? 0) : median(measured.map((m) => m.value)) * 1.25;
  // An explicit exclusion set replaces the cutoff; an empty one draws every lap.
  const plotted = excluded === undefined ? measured.filter((entry) => entry.value <= cutoff) : measured;
  const used = plotted.length > 0 ? plotted : measured;
  const lowest = Math.min(...used.map((entry) => entry.value));
  const highest = Math.max(...used.map((entry) => entry.value));
  const scale = niceScale(metric === "delta" ? Math.min(0, lowest) : lowest, highest);
  let personalBest = Number.POSITIVE_INFINITY;
  const values: Record<number, number> = {};
  const points: ProgressionPoint[] = used.map(({ lap, value }) => {
    values[lap.lap] = value;
    let tone: ProgressionPoint["tone"] = "orange";
    if (value < personalBest) {
      personalBest = value;
      tone = "green";
    }
    if (value === lowest) tone = "purple";
    if (lap.id === selectedId) tone = "blue";
    return { lap: lap.lap, ms: value, tone };
  });
  return { points, ...scale, xMax: Math.max(10, ...used.map((entry) => entry.lap.lap)), illustrative: false, values };
}

function lapTimeSeries(
  laps: OverviewLap[],
  selectedId: string,
  bestId: string | null,
  excluded: ReadonlySet<string>,
): ChartSeries {
  const valid = laps.filter((lap) => lap.valid);
  const plotted = valid.filter((lap) => !excluded.has(lap.id));
  const used = plotted.length > 0 ? plotted : valid;
  if (used.length === 0) return EMPTY_SERIES;
  const scale = niceScale(Math.min(...used.map((lap) => lap.ms)), Math.max(...used.map((lap) => lap.ms)));
  const values: Record<number, number> = {};
  for (const lap of laps) values[lap.lap] = lap.ms;
  let personalBest = Number.POSITIVE_INFINITY;
  const points: ProgressionPoint[] = used.map((lap) => {
    let tone: ProgressionPoint["tone"] = "orange";
    if (lap.ms < personalBest) {
      personalBest = lap.ms;
      tone = "green";
    }
    if (lap.id === bestId) tone = "purple";
    if (lap.id === selectedId) tone = "blue";
    return { lap: lap.lap, ms: lap.ms, tone };
  });
  return { points, ...scale, xMax: Math.max(10, ...laps.map((lap) => lap.lap)), illustrative: false, values };
}

export function formatAxisValue(metric: ChartMetric, value: number): string {
  if (metric === "delta") return `+${(value / 1000).toFixed(1)}`;
  if (metric === "lap") {
    const minutes = Math.floor(value / 60000);
    const seconds = (value - minutes * 60000) / 1000;
    // Half-second steps keep their decimal so ticks stay distinct.
    const text = Number.isInteger(seconds) ? String(seconds) : seconds.toFixed(1);
    return `${minutes}:${text.padStart(Number.isInteger(seconds) ? 2 : 4, "0")}`;
  }
  return (value / 1000).toFixed(1);
}

export function formatMetricValue(metric: ChartMetric, value: number): string {
  if (metric === "lap") return formatLapTime(value);
  if (metric === "delta") return formatDelta(value);
  return (value / 1000).toFixed(3);
}

/* ---------------------------------------------------------------- events */

const RACE_CONTROL = new Set([
  "lights_out",
  "session_started",
  "session_ended",
  "chequered_flag",
  "safety_car",
  "virtual_safety_car",
  "formation_lap_safety_car",
  "red_flag",
  "yellow_flag",
  "drs_enabled",
  "drs_disabled",
]);
const PENALTIES = new Set(["penalty", "drive_through_served", "stop_go_served"]);
const PIT = new Set(["pit_entry", "pit_exit", "pit_lap"]);
const MY_LAPS = new Set(["invalid_lap", "not_completed"]);
const INCIDENTS = new Set(["collision", "retirement", "flashback", "finish", "race_winner"]);

export function categoryOf(kind: string, isPlayer = false): EventCategory {
  if (RACE_CONTROL.has(kind)) return "race_control";
  if (PENALTIES.has(kind)) return "penalties";
  if (PIT.has(kind)) return "pit";
  if (INCIDENTS.has(kind)) return "incidents";
  if (MY_LAPS.has(kind) || (kind === "fastest_lap" && isPlayer)) return "my_laps";
  return "other";
}

export function eventIconFor(kind: string, category: EventCategory): EventIcon {
  switch (category) {
    case "race_control":
      return "flag";
    case "penalties":
      return "alert-red";
    case "pit":
      return "pit";
    case "incidents":
      return "car";
    case "my_laps":
      return kind === "invalid_lap" || kind === "not_completed" ? "alert-amber" : "check-green";
    default:
      return kind === "overtake" ? "car" : "ring-green";
  }
}

function summaryEventToOverview(event: SummaryEvent, index: number): OverviewEvent {
  const category = categoryOf(event.kind, event.isPlayer);
  return {
    id: `summary-${index}-${event.kind}-${event.lap ?? "x"}`,
    kind: event.kind,
    category,
    lap: event.lap,
    timeS: event.sessionTimeS,
    label: event.label,
    isPlayer: event.isPlayer,
    icon: eventIconFor(event.kind, category),
    highlight: event.kind === "fastest_lap" && event.isPlayer ? true : undefined,
    source: "summary",
  };
}

function lapEvent(
  kind: string,
  lap: number,
  label: string,
  extra: Partial<Pick<OverviewEvent, "highlight">> = {},
): OverviewEvent {
  const category = categoryOf(kind, true);
  return {
    id: `lap-${kind}-${lap}`,
    kind,
    category,
    lap,
    timeS: null,
    label,
    isPlayer: true,
    icon: eventIconFor(kind, category),
    source: "lap",
    ...extra,
  };
}

/** Notable events derivable from the lap list alone: session best, pit laps, invalidated laps. */
export function lapEvents(laps: OverviewLap[], bestId: string | null): OverviewEvent[] {
  const events: OverviewEvent[] = [];
  for (const lap of laps) {
    if (lap.id === bestId) {
      events.push(lapEvent("fastest_lap", lap.lap, `Session best (${formatLapTime(lap.ms)})`, { highlight: true }));
    }
    if (lap.pit) events.push(lapEvent("pit_lap", lap.lap, "Pit lane in or out lap"));
    if (!lap.valid) events.push(lapEvent("invalid_lap", lap.lap, "Lap invalidated by the game"));
  }
  return events;
}

export function notCompletedEvents(lapNumbers: number[]): OverviewEvent[] {
  return lapNumbers.map((lap) => lapEvent("not_completed", lap, "Lap not completed"));
}

/**
 * `/summary` events plus lap-derived events. Duplicates (same lap, kind and
 * driver) keep the summary's wording; other drivers' overtakes are left out.
 */
export function mergeEvents(base: OverviewEvent[], summaryEvents: SummaryEvent[] | null): OverviewEvent[] {
  const fromSummary = (summaryEvents ?? [])
    .filter((event) => !(event.kind === "overtake" && !event.isPlayer))
    .map(summaryEventToOverview);
  const seen = new Set(fromSummary.map((event) => `${event.lap}|${event.kind}|${event.isPlayer}`));
  const extra = base.filter((event) => !seen.has(`${event.lap}|${event.kind}|${event.isPlayer}`));
  return [...fromSummary, ...extra]
    .map((event, index) => ({ event, index }))
    .sort(
      (a, b) =>
        (a.event.lap ?? 0) - (b.event.lap ?? 0) ||
        (a.event.timeS ?? Number.POSITIVE_INFINITY) - (b.event.timeS ?? Number.POSITIVE_INFINITY) ||
        a.index - b.index,
    )
    .map((entry) => entry.event);
}

export type EventFilter = "all" | Exclude<EventCategory, "other">;

export const EVENT_FILTERS: Array<{ key: EventFilter; label: string }> = [
  { key: "all", label: "All" },
  { key: "race_control", label: "Race control" },
  { key: "penalties", label: "Penalties" },
  { key: "pit", label: "Pit" },
  { key: "my_laps", label: "My laps" },
  { key: "incidents", label: "Incidents" },
];

export function eventCounts(events: OverviewEvent[]): Record<EventFilter, number> {
  const counts: Record<EventFilter, number> = {
    all: events.length,
    race_control: 0,
    penalties: 0,
    pit: 0,
    my_laps: 0,
    incidents: 0,
  };
  for (const event of events) if (event.category !== "other") counts[event.category] += 1;
  return counts;
}

/** Race control that changes how everyone drives; DRS toggles and session bookkeeping are left out. */
const KEY_RACE_CONTROL = new Set([
  "lights_out",
  "chequered_flag",
  "safety_car",
  "virtual_safety_car",
  "formation_lap_safety_car",
  "red_flag",
  "yellow_flag",
  "race_winner",
]);

/** The player's own events plus race control that affected everyone. */
export function isKeyEvent(event: OverviewEvent): boolean {
  // A speed trap fires every lap; it is detail, not a notable event.
  if (event.kind === "speed_trap") return false;
  return event.isPlayer || KEY_RACE_CONTROL.has(event.kind);
}

/** Events shown by default; `allCars` adds other drivers and minor race control. */
export function scopeEvents(events: OverviewEvent[], allCars: boolean): OverviewEvent[] {
  return allCars ? events : events.filter(isKeyEvent);
}

/** Category chip plus the "all cars" switch. */
export function filterEvents(events: OverviewEvent[], filter: EventFilter, allCars: boolean): OverviewEvent[] {
  return scopeEvents(events, allCars).filter((event) => filter === "all" || event.category === filter);
}

/** 125 -> "02:05", 5000 -> "1:23:20" */
export function formatClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(rest).padStart(2, "0");
  return hours ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** Earliest game session time among the events, the zero of the Events time column. */
export function eventOrigin(events: OverviewEvent[]): number | null {
  const times = events.flatMap((event) => (event.timeS === null ? [] : [event.timeS]));
  return times.length ? Math.min(...times) : null;
}

export function relativeEventTime(event: OverviewEvent, origin: number | null): string | null {
  return event.timeS === null || origin === null ? null : formatClock(event.timeS - origin);
}

function lowerFirst(text: string): string {
  return text ? text[0].toLowerCase() + text.slice(1) : text;
}

export function scanStoppedText(stopReason: string | null): string {
  const reason = lowerFirst(humanReason(stopReason).replace(/\.$/, ""));
  return `The event scan stopped early${reason ? ` (${reason})` : ""}, so later events may be missing.`;
}

export function eventsFooter(shown: number, summary: SessionSummary | null): string {
  const omittedMinor = summary?.totals.omittedMinor ?? 0;
  const omittedBudget = summary?.totals.omittedBudget ?? 0;
  const observed = shown + omittedMinor + omittedBudget;
  let text = `Showing ${shown} of ${observed} events.`;
  if (omittedMinor > 0) {
    text += ` ${omittedMinor} minor events (speed traps, other drivers' overtakes) are not listed.`;
  }
  if (omittedBudget > 0) text += ` ${omittedBudget} more events were left out to keep the list short.`;
  return text;
}

/* --------------------------------------------------------------- summary */

export function countSafetyCarPeriods(events: SummaryEvent[]): number {
  return events.filter(
    (event) => (event.kind === "safety_car" || event.kind === "virtual_safety_car") && event.detail.event === "deployed",
  ).length;
}

/** Number of times the reported weather changed between consecutive points. */
export function weatherChanges(points: WeatherPoint[]): number {
  let changes = 0;
  let previous: string | null = null;
  for (const point of points) {
    if (point.weather === null) continue;
    if (previous !== null && point.weather !== previous) changes += 1;
    previous = point.weather;
  }
  return changes;
}

export type DerivedSummary = { text: string; items: SummaryItem[] };

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

export function bestLapOf(laps: OverviewLap[], bestId: string | null): OverviewLap | null {
  return laps.find((lap) => lap.id === bestId) ?? null;
}

/**
 * Summary text and glyph items for a real session, in the planned order, each
 * only when its data exists. Null when there are no timed laps.
 */
export function deriveSummary(input: {
  laps: OverviewLap[];
  bestId: string | null;
  stintCount: number | null;
  summary: SessionSummary | null;
  /** Plain-language limitations, each shown as an amber item. */
  limitations?: string[];
}): DerivedSummary | null {
  const { laps, summary } = input;
  if (laps.length === 0) return null;
  const best = bestLapOf(laps, input.bestId);
  const stints = input.stintCount && input.stintCount > 0 ? ` across ${plural(input.stintCount, "stint")}` : "";
  let text = `${plural(laps.length, "timed lap")} recorded${stints}.`;
  if (best) {
    const valid = laps.filter((lap) => lap.valid);
    const typical = valid.length >= 3 ? median(valid.map((lap) => lap.ms)) : null;
    const faster = typical !== null && typical > best.ms ? `, ${((typical - best.ms) / 1000).toFixed(3)}s faster than the median lap` : "";
    text += ` Best lap ${formatLapTime(best.ms)} on lap ${best.lap}${faster}.`;
  }
  const items: SummaryItem[] = [];
  if (best) items.push({ tone: "green", glyph: "check", text: `Best lap ${formatLapTime(best.ms)} (lap ${best.lap})` });
  const consistency = consistencyOf(laps);
  if (consistency) {
    items.push({
      tone: consistency.spreadMs <= 500 ? "green" : "amber",
      glyph: consistency.spreadMs <= 500 ? "check" : "alert",
      text: `${consistency.rating}: lap times within ${formatSpread(consistency)} over ${plural(consistency.laps, "clean lap")}`,
    });
  }
  const theory = theoreticalBest(laps, best?.ms ?? null);
  if (theory && theory.gainMs !== null && theory.gainMs >= 1) {
    items.push({ tone: "blue", glyph: "info", text: `Theoretical best ${theoreticalBestText(theory)}` });
  }
  const invalid = laps.filter((lap) => !lap.valid).length;
  if (invalid > 0) items.push({ tone: "amber", glyph: "alert", text: `${plural(invalid, "lap")} invalidated by the game` });
  if (summary) {
    if (summary.pitStopCount !== null && summary.pitStopCount > 0) {
      items.push({ tone: "blue", glyph: "info", text: plural(summary.pitStopCount, "pit stop") });
    }
    const safetyCars = countSafetyCarPeriods(summary.events);
    if (safetyCars > 0) items.push({ tone: "amber", glyph: "alert", text: plural(safetyCars, "safety car period") });
    const changes = weatherChanges(summary.weather);
    if (changes > 0) items.push({ tone: "grey", glyph: "info", text: `Weather changed ${plural(changes, "time")}` });
  }
  for (const limitation of input.limitations ?? []) items.push({ tone: "amber", glyph: "alert", text: limitation });
  return { text, items };
}

/* ------------------------------------------------------------------ hero */

export const HERO_STAT_LABELS = ["Laps", "Best Lap", "Conditions", "Track Temp"] as const;
export const HERO_DETAIL_LABELS = ["Game", "Track Length", "Time Driven", "Rule Set", "Data Source"] as const;

export function formatTemperature(celsius: number | null | undefined): string | null {
  return typeof celsius === "number" && Number.isFinite(celsius) ? `${Math.round(celsius)}°C` : null;
}

/** "Clear 26°C"; either part may be missing. */
export function conditionsText(weather: string | null | undefined, airC: number | null | undefined): string | null {
  const parts = [formatWeather(weather), formatTemperature(airC)].filter((part): part is string => part !== null);
  return parts.length ? parts.join(" ") : null;
}

/** The hero field set shared by v1 and v2 sessions (no Build, Assists or Damage). */
export function realHeroFields(input: {
  source: "v1" | "v2";
  lapCount: number;
  bestMs: number | null;
  conditions: string | null;
  trackTemperatureC: number | null;
  packetFormat: number | null;
  trackLengthM: number | null;
  timeDrivenMs: number | null;
  ruleSet: string | null;
  partial: boolean;
}): { stats: HeroField[]; details: HeroField[] } {
  const trackLength = input.trackLengthM ? formatTrackLength(input.trackLengthM) : null;
  return {
    stats: [
      { label: "Laps", value: String(input.lapCount) },
      input.bestMs === null
        ? { label: "Best Lap", value: null, reason: "No valid timed lap" }
        : { label: "Best Lap", value: formatLapTime(input.bestMs) },
      input.conditions
        ? { label: "Conditions", value: input.conditions }
        : { label: "Conditions", value: null, reason: "Weather was not reported" },
      formatTemperature(input.trackTemperatureC)
        ? { label: "Track Temp", value: formatTemperature(input.trackTemperatureC) }
        : { label: "Track Temp", value: null, reason: "Track temperature was not reported" },
    ],
    details: [
      input.packetFormat
        ? { label: "Game", value: formatGame(input.packetFormat) }
        : { label: "Game", value: null, reason: "The game version was not reported" },
      trackLength
        ? { label: "Track Length", value: trackLength }
        : { label: "Track Length", value: null, reason: "Track length was not reported" },
      input.timeDrivenMs === null
        ? { label: "Time Driven", value: null, reason: "No timed laps were recorded" }
        : { label: "Time Driven", value: formatDuration(input.timeDrivenMs) },
      input.ruleSet
        ? { label: "Rule Set", value: formatSessionType(input.ruleSet) }
        : { label: "Rule Set", value: null, reason: "Rule set was not reported" },
      {
        label: "Data Source",
        value: input.source === "v1" ? "Import" : input.partial ? "Game (Live, partial)" : "Game (Live)",
      },
    ],
  };
}

/** Hero tuples of the reference become fields (no unavailable values). */
export function referenceHeroFields(fields: Array<[string, string]>): HeroField[] {
  return fields.map(([value, label]) => ({ label, value }));
}

export function referenceDetailFields(fields: Array<[string, string]>): HeroField[] {
  return fields.map(([label, value]) => ({ label, value }));
}

export const OBSERVED_TITLE = "Time the engine first observed this session";

/** First hero line: type, then the observed date when known, else a placeholder and the short id. */
export function heroDateLine(typeLabel: string, startedAtUtc: string | null, shortId: string): HeroPart[] {
  return startedAtUtc
    ? [typeLabel, { text: formatDayTime(startedAtUtc), title: OBSERVED_TITLE, iso: startedAtUtc }]
    : [typeLabel, "Date not recorded", `#${shortId}`];
}

/** Fills hero fields `/summary` can supply once it has loaded (v2 only). */
export function applySummaryToHero(
  hero: OverviewModel["hero"],
  summary: SessionSummary | null,
  source: OverviewModel["source"],
  shortId: string | null,
): OverviewModel["hero"] {
  if (!summary || source !== "v2") return hero;
  const last = [...summary.weather].reverse().find((point) => point.weather !== null || point.airTemperatureC !== null);
  const stats = hero.stats.map((field) => {
    if (field.value !== null || !last) return field;
    if (field.label === "Conditions") {
      const text = conditionsText(last.weather, last.airTemperatureC);
      return text ? { label: field.label, value: text } : field;
    }
    if (field.label === "Track Temp") {
      const text = formatTemperature(last.trackTemperatureC);
      return text ? { label: field.label, value: text } : field;
    }
    return field;
  });
  const first = hero.lines[0];
  const lines =
    summary.startedAtUtc && first && shortId !== null
      ? [heroDateLine(typeof first[0] === "string" ? first[0] : first[0].text, summary.startedAtUtc, shortId), ...hero.lines.slice(1)]
      : hero.lines;
  return { ...hero, stats, lines };
}

/* ----------------------------------------------------------------- setup */

const fixed = (digits: number) => (value: number | null | undefined) =>
  typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : null;
const whole = fixed(0);
const one = fixed(1);
const two = fixed(2);

function withUnit(value: string | null, unit: string): string | null {
  return value === null ? null : `${value}${unit}`;
}

/** Setup values grouped as the setup screen shows them, with units. */
export function setupGroups(snapshot: PlayerCarSetupSnapshot): SetupView["groups"] {
  const invalid = new Set(snapshot.invalid_fields ?? []);
  const field = <K extends keyof PlayerCarSetupSnapshot>(key: K, format: (value: number | null) => string | null) => {
    if (invalid.has(String(key))) return null;
    const value = snapshot[key];
    return typeof value === "number" || value === null ? format(value) : null;
  };
  const degrees = (key: keyof PlayerCarSetupSnapshot) => withUnit(field(key, two), "°");
  const pct = (key: keyof PlayerCarSetupSnapshot) => withUnit(field(key, whole), "%");
  const int = (key: keyof PlayerCarSetupSnapshot) => field(key, whole);
  return [
    {
      title: "Aerodynamics",
      rows: [
        { label: "Front wing", value: int("front_wing") },
        { label: "Rear wing", value: int("rear_wing") },
      ],
    },
    {
      title: "Transmission",
      rows: [
        { label: "On-throttle differential", value: pct("on_throttle_differential") },
        { label: "Off-throttle differential", value: pct("off_throttle_differential") },
      ],
    },
    {
      title: "Suspension geometry",
      rows: [
        { label: "Front camber", value: degrees("front_camber") },
        { label: "Rear camber", value: degrees("rear_camber") },
        { label: "Front toe", value: degrees("front_toe") },
        { label: "Rear toe", value: degrees("rear_toe") },
      ],
    },
    {
      title: "Suspension",
      rows: [
        { label: "Front suspension", value: int("front_suspension") },
        { label: "Rear suspension", value: int("rear_suspension") },
        { label: "Front anti-roll bar", value: int("front_anti_roll_bar") },
        { label: "Rear anti-roll bar", value: int("rear_anti_roll_bar") },
        { label: "Front ride height", value: int("front_suspension_height") },
        { label: "Rear ride height", value: int("rear_suspension_height") },
      ],
    },
    {
      title: "Brakes",
      rows: [
        { label: "Brake pressure", value: pct("brake_pressure_percent") },
        { label: "Brake bias", value: pct("brake_bias_percent") },
        { label: "Engine braking", value: pct("engine_braking_percent") },
      ],
    },
    {
      title: "Tyres",
      rows: [
        { label: "Front left", value: withUnit(field("front_left_tyre_pressure_psi", one), " psi") },
        { label: "Front right", value: withUnit(field("front_right_tyre_pressure_psi", one), " psi") },
        { label: "Rear left", value: withUnit(field("rear_left_tyre_pressure_psi", one), " psi") },
        { label: "Rear right", value: withUnit(field("rear_right_tyre_pressure_psi", one), " psi") },
      ],
    },
    {
      title: "Weight",
      rows: [
        { label: "Ballast", value: int("ballast") },
        { label: "Fuel load", value: withUnit(field("fuel_load", one), " kg") },
      ],
    },
  ];
}

/** Setup at the start of the session from the first lap that reported one (v1 laps). */
export function setupFromContexts(contexts: Array<PlayerCarSetupContext | null | undefined>): SetupView | null {
  for (const context of contexts) {
    const snapshot = context?.at_start?.setup;
    if (context && snapshot) {
      return { groups: setupGroups(snapshot), changeCount: Math.max(0, context.observed_change_count ?? 0) };
    }
  }
  return null;
}

/* -------------------------------------------------------------- conditions */

export function weatherKind(weather: string | null): WeatherKind {
  const text = (weather ?? "").toLowerCase();
  if (text.includes("storm") || text.includes("thunder")) return "storm";
  if (text.includes("rain") || text.includes("wet")) return text.includes("heavy") ? "heavy_rain" : "light_rain";
  if (text.includes("light") && text.includes("cloud")) return "light_cloud";
  if (text.includes("cloud") || text.includes("overcast")) return "overcast";
  return "clear";
}

export type WeatherRow = {
  id: string;
  timeS: number | null;
  kind: WeatherKind;
  label: string;
  detail: string;
};

const MAX_WEATHER_ROWS = 24;

/** "Air 24°C · Track 38°C", either part optional. */
export function temperaturesText(airC: number | null, trackC: number | null): string {
  const parts = [
    formatTemperature(airC) ? `Air ${formatTemperature(airC)}` : null,
    formatTemperature(trackC) ? `Track ${formatTemperature(trackC)}` : null,
  ].filter((part): part is string => part !== null);
  return parts.join(" · ");
}

/** Weather points with consecutive repeats removed, capped for display. */
export function weatherRows(points: WeatherPoint[]): { rows: WeatherRow[]; total: number } {
  const rows: WeatherRow[] = [];
  let previousKey = "";
  points.forEach((point, index) => {
    if (point.weather === null && point.airTemperatureC === null && point.trackTemperatureC === null) return;
    const key = [point.weather, formatTemperature(point.airTemperatureC), formatTemperature(point.trackTemperatureC)].join("|");
    if (key === previousKey) return;
    previousKey = key;
    rows.push({
      id: `weather-${index}`,
      timeS: point.sessionTimeS,
      kind: weatherKind(point.weather),
      label: formatWeather(point.weather) ?? "Weather",
      detail: temperaturesText(point.airTemperatureC, point.trackTemperatureC),
    });
  });
  return { rows: rows.slice(0, MAX_WEATHER_ROWS), total: rows.length };
}

export type FactRow = {
  label: string;
  value: string | null;
  reason?: string;
  /** True when the value comes from `/summary` and shows a placeholder while that loads. */
  needsSummary?: boolean;
  /** ISO instant behind `value`, shown in the viewer's time zone. */
  iso?: string;
};

/** Rows of the Session card on the Setup & Conditions tab. */
export function sessionFacts(context: OverviewContext, summary: SessionSummary | null): FactRow[] {
  const totalLaps = summary?.totalLaps ?? context.totalLaps;
  const duration = summary?.durationS ?? context.durationS;
  const scheduled = summary?.scheduledDurationS ?? null;
  const timeReason = humanReason(summary?.timeReason) || "Capture times were not recorded";
  const started = summary?.startedAtUtc ?? context.startedAtUtc;
  const ended = summary?.endedAtUtc ?? context.endedAtUtc;
  const length = context.trackLengthM ? formatTrackLength(context.trackLengthM) : null;
  return [
    { label: "Type", value: formatSessionType(context.sessionType) },
    context.ruleSet
      ? { label: "Rule set", value: formatSessionType(context.ruleSet) }
      : { label: "Rule set", value: null, reason: "Rule set was not reported" },
    totalLaps && totalLaps > 0
      ? { label: "Laps", value: `${totalLaps} scheduled`, needsSummary: true }
      : { label: "Laps", value: null, reason: "The scheduled lap count was not reported", needsSummary: true },
    duration !== null
      ? {
          label: "Duration",
          value: `${formatDuration(duration * 1000)}${scheduled ? ` (${formatDuration(scheduled * 1000)} scheduled)` : ""}`,
          needsSummary: true,
        }
      : { label: "Duration", value: null, reason: "Session duration was not reported", needsSummary: true },
    started
      ? { label: "Started", value: formatDayTime(started), needsSummary: true, iso: started }
      : { label: "Started", value: null, reason: timeReason, needsSummary: true },
    ended
      ? { label: "Ended", value: formatDayTime(ended), needsSummary: true, iso: ended }
      : { label: "Ended", value: null, reason: timeReason, needsSummary: true },
    context.trackName
      ? { label: "Track", value: length ? `${context.trackName} · ${length}` : context.trackName }
      : { label: "Track", value: null, reason: "The circuit was not reported" },
    context.packetFormat
      ? { label: "Game", value: formatGame(context.packetFormat) }
      : { label: "Game", value: null, reason: "The game version was not reported" },
  ];
}

/* --------------------------------------------------------- compare / links */

/** Reference lap used when the user has not chosen one: the session best, else the next fastest valid lap. */
export function defaultReferenceId(laps: OverviewLap[], bestId: string | null, targetId: string): string | null {
  if (bestId && bestId !== targetId && laps.some((lap) => lap.id === bestId)) return bestId;
  const next = laps
    .filter((lap) => lap.valid && lap.id !== targetId)
    .sort((a, b) => a.ms - b.ms)[0];
  return next?.id ?? null;
}

export type ResolvedCompare = {
  target: OverviewLap | null;
  reference: OverviewLap | null;
  referenceId: string | null;
  deltaMs: number | null;
};

/** The explicit reference is honoured only while it exists and differs from the target. */
export function resolveCompare(input: {
  laps: OverviewLap[];
  bestId: string | null;
  targetId: string;
  referenceId: string | null;
}): ResolvedCompare {
  const target = input.laps.find((lap) => lap.id === input.targetId) ?? null;
  const explicit = input.referenceId && input.referenceId !== input.targetId ? input.referenceId : null;
  const referenceId =
    explicit && input.laps.some((lap) => lap.id === explicit)
      ? explicit
      : defaultReferenceId(input.laps, input.bestId, input.targetId);
  const reference = input.laps.find((lap) => lap.id === referenceId) ?? null;
  return { target, reference, referenceId, deltaMs: target && reference ? target.ms - reference.ms : null };
}

/** "Lap 28 is +0.578 slower than Lap 57." */
export function compareSentence(target: OverviewLap, reference: OverviewLap): string {
  const delta = target.ms - reference.ms;
  if (delta === 0) return `Lap ${target.lap} has the same time as Lap ${reference.lap}.`;
  return `Lap ${target.lap} is ${formatDelta(delta)} ${delta > 0 ? "slower" : "faster"} than Lap ${reference.lap}.`;
}

export function compareWarnings(target: OverviewLap, reference: OverviewLap): string[] {
  const warnings: string[] = [];
  for (const lap of [target, reference]) {
    if (!lap.valid) warnings.push(`Lap ${lap.lap} was invalidated; the comparison may be misleading.`);
    if (!lap.traceAvailable) warnings.push(`Lap ${lap.lap} has no processed telemetry; Lap Analysis can process it.`);
  }
  return warnings;
}

/** Lap Analysis link carrying the target and (when known) reference lap ids. */
export function analysisHref(sessionKey: string, targetId: string | null, referenceId: string | null): string {
  const query = new URLSearchParams();
  if (targetId) query.set("target", targetId);
  if (referenceId) query.set("reference", referenceId);
  const encoded = query.toString();
  return `/review/${encodeURIComponent(sessionKey)}/laps${encoded ? `?${encoded}` : ""}`;
}

/* --------------------------------------------------------------- URL state */

export const OVERVIEW_VIEWS = ["overview", "laps", "stints", "events", "setup"] as const;
export type OverviewView = (typeof OVERVIEW_VIEWS)[number];
export type LapMode = "single" | "compare";

export type OverviewUrlState = {
  view: OverviewView;
  mode: LapMode;
  selectedId: string;
  /** Reference chosen by the user (compare mode); null means "use the default". */
  referenceId: string | null;
};

type ParamSource = URLSearchParams | Record<string, string | string[] | undefined>;

function param(source: ParamSource, key: string): string | null {
  if (source instanceof URLSearchParams) return source.get(key);
  const value = source[key];
  const first = Array.isArray(value) ? value[0] : value;
  return typeof first === "string" && first !== "" ? first : null;
}

/** View, lap and compare pair from `?view=&lap=&mode=compare&target=&reference=`; unknown ids are ignored. */
export function parseOverviewParams(
  source: ParamSource,
  laps: OverviewLap[],
  defaults: { selectedId: string },
): OverviewUrlState {
  const view = param(source, "view");
  const exists = (id: string | null) => (id !== null && laps.some((lap) => lap.id === id) ? id : null);
  const mode: LapMode = param(source, "mode") === "compare" ? "compare" : "single";
  const target = exists(param(source, mode === "compare" ? "target" : "lap")) ?? exists(param(source, "lap"));
  const selectedId = target ?? defaults.selectedId;
  const reference = mode === "compare" ? exists(param(source, "reference")) : null;
  return {
    view: (OVERVIEW_VIEWS as readonly string[]).includes(view ?? "") ? (view as OverviewView) : "overview",
    mode,
    selectedId,
    referenceId: reference && reference !== selectedId ? reference : null,
  };
}

/** Query string for the state, keeping unrelated parameters and omitting defaults. */
export function overviewQuery(
  current: string,
  state: OverviewUrlState & { resolvedReferenceId: string | null },
  defaults: { selectedId: string },
): string {
  const params = new URLSearchParams(current);
  for (const key of ["view", "lap", "mode", "target", "reference"]) params.delete(key);
  if (state.view !== "overview") params.set("view", state.view);
  if (state.mode === "compare") {
    params.set("mode", "compare");
    params.set("target", state.selectedId);
    if (state.resolvedReferenceId) params.set("reference", state.resolvedReferenceId);
  } else if (state.selectedId !== defaults.selectedId) {
    params.set("lap", state.selectedId);
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

/* ----------------------------------------------------- deferred resolution */

/** What the browser has fetched so far for a deferred section. */
export type LiveRead<R> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "loaded"; value: R }
  | { status: "unavailable"; reason: string };

export type EnrichmentView =
  | { status: "loading"; laps: OverviewLap[] }
  | { status: "loaded"; laps: OverviewLap[]; stints: OverviewStint[]; partialReason: string | null }
  | { status: "unavailable"; laps: OverviewLap[]; reason: string };

export function resolveEnrichment(model: OverviewModel, live: LiveRead<LapEnrichment>): EnrichmentView {
  const section = model.enrichment;
  if (section.state === "loaded") {
    return { status: "loaded", laps: model.laps, stints: section.value.stints, partialReason: section.partialReason };
  }
  if (section.state === "unavailable") return { status: "unavailable", laps: model.laps, reason: section.reason };
  if (live.status === "loaded") {
    const applied = applyEnrichment(model.laps, live.value);
    return { status: "loaded", laps: applied.laps, stints: applied.stints, partialReason: applied.partialReason };
  }
  if (live.status === "unavailable") return { status: "unavailable", laps: model.laps, reason: live.reason };
  return { status: "loading", laps: model.laps };
}

export type SummaryView =
  | { status: "loading" }
  | { status: "loaded"; value: SessionSummary }
  | { status: "unavailable"; reason: string };

export function resolveSummary(model: OverviewModel, live: LiveRead<SessionSummary>): SummaryView {
  const section = model.summaryData;
  if (section.state === "loaded") return { status: "loaded", value: section.value };
  if (section.state === "unavailable") return { status: "unavailable", reason: section.reason };
  if (live.status === "loaded") return { status: "loaded", value: live.value };
  if (live.status === "unavailable") return { status: "unavailable", reason: live.reason };
  return { status: "loading" };
}

/** Events for the views: the `/summary` list merged with the model's own events. */
export function eventsFor(model: OverviewModel, summary: SummaryView): OverviewEvent[] {
  return mergeEvents(model.events, summary.status === "loaded" ? summary.value.events : null);
}
