import type {
  AttemptRegionReport,
  AttemptTrajectoryPreview,
  Comparison,
  CornerRegion,
  ObservedTrajectoryComparisonPreview,
  PairedRegionReport,
  RegionAttempt,
} from "./api";

export type RegionStatus = "faster" | "similar" | "slower" | "unknown";
export type TrackPoint = { distanceM: number; x: number; z: number };
export type TrackRegion = {
  id: string;
  label: string;
  startM: number;
  endM: number;
  status: RegionStatus;
  deltaS?: number | null;
  minimumSpeedKph?: number | null;
  referenceMinimumSpeedKph?: number | null;
  throttlePickupM?: number | null;
  referenceThrottlePickupM?: number | null;
};
export type TrackShape = {
  name: string;
  lengthM: number;
  geometryKind: "observed" | "supplied";
  segments: TrackPoint[][];
  regions: TrackRegion[];
  similarThresholdS: number;
};
export type TraceChannel = "speed" | "throttle" | "brake" | "gear" | "steering";
export type TraceValues = Partial<Record<TraceChannel, Array<number | null>>>;
export type TraceLap = {
  label: string;
  values: TraceValues;
  masks?: Partial<Record<TraceChannel, boolean[]>>;
};
export type LapTraceInput = {
  distanceM: number[];
  target: TraceLap;
  reference: TraceLap;
  deltaS: Array<number | null>;
  deltaMask?: boolean[];
};
export type AnalysisInput = {
  version: 1;
  track?: TrackShape;
  comparison?: LapTraceInput;
};

export const REGION_COLOURS: Record<RegionStatus, string> = {
  faster: "#00bd60",
  similar: "#ffc400",
  slower: "#ff172b",
  unknown: "#a0aec0",
};
export function classifyRegion(
  delta: CornerRegion["delta_change"],
  thresholdS = 0.01,
): RegionStatus {
  if (
    ![
      "diagnostic_region_delta_change",
      "supported_region_delta_change",
    ].includes(delta.status) ||
    !delta.interval_connected_supported_time ||
    !Number.isFinite(delta.delta_change_s)
  )
    return "unknown";
  const value = delta.delta_change_s!;
  return Math.abs(value) <= thresholdS
    ? "similar"
    : value < 0
      ? "faster"
      : "slower";
}
function regionMetrics(observation: RegionAttempt) {
  const pickup = observation.throttle_pickup?.["0.5"];
  return {
    minimumSpeedKph: observation.minimum_speed?.speed_kph ?? null,
    throttlePickupM: pickup?.status === "detected" ? pickup.distance_m : null,
  };
}
export function regionsFromComparison(
  report: PairedRegionReport | null,
  comparison: Comparison | null,
): TrackRegion[] {
  return (report?.regions ?? comparison?.corner_analysis?.regions ?? []).map(
    (region) => {
      const target = regionMetrics(region.target),
        reference = regionMetrics(region.reference);
      const status = classifyRegion(region.delta_change);
      return {
        id: region.identifier,
        label: region.label,
        startM: region.analysis_window_m[0],
        endM: region.analysis_window_m[1],
        status,
        deltaS:
          status === "unknown" ? null : region.delta_change.delta_change_s,
        ...target,
        referenceMinimumSpeedKph: reference.minimumSpeedKph,
        referenceThrottlePickupM: reference.throttlePickupM,
      };
    },
  );
}
export function shapeFromTrajectory(
  trajectory:
    AttemptTrajectoryPreview | ObservedTrajectoryComparisonPreview | null,
  name: string,
  lengthM: number,
  regions: TrackRegion[],
): TrackShape {
  const segments =
    trajectory && "paths" in trajectory
      ? trajectory.paths.target.segments
      : (trajectory?.segments ?? []);
  return {
    name,
    lengthM,
    geometryKind: "observed",
    similarThresholdS: 0.01,
    regions,
    segments: segments.map((segment) =>
      segment.points.map((point) => ({
        distanceM: point.lap_distance_m,
        x: point.world_position_m.x,
        z: point.world_position_m.z,
      })),
    ),
  };
}
export function regionsFromAttempt(
  report: AttemptRegionReport | null,
): TrackRegion[] {
  return (report?.regions ?? []).map((region) => ({
    id: region.identifier,
    label: region.label,
    startM: region.analysis_window_m[0],
    endM: region.analysis_window_m[1],
    status: "unknown",
    ...regionMetrics(region.observations),
  }));
}
export function tracesFromComparison(
  comparison: Comparison,
  targetLabel: string,
  referenceLabel: string,
): LapTraceInput {
  const lap = (trace: Comparison["target_trace"], label: string): TraceLap => {
    const values: TraceValues = {},
      masks: NonNullable<TraceLap["masks"]> = {};
    for (const channel of [
      "speed",
      "throttle",
      "brake",
      "gear",
      "steering",
    ] as const) {
      const source = channel === "speed" ? "speed_mps" : channel;
      if (trace.values[source]) {
        values[channel] = trace.values[source].map((value) =>
          typeof value === "number" && Number.isFinite(value)
            ? value * (channel === "speed" ? 3.6 : 1)
            : null,
        );
        masks[channel] = trace.masks[source];
      }
    }
    return { label, values, masks };
  };
  return {
    distanceM: comparison.distance_m,
    target: lap(comparison.target_trace, targetLabel),
    reference: lap(comparison.reference_trace, referenceLabel),
    deltaS: comparison.delta_s,
    deltaMask: comparison.delta_mask,
  };
}

