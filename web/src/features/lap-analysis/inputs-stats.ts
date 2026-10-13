import { clipRuns, valueAt, type ChannelRuns, type DistanceRange, type LapTrace } from "./lap-telemetry";

// Driver-input summary of one lap (or a distance window of it) for the Inputs
// tab. Pure: works on the thinned channel runs the trace parser produced.

export const FULL_THROTTLE_PERCENT = 98;
export const COASTING_PERCENT = 5;
export const BRAKE_ZONE_PERCENT = 10;
/** Brake applications closer than this belong to one braking zone. */
export const BRAKE_ZONE_MERGE_M = 20;

export type InputStats = {
  /** Share of the throttle-covered distance at (nearly) full throttle, 0-100. */
  fullThrottlePct: number | null;
  /** Share of the distance with throttle and brake both released, 0-100. */
  coastingPct: number | null;
  brakingZones: number | null;
  brakingDistanceM: number | null;
  peakBrakePct: number | null;
  /** Sum of steering movement (percent of lock) per 100 m driven. */
  steeringPer100m: number | null;
  /** Null when the lap carries no gear channel. */
  gearChanges: number | null;
};

function runs(trace: LapTrace | undefined, key: "throttle" | "brake" | "steering" | "gear", range: DistanceRange | null): ChannelRuns | null {
  if (trace?.state !== "available") return null;
  const source = trace.channels[key];
  if (!source) return null;
  const clipped = clipRuns(source, range);
  return clipped.length ? clipped : null;
}

function shareWhere(
  throttle: ChannelRuns,
  predicate: (midpoint: number, throttleValue: number) => boolean | null,
): number | null {
  let total = 0;
  let matched = 0;
  for (const run of throttle) {
    for (let i = 1; i < run.length; i += 1) {
      const length = run[i][0] - run[i - 1][0];
      if (length <= 0) continue;
      const midpoint = (run[i][0] + run[i - 1][0]) / 2;
      const verdict = predicate(midpoint, Math.max(run[i][1], run[i - 1][1]));
      if (verdict === null) continue;
      total += length;
      if (verdict) matched += length;
    }
  }
  return total > 0 ? (matched / total) * 100 : null;
}

export function inputStats(trace: LapTrace | undefined, range: DistanceRange | null = null): InputStats {
  const throttle = runs(trace, "throttle", range);
  const brake = runs(trace, "brake", range);
  const steering = runs(trace, "steering", range);
  const gear = runs(trace, "gear", range);

  // Full throttle means both ends of a segment were at the threshold.
  let fullThrottlePct: number | null = null;
  if (throttle) {
    let total = 0;
    let full = 0;
    for (const run of throttle) {
      for (let i = 1; i < run.length; i += 1) {
        const length = run[i][0] - run[i - 1][0];
        total += length;
        if (run[i][1] >= FULL_THROTTLE_PERCENT && run[i - 1][1] >= FULL_THROTTLE_PERCENT) full += length;
      }
    }
    fullThrottlePct = total > 0 ? (full / total) * 100 : null;
  }

  const coastingPct =
    throttle && brake
      ? shareWhere(throttle, (midpoint, value) => {
          const braking = valueAt(brake, midpoint);
          if (braking === null) return null;
          return value < COASTING_PERCENT && braking < COASTING_PERCENT;
        })
      : null;

  let zones: Array<[number, number]> = [];
  let peakBrakePct: number | null = null;
  if (brake) {
    for (const run of brake) {
      let start: number | null = null;
      let last = 0;
      for (const [d, value] of run) {
        peakBrakePct = peakBrakePct === null ? value : Math.max(peakBrakePct, value);
        if (value >= BRAKE_ZONE_PERCENT) {
          if (start === null) start = d;
          last = d;
        } else if (start !== null) {
          zones.push([start, last]);
          start = null;
        }
      }
      if (start !== null) zones.push([start, last]);
    }
    zones.sort((a, b) => a[0] - b[0]);
    const merged: Array<[number, number]> = [];
    for (const zone of zones) {
      const previous = merged[merged.length - 1];
      if (previous && zone[0] - previous[1] <= BRAKE_ZONE_MERGE_M) previous[1] = Math.max(previous[1], zone[1]);
      else merged.push([zone[0], zone[1]]);
    }
    zones = merged;
  }

  let steeringPer100m: number | null = null;
  if (steering) {
    let movement = 0;
    let distance = 0;
    for (const run of steering) {
      for (let i = 1; i < run.length; i += 1) {
        movement += Math.abs(run[i][1] - run[i - 1][1]);
        distance += run[i][0] - run[i - 1][0];
      }
    }
    steeringPer100m = distance > 0 ? (movement / distance) * 100 : null;
  }

  let gearChanges: number | null = null;
  if (gear) {
    gearChanges = 0;
    for (const run of gear) {
      for (let i = 1; i < run.length; i += 1) if (run[i][1] !== run[i - 1][1]) gearChanges += 1;
    }
  }

  return {
    fullThrottlePct,
    coastingPct,
    brakingZones: brake ? zones.length : null,
    brakingDistanceM: brake ? zones.reduce((total, [start, end]) => total + (end - start), 0) : null,
    peakBrakePct,
    steeringPer100m,
    gearChanges,
  };
}
