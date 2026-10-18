import type { Tone } from "@/features/ui-foundation/primitives";
import type { AnalysisLap } from "./load";

// Pure helpers that turn backend lap traces into chart series and plain-language
// insights. Nothing here performs I/O so the mapping can be tested without a
// backend.
//
// v2 attempt detail (`GET /api/v2/session-evidence/sessions/{id}/attempts/{revision}`):
//   data.samples[]  lap_distance_m, speed_mps, throttle (0..1), brake (0..1),
//                   steering (-1..1), frame_ordinal, and when recorded gear and
//                   world_position_x_m / world_position_z_m (motion packets)
// v1 trace preview (`GET /api/v1/attempts/{key}/traces`):
//   data.channels.{speed,throttle,brake,steering}.segments[].points[]
//   with lap_distance_m and value (km/h, percent, percent, -1..1)

export type ChannelKey = "speed" | "throttle" | "brake" | "steering" | "gear";
/** Channels every recorded lap is expected to carry; gear is optional. */
export const CHANNEL_KEYS: readonly ChannelKey[] = ["speed", "throttle", "brake", "steering"];
export const ALL_CHANNEL_KEYS: readonly ChannelKey[] = [...CHANNEL_KEYS, "gear"];

export type TracePoint = [number, number];
/** Unbroken stretches of one channel; a gap in the data starts a new run. */
export type ChannelRuns = TracePoint[][];

/** Distance covered by one channel (before thinning) and how many unbroken runs it had. */
export type ChannelCoverage = { metres: number; runs: number };
/** Recorded car positions as [lap distance, world x, world z]. */
export type TracePath = Array<[number, number, number]>;

export type LapTrace =
  | {
      state: "available";
      /** Samples that carried a usable lap distance. */
      samples: number;
      /** Furthest lap distance covered, in metres. */
      length: number;
      /** Speed in km/h, throttle and brake in percent, steering in percent of full lock, gear as a number. */
      channels: Partial<Record<ChannelKey, ChannelRuns>>;
      /** Per channel: metres covered and number of unbroken runs (runs - 1 = gaps). */
      coverage: Partial<Record<ChannelKey, ChannelCoverage>>;
      /** World positions when the samples carried them; null otherwise. */
      path: TracePath | null;
    }
  | {
      state: "unavailable";
      reason: string;
      /** Backend or local reason code, when there is one. */
      code: string | null;
      /** True when the lap's detail was deferred and can be processed on request. */
      processable: boolean;
    };

export type LapTelemetry =
  /** Fixture session: the illustrative reference traces drawn by the chart. */
  | { kind: "reference" }
  /** Real session: traces keyed by lap id for the laps the server loaded. */
  | { kind: "real"; traces: Record<string, LapTrace> };

export type Difference = { text: string; emphasis?: boolean };
export type QualityItem = { text: string; tone: Tone };

const MIN_SAMPLES = 20;
/** Points closer than this along the lap are thinned before reaching the client. */
const DECIMATE_METRES = 5;
const FULL_THROTTLE_PERCENT = 98;

/* ---------------------------------------------------------------- reasons */

const REASONS: Record<string, string> = {
  no_samples: "The backend returned no telemetry samples for this lap.",
  too_few_samples: "Too few telemetry samples were recorded for this lap.",
  not_processed: "Detailed telemetry has not been processed for this lap.",
  trace_not_selected: "Detailed telemetry was not selected for processing for this lap.",
  comparison_read_budget_busy: "The telemetry reader is busy. Reload to try again.",
  analysis_read_budget_exceeded: "The telemetry for this lap is too large to read.",
  attempt_trace_unavailable: "Telemetry is not available for this lap.",
  unreachable: "The local engine could not be reached.",
};

/** Plain-language explanation for a backend or local reason code. */
export function traceUnavailableReason(code: string | null | undefined): string {
  if (code && REASONS[code]) return REASONS[code];
  return "Telemetry samples for this lap could not be read.";
}

const PROCESSABLE_CODES = new Set(["not_processed", "trace_not_selected"]);

export function unavailableTrace(code: string | null | undefined): LapTrace {
  return {
    state: "unavailable",
    reason: traceUnavailableReason(code),
    code: code ?? null,
    processable: code !== null && code !== undefined && PROCESSABLE_CODES.has(code),
  };
}

