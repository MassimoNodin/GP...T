import type { RegionModel, TrackModelSummary, TrackRegion } from "@/features/review-data/regions";
import { deltaBetween, isDelta, timeAt, type DeltaSeries } from "./delta";
import { clipRuns, valueAt, type ChannelRuns, type DistanceRange, type LapTrace } from "./lap-telemetry";

// Pure helpers over track-model regions: choices for the region picker,
// quick jumps, per-region corner metrics and the heat colour of a region.
// Distances already include the model origin offset (backend contract).

/** Selection id meaning the whole lap. */
export const FULL_LAP = "";

export type RegionChoice = {
  /** `r:<region id>` or `cx:<complex id>`; stable across reloads, used in the URL. */
  id: string;
  /** Label for tables and chips, e.g. `T6` or `T6–T8`. */
  label: string;
  range: DistanceRange;
  apexM: number | null;
  complex: boolean;
  /** Regions inside a complex, in lap order. */
  members: TrackRegion[];
  complexId: string | null;
};

function byStart(a: TrackRegion, b: TrackRegion) {
  return a.startM - b.startM || a.endM - b.endM;
}

function complexLabel(members: TrackRegion[]): string {
  const first = members[0].label;
  const last = members[members.length - 1].label;
  return first === last ? first : `${first}–${last}`;
}

/** Complexes (two or more regions sharing a complex id) first, then every region, in lap order. */
export function regionChoices(model: RegionModel | null): RegionChoice[] {
  if (!model) return [];
  const regions = [...model.regions].sort(byStart);
  const groups = new Map<string, TrackRegion[]>();
  for (const region of regions) {
    if (!region.complexId) continue;
    const list = groups.get(region.complexId) ?? [];
    list.push(region);
    groups.set(region.complexId, list);
  }
  const complexes: RegionChoice[] = [];
  for (const [complexId, members] of groups) {
    if (members.length < 2) continue;
    complexes.push({
      id: `cx:${complexId}`,
      label: complexLabel(members),
      range: { from: Math.min(...members.map((m) => m.startM)), to: Math.max(...members.map((m) => m.endM)) },
      apexM: null,
      complex: true,
      members,
      complexId,
    });
  }
  complexes.sort((a, b) => a.range.from - b.range.from);
  const singles = regions.map(
    (region): RegionChoice => ({
      id: `r:${region.id}`,
      label: region.label,
      range: { from: region.startM, to: region.endM },
      apexM: region.apexM,
      complex: false,
      members: [region],
      complexId: region.complexId,
    }),
  );
  return [...complexes, ...singles];
}

export function findChoice(choices: readonly RegionChoice[], id: string | null | undefined): RegionChoice | null {
  return id ? (choices.find((choice) => choice.id === id) ?? null) : null;
}

const METRES = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export function formatMetres(value: number): string {
  return `${METRES.format(Math.round(value))} m`;
}

export function formatRange(range: DistanceRange): string {
  return `${METRES.format(Math.round(range.from))}–${METRES.format(Math.round(range.to))} m`;
}

/** Option text of the region picker: `T6 · 2,340–2,980 m`, complexes marked. */
export function choiceOptionText(choice: RegionChoice): string {
  return `${choice.label}${choice.complex ? " (complex)" : ""} · ${formatRange(choice.range)}`;
}

/** `T6`, `T10` style labels collapse to `T6-10`; anything else keeps the full label. */
function jumpLabel(choice: RegionChoice): string {
  const first = choice.members[0].label.match(/^T(\d+)$/);
  const last = choice.members[choice.members.length - 1].label.match(/^T(\d+)$/);
  return first && last ? `T${first[1]}-${last[1]}` : choice.label;
}

/** Chips for the track card: the complexes of the model, at most six. */
export function quickJumps(choices: readonly RegionChoice[]): Array<{ id: string; label: string }> {
  return choices
    .filter((choice) => choice.complex)
    .slice(0, 6)
    .map((choice) => ({ id: choice.id, label: jumpLabel(choice) }));
}

/** Window for the chart: the region with a margin on both sides, clipped to the lap. */
export function paddedWindow(range: DistanceRange, lapLength: number): DistanceRange {
  const margin = Math.max(50, (range.to - range.from) * 0.12);
  return { from: Math.max(0, range.from - margin), to: Math.min(lapLength, range.to + margin) };
}

/** Human description of the model for Data Quality: `Monaco r3 (draft)`. */
export function describeRegionModel(model: RegionModel): string {
  return `${model.trackName} r${model.revision} (${model.validation})`;
}

/**
 * Track model for a v1 session from the catalog list: same circuit name, a
 * plausible length, validated over draft, then the highest revision.
 */
export function matchTrackModel(
  models: readonly TrackModelSummary[],
  trackName: string | null,
  trackLengthM: number | null,
): TrackModelSummary | null {
  if (!trackName) return null;
  const name = trackName.trim().toLowerCase();
  const candidates = models
    .filter((model) => model.trackName.trim().toLowerCase() === name && model.regionCount > 0)
    .filter(
      (model) =>
        trackLengthM === null ||
        model.trackLengthM === null ||
        !(model.trackLengthM > 0) ||
        Math.abs(model.trackLengthM - trackLengthM) <= model.trackLengthM * 0.02,
    )
    .sort(
      (a, b) =>
        Number(b.validation === "validated") - Number(a.validation === "validated") ||
        b.revision - a.revision ||
        a.modelId.localeCompare(b.modelId),
    );
  return candidates[0] ?? null;
}

/* ------------------------------------------------------------ region heat */

export const HEAT_THRESHOLD_S = 0.05;
export type Heat = "gain" | "loss" | "even" | "none";

