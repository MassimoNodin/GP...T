import { useState } from "react";
import type { ProgressionPoint } from "@/features/review-data/fixtures";
import { chartHeight, useElementWidth } from "@/features/ui-foundation/chart-frame";
import { LapGrid, lapScale } from "@/features/ui-foundation/lap-grid";
import {
  COMPOUND_TINT,
  compoundColor,
  formatAxisValue,
  formatMetricValue,
  type ChartMetric,
  type CompoundBand,
  type LapMarker,
  type PitStop,
} from "./overview-data";
import type { Compound } from "./types";
import styles from "./SessionOverview.module.css";

const MARKER_LABELS: Record<LapMarker["kind"], string> = { pit: "PIT", sc: "SC", vsc: "VSC" };
const FALLBACK_WIDTH = 470;
const PAD = { left: 40, right: 10, top: 8, bottom: 30 };

export function LapTimeChart({
  points,
  yMin,
  yMax,
  yStep,
  xMax,
  metric = "lap",
  markers = [],
  bands = [],
  stops = [],
  compounds = {},
}: {
  points: ProgressionPoint[];
  yMin: number;
  yMax: number;
  yStep: number;
  xMax: number;
  metric?: ChartMetric;
  markers?: LapMarker[];
  bands?: CompoundBand[];
  stops?: PitStop[];
  compounds?: Record<number, Compound>;
}) {
  const [hoveredLap, setHoveredLap] = useState<number | null>(null);
  const hoveredPoint = points.find((point) => point.lap === hoveredLap);
  const [frame, frameWidth] = useElementWidth<HTMLDivElement>(FALLBACK_WIDTH);
  const width = Math.max(frameWidth, 240);
  const height = chartHeight(width);
  const plotWidth = width - PAD.left - PAD.right;
  const plotHeight = height - PAD.top - PAD.bottom;
  const firstLap = 1;
  const lastLap = Math.max(xMax, 1);
  const scale = lapScale(firstLap, lastLap, PAD.left, plotWidth);
  const xPosition = scale.x;
  const yPosition = (value: number) =>
    PAD.top + (1 - (Math.min(Math.max(value, yMin), yMax) - yMin) / (yMax - yMin)) * plotHeight;
  const yTicks: number[] = [];
  for (let value = yMin; value <= yMax + 1; value += yStep) yTicks.push(value);
  const metricName = metric === "lap" ? "Lap time" : metric === "delta" ? "Delta to best" : "Sector " + metric.slice(1) + " time";

  return (
    <div ref={frame} className={styles.chartWrap}>
      <svg
        width={width}
        height={height}
        viewBox={"0 0 " + width + " " + height}
        className={styles.chart}
        role="img"
        aria-label={metricName + " progression across " + points.length + " laps."}
      >
        {bands.map((band) => (
          <rect
            key={band.from + "-" + band.compound}
            x={xPosition(band.from)}
            y={PAD.top}
            width={Math.max(xPosition(band.to >= lastLap ? scale.hi : band.to) - xPosition(band.from), 0)}
            height={plotHeight}
            fill={compoundColor(band.compound)}
            fillOpacity={COMPOUND_TINT}
            data-compound={band.compound}
          />
        ))}
        {yTicks.map((tick) => (
          <g key={tick}>
            <line x1={PAD.left} x2={width - PAD.right} y1={yPosition(tick)} y2={yPosition(tick)} stroke="var(--chart-grid)" />
            <text x={PAD.left - 8} y={yPosition(tick) + 3.5} textAnchor="end" className={styles.axis}>
              {formatAxisValue(metric, tick)}
            </text>
          </g>
        ))}
        <LapGrid scale={scale} firstLap={firstLap} lastLap={lastLap} top={PAD.top} bottom={height - PAD.bottom} labelY={height - 10} labelClassName={styles.axis} />
        {points.slice(1).map((point, index) => {
          const previous = points[index];
          return (
            <line
              key={"seg-" + point.lap}
              x1={xPosition(previous.lap)}
              y1={yPosition(previous.ms)}
              x2={xPosition(point.lap)}
              y2={yPosition(point.ms)}
              stroke="var(--line-strong)"
              strokeWidth="1.5"
            />
          );
        })}
        {points.map((point) => {
          const compound = compounds[point.lap] ?? bands.find((band) => point.lap >= band.from && (point.lap < band.to || band.to >= lastLap))?.compound ?? "unknown";
          return (
            <g
              key={point.lap}
              tabIndex={0}
              aria-label={"Lap " + point.lap + ": " + formatMetricValue(metric, point.ms)}
              onPointerEnter={() => setHoveredLap(point.lap)}
              onPointerLeave={() => setHoveredLap(null)}
              onPointerDown={() => setHoveredLap(point.lap)}
              onFocus={() => setHoveredLap(point.lap)}
              onBlur={() => setHoveredLap(null)}
            >
              <circle cx={xPosition(point.lap)} cy={yPosition(point.ms)} r="10" fill="transparent" />
              <circle cx={xPosition(point.lap)} cy={yPosition(point.ms)} r="5" fill={compoundColor(compound)} stroke="var(--surface)" strokeWidth="1" />
            </g>
          );
        })}
        {stops.map((stop) => (
          <g key={"stop-" + stop.after} role="img" aria-label={"Pit stop after lap " + stop.after}>
            <title>{"Pit stop after lap " + stop.after}</title>
            <line x1={xPosition(stop.at)} x2={xPosition(stop.at)} y1={PAD.top} y2={height - PAD.bottom} stroke="transparent" strokeWidth="12" />
            <line x1={xPosition(stop.at)} x2={xPosition(stop.at)} y1={PAD.top} y2={height - PAD.bottom} stroke="var(--text)" strokeWidth="2" strokeLinecap="round" strokeDasharray="0.1 5" strokeOpacity={0.7} />
          </g>
        ))}
        {markers.map((marker) => {
          if (marker.kind === "pit" && stops.length > 0) return null;
          const stack = markers.filter((other) => other.lap === marker.lap).indexOf(marker);
          const label = MARKER_LABELS[marker.kind];
          const labelWidth = label.length * 5.4 + 8;
          const top = height - PAD.bottom - 14 - stack * 15;
          return (
            <g key={marker.kind + "-" + marker.lap} className={styles.lapMarker} data-kind={marker.kind}>
              <rect x={xPosition(marker.lap) - labelWidth / 2} y={top} width={labelWidth} height={12} rx={3} />
              <text x={xPosition(marker.lap)} y={top + 9} textAnchor="middle">{label}</text>
            </g>
          );
        })}
      </svg>
      {hoveredPoint && (
        <div
          role="tooltip"
          className={styles.chartTooltip}
          style={{ left: Math.max(0, Math.min(xPosition(hoveredPoint.lap) - 80, width - 160)), top: Math.max(58, yPosition(hoveredPoint.ms) - 14) }}
        >
          <strong>{"Lap " + hoveredPoint.lap}</strong>
          <span>{metricName + ": " + formatMetricValue(metric, hoveredPoint.ms)}</span>
        </div>
      )}
    </div>
  );
}