/* ----------------------------------------------------------------- parsing */

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Keeps the first point and then one point per `DECIMATE_METRES`, plus the last. */
function thin(run: TracePoint[]): TracePoint[] {
  if (run.length <= 2) return run;
  const kept: TracePoint[] = [run[0]];
  for (let i = 1; i < run.length - 1; i += 1) {
    if (run[i][0] - kept[kept.length - 1][0] >= DECIMATE_METRES) kept.push(run[i]);
  }
  kept.push(run[run.length - 1]);
  return kept;
}

/** Minimum recorded positions before a measured track line is drawn. */
const MIN_PATH_POINTS = 20;
/** Positions closer than this along the lap are thinned from the measured line. */
const PATH_METRES = 4;

function finishTrace(
  runsByChannel: Partial<Record<ChannelKey, TracePoint[][]>>,
  samples: number,
  length: number,
  path: TracePath | null = null,
): LapTrace {
  const channels: Partial<Record<ChannelKey, ChannelRuns>> = {};
  const coverage: Partial<Record<ChannelKey, ChannelCoverage>> = {};
  for (const key of ALL_CHANNEL_KEYS) {
    const raw = (runsByChannel[key] ?? []).filter((run) => run.length >= 2);
    if (raw.length === 0) continue;
    coverage[key] = {
      metres: raw.reduce((total, run) => total + (run[run.length - 1][0] - run[0][0]), 0),
      runs: raw.length,
    };
    channels[key] = raw.map(thin);
  }
  if (!CHANNEL_KEYS.some((key) => channels[key])) return unavailableTrace("no_samples");
  return {
    state: "available",
    samples,
    length,
    channels,
    coverage,
    path: path && path.length >= MIN_PATH_POINTS ? path : null,
  };
}

function thinPath(points: TracePath): TracePath {
  const kept: TracePath = [];
  for (const point of points) {
    if (!kept.length || point[0] - kept[kept.length - 1][0] >= PATH_METRES) kept.push(point);
  }
  return kept;
}

/** World x/z of one v2 sample: separate fields, or the `world_position_m` tuple. */
function samplePosition(sample: Record<string, unknown>): [number, number] | null {
  if (sample.motion_available === false) return null;
  const x = finite(sample.world_position_x_m);
  const z = finite(sample.world_position_z_m);
  if (x !== null && z !== null) return [x, z];
  const tuple = sample.world_position_m;
  if (Array.isArray(tuple) && tuple.length >= 3) {
    const tupleX = finite(tuple[0]);
    const tupleZ = finite(tuple[2]);
    if (tupleX !== null && tupleZ !== null) return [tupleX, tupleZ];
  }
  return null;
}

/** Maps a v2 attempt-detail response (`{data: {attempt, samples}}`) onto chart series. */
export function traceFromEvidence(body: unknown): LapTrace {
  const samples = asRecord(asRecord(body)?.data)?.samples;
  if (!Array.isArray(samples)) return unavailableTrace("no_samples");

  type Row = {
    d: number;
    order: number;
    values: Record<ChannelKey, number | null>;
    position: [number, number] | null;
  };
  const rows: Row[] = [];
  samples.forEach((item, index) => {
    const sample = asRecord(item);
    const distance = finite(sample?.lap_distance_m);
    if (!sample || distance === null || distance < 0 || sample.car_telemetry_available === false) return;
    const speed = finite(sample.speed_mps);
    const throttle = finite(sample.throttle);
    const brake = finite(sample.brake);
    const steering = finite(sample.steering);
    rows.push({
      position: samplePosition(sample),
      d: distance,
      order: finite(sample.frame_ordinal) ?? index,
      values: {
        speed: speed === null ? null : speed * 3.6,
        throttle: throttle === null ? null : throttle * 100,
        brake: brake === null ? null : brake * 100,
        steering: steering === null ? null : steering * 100,
        gear: finite(sample.gear),
      },
    });
  });
  rows.sort((a, b) => a.order - b.order);

  // Distance only grows along a lap; drop samples that jump backwards.
  const ordered: Row[] = [];
  let furthest = -1;
  for (const row of rows) {
    if (row.d < furthest) continue;
    furthest = row.d;
    ordered.push(row);
  }
  if (ordered.length < MIN_SAMPLES) return unavailableTrace(ordered.length ? "too_few_samples" : "no_samples");

  const runs: Partial<Record<ChannelKey, TracePoint[][]>> = {};
  const open: Partial<Record<ChannelKey, TracePoint[]>> = {};
  const positions: TracePath = [];
  for (const row of ordered) {
    if (row.position) positions.push([row.d, row.position[0], row.position[1]]);
    for (const key of ALL_CHANNEL_KEYS) {
      const value = row.values[key];
      if (value === null) {
        open[key] = undefined;
        continue;
      }
      let run = open[key];
      if (!run) {
        run = [];
        open[key] = run;
        (runs[key] ??= []).push(run);
      }
      run.push([row.d, value]);
    }
  }
  return finishTrace(runs, ordered.length, furthest, positions.length ? thinPath(positions) : null);
}

