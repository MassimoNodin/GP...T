import type { LapTrace, TracePoint } from "./lap-telemetry";

// Time delta between two laps, derived from their speed traces. Pure: nothing
// here performs I/O. The delta is an estimate (speed integrated over distance),
// normalised so each lap's cumulative time ends at its official lap time, and
// is always labelled "derived from speed" in the UI.

/** Grid spacing the traces are resampled to, in metres. */
export const DELTA_STEP_M = 5;
/** Speeds below this are floored before integrating (5 km/h, in m/s). */
export const MIN_SPEED_MPS = 5 / 3.6;
/** Each speed trace must cover at least this share of the lap length. */
export const MIN_DELTA_COVERAGE = 0.95;

export const DELTA_INCOMPLETE = "Delta needs complete speed traces for both laps.";
export const DELTA_NO_REFERENCE = "Select a reference lap to see the time delta.";
export const DELTA_NO_LAP_TIME = "Delta needs a recorded lap time for both laps.";

export type DeltaSeries =
  | {
      /** [distance m, target time - reference time in seconds]; positive = target slower. */
      points: TracePoint[];
      /** Difference at the end of the lap in ms; equals the official lap-time difference. */
      endMs: number;
      /** Cumulative time per lap in seconds on the same grid, scaled to the official lap times. */
      targetTime: TracePoint[];
      referenceTime: TracePoint[];
    }
  | { unavailable: string };

export function isDelta(series: DeltaSeries | null | undefined): series is Exclude<DeltaSeries, { unavailable: string }> {
  return !!series && "points" in series;
}

function flatten(trace: LapTrace): TracePoint[] | null {
  if (trace.state !== "available") return null;
  const runs = trace.channels.speed;
  if (!runs) return null;
  const flat: TracePoint[] = [];
  for (const run of runs) for (const point of run) flat.push(point);
  // Runs are already ordered by distance; keep it strict so interpolation is safe.
  const ordered: TracePoint[] = [];
  for (const point of flat) {
    if (!ordered.length || point[0] > ordered[ordered.length - 1][0]) ordered.push(point);
  }
  return ordered.length >= 2 ? ordered : null;
}

/** Linear resampling onto `grid` (ascending); the grid must lie inside the points' range. */
function resample(points: TracePoint[], grid: number[]): number[] {
  const values: number[] = [];
  let index = 0;
  for (const d of grid) {
    while (index < points.length - 2 && points[index + 1][0] < d) index += 1;
    const [d0, v0] = points[index];
    const [d1, v1] = points[index + 1];
    values.push(d1 === d0 ? v1 : v0 + ((v1 - v0) * (d - d0)) / (d1 - d0));
  }
  return values;
}

/** Cumulative driving time in seconds along the grid from speeds in km/h. */
function cumulativeTime(grid: number[], speedsKph: number[]): number[] {
  const inverse = speedsKph.map((kph) => 1 / Math.max(kph / 3.6, MIN_SPEED_MPS));
  const times = [0];
  for (let i = 1; i < grid.length; i += 1) {
    times.push(times[i - 1] + (grid[i] - grid[i - 1]) * 0.5 * (inverse[i - 1] + inverse[i]));
  }
  return times;
}

function coveredMetres(trace: LapTrace): number {
  if (trace.state !== "available") return 0;
  const coverage = trace.coverage?.speed?.metres;
  if (typeof coverage === "number") return coverage;
  const flat = flatten(trace);
  return flat ? flat[flat.length - 1][0] - flat[0][0] : 0;
}

/**
 * Cumulative time difference target - reference along the lap.
 * Resamples both speed traces onto a 5 m grid over the shared covered range,
 * integrates dt = Δd / v (v floored at 5 km/h), scales each lap's cumulative
 * time so its final value equals the official lap time, and subtracts.
 * Unavailable when either speed trace covers < 95% of the lap length.
 */