/** Colour class of a region from the time difference over it (seconds, positive = target slower). */
export function heatOf(deltaS: number | null | undefined): Heat {
  if (deltaS === null || deltaS === undefined || !Number.isFinite(deltaS)) return "none";
  if (deltaS <= -HEAT_THRESHOLD_S) return "gain";
  if (deltaS >= HEAT_THRESHOLD_S) return "loss";
  return "even";
}

export function regionDelta(range: DistanceRange, delta: DeltaSeries | null | undefined): number | null {
  return deltaBetween(delta, range.from, range.to);
}

/* --------------------------------------------------------- corner metrics */

const BRAKE_ON_PERCENT = 10;
const THROTTLE_PICKUP_PERCENT = 90;
const BRAKE_SEARCH_BEFORE_M = 150;
const THROTTLE_SEARCH_AFTER_M = 150;
/** Brake or pickup points closer than this count as the same. */
export const SAME_POINT_M = 2;

export type LapPair<T> = { target: T | null; reference: T | null };

export type CornerMetrics = {
  entryKph: LapPair<number>;
  minKph: LapPair<number>;
  minAtM: LapPair<number>;
  exitKph: LapPair<number>;
  timeS: LapPair<number>;
  /** Δ time over the corner, seconds (positive = target slower). */
  deltaTimeS: number | null;
  brakePointM: LapPair<number>;
  throttlePickupM: LapPair<number>;
};

function speedRuns(trace: LapTrace | undefined): ChannelRuns | undefined {
  return trace?.state === "available" ? trace.channels.speed : undefined;
}

function minimumSpeed(trace: LapTrace | undefined, range: DistanceRange): { kph: number; atM: number } | null {
  let best: { kph: number; atM: number } | null = null;
  for (const run of clipRuns(speedRuns(trace), range)) {
    for (const [d, v] of run) if (!best || v < best.kph) best = { kph: v, atM: d };
  }
  return best;
}

/** First distance in [from, to] where the channel reaches the threshold; null when it never does. */
function firstAtOrAbove(runs: ChannelRuns | undefined, from: number, to: number, threshold: number): number | null {
  for (const run of clipRuns(runs, { from, to })) {
    for (let i = 0; i < run.length; i += 1) {
      const [d, v] = run[i];
      if (v < threshold) continue;
      if (i === 0) return d;
      // Interpolate the crossing from the previous point.
      const [pd, pv] = run[i - 1];
      return pv >= threshold ? d : pd + ((threshold - pv) * (d - pd)) / (v - pv);
    }
  }
  return null;
}

function channel(trace: LapTrace | undefined, key: "brake" | "throttle"): ChannelRuns | undefined {
  return trace?.state === "available" ? trace.channels[key] : undefined;
}

function pair<T>(f: (trace: LapTrace | undefined) => T | null, target: LapTrace | undefined, reference: LapTrace | undefined): LapPair<T> {
  return { target: f(target), reference: f(reference) };
}

/**
 * Corner metrics over one region (or a complex), for the Sectors & Corners
 * table: entry, minimum and exit speed, time in the corner (integrated from
 * speed and scaled to the official lap times), brake point and throttle
 * pickup. A value is null when the channel does not cover the region.
 */
export function cornerMetrics(
  region: { range: DistanceRange; apexM: number | null },
  target: LapTrace | undefined,
  reference: LapTrace | undefined,
  delta: DeltaSeries | null | undefined,
): CornerMetrics {
  const { from, to } = region.range;
  const apex = region.apexM ?? (from + to) / 2;
  const minimum = pair((trace) => minimumSpeed(trace, region.range), target, reference);
  const throttleAfter = (trace: LapTrace | undefined, minAt: number | undefined) =>
    firstAtOrAbove(channel(trace, "throttle"), minAt ?? apex, to + THROTTLE_SEARCH_AFTER_M, THROTTLE_PICKUP_PERCENT);

  const timeIn = (points: Array<[number, number]> | null): number | null => {
    if (!points) return null;
    const start = timeAt(points, from);
    const end = timeAt(points, to);
    return start === null || end === null ? null : end - start;
  };
  const cumulative = isDelta(delta) ? delta : null;

  return {
    entryKph: { target: valueAt(speedRuns(target), from), reference: valueAt(speedRuns(reference), from) },
    minKph: { target: minimum.target?.kph ?? null, reference: minimum.reference?.kph ?? null },
    minAtM: { target: minimum.target?.atM ?? null, reference: minimum.reference?.atM ?? null },
    exitKph: { target: valueAt(speedRuns(target), to), reference: valueAt(speedRuns(reference), to) },
    timeS: { target: timeIn(cumulative?.targetTime ?? null), reference: timeIn(cumulative?.referenceTime ?? null) },
    deltaTimeS: regionDelta(region.range, delta),
    brakePointM: pair(
      (trace) => firstAtOrAbove(channel(trace, "brake"), Math.max(0, from - BRAKE_SEARCH_BEFORE_M), Math.max(apex, from), BRAKE_ON_PERCENT),
      target,
      reference,
    ),
    throttlePickupM: {
      target: throttleAfter(target, minimum.target?.atM),
      reference: throttleAfter(reference, minimum.reference?.atM),
    },
  };
}

/** `6 m later`, `4 m earlier` or `same`, comparing the target's point with the reference's. */
export function distanceDifferenceLabel(target: number | null, reference: number | null): string | null {
  if (target === null || reference === null) return null;
  const difference = target - reference;
  if (Math.abs(difference) < SAME_POINT_M) return "same";
  return `${Math.round(Math.abs(difference))} m ${difference > 0 ? "later" : "earlier"}`;
}