/** Maps a v1 trace preview (`{data: {channels}}`) onto chart series. */
export function traceFromPreview(body: unknown): LapTrace {
  const channels = asRecord(asRecord(body)?.data)?.channels;
  const record = asRecord(channels);
  if (!record) return unavailableTrace("no_samples");

  const runs: Partial<Record<ChannelKey, TracePoint[][]>> = {};
  let samples = 0;
  let length = 0;
  for (const key of CHANNEL_KEYS) {
    const segments = asRecord(record[key])?.segments;
    if (!Array.isArray(segments)) continue;
    const scale = key === "steering" ? 100 : 1;
    for (const segment of segments) {
      const points = asRecord(segment)?.points;
      if (!Array.isArray(points)) continue;
      const run: TracePoint[] = [];
      for (const item of points) {
        const point = asRecord(item);
        const distance = finite(point?.lap_distance_m);
        const value = finite(point?.value);
        if (distance === null || value === null || distance < 0) continue;
        if (run.length && distance < run[run.length - 1][0]) continue;
        run.push([distance, value * scale]);
      }
      if (run.length < 2) continue;
      (runs[key] ??= []).push(run);
      samples = Math.max(samples, run.length);
      length = Math.max(length, run[run.length - 1][0]);
    }
  }
  if (samples < MIN_SAMPLES) return unavailableTrace(samples ? "too_few_samples" : "no_samples");
  return finishTrace(runs, samples, length);
}

/* -------------------------------------------------------------------- axis */

export type Axis = { from: number; to: number; ticks: number[] };

/** Full-lap distance axis covering every available trace. */
export function axisFor(traces: Array<LapTrace | undefined>): Axis | null {
  let length = 0;
  for (const trace of traces) {
    if (trace?.state === "available") length = Math.max(length, trace.length);
  }
  if (length <= 0) return null;
  const to = Math.ceil(length / 50) * 50;
  const step = [250, 500, 1000, 2000].find((candidate) => to / candidate <= 8) ?? 2000;
  const ticks: number[] = [];
  for (let tick = 0; tick <= to; tick += step) ticks.push(tick);
  return { from: 0, to, ticks };
}

/** A distance window in metres. */
export type DistanceRange = { from: number; to: number };

/** Round tick positions for any window, at most about eight. */
export function ticksBetween(from: number, to: number): number[] {
  const width = to - from;
  if (!(width > 0)) return [from];
  const step = [10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000].find((candidate) => width / candidate <= 8) ?? 2000;
  const ticks: number[] = [];
  for (let tick = Math.ceil(from / step) * step; tick <= to + 1e-6; tick += step) ticks.push(tick);
  return ticks;
}

/**
 * Value of a channel at lap distance `d`, interpolated between neighbouring
 * samples of the run that spans it. `null` when no run covers `d` or the
 * neighbours are further apart than `maxGapM` (a hole in the data). `step`
 * holds the previous value instead of interpolating (gear).
 */
export function valueAt(runs: ChannelRuns | undefined, d: number, options: { maxGapM?: number; step?: boolean } = {}): number | null {
  if (!runs) return null;
  const maxGap = options.maxGapM ?? 40;
  for (const run of runs) {
    if (run.length === 0 || d < run[0][0] || d > run[run.length - 1][0]) continue;
    let low = 0;
    let high = run.length - 1;
    while (high - low > 1) {
      const mid = (low + high) >> 1;
      if (run[mid][0] <= d) low = mid;
      else high = mid;
    }
    const [d0, v0] = run[low];
    const [d1, v1] = run[high];
    if (d1 - d0 > maxGap) return null;
    if (options.step || d1 === d0) return d >= d1 ? v1 : v0;
    return v0 + ((v1 - v0) * (d - d0)) / (d1 - d0);
  }
  return null;
}

