import type { ProgressionPoint } from "@/features/review-data/fixtures";
import { formatDelta, formatLapTime } from "@/features/review-data/format";
import { formatAxisValue, formatMetricValue, type ChartMetric, type LapMarker } from "./overview-data";
import styles from "./SessionOverview.module.css";

const MARKER_LABELS: Record<LapMarker["kind"], string> = { pit: "PIT", sc: "SC", vsc: "VSC" };

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
  metric = "lap",
  selectedValue,
  reference,
  markers = [],
}: {
  points: ProgressionPoint[];
  yMin: number;
  yMax: number;
  yStep: number;
  xMax: number;
  selected: { lap: number; ms: number };
  bestMs: number | null;
  /** What `points` plot; the reference chart is always lap time. */
  metric?: ChartMetric;
  /** Plotted value of the selected lap when it differs from its lap time. */
  selectedValue?: number;
  /** Compare mode: the reference lap, drawn as a ring. */
  reference?: { lap: number; value: number } | null;
  /** Pit and safety car laps, tagged along the bottom of the plot. */
  markers?: LapMarker[];
}) {
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const xDomain = Math.max(xMax, 1);
  const x = (lap: number) => PAD.left + (lap / xDomain) * plotW;
  const y = (value: number) =>
    PAD.top + (1 - (Math.min(Math.max(value, yMin), yMax) - yMin) / (yMax - yMin)) * plotH;

  const yTicks: number[] = [];
  for (let value = yMin; value <= yMax + 1; value += yStep) yTicks.push(value);
  const xTickStep = xMax > 40 ? 10 : 5;
  const xTicks: number[] = [];
  for (let value = 0; value <= xMax; value += xTickStep) xTicks.push(value);

  // Selected marker: the plotted point for the lap, or the curve position.
  const own = selectedValue ?? selected.ms;
  const plotted = points.find((point) => point.lap === selected.lap);
  // A lap left off the curve (an excluded outlier) still sits at its own value.
  let markerValue = plotted?.ms ?? selectedValue;
  if (markerValue === undefined) {
    const before = [...points].reverse().find((point) => point.lap < selected.lap);
    const after = points.find((point) => point.lap > selected.lap);
    if (before && after) {
      const t = (selected.lap - before.lap) / (after.lap - before.lap);
      markerValue = before.ms + (after.ms - before.ms) * t;
    } else {
      markerValue = (before ?? after)?.ms ?? own;
    }
  }
  const mx = x(selected.lap);
  const my = y(markerValue);
  const delta = bestMs === null || metric !== "lap" ? null : selected.ms - bestMs;
  const showReference = reference && reference.lap !== selected.lap;
  const metricName = metric === "lap" ? "Lap time" : metric === "delta" ? "Delta to best" : `Sector ${metric.slice(1)} time`;

  return (
    <div className={styles.chartWrap}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className={styles.chart}
        role="img"
        aria-label={`${metricName} progression across ${points.length} laps. Lap ${selected.lap} selected at ${formatMetricValue(metric, own)}${showReference ? `, compared with lap ${reference.lap}` : ""}.`}
      >
        {yTicks.map((tick) => (
          <g key={tick}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(tick)} y2={y(tick)} stroke="#eef1f4" />
            <text x={PAD.left - 8} y={y(tick) + 3.5} textAnchor="end" className={styles.axis}>
              {formatAxisValue(metric, tick)}
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
        {markers.map((marker) => {
          // Stack a lap's tags upward from the axis.
          const stack = markers.filter((other) => other.lap === marker.lap).indexOf(marker);
          const label = MARKER_LABELS[marker.kind];
          const width = label.length * 5.4 + 8;
          const top = H - PAD.bottom - 14 - stack * 15;
          return (
            <g key={`${marker.kind}-${marker.lap}`} className={styles.lapMarker} data-kind={marker.kind}>
              <rect x={x(marker.lap) - width / 2} y={top} width={width} height={12} rx={3} />
              <text x={x(marker.lap)} y={top + 9} textAnchor="middle">
                {label}
              </text>
            </g>
          );
        })}
        <circle cx={mx} cy={my} r="6" fill="#2563eb" stroke="#fff" strokeWidth="1.6" />
        {showReference && (
          <circle
            cx={x(reference.lap)}
            cy={y(reference.value)}
            r="6"
            fill="none"
            stroke="var(--reference)"
            strokeWidth="2"
          />
        )}
      </svg>
      <div
        className={styles.tooltip}
        data-flip={mx / W > 0.6 ? "true" : undefined}
        style={{ left: `${(mx / W) * 100}%`, top: `${(Math.max(my - 112, 0) / H) * 100}%` }}
        aria-hidden="true"
      >
        <div className={styles.tooltipLap}>Lap {selected.lap}</div>
        <div className={styles.tooltipTime}>{metric === "lap" ? formatLapTime(selected.ms) : formatMetricValue(metric, own)}</div>
        {delta !== null && (
          <div className={styles.tooltipDelta} data-sign={delta > 0 ? "pos" : delta < 0 ? "neg" : "zero"}>
            {formatDelta(delta)}
          </div>
        )}
        {showReference && <div className={styles.tooltipDelta}>vs Lap {reference.lap}</div>}
      </div>
    </div>
  );
}
