"use client";

import { useRef, useState, type PointerEvent } from "react";
import { FilterChips } from "@/features/ui-foundation/FilterChips";
import { Button, Card } from "@/features/ui-foundation/primitives";
import { useAnalysis } from "../analysis-context";
import { deltaAt } from "../delta";
import { valueAt, type ChannelKey, type DistanceRange, type LapTrace } from "../lap-telemetry";
import { MeasuredCharts } from "../MeasuredCharts";
import { formatRange } from "../regions";
import type { RowKey } from "../TelemetryCharts";
import { LegendDot, useElementWidth } from "./parts";
import chartStyles from "../LapAnalysis.module.css";
import styles from "../Views.module.css";

const ROW_LABEL: Record<RowKey, string> = {
  delta: "Delta",
  speed: "Speed",
  throttle: "Throttle",
  brake: "Brake",
  steering: "Steering",
  gear: "Gear",
};
const ALL_ROWS: RowKey[] = ["delta", "speed", "throttle", "brake", "steering", "gear"];
const ROW_HEIGHTS = { delta: 70, speed: 140, throttle: 90, brake: 90, steering: 100, gear: 70 } as const;

function readout(trace: LapTrace | undefined, d: number): string {
  if (trace?.state !== "available") return "No telemetry";
  const at = (key: ChannelKey) => valueAt(trace.channels[key], d, { step: key === "gear" });
  const parts: string[] = [];
  const speed = at("speed");
  if (speed !== null) parts.push(`${Math.round(speed)} km/h`);
  const throttle = at("throttle");
  if (throttle !== null) parts.push(`Thr ${Math.round(throttle)}%`);
  const brake = at("brake");
  if (brake !== null) parts.push(`Brk ${Math.round(brake)}%`);
  const steering = at("steering");
  if (steering !== null) parts.push(`Str ${Math.round(steering)}%`);
  const gear = at("gear");
  if (gear !== null) parts.push(gear === 0 ? "N" : gear < 0 ? "R" : `G${Math.round(gear)}`);
  return parts.length ? parts.join(" · ") : "No data here";
}