/** Runs clipped to a window; the cut ends are interpolated so no data outside leaks in. */
export function clipRuns(runs: ChannelRuns | undefined, range: DistanceRange | null): ChannelRuns {
  if (!runs) return [];
  if (!range) return runs;
  const clipped: ChannelRuns = [];
  for (const run of runs) {
    if (run.length < 2 || run[run.length - 1][0] < range.from || run[0][0] > range.to) continue;
    const points: TracePoint[] = [];
    for (let i = 0; i < run.length; i += 1) {
      const [d, v] = run[i];
      if (d < range.from) {
        const next = run[i + 1];
        if (next && next[0] > range.from) points.push([range.from, v + ((next[1] - v) * (range.from - d)) / (next[0] - d)]);
        continue;
      }
      if (d > range.to) {
        const previous = run[i - 1];
        if (previous && previous[0] < range.to) {
          points.push([range.to, previous[1] + ((v - previous[1]) * (range.to - previous[0])) / (d - previous[0])]);
        }
        break;
      }
      points.push([d, v]);
    }
    if (points.length >= 2) clipped.push(points);
  }
  return clipped;
}

/* ---------------------------------------------------------------- insights */

function span(
  trace: LapTrace | undefined,
  key: ChannelKey,
  range: DistanceRange | null = null,
): { min: number; max: number } | null {
  if (trace?.state !== "available") return null;
  const runs = trace.channels[key];
  if (!runs) return null;
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const run of clipRuns(runs, range)) {
    for (const [, value] of run) {
      if (value < min) min = value;
      if (value > max) max = value;
    }
  }
  return Number.isFinite(min) ? { min, max } : null;
}

/** Share of the recorded throttle distance spent at (nearly) full throttle, 0..100. */
function fullThrottleShare(trace: LapTrace | undefined, range: DistanceRange | null = null): number | null {
  if (trace?.state !== "available") return null;
  const runs = trace.channels.throttle;
  if (!runs) return null;
  let total = 0;
  let full = 0;
  for (const run of clipRuns(runs, range)) {
    for (let i = 1; i < run.length; i += 1) {
      const length = run[i][0] - run[i - 1][0];
      total += length;
      if (run[i][1] >= FULL_THROTTLE_PERCENT && run[i - 1][1] >= FULL_THROTTLE_PERCENT) full += length;
    }
  }
  return total > 0 ? (full / total) * 100 : null;
}

function seconds(ms: number) {
  return (Math.abs(ms) / 1000).toFixed(3);
}

/**
 * Differences between two laps derived from lap times and, when both traces are
 * available, measured speed and throttle. Empty without a reference lap.
 */
export function lapDifferences(
  target: AnalysisLap | undefined,
  reference: AnalysisLap | null,
  targetTrace: LapTrace | undefined,
  referenceTrace: LapTrace | undefined,
  options: {
    /** Scope the trace-derived differences to this window instead of the whole lap. */
    range?: DistanceRange | null;
    /** Time difference over the window in seconds (positive = target slower); replaces the lap-time line. */
    timeDeltaS?: number | null;
  } = {},
): Difference[] {
  if (!target || !reference) return [];
  if (target.id === reference.id) return [{ text: "Target and reference are the same lap." }];
  const range = options.range ?? null;
  const items: Difference[] = [];
  const delta = range ? (options.timeDeltaS === null || options.timeDeltaS === undefined ? null : options.timeDeltaS * 1000) : target.ms - reference.ms;
  if (delta !== null) {
    const rounded = Math.round(delta);
    items.push({
      text:
        rounded === 0
          ? range
            ? "No measurable time difference in this region"
            : "Same lap time as the reference lap"
          : `${rounded > 0 ? "+" : "-"}${seconds(rounded)}s (Lap ${target.lap} ${rounded > 0 ? "slower" : "faster"})`,
      emphasis: true,
    });
  }

  const minimum = [span(targetTrace, "speed", range), span(referenceTrace, "speed", range)];
  if (minimum[0] && minimum[1]) {
    const lowest = Math.round(minimum[0].min - minimum[1].min);
    if (lowest !== 0) items.push({ text: `${lowest < 0 ? "Lower" : "Higher"} minimum speed (${lowest > 0 ? "+" : "-"}${Math.abs(lowest)} km/h)` });
    const top = Math.round(minimum[0].max - minimum[1].max);
    if (top !== 0) items.push({ text: `${top < 0 ? "Lower" : "Higher"} top speed (${top > 0 ? "+" : "-"}${Math.abs(top)} km/h)` });
  }
  const targetFull = fullThrottleShare(targetTrace, range);
  const referenceFull = fullThrottleShare(referenceTrace, range);
  if (targetFull !== null && referenceFull !== null) {
    const share = Math.round(targetFull - referenceFull);
    if (share !== 0) {
      items.push({
        text: `${share < 0 ? "Less" : "More"} time at full throttle (${share > 0 ? "+" : "-"}${Math.abs(share)}% of ${range ? "region" : "lap"})`,
      });
    }
  }
  return items;
}

