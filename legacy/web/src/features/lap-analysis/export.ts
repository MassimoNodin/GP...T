import { formatLapTime, formatSectorTime } from "@/features/review-data/format";
import { deltaAt, isDelta, type DeltaSeries } from "./delta";
import { valueAt, type ChannelKey, type LapTrace } from "./lap-telemetry";

// CSV exports behind "Open in External Tool". Pure: text in, text out.

/** Quotes a field when it holds a comma, quote or line break. */
export function csvField(value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function csvRows(rows: ReadonlyArray<ReadonlyArray<string | number | boolean | null | undefined>>): string {
  return rows.map((row) => row.map(csvField).join(",")).join("\n");
}

export type ExportLap = {
  lap: number;
  ms: number;
  sectors: [number, number, number] | null;
  valid?: boolean | null;
  pit?: boolean;
  tyre?: { label: string; ageLaps: number | null } | null;
};

export const LAP_TIMES_HEADER = ["lap", "lap_time", "lap_time_ms", "s1", "s2", "s3", "valid", "pit", "tyre", "tyre_age"] as const;

/** Lap and sector times of the session, one row per lap. */
export function lapTimesCsv(laps: readonly ExportLap[]): string {
  return csvRows([
    [...LAP_TIMES_HEADER],
    ...laps.map((lap) => [
      lap.lap,
      formatLapTime(lap.ms),
      lap.ms,
      ...[0, 1, 2].map((i) => (lap.sectors ? formatSectorTime(lap.sectors[i]) : "")),
      lap.valid === null || lap.valid === undefined ? "" : lap.valid,
      lap.pit === undefined ? "" : lap.pit,
      lap.tyre?.label ?? "",
      lap.tyre?.ageLaps ?? "",
    ]),
  ]);
}

export const TELEMETRY_STEP_M = 5;

const COLUMNS: Array<{ key: ChannelKey; name: string; digits: number }> = [
  { key: "speed", name: "speed_kph", digits: 1 },
  { key: "throttle", name: "throttle_pct", digits: 1 },
  { key: "brake", name: "brake_pct", digits: 1 },
  { key: "steering", name: "steering_pct", digits: 1 },
  { key: "gear", name: "gear", digits: 0 },
];

/** Header of the telemetry export for the channels the laps actually have. */
export function telemetryHeader(target: LapTrace, reference: LapTrace | null, withDelta: boolean): string[] {
  const has = (trace: LapTrace | null, key: ChannelKey) => trace?.state === "available" && !!trace.channels[key];
  const header = ["distance_m"];
  for (const column of COLUMNS) if (has(target, column.key)) header.push(`target_${column.name}`);
  if (reference) for (const column of COLUMNS) if (has(reference, column.key)) header.push(`reference_${column.name}`);
  if (withDelta) header.push("delta_s");
  return header;
}

/**
 * Both laps on a 5 m distance grid: `distance_m`, the target's channels, the
 * reference's channels and `delta_s` (target - reference, derived from speed)
 * when the delta is available. Returns null when the target has no trace.
 */
export function telemetryCsv(
  target: LapTrace | undefined,
  reference: LapTrace | null | undefined,
  delta: DeltaSeries | null | undefined,
): string | null {
  if (target?.state !== "available") return null;
  const other = reference?.state === "available" ? reference : null;
  const withDelta = isDelta(delta);
  const header = telemetryHeader(target, other, withDelta);
  const end = Math.max(target.length, other?.length ?? 0);
  const rows: Array<Array<string | number>> = [header];
  const cell = (trace: LapTrace, key: ChannelKey, d: number, digits: number): string => {
    if (trace.state !== "available" || !trace.channels[key]) return "";
    const value = valueAt(trace.channels[key], d, { step: key === "gear" });
    return value === null ? "" : value.toFixed(digits);
  };
  for (let d = 0; d <= end + 1e-6; d += TELEMETRY_STEP_M) {
    const row: Array<string | number> = [d];
    for (const column of COLUMNS) {
      if (target.channels[column.key]) row.push(cell(target, column.key, d, column.digits));
    }
    if (other) {
      for (const column of COLUMNS) {
        if (other.channels[column.key]) row.push(cell(other, column.key, d, column.digits));
      }
    }
    if (withDelta) {
      const value = deltaAt(delta, d);
      row.push(value === null ? "" : value.toFixed(3));
    }
    rows.push(row);
  }
  return csvRows(rows);
}

/** File-name safe slug: `Monaco Grand Prix` becomes `monaco-grand-prix`. */
export function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "session";
}