// Render geometry at region boundaries without connecting separate observed runs.
export function colouredTrackEdges(shape: TrackShape) {
  const edges: Array<{
    a: TrackPoint;
    b: TrackPoint;
    status: RegionStatus;
    regionId: string | null;
  }> = [];
  for (const segment of shape.segments)
    for (let i = 1; i < segment.length; i++) {
      const a = segment[i - 1],
        b = segment[i];
      if (
        ![a.x, a.z, a.distanceM, b.x, b.z, b.distanceM].every(
          Number.isFinite,
        ) ||
        b.distanceM <= a.distanceM
      )
        continue;
      const boundaries = Array.from(
        new Set([
          a.distanceM,
          b.distanceM,
          ...shape.regions
            .flatMap((r) => [r.startM, r.endM])
            .filter((d) => d > a.distanceM && d < b.distanceM),
        ]),
      ).sort((x, y) => x - y);
      const point = (d: number) => {
        const t = (d - a.distanceM) / (b.distanceM - a.distanceM);
        return {
          distanceM: d,
          x: a.x + t * (b.x - a.x),
          z: a.z + t * (b.z - a.z),
        };
      };
      for (let j = 1; j < boundaries.length; j++) {
        const middle = (boundaries[j - 1] + boundaries[j]) / 2;
        const matches = shape.regions.filter(
          (r) => middle >= r.startM && middle < r.endM,
        );
        const region = matches.length === 1 ? matches[0] : null;
        edges.push({
          a: point(boundaries[j - 1]),
          b: point(boundaries[j]),
          status: region?.status ?? "unknown",
          regionId: region?.id ?? null,
        });
      }
    }
  return edges;
}
export function pointAtDistance(
  shape: TrackShape,
  distance: number,
): TrackPoint | null {
  for (const segment of shape.segments)
    for (let i = 0; i < segment.length; i++) {
      const a = segment[i],
        b = segment[i + 1];
      if (a.distanceM === distance) return a;
      if (b && a.distanceM < distance && distance < b.distanceM) {
        const t = (distance - a.distanceM) / (b.distanceM - a.distanceM);
        return {
          distanceM: distance,
          x: a.x + (b.x - a.x) * t,
          z: a.z + (b.z - a.z) * t,
        };
      }
    }
  return null;
}
export function traceRuns(
  distance: number[],
  values: Array<number | null>,
  mask?: boolean[],
  window?: [number, number] | null,
) {
  const runs: Array<Array<{ distance: number; value: number; index: number }>> =
    [];
  let run: (typeof runs)[number] = [];
  for (let i = 0; i < distance.length; i++) {
    const value = values[i],
      d = distance[i];
    if (
      !Number.isFinite(d) ||
      (i > 0 && d <= distance[i - 1]) ||
      !Number.isFinite(value) ||
      (mask && mask[i] !== true) ||
      (window && (d < window[0] || d >= window[1]))
    ) {
      if (run.length) runs.push(run);
      run = [];
      continue;
    }
    run.push({ distance: d, value: value!, index: i });
  }
  if (run.length) runs.push(run);
  return runs;
}
export function nearestSample(distance: number[], requested: number): number {
  let low = 0,
    high = distance.length - 1;
  while (low < high) {
    const mid = (low + high) >>> 1;
    if (distance[mid] < requested) low = mid + 1;
    else high = mid;
  }
  return low > 0 && requested - distance[low - 1] <= distance[low] - requested
    ? low - 1
    : low;
}