function missingChannels(trace: LapTrace | undefined): ChannelKey[] {
  if (trace?.state !== "available") return [];
  return CHANNEL_KEYS.filter((key) => !trace.channels[key]);
}

/** Data-quality notes drawn only from the laps, their traces and the session. */
export function lapQuality(input: {
  target: AnalysisLap | undefined;
  reference: AnalysisLap | null;
  targetTrace: LapTrace | undefined;
  referenceTrace: LapTrace | undefined;
  lapDataNote: string | null;
  trackModel: string | null;
}): QualityItem[] {
  const { target, reference, targetTrace, referenceTrace } = input;
  const items: QualityItem[] = [];
  if (target) {
    if (target.valid === false) items.push({ text: `Lap ${target.lap} was invalidated by the game`, tone: "amber" });
    else if (target.valid === true) items.push({ text: `Lap ${target.lap} is valid per the game`, tone: "green" });
    if (target.pit) items.push({ text: `Lap ${target.lap} includes the pit lane`, tone: "amber" });
    if (target.startObserved === false) items.push({ text: `Start of Lap ${target.lap} was not observed`, tone: "amber" });
  }
  const traced = [target && targetTrace ? [target, targetTrace] : null, reference && referenceTrace ? [reference, referenceTrace] : null];
  for (const entry of traced) {
    if (!entry) continue;
    const [lap, trace] = entry as [AnalysisLap, LapTrace];
    if (trace.state === "unavailable") {
      items.push({ text: `No telemetry samples for Lap ${lap.lap}`, tone: "amber" });
      continue;
    }
    const missing = missingChannels(trace);
    items.push(
      missing.length
        ? { text: `Lap ${lap.lap} is missing ${missing.join(", ")} data`, tone: "amber" }
        : { text: `Complete telemetry for Lap ${lap.lap}`, tone: "green" },
    );
  }
  if (input.lapDataNote) items.push({ text: input.lapDataNote, tone: "amber" });
  return items;
}

/* ------------------------------------------------------------- track model */

export type TrackModelInfo = {
  track_name: string;
  track_length_m: number;
  revision: number;
  validation_status: string;
};

/**
 * Registered track model for this circuit, or null. Matched on the track name
 * (and length when both sides report one); validated models and newer
 * revisions win.
 */
export function trackModelLabel(
  models: TrackModelInfo[] | null,
  trackName: string | null,
  trackLengthM: number | null,
): string | null {
  if (!models || !trackName) return null;
  const name = trackName.trim().toLowerCase();
  const matches = models
    .filter((model) => model.track_name.trim().toLowerCase() === name)
    .filter(
      (model) =>
        trackLengthM === null || !(model.track_length_m > 0) || Math.abs(model.track_length_m - trackLengthM) <= model.track_length_m * 0.02,
    )
    .sort(
      (a, b) =>
        Number(b.validation_status === "validated") - Number(a.validation_status === "validated") || b.revision - a.revision,
    );
  const best = matches[0];
  return best ? `${best.track_name} r${best.revision} (${best.validation_status})` : null;
}
