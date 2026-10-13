"use client";

import { useId, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { deltaExtent, isDelta, signedAreas, type DeltaSeries } from "./delta";
import { valueAt, type ChannelKey, type ChannelRuns, type DistanceRange } from "./lap-telemetry";
import { referenceTraces, type Trace } from "./traces";
import { MPH_PER_KMH, useUnits, type Units } from "@/features/ui-foundation/units";
import styles from "./LapAnalysis.module.css";

const DEFAULT_WIDTH = 720;
const LABEL_W = 92;
const AXIS_W = 30;
const RIGHT = 8;
const GAP = 8;

export type RowKey = ChannelKey | "delta";

/** Chart rows top to bottom. */
const ORDER: readonly RowKey[] = ["delta", "speed", "throttle", "brake", "steering", "gear"];

type RowSpec = {
  key: RowKey;
  label: string;
  unit: string;
  height: number;
  min: number;
  max: number;
  ticks: readonly number[];
  /** Tick labels when they differ from the plotted value (speed in mph). */
  tickLabels?: readonly number[];
};

/** Speed ticks in mph, placed at their km/h positions since traces stay in km/h. */
const MPH_TICKS = [180, 120, 60, 0];

function inUnits(spec: RowSpec, units: Units): RowSpec {
  if (spec.key !== "speed" || units.speed !== "mph") return spec;
  return { ...spec, unit: "mph", ticks: MPH_TICKS.map((mph) => mph / MPH_PER_KMH), tickLabels: MPH_TICKS };
}

// Illustrative reference rows (fixture session), as drawn in the reference.
const FIXTURE_ROWS: Partial<Record<RowKey, RowSpec>> = {
  speed: { key: "speed", label: "Speed", unit: "km/h", height: 112, min: 0, max: 300, ticks: [300, 200, 100, 0] },
  throttle: { key: "throttle", label: "Throttle", unit: "%", height: 82, min: 0, max: 110, ticks: [100, 50, 0] },
  brake: { key: "brake", label: "Brake", unit: "%", height: 76, min: 0, max: 50, ticks: [45, 25, 0] },
  steering: { key: "steering", label: "Steering", unit: "°", height: 92, min: -115, max: -15, ticks: [-30, -100] },
};

// Measured traces use the true value ranges rather than the reference drawing's.
const MEASURED_ROWS: Record<ChannelKey, RowSpec> = {
  speed: { key: "speed", label: "Speed", unit: "km/h", height: 112, min: 0, max: 350, ticks: [300, 200, 100, 0] },
  throttle: { key: "throttle", label: "Throttle", unit: "%", height: 82, min: 0, max: 105, ticks: [100, 50, 0] },
  brake: { key: "brake", label: "Brake", unit: "%", height: 76, min: 0, max: 105, ticks: [100, 50, 0] },
  steering: { key: "steering", label: "Steering", unit: "%", height: 92, min: -105, max: 105, ticks: [100, 0, -100] },
  gear: { key: "gear", label: "Gear", unit: "", height: 70, min: 0, max: 9, ticks: [8, 4, 0] },
};

const DELTA_HEIGHT = 56;

/** Measured series for one chart: target and, when selected, reference lap. */
export type MeasuredSeries = {
  target: Partial<Record<ChannelKey, ChannelRuns>>;
  reference: Partial<Record<ChannelKey, ChannelRuns>> | null;
};

export const TARGET_COLOR = "#3f63ee";
export const REFERENCE_COLOR = "#b14cf0";

export type CursorSource = "hover" | "pin" | "key" | "leave";

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
  delta,
  width = DEFAULT_WIDTH,
  rowHeights,
  cursor,
  onCursor,
  onBrush,
  label,
}: {
  from: number;
  to: number;
  ticks: number[];
  band: { from: number; to: number; inner?: number } | null;
  channels: ReadonlyArray<RowKey>;
  /** Real sessions: draw these series instead of the illustrative reference traces. */
  measured?: MeasuredSeries;
  /** Time delta drawn in the "delta" row when that row is listed in `channels`. */
  delta?: DeltaSeries | null;
  /** Drawing width in px (the SVG still scales to its container); default 720. */
  width?: number;
  rowHeights?: Partial<Record<RowKey, number>>;
  /** Lap distance of the cursor line in metres, or null. */
  cursor?: number | null;
  /** Makes the chart interactive: hover moves the cursor, click pins it, arrow keys step it. */
  onCursor?: (distance: number | null, source: CursorSource) => void;
  /** Shift+drag selects a range; Escape clears it (null). */
  onBrush?: (range: DistanceRange | null) => void;
  label?: string;
}) {
  const traces = measured ? null : referenceTraces(from, to);
  const clipId = useId();
  const units = useUnits();
  const svgRef = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<{ start: number; now: number } | null>(null);
  const plotLeft = LABEL_W + AXIS_W;
  const plotW = width - plotLeft - RIGHT;
  const span = to - from || 1;
  const x = (d: number) => plotLeft + ((d - from) / span) * plotW;

  const rows: RowSpec[] = ORDER.filter((key) => channels.includes(key)).flatMap((key): RowSpec[] => {
    if (key === "delta") {
      if (!measured) return [];
      const extent = isDelta(delta) ? deltaExtent(delta.points, from, to) : 0.5;
      return [
        {
          key: "delta",
          label: "Delta",
          unit: "s",
          height: rowHeights?.delta ?? DELTA_HEIGHT,
          min: -extent,
          max: extent,
          ticks: [extent, 0, -extent],
        },
      ];
    }
    const spec = measured ? MEASURED_ROWS[key] : FIXTURE_ROWS[key];
    return spec ? [{ ...inUnits(spec, units), height: rowHeights?.[key] ?? spec.height }] : [];
  });
  let top = 6;
  const layout = rows.map((row) => {
    const y0 = top;
    top += row.height + GAP;
    return { ...row, y0 };
  });
  const plotBottom = top - GAP;
  const H = plotBottom + 46;

  const interactive = !!onCursor;
  const target = measured?.target;
  const reference = measured?.reference ?? null;

  function distanceOf(event: PointerEvent<SVGSVGElement>): number | null {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    if (rect.width <= 0) return null;
    const view = ((event.clientX - rect.left) / rect.width) * width;
    if (view < plotLeft || view > width - RIGHT) return null;
    return Math.min(to, Math.max(from, from + ((view - plotLeft) / plotW) * span));
  }

  function onPointerDown(event: PointerEvent<SVGSVGElement>) {
    const d = distanceOf(event);
    if (d === null) return;
    if (event.shiftKey && onBrush) {
      event.currentTarget.setPointerCapture(event.pointerId);
      setDrag({ start: d, now: d });
      return;
    }
    onCursor?.(d, "pin");
  }

  function onPointerMove(event: PointerEvent<SVGSVGElement>) {
    const d = distanceOf(event);
    if (drag && d !== null) {
      setDrag({ start: drag.start, now: d });
      return;
    }
    if (d !== null) onCursor?.(d, "hover");
  }

  function onPointerUp() {
    if (drag) {
      const range = { from: Math.min(drag.start, drag.now), to: Math.max(drag.start, drag.now) };
      setDrag(null);
      if (range.to - range.from >= 10) onBrush?.(range);
    }
  }

  function onKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const step = event.shiftKey ? 100 : 10;
    const current = cursor ?? (from + to) / 2;
    let next: number | null = null;
    if (event.key === "ArrowLeft") next = current - step;
    else if (event.key === "ArrowRight") next = current + step;
    else if (event.key === "Home") next = from;
    else if (event.key === "End") next = to;
    else if (event.key === "Escape" && onBrush) {
      onBrush(null);
      return;
    }
    if (next === null) return;
    event.preventDefault();
    onCursor?.(Math.min(to, Math.max(from, next)), "key");
  }

  const names = rows.map((row) => row.label).join(", ");
  const compared = measured && !measured.reference ? "." : ", reference lap in purple.";
  const description =
    label ?? `Telemetry comparison from ${Math.round(from)} to ${Math.round(to)} metres: ${names}. Target lap in blue${compared}`;

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${width} ${H}`}
      className={styles.telemetry}
      role="img"
      aria-label={interactive ? `${description} Use the arrow keys to move the cursor.` : description}
      tabIndex={interactive ? 0 : undefined}
      data-interactive={interactive ? "true" : undefined}
      onPointerDown={interactive ? onPointerDown : undefined}
      onPointerMove={interactive ? onPointerMove : undefined}
      onPointerUp={interactive ? onPointerUp : undefined}
      onPointerLeave={interactive ? () => onCursor?.(null, "leave") : undefined}
      onKeyDown={interactive ? onKeyDown : undefined}
    >
      <defs>
        <clipPath id={`${clipId}-plot`}>
          <rect x={plotLeft} y={0} width={plotW} height={plotBottom + 4} />
        </clipPath>
      </defs>
      {band && (
        <g>
          <rect x={x(band.from)} y={0} width={Math.max(0, x(band.to) - x(band.from))} height={plotBottom + 4} fill="var(--blue-soft)" opacity="0.75" />
          {band.inner !== undefined && (
            <rect x={x(band.inner)} y={0} width={x(band.to) - x(band.inner)} height={plotBottom + 4} fill="var(--selected-bg)" opacity="0.6" />
          )}
        </g>
      )}
      {layout.map((row) => {
        const y = (v: number) => row.y0 + (1 - (v - row.min) / (row.max - row.min)) * row.height;
        const key = row.key;
        const missing =
          measured && key !== "delta" && !measured.target[key] && !measured.reference?.[key];
        const zeroY = y(0);
        return (
          <g key={row.key}>
            <text x={0} y={row.y0 + 14} className={styles.rowLabel}>
              {row.label}
            </text>
            {row.unit && (
              <text x={0} y={row.y0 + 30} className={styles.rowUnit}>
                {row.unit}
              </text>
            )}
            {row.ticks.map((tick, tickIndex) => (
              <g key={tick}>
                <line x1={plotLeft} x2={width - RIGHT} y1={y(tick)} y2={y(tick)} stroke="var(--chart-grid)" />
                <text x={plotLeft - 8} y={y(tick) + 3.5} textAnchor="end" className={styles.tick}>
                  {row.tickLabels?.[tickIndex] ?? (key === "delta" && tick > 0 ? `+${tick}` : tick)}
                </text>
              </g>
            ))}
            {ticks.map((tick) => (
              <line key={tick} x1={x(tick)} x2={x(tick)} y1={row.y0} y2={row.y0 + row.height} stroke="var(--divider)" />
            ))}
            {missing && (
              <text x={plotLeft + plotW / 2} y={row.y0 + row.height / 2 + 4} textAnchor="middle" className={styles.tick}>
                Channel not recorded
              </text>
            )}
            {key === "delta" ? (
              <g clipPath={`url(#${clipId}-plot)`}>
                {isDelta(delta) ? (
                  <>
                    {(["positive", "negative"] as const).flatMap((sign) =>
                      signedAreas(delta.points)[sign].map((polygon, index) => {
                        const outline = polygon.map(([d, v], i) => `${i ? "L" : "M"}${x(d).toFixed(1)},${y(v).toFixed(1)}`).join("");
                        const last = polygon[polygon.length - 1];
                        const first = polygon[0];
                        return (
                          <path
                            key={`${sign}-${index}`}
                            d={`${outline}L${x(last[0]).toFixed(1)},${zeroY.toFixed(1)}L${x(first[0]).toFixed(1)},${zeroY.toFixed(1)}Z`}
                            fill={sign === "positive" ? "var(--loss)" : "var(--gain)"}
                            fillOpacity="0.16"
                            stroke="none"
                          />
                        );
                      }),
                    )}
                    <path d={path(delta.points, x, y)} fill="none" stroke="var(--text-2)" strokeWidth="1.4" strokeLinejoin="round" />
                  </>
                ) : (
                  <text x={plotLeft + plotW / 2} y={row.y0 + row.height / 2 + 4} textAnchor="middle" className={styles.tick}>
                    {delta && "unavailable" in delta ? delta.unavailable : "Delta not available"}
                  </text>
                )}
                <line x1={plotLeft} x2={width - RIGHT} y1={zeroY} y2={zeroY} stroke="var(--chart-axis)" strokeWidth="0.8" />
              </g>
            ) : measured ? (
              <g clipPath={`url(#${clipId}-plot)`}>
                {measured.reference && (
                  <path d={runsPath(measured.reference[key], x, y)} fill="none" stroke={REFERENCE_COLOR} strokeWidth="1.5" strokeLinejoin="round" />
                )}
                <path d={runsPath(measured.target[key], x, y)} fill="none" stroke={TARGET_COLOR} strokeWidth="1.5" strokeLinejoin="round" />
              </g>
            ) : (
              traces &&
              key !== "gear" && (
                <>
                  <path d={path(traces[key as keyof typeof traces].reference, x, y)} fill="none" stroke={REFERENCE_COLOR} strokeWidth="1.5" strokeLinejoin="round" />
                  <path d={path(traces[key as keyof typeof traces].target, x, y)} fill="none" stroke={TARGET_COLOR} strokeWidth="1.5" strokeLinejoin="round" />
                </>
              )
            )}
            {measured && cursor !== null && cursor !== undefined && key !== "delta" && (
              <>
                {[
                  { runs: reference?.[key], color: REFERENCE_COLOR },
                  { runs: target?.[key], color: TARGET_COLOR },
                ].map(({ runs, color }) => {
                  const value = valueAt(runs, cursor, { step: key === "gear" });
                  return value === null ? null : (
                    <circle key={color} cx={x(cursor)} cy={y(value)} r="3" fill={color} stroke="var(--surface)" strokeWidth="1" />
                  );
                })}
              </>
            )}
          </g>
        );
      })}
      {cursor !== null && cursor !== undefined && cursor >= from && cursor <= to && (
        <line x1={x(cursor)} x2={x(cursor)} y1={0} y2={plotBottom + 4} stroke="var(--text-2)" strokeWidth="1" strokeDasharray="3 3" />
      )}
      {drag && (
        <rect
          x={x(Math.min(drag.start, drag.now))}
          y={0}
          width={Math.abs(x(drag.now) - x(drag.start))}
          height={plotBottom + 4}
          fill="var(--blue)"
          fillOpacity="0.14"
        />
      )}
      {ticks.map((tick) => (
        <text key={tick} x={x(tick)} y={plotBottom + 22} textAnchor="middle" className={styles.tick}>
          {tick.toLocaleString("en-US")}
        </text>
      ))}
      <text x={plotLeft + plotW / 2} y={plotBottom + 42} textAnchor="middle" className={styles.axisTitle}>
        {channels.includes("delta") && isDelta(delta) ? "Distance (m) · Delta (s), derived from speed" : "Distance (m)"}
      </text>
    </svg>
  );
}