export function boundedTraceRuns(
  runs: ReturnType<typeof traceRuns>,
  pointLimit = 2000,
  runLimit = 128,
) {
  const selected =
    runs.length <= runLimit
      ? runs
      : Array.from(
          { length: runLimit },
          (_, i) => runs[Math.round((i * (runs.length - 1)) / (runLimit - 1))],
        );
  const budget = Math.max(
    2,
    Math.floor(pointLimit / Math.max(1, selected.length)),
  );
  return selected.map((run) => {
    if (run.length <= budget) return run;
    const points = [run[0]],
      buckets = Math.floor((budget - 2) / 2);
    for (let i = 0; i < buckets; i++) {
      const start = 1 + Math.floor((i * (run.length - 2)) / buckets),
        end = 1 + Math.floor(((i + 1) * (run.length - 2)) / buckets);
      let low = start,
        high = start;
      for (let j = start + 1; j < end; j++) {
        if (run[j].value < run[low].value) low = j;
        if (run[j].value > run[high].value) high = j;
      }
      points.push(run[Math.min(low, high)]);
      if (low !== high) points.push(run[Math.max(low, high)]);
    }
    points.push(run[run.length - 1]);
    return points;
  });
}
const object = (v: unknown): v is Record<string, unknown> =>
  v !== null && typeof v === "object" && !Array.isArray(v);
const finite = (v: unknown): v is number =>
  typeof v === "number" && Number.isFinite(v);
