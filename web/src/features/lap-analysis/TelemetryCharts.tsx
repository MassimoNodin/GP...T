import type { ChannelKey, ChannelRuns } from "./lap-telemetry";
import { referenceTraces, type Trace } from "./traces";
import styles from "./LapAnalysis.module.css";

const W = 720;
const LABEL_W = 92;
const AXIS_W = 30;
const RIGHT = 8;
const GAP = 8;

const ROWS = [
  { key: "speed", label: "Speed", unit: "km/h", height: 112, min: 0, max: 300, ticks: [300, 200, 100, 0] },
  { key: "throttle", label: "Throttle", unit: "%", height: 82, min: 0, max: 110, ticks: [100, 50, 0] },
  { key: "brake", label: "Brake", unit: "%", height: 76, min: 0, max: 50, ticks: [45, 25, 0] },
  { key: "steering", label: "Steering", unit: "°", height: 92, min: -115, max: -15, ticks: [-30, -100] },
] as const;

// Measured traces use the true value ranges rather than the reference drawing's.
const MEASURED_ROWS: Record<ChannelKey, { unit: string; min: number; max: number; ticks: readonly number[] }> = {
  speed: { unit: "km/h", min: 0, max: 350, ticks: [300, 200, 100, 0] },
  throttle: { unit: "%", min: 0, max: 105, ticks: [100, 50, 0] },
  brake: { unit: "%", min: 0, max: 105, ticks: [100, 50, 0] },
  steering: { unit: "%", min: -105, max: 105, ticks: [100, 0, -100] },
};

/** Measured series for one chart: target and, when selected, reference lap. */
export type MeasuredSeries = {
  target: Partial<Record<ChannelKey, ChannelRuns>>;
  reference: Partial<Record<ChannelKey, ChannelRuns>> | null;
};

export const TARGET_COLOR = "#3f63ee";
export const REFERENCE_COLOR = "#b14cf0";

function path(trace: Trace, x: (d: number) => number, y: (v: number) => number) {
  return trace.map(([d, v], i) => `${i ? "L" : "M"}${x(d).toFixed(1)},${y(v).toFixed(1)}`).join("");
}

function runsPath(runs: ChannelRuns | undefined, x: (d: number) => number, y: (v: number) => number) {
  return (runs ?? []).map((run) => path(run, x, y)).join("");
}

export function TelemetryCharts({
  from,
  to,
  ticks,
  band,
  channels,
  measured,
}: {
  from: number;
  to: number;
  ticks: number[];
  band: { from: number; to: number; inner?: number } | null;
  channels: ReadonlyArray<(typeof ROWS)[number]["key"]>;
  /** Real sessions: draw these series instead of the illustrative reference traces. */
  measured?: MeasuredSeries;
}) {
  const traces = measured ? null : referenceTraces(from, to);
  const plotLeft = LABEL_W + AXIS_W;
  const plotW = W - plotLeft - RIGHT;
  const x = (d: number) => plotLeft + ((d - from) / (to - from)) * plotW;
  const rows = ROWS.filter((row) => channels.includes(row.key)).map((row) =>
    measured ? { ...row, ...MEASURED_ROWS[row.key] } : row,
  );
  let top = 6;
  const layout = rows.map((row) => {
    const y0 = top;
    top += row.height + GAP;
    return { ...row, y0 };
  });
  const plotBottom = top - GAP;
  const H = plotBottom + 46;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className={styles.telemetry}
      role="img"
      aria-label={`Telemetry comparison from ${from} to ${to} metres: ${rows.map((row) => row.label).join(", ")}. Target lap in blue${measured && !measured.reference ? "." : ", reference lap in purple."}`}
    >
      {band && (
        <g>
          <rect x={x(band.from)} y={0} width={x(band.to) - x(band.from)} height={plotBottom + 4} fill="#dfe9fd" opacity="0.75" />
          {band.inner !== undefined && (
            <rect x={x(band.inner)} y={0} width={x(band.to) - x(band.inner)} height={plotBottom + 4} fill="#cddcfb" opacity="0.6" />
          )}
        </g>
      )}
      {layout.map((row) => {
        const y = (v: number) => row.y0 + (1 - (v - row.min) / (row.max - row.min)) * row.height;
        const key = row.key as ChannelKey;
        const missing = measured && !measured.target[key] && !measured.reference?.[key];
        return (
          <g key={row.key}>
            <text x={0} y={row.y0 + 14} className={styles.rowLabel}>
              {row.label}
            </text>
            <text x={0} y={row.y0 + 30} className={styles.rowUnit}>
              {row.unit}
            </text>
            {row.ticks.map((tick) => (
              <g key={tick}>
                <line x1={plotLeft} x2={W - RIGHT} y1={y(tick)} y2={y(tick)} stroke="#edf0f3" />
                <text x={plotLeft - 8} y={y(tick) + 3.5} textAnchor="end" className={styles.tick}>
                  {tick}
                </text>
              </g>
            ))}
            {ticks.map((tick) => (
              <line key={tick} x1={x(tick)} x2={x(tick)} y1={row.y0} y2={row.y0 + row.height} stroke="#f0f2f5" />
            ))}
            {missing && (
              <text x={plotLeft + plotW / 2} y={row.y0 + row.height / 2 + 4} textAnchor="middle" className={styles.tick}>
                Channel not recorded
              </text>
            )}
            {measured ? (
              <>
                {measured.reference && (
                  <path d={runsPath(measured.reference[key], x, y)} fill="none" stroke={REFERENCE_COLOR} strokeWidth="1.5" strokeLinejoin="round" />
                )}
                <path d={runsPath(measured.target[key], x, y)} fill="none" stroke={TARGET_COLOR} strokeWidth="1.5" strokeLinejoin="round" />
              </>
            ) : (
              traces && (
                <>
                  <path d={path(traces[key].reference, x, y)} fill="none" stroke={REFERENCE_COLOR} strokeWidth="1.5" strokeLinejoin="round" />
                  <path d={path(traces[key].target, x, y)} fill="none" stroke={TARGET_COLOR} strokeWidth="1.5" strokeLinejoin="round" />
                </>
              )
            )}
          </g>
        );
      })}
      {ticks.map((tick) => (
        <text key={tick} x={x(tick)} y={plotBottom + 22} textAnchor="middle" className={styles.tick}>
          {tick.toLocaleString("en-US")}
        </text>
      ))}
      <text x={plotLeft + plotW / 2} y={plotBottom + 42} textAnchor="middle" className={styles.axisTitle}>
        Distance (m)
      </text>
    </svg>
  );
}