export function deltaSeries(
  target: LapTrace | undefined,
  reference: LapTrace | undefined,
  targetMs: number | null | undefined,
  referenceMs: number | null | undefined,
  lapLengthM?: number | null,
): DeltaSeries {
  if (!reference) return { unavailable: DELTA_NO_REFERENCE };
  if (!target) return { unavailable: DELTA_INCOMPLETE };
  if (
    typeof targetMs !== "number" ||
    typeof referenceMs !== "number" ||
    !(targetMs > 0) ||
    !(referenceMs > 0) ||
    !Number.isFinite(targetMs) ||
    !Number.isFinite(referenceMs)
  ) {
    return { unavailable: DELTA_NO_LAP_TIME };
  }
  const targetPoints = flatten(target);
  const referencePoints = flatten(reference);
  if (target.state !== "available" || reference.state !== "available" || !targetPoints || !referencePoints) {
    return { unavailable: DELTA_INCOMPLETE };
  }
  const length = lapLengthM && lapLengthM > 0 ? lapLengthM : Math.max(target.length, reference.length);
  if (
    !(length > 0) ||
    coveredMetres(target) < MIN_DELTA_COVERAGE * length ||
    coveredMetres(reference) < MIN_DELTA_COVERAGE * length
  ) {
    return { unavailable: DELTA_INCOMPLETE };
  }

  const start = Math.max(targetPoints[0][0], referencePoints[0][0]);
  const end = Math.min(targetPoints[targetPoints.length - 1][0], referencePoints[referencePoints.length - 1][0]);
  if (!(end - start >= MIN_DELTA_COVERAGE * length * 0.9)) return { unavailable: DELTA_INCOMPLETE };

  const grid: number[] = [];
  for (let d = start; d < end; d += DELTA_STEP_M) grid.push(d);
  grid.push(end);

  const targetTimes = cumulativeTime(grid, resample(targetPoints, grid));
  const referenceTimes = cumulativeTime(grid, resample(referencePoints, grid));
  const targetEnd = targetTimes[targetTimes.length - 1];
  const referenceEnd = referenceTimes[referenceTimes.length - 1];
  if (!(targetEnd > 0) || !(referenceEnd > 0)) return { unavailable: DELTA_INCOMPLETE };
  const targetScale = targetMs / 1000 / targetEnd;
  const referenceScale = referenceMs / 1000 / referenceEnd;

  const targetTime: TracePoint[] = [];
  const referenceTime: TracePoint[] = [];
  const points: TracePoint[] = [];
  grid.forEach((d, i) => {
    const t = targetTimes[i] * targetScale;
    const r = referenceTimes[i] * referenceScale;
    targetTime.push([d, t]);
    referenceTime.push([d, r]);
    points.push([d, t - r]);
  });
  return { points, endMs: Math.round(points[points.length - 1][1] * 1000), targetTime, referenceTime };
}

function interpolate(points: TracePoint[], d: number): number | null {
  if (points.length === 0 || d < points[0][0] || d > points[points.length - 1][0]) return null;
  let low = 0;
  let high = points.length - 1;
  while (high - low > 1) {
    const mid = (low + high) >> 1;
    if (points[mid][0] <= d) low = mid;
    else high = mid;
  }
  const [d0, v0] = points[low];
  const [d1, v1] = points[high];
  return d1 === d0 ? v1 : v0 + ((v1 - v0) * (d - d0)) / (d1 - d0);
}

/** Delta in seconds at a lap distance, or null outside the covered range. */
export function deltaAt(series: DeltaSeries | null | undefined, d: number): number | null {
  return isDelta(series) ? interpolate(series.points, d) : null;
}

/** Time difference accumulated between two distances, in seconds. */
export function deltaBetween(series: DeltaSeries | null | undefined, from: number, to: number): number | null {
  if (!isDelta(series)) return null;
  const first = series.points[0][0];
  const last = series.points[series.points.length - 1][0];
  const a = interpolate(series.points, Math.min(Math.max(from, first), last));
  const b = interpolate(series.points, Math.min(Math.max(to, first), last));
  return a === null || b === null ? null : b - a;
}

/** Cumulative driving time of one lap at a distance, in seconds. */
export function timeAt(points: TracePoint[], d: number): number | null {
  return interpolate(points, d);
}

/**
 * Polygons for the signed area between the delta line and zero: positive
 * (target losing) and negative (target gaining), with the zero crossings
 * interpolated so the fills meet the axis exactly.
 */
export function signedAreas(points: TracePoint[]): { positive: TracePoint[][]; negative: TracePoint[][] } {
  const positive: TracePoint[][] = [];
  const negative: TracePoint[][] = [];
  let run: TracePoint[] = [];
  let runSign = 0;
  const flush = () => {
    if (run.length >= 2 && runSign !== 0) (runSign > 0 ? positive : negative).push(run);
    run = [];
    runSign = 0;
  };
  for (let i = 0; i < points.length; i += 1) {
    const [d, v] = points[i];
    const sign = Math.sign(v);
    if (i > 0) {
      const [previousD, previousV] = points[i - 1];
      if (Math.sign(previousV) * sign < 0) {
        const cross: TracePoint = [previousD + (d - previousD) * (previousV / (previousV - v)), 0];
        run.push(cross);
        flush();
        run = [cross];
        runSign = sign;
      }
    }
    if (sign === 0) {
      if (run.length) {
        run.push([d, 0]);
        flush();
      }
      run = [[d, 0]];
      continue;
    }
    if (runSign === 0) runSign = sign;
    run.push([d, v]);
  }
  flush();
  return { positive, negative };
}

/** Symmetric chart extent in seconds (at least 0.25) that holds every point in the window. */
export function deltaExtent(points: TracePoint[], from: number, to: number): number {
  let largest = 0;
  for (const [d, v] of points) {
    if (d >= from && d <= to) largest = Math.max(largest, Math.abs(v));
  }
  const padded = Math.max(0.25, largest * 1.15);
  const step = padded > 2 ? 0.5 : 0.25;
  return Math.ceil(padded / step) * step;
}
