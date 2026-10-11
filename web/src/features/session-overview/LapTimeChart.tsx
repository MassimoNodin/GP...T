import type { ProgressionPoint } from "@/features/review-data/fixtures";
import { formatAxisLapTime, formatDelta, formatLapTime } from "@/features/review-data/format";
import styles from "./SessionOverview.module.css";

const W = 470;
const H = 232;
const PAD = { left: 34, right: 10, top: 8, bottom: 30 };

const TONES: Record<ProgressionPoint["tone"], string> = {
  blue: "#3b8fe0",
  green: "#22a36b",
  purple: "#9b34ef",
  orange: "#eba43a",
  navy: "#2f5fb8",
};

export function LapTimeChart({
  points,
  yMin,
  yMax,
  yStep,
  xMax,
  selected,
  bestMs,
}: {
  points: ProgressionPoint[];
  yMin: number;
  yMax: number;
  yStep: number;
  xMax: number;
  selected: { lap: number; ms: number };
  bestMs: number | null;
}) {
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const xDomain = Math.max(xMax, 1);
  const x = (lap: number) => PAD.left + (lap / xDomain) * plotW;
  const y = (ms: number) =>
    PAD.top + (1 - (Math.min(Math.max(ms, yMin), yMax) - yMin) / (yMax - yMin)) * plotH;

  const yTicks: number[] = [];
  for (let value = yMin; value <= yMax + 1; value += yStep) yTicks.push(value);
  const xTickStep = xMax > 40 ? 10 : 5;
  const xTicks: number[] = [];
  for (let value = 0; value <= xMax; value += xTickStep) xTicks.push(value);

  // Selected marker: the plotted point for the lap, or the curve position.
  const plotted = points.find((point) => point.lap === selected.lap);
  let markerMs = plotted?.ms;
  if (markerMs === undefined) {
    const before = [...points].reverse().find((point) => point.lap < selected.lap);
    const after = points.find((point) => point.lap > selected.lap);
    if (before && after) {
      const t = (selected.lap - before.lap) / (after.lap - before.lap);
      markerMs = before.ms + (after.ms - before.ms) * t;
    } else {
      markerMs = (before ?? after)?.ms ?? selected.ms;
    }
  }
  const mx = x(selected.lap);
  const my = y(markerMs);
  const delta = bestMs === null ? null : selected.ms - bestMs;

  return (
    <div className={styles.chartWrap}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className={styles.chart}
        role="img"
        aria-label={`Lap time progression across ${points.length} laps. Lap ${selected.lap} selected at ${formatLapTime(selected.ms)}.`}
      >
        {yTicks.map((tick) => (
          <g key={tick}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(tick)} y2={y(tick)} stroke="#eef1f4" />
            <text x={PAD.left - 8} y={y(tick) + 3.5} textAnchor="end" className={styles.axis}>
              {formatAxisLapTime(tick)}
            </text>
          </g>
        ))}
        {xTicks.map((tick) => (
          <g key={tick}>
            <line x1={x(tick)} x2={x(tick)} y1={PAD.top} y2={H - PAD.bottom} stroke="#f1f3f6" />
            <text x={x(tick)} y={H - 10} textAnchor="middle" className={styles.axis}>
              {tick}
            </text>
          </g>
        ))}
        <line x1={mx} x2={mx} y1={PAD.top} y2={H - PAD.bottom} stroke="#8fb7f3" strokeWidth="1.2" />
        {points.slice(1).map((point, index) => {
          const previous = points[index];
          const tone = previous.tone === "orange" || previous.tone === "navy" ? "#cdd2d8" : TONES[previous.tone];
          return (
            <line
              key={`seg-${point.lap}`}
              x1={x(previous.lap)}
              y1={y(previous.ms)}
              x2={x(point.lap)}
              y2={y(point.ms)}
              stroke={tone}
              strokeOpacity={0.75}
              strokeWidth="1.3"
            />
          );
        })}
        {points.map((point) =>
          point.lap === selected.lap ? null : (
            <circle key={point.lap} cx={x(point.lap)} cy={y(point.ms)} r="2.9" fill={TONES[point.tone]} />
          ),
        )}
        <circle cx={mx} cy={my} r="6" fill="#2563eb" stroke="#fff" strokeWidth="1.6" />
      </svg>
      <div
        className={styles.tooltip}
        data-flip={mx / W > 0.6 ? "true" : undefined}
        style={{ left: `${(mx / W) * 100}%`, top: `${(Math.max(my - 112, 0) / H) * 100}%` }}
        aria-hidden="true"
      >
        <div className={styles.tooltipLap}>Lap {selected.lap}</div>
        <div className={styles.tooltipTime}>{formatLapTime(selected.ms)}</div>
        {delta !== null && (
          <div className={styles.tooltipDelta} data-sign={delta > 0 ? "pos" : delta < 0 ? "neg" : "zero"}>
            {formatDelta(delta)}
          </div>
        )}
      </div>
    </div>
  );
}