function requireInput(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
// A bounded local-file input contract; no uploaded file leaves the browser.
export function parseAnalysisInput(value: unknown): AnalysisInput {
  requireInput(
    object(value) && value.version === 1 && (value.track || value.comparison),
    "Expected version 1 with a track or comparison.",
  );
  if (value.track) {
    const t = value.track;
    requireInput(
      object(t) &&
        typeof t.name === "string" &&
        t.name.length <= 160 &&
        finite(t.lengthM) &&
        t.lengthM > 0 &&
        t.lengthM <= 1e7 &&
        ["observed", "supplied"].includes(String(t.geometryKind)),
      "Track name, positive lengthM and geometryKind are required.",
    );
    requireInput(
      finite(t.similarThresholdS) && t.similarThresholdS >= 0,
      "similarThresholdS must be a nonnegative number.",
    );
    requireInput(
      Array.isArray(t.segments) &&
        t.segments.length <= 256 &&
        Array.isArray(t.regions) &&
        t.regions.length <= 128,
      "Track requires segments and up to 128 regions.",
    );
    let count = 0;
    for (const segment of t.segments) {
      requireInput(
        Array.isArray(segment),
        "Each segment must be an array of points.",
      );
      count += segment.length;
      let previous = -Infinity;
      for (const p of segment) {
        requireInput(
          object(p) &&
            finite(p.x) &&
            Math.abs(p.x) <= 1e7 &&
            finite(p.z) &&
            Math.abs(p.z) <= 1e7 &&
            finite(p.distanceM) &&
            p.distanceM >= 0 &&
            p.distanceM <= t.lengthM &&
            p.distanceM > previous,
          "Track points require x/z within ±10,000,000 and increasing distanceM within track length.",
        );
        previous = p.distanceM;
      }
    }
    requireInput(count <= 20_000, "Track geometry exceeds 20,000 points.");
    const ids = new Set<string>();
    for (const r of t.regions) {
      requireInput(
        object(r) &&
          typeof r.id === "string" &&
          r.id.length > 0 &&
          !ids.has(r.id) &&
          typeof r.label === "string" &&
          r.label.length <= 160,
        "Region IDs must be unique and labels must be strings.",
      );
      ids.add(r.id);
      requireInput(
        finite(r.startM) &&
          finite(r.endM) &&
          r.startM >= 0 &&
          r.startM < r.endM &&
          r.endM <= t.lengthM &&
          ["faster", "similar", "slower", "unknown"].includes(String(r.status)),
        "Regions require valid startM/endM bounds and status.",
      );
      for (const key of [
        "deltaS",
        "minimumSpeedKph",
        "referenceMinimumSpeedKph",
        "throttlePickupM",
        "referenceThrottlePickupM",
      ])
        requireInput(
          r[key] == null || finite(r[key]),
          `Region ${key} must be finite or null.`,
        );
      if (finite(r.deltaS) && r.status !== "unknown")
        requireInput(
          r.status ===
            (Math.abs(r.deltaS) <= t.similarThresholdS
              ? "similar"
              : r.deltaS < 0
                ? "faster"
                : "slower"),
          "Region status disagrees with deltaS and similarThresholdS.",
        );
    }
  }
  if (value.comparison) {
    const c = value.comparison;
    requireInput(
      object(c) &&
        Array.isArray(c.distanceM) &&
        c.distanceM.length >= 2 &&
        c.distanceM.length <= 100_000 &&
        c.distanceM.every(
          (d, i, all) =>
            finite(d) && d >= 0 && d <= 1e7 && (i === 0 || d > all[i - 1]),
        ),
      "Comparison distanceM requires 2–100,000 strictly increasing finite distances.",
    );
    const length = c.distanceM.length;
    const validateValues = (v: unknown): v is Array<number | null> =>
      Array.isArray(v) &&
      v.length === length &&
      v.every((n) => n === null || finite(n));
    const validateMask = (v: unknown) =>
      v === undefined ||
      (Array.isArray(v) &&
        v.length === length &&
        v.every((n) => typeof n === "boolean"));
    requireInput(
      validateValues(c.deltaS) &&
        c.deltaS.every((v) => v === null || Math.abs(v) <= 86_400) &&
        validateMask(c.deltaMask),
      "Delta values must be within ±86,400 seconds, and arrays must match distanceM.",
    );
    for (const key of ["target", "reference"]) {
      const lap = c[key];
      requireInput(
        object(lap) &&
          typeof lap.label === "string" &&
          lap.label.length <= 160 &&
          object(lap.values) &&
          (lap.masks === undefined || object(lap.masks)),
        "Each lap needs label, values and optional masks.",
      );
      for (const channel of [
        "speed",
        "throttle",
        "brake",
        "gear",
        "steering",
      ]) {
        const values = lap.values[channel];
        if (values !== undefined)
          requireInput(
            validateValues(values),
            `Lap ${channel} must match distanceM.`,
          );
        requireInput(
          validateMask(object(lap.masks) ? lap.masks[channel] : undefined),
          `Lap ${channel} mask must match distanceM.`,
        );
        if (Array.isArray(values))
          requireInput(
            values.every(
              (v) =>
                v === null ||
                (channel === "speed"
                  ? v >= 0 && v <= 500
                  : channel === "gear"
                    ? Number.isInteger(v) && v >= -1 && v <= 8
                    : channel === "steering"
                      ? Math.abs(v) <= 1
                      : v >= 0 && v <= 1),
            ),
            `Invalid ${channel} units or range.`,
          );
      }
    }
  }
  return value as unknown as AnalysisInput;
}
