"use client";

import { useEffect, useState } from "react";
import type { LiveTelemetryRecord } from "@/lib/api";
import {
  EMPTY_BROWSER_TELEMETRY_HISTORY,
  collectBrowserTelemetryPoint,
  type BrowserTelemetryPoint,
} from "@/lib/live-chart.mjs";

const chart = { left: 82, right: 790, top: 24, bottom: 210 };
const lanes = [
  { key: "speedKph", label: "SPEED", unit: "km/h", min: 0, max: 500, y: 58, color: "#e2b15c" },
  { key: "throttle", label: "THROTTLE", unit: "%", min: 0, max: 1, y: 122, color: "#74c7b5" },
  { key: "brake", label: "BRAKE", unit: "%", min: 0, max: 1, y: 186, color: "#e68670" },
] as const;

export default function LiveTelemetryChart({
  telemetry,
  sourceState,
}: {
  telemetry: LiveTelemetryRecord;
  sourceState: string;
}) {
  const [history, setHistory] = useState(EMPTY_BROWSER_TELEMETRY_HISTORY);

  useEffect(() => {
    setHistory((previous) =>
      collectBrowserTelemetryPoint(previous, telemetry, sourceState),
    );
  }, [sourceState, telemetry]);

  const points = history.points;
  const latest = points.at(-1) ?? null;
  const domainEnd = latest?.sessionTimeS ?? 0;
  const domainSpan = Math.min(60, Math.max(1, domainEnd));
  const domainStart = Math.max(0, domainEnd - domainSpan);
  const xFor = (time: number) =>
    chart.left + ((time - domainStart) / domainSpan) * (chart.right - chart.left);
  const summary = latest
    ? `Latest browser-observed values: speed ${format(latest.speedKph, 0)} kilometres per hour, throttle ${formatPercent(latest.throttle)}, brake ${formatPercent(latest.brake)}.`
    : "Waiting for a fresh telemetry frame with valid source identity.";
  const epochAvailable =
    typeof telemetry.source_epoch === "string" && telemetry.source_epoch.length > 0;
  const observationState =
    sourceState === "paused"
      ? "PAUSED"
      : telemetry.status === "stale"
        ? "STALE"
        : history.awaitingMonotonicResume
          ? "RESET"
          : "OBSERVING";

  return (
    <section className="live-chart-panel" aria-labelledby="live-chart-title">
      <div className="live-chart-heading">
        <div>
          <h3 id="live-chart-title">Browser-observed telemetry</h3>
          <p>
            Speed, throttle and brake samples seen by this page. Polling runs about
            twice per second, so intermediate acquisition frames are skipped.
          </p>
        </div>
        <span>{observationState} · {points.length} POINTS</span>
      </div>

      {!epochAvailable ? (
        <p className="live-chart-empty" role="status">
          Chart unavailable because this source response has no continuity identity.
          The live monitor remains available above.
        </p>
      ) : points.length === 0 ? (
        <p className="live-chart-empty" role="status">
          {sourceState === "paused"
            ? "Replay is paused. The chart will add a point after playback advances."
            : telemetry.status === "stale"
              ? "The latest telemetry is stale. No new chart point was added."
              : history.awaitingMonotonicResume
                ? "Source time moved backward. History is clear while the chart waits for a later monotonic sample."
              : "Waiting for a fresh telemetry frame with valid source identity."}
        </p>
      ) : (
        <>
          <svg
            className="live-chart-svg"
            viewBox="0 0 820 250"
            role="img"
            aria-label={`${summary} Points are separate observations with no line between them.`}
          >
            {[0, 0.5, 1].map((fraction) => {
              const x = chart.left + fraction * (chart.right - chart.left);
              const label = fraction === 1 ? "LATEST SAMPLE" : `−${Math.round((1 - fraction) * domainSpan)}s`;
              return (
                <g key={fraction}>
                  <line x1={x} x2={x} y1={chart.top} y2={chart.bottom} className="live-chart-grid" />
                  <text x={x} y="232" textAnchor={fraction === 0 ? "start" : fraction === 1 ? "end" : "middle"}>
                    {label}
                  </text>
                </g>
              );
            })}
            {lanes.map((lane) => {
              const field = lane.key;
              return (
                <g key={field}>
                  <line
                    x1={chart.left}
                    x2={chart.right}
                    y1={lane.y}
                    y2={lane.y}
                    className="live-chart-lane"
                  />
                  <text x="4" y={lane.y + 3} className="live-chart-lane-label">
                    {lane.label}
                  </text>
                  <text x={chart.left - 6} y={lane.y - 13} textAnchor="end">
                    {lane.key === "speedKph" ? "500" : "100%"}
                  </text>
                  <text x={chart.left - 6} y={lane.y + 25} textAnchor="end">
                    {lane.key === "speedKph" ? "0" : "0%"}
                  </text>
                  {points.map((point) => {
                    const value = point[field];
                    if (value === null) return null;
                    const ratio = (value - lane.min) / (lane.max - lane.min);
                    const y = lane.y + 17 - ratio * 34;
                    return (
                      <circle
                        key={`${point.epoch}:${point.frameIdentifier}:${field}`}
                        cx={xFor(point.sessionTimeS)}
                        cy={y}
                        r="3.2"
                        fill={lane.color}
                      >
                        <title>
                          {`${lane.label.toLowerCase()} ${lane.key === "speedKph" ? `${Math.round(value)} km/h` : formatPercent(value)} at source time ${point.sessionTimeS.toFixed(2)} s`}
                        </title>
                      </circle>
                    );
                  })}
                </g>
              );
            })}
          </svg>
          <div className="live-chart-legend" aria-hidden="true">
            {lanes.map((lane) => (
              <span key={lane.key}>
                <i style={{ background: lane.color }} />
                {lane.label} {lane.key === "speedKph" ? `(${lane.unit})` : "(%)"}
              </span>
            ))}
          </div>
          <p className="live-chart-axis">
            Horizontal axis: source session time, last 60 seconds. Each dot is an
            admitted telemetry snapshot; no line implies coverage between dots.
            {sourceState === "paused"
              ? " Replay is paused; the chart holds its existing points."
              : telemetry.status === "stale"
                ? " The latest observed point is stale; history is held."
                : ""}
          </p>
          <p className="live-chart-summary">{summary}</p>
        </>
      )}
    </section>
  );
}

function format(value: number | null, decimals: number) {
  return value === null ? "unavailable" : value.toFixed(decimals);
}

function formatPercent(value: number | null) {
  return value === null ? "unavailable" : `${Math.round(value * 100)}%`;
}