/** Whole-lap speed outline with the brushed window; drag on it to select a distance range. */
function RangeStrip({ length, brush, onBrush }: { length: number; brush: DistanceRange | null; onBrush: (range: DistanceRange | null) => void }) {
  const { targetTrace } = useAnalysis();
  const ref = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<{ start: number; now: number } | null>(null);
  const W = 1000;
  const H = 40;
  const speedRuns = targetTrace?.state === "available" ? (targetTrace.channels.speed ?? []) : [];
  const outline = speedRuns
    .map((run) =>
      run
        .map(([m, v], i) => `${i ? "L" : "M"}${((m / length) * W).toFixed(1)},${(H - 4 - (Math.min(v, 350) / 350) * (H - 8)).toFixed(1)}`)
        .join(""),
    )
    .join("");
  const at = (event: PointerEvent<SVGSVGElement>) => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return null;
    return Math.min(length, Math.max(0, ((event.clientX - rect.left) / rect.width) * length));
  };
  const shown = drag ? { from: Math.min(drag.start, drag.now), to: Math.max(drag.start, drag.now) } : brush;
  return (
    <svg
      ref={ref}
      className={styles.strip}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
      onPointerDown={(event) => {
        const start = at(event);
        if (start === null) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        setDrag({ start, now: start });
      }}
      onPointerMove={(event) => {
        const now = at(event);
        if (drag && now !== null) setDrag({ start: drag.start, now });
      }}
      onPointerUp={() => {
        if (!drag) return;
        const range = { from: Math.min(drag.start, drag.now), to: Math.max(drag.start, drag.now) };
        setDrag(null);
        onBrush(range.to - range.from >= 10 ? range : null);
      }}
    >
      <path d={outline} fill="none" stroke="var(--target)" strokeWidth="1.2" vectorEffect="non-scaling-stroke" opacity="0.7" />
      {shown && (
        <rect
          x={(shown.from / length) * W}
          y={0}
          width={Math.max(2, ((shown.to - shown.from) / length) * W)}
          height={H}
          fill="var(--blue)"
          fillOpacity="0.2"
          stroke="var(--blue)"
          strokeWidth="1"
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  );
}

/** Every channel against distance with a precise cursor, zoomed by a brushed range or region. */
export function TelemetryView() {
  const { target, reference, targetTrace, referenceTrace, compare, delta, cursor, pinned, axis, brush, setBrush, range, selection, setSelection } =
    useAnalysis();
  const [hidden, setHidden] = useState<RowKey[]>([]);
  const [frame, width] = useElementWidth<HTMLDivElement>(720);

  const hasGear =
    (targetTrace?.state === "available" && !!targetTrace.channels.gear) ||
    (referenceTrace?.state === "available" && !!referenceTrace.channels.gear);
  const available = ALL_ROWS.filter((row) => (row === "delta" ? compare === "delta" && !!reference : row === "gear" ? hasGear : true));
  const visible = available.filter((row) => !hidden.includes(row));
  const shown: RowKey[] = visible.length ? visible : available.slice(0, 1);

  function toggle(row: RowKey) {
    setHidden((current) => {
      if (!current.includes(row)) return available.length - current.length > 1 ? [...current, row] : current;
      return current.filter((item) => item !== row);
    });
  }

  const zoom = brush ?? range;
  const clamped = zoom && axis ? { from: Math.max(0, zoom.from), to: Math.min(axis.to, zoom.to) } : null;
  const referenceReady = referenceTrace?.state === "available" ? referenceTrace : undefined;
  const deltaNow = cursor !== null && compare === "delta" ? deltaAt(delta, cursor) : null;

  return (
    <Card className={chartStyles.chartCard}>
      <div className={styles.telemetryBar}>
        <FilterChips label="Channels" items={available.map((row) => ({ key: row, label: ROW_LABEL[row] }))} value={shown} onChange={toggle} />
        <div className={styles.rangeInfo}>
          {clamped ? (
            <>
              <span>Range: {formatRange(clamped)}</span>
              <Button
                variant="muted"
                onClick={() => {
                  setBrush(null);
                  if (selection) setSelection(null);
                }}
              >
                Reset
              </Button>
            </>
          ) : (
            <span className={styles.rangeHint}>Shift + drag on the chart or drag on the strip to zoom</span>
          )}
        </div>
      </div>
      {targetTrace?.state === "available" && (
        <output className={styles.readout} aria-live="off">
          <span className={styles.readoutItem}>
            Readout: {cursor !== null ? `${Math.round(cursor).toLocaleString("en-US")} m${pinned ? " (pinned)" : ""}` : "move over the chart"}
          </span>
          {cursor !== null && target && (
            <span className={styles.readoutItem}>
              <LegendDot tone="target" />
              Lap {target.lap} {readout(targetTrace, cursor)}
            </span>
          )}
          {cursor !== null && reference && referenceReady && (
            <span className={styles.readoutItem}>
              <LegendDot tone="reference" />
              Lap {reference.lap} {readout(referenceReady, cursor)}
            </span>
          )}
          {deltaNow !== null && (
            <span className={styles.readoutItem}>
              Δ {deltaNow > 0 ? "+" : deltaNow < 0 ? "-" : ""}
              {Math.abs(deltaNow).toFixed(3)} s
            </span>
          )}
        </output>
      )}
      <div ref={frame}>
        <MeasuredCharts rows={shown} zoom={clamped} band={null} interactive width={Math.min(1400, Math.max(560, width))} rowHeights={ROW_HEIGHTS} />
      </div>
      {axis && targetTrace?.state === "available" && <RangeStrip length={axis.to} brush={clamped} onBrush={(next) => setBrush(next)} />}
    </Card>
  );
}
