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

export const TARGET_COLOR = "#3f63ee";
export const REFERENCE_COLOR = "#b14cf0";

function path(trace: Trace, x: (d: number) => number, y: (v: number) => number) {
  return trace.map(([d, v], i) => `${i ? "L" : "M"}${x(d).toFixed(1)},${y(v).toFixed(1)}`).join("");
}

export function TelemetryCharts({
  from,
  to,
  ticks,
  band,
  channels,
}: {
  from: number;
  to: number;
  ticks: number[];
  band: { from: number; to: number; inner?: number } | null;
  channels: ReadonlyArray<(typeof ROWS)[number]["key"]>;
}) {
  const traces = referenceTraces(from, to);
  const plotLeft = LABEL_W + AXIS_W;
  const plotW = W - plotLeft - RIGHT;
  const x = (d: number) => plotLeft + ((d - from) / (to - from)) * plotW;
  const rows = ROWS.filter((row) => channels.includes(row.key));
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
      aria-label={`Telemetry comparison from ${from} to ${to} metres: ${rows.map((row) => row.label).join(", ")}. Target lap in blue, reference lap in purple.`}
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
        const data = traces[row.key];
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
            <path d={path(data.reference, x, y)} fill="none" stroke={REFERENCE_COLOR} strokeWidth="1.5" strokeLinejoin="round" />
            <path d={path(data.target, x, y)} fill="none" stroke={TARGET_COLOR} strokeWidth="1.5" strokeLinejoin="round" />
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
