"use client";

import { useMemo, useRef, useState } from "react";
import type { ChangeEvent, ReactNode } from "react";
import {
  colouredTrackEdges,
  nearestSample,
  parseAnalysisInput,
  pointAtDistance,
  REGION_COLOURS,
  traceRuns,
  boundedTraceRuns,
} from "@/lib/analysis-display";
import type {
  AnalysisInput,
  LapTraceInput,
  TrackShape,
  TraceChannel,
} from "@/lib/analysis-display";
import { CircuitIcon, UploadIcon, WaveIcon } from "./Icons";

export function AnalysisPanel({
  title,
  icon,
  action,
  children,
  className = "",
}: {
  title: string;
  icon?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`ref-panel ${className}`}>
      <header className="ref-panel-heading">
        <span className="ref-panel-icon" aria-hidden="true">
          {icon ?? <WaveIcon size={22} />}
        </span>
        <h2>{title}</h2>
        <div className="ref-panel-action">{action}</div>
      </header>
      {children}
    </section>
  );
}
export function AnalysisFileInput({
  onLoad,
  onReset,
  loaded,
}: {
  onLoad: (input: AnalysisInput) => void;
  onReset: () => void;
  loaded: boolean;
}) {
  const [error, setError] = useState<string | null>(null);
  const sequence = useRef(0);
  async function load(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    const request = ++sequence.current;
    try {
      if (file.size > 8_000_000)
        throw new Error("Choose an analysis JSON file smaller than 8 MB.");
      const input = parseAnalysisInput(JSON.parse(await file.text()));
      if (request === sequence.current) {
        onLoad(input);
        setError(null);
      }
    } catch (cause) {
      if (request === sequence.current)
        setError(
          cause instanceof Error
            ? cause.message
            : "Could not read analysis data.",
        );
    }
  }
  return (
    <div className="analysis-file-controls">
      <label className="ref-button">
        <UploadIcon size={14} /> Load analysis JSON
        <input type="file" accept=".json,application/json" onChange={load} />
      </label>
      {loaded ? (
        <button
          className="ref-button"
          onClick={() => {
            sequence.current++;
            onReset();
            setError(null);
          }}
        >
          Use selected session
        </button>
      ) : null}
      <a className="ref-button" href="/analysis-input-example.json" download>
        Example JSON
      </a>
      {error ? <span role="alert">{error}</span> : null}
    </div>
  );
}
export function TrackAnalysisMap({
  shape,
  selectedRegionId,
  onSelectRegion,
  cursorDistanceM,
}: {
  shape: TrackShape;
  selectedRegionId?: string | null;
  onSelectRegion?: (id: string) => void;
  cursorDistanceM?: number | null;
}) {
  const geometry = useMemo(() => {
    const points = shape.segments
      .flat()
      .filter((p) => [p.x, p.z, p.distanceM].every(Number.isFinite));
    if (points.length < 2) return null;
    let minX = Infinity,
      minZ = Infinity,
      maxX = -Infinity,
      maxZ = -Infinity;
    for (const p of points) {
      minX = Math.min(minX, p.x);
      maxX = Math.max(maxX, p.x);
      minZ = Math.min(minZ, p.z);
      maxZ = Math.max(maxZ, p.z);
    }
    const scale = Math.min(
      510 / Math.max(1, maxX - minX),
      280 / Math.max(1, maxZ - minZ),
    );
    const project = (p: { x: number; z: number }) => ({
      x: 310 + (p.x - (minX + maxX) / 2) * scale,
      y: 180 - (p.z - (minZ + maxZ) / 2) * scale,
    });
    const edges = colouredTrackEdges(shape);
    const paths = new Map<
      string,
      {
        d: string;
        status: keyof typeof REGION_COLOURS;
        regionId: string | null;
      }
    >();
    for (const edge of edges) {
      const a = project(edge.a),
        b = project(edge.b),
        key = `${edge.status}:${edge.regionId}`;
      const run = paths.get(key) ?? {
        d: "",
        status: edge.status,
        regionId: edge.regionId,
      };
      run.d += `M${a.x.toFixed(2)},${a.y.toFixed(2)}L${b.x.toFixed(2)},${b.y.toFixed(2)}`;
      paths.set(key, run);
    }
    return { project, paths: [...paths.values()] };
  }, [shape]);
  const selected = shape.regions.find((r) => r.id === selectedRegionId);
  const cursorPoint =
    cursorDistanceM == null ? null : pointAtDistance(shape, cursorDistanceM);
  return (
    <div className="analysis-map">
      <div className="analysis-map-legend">
        {(["faster", "similar", "slower", "unknown"] as const).map((status) => (
          <span key={status}>
            <i style={{ background: REGION_COLOURS[status] }} />
            {status === "unknown" ? "No timing" : status}
          </span>
        ))}
      </div>
      {geometry ? (
        <svg
          viewBox="0 0 620 360"
          role="img"
          aria-label={`${shape.name} ${shape.geometryKind === "observed" ? "observed path" : "supplied track shape"}, coloured by region time difference`}
        >
          {geometry.paths.map((path, i) => (
            <path
              key={i}
              d={path.d}
              fill="none"
              stroke="#eaf1f5"
              strokeWidth="44"
              strokeLinecap="round"
            />
          ))}
          {geometry.paths.map((path, i) => (
            <path
              key={i}
              d={path.d}
              fill="none"
              stroke="#25394e"
              strokeWidth={path.regionId === selectedRegionId ? 10 : 7}
              strokeLinecap="round"
            />
          ))}
          {geometry.paths.map((path, i) => (
            <path
              key={i}
              d={path.d}
              fill="none"
              stroke={REGION_COLOURS[path.status]}
              strokeWidth={path.regionId === selectedRegionId ? 7 : 4}
              strokeLinecap="round"
            />
          ))}
          {shape.regions.map((region, index) => {
            const point = pointAtDistance(
              shape,
              (region.startM + region.endM) / 2,
            );
            if (!point) return null;
            const p = geometry.project(point),
              active = region.id === selectedRegionId;
            return (
              <g key={region.id}>
                <title>
                  {region.label}: {region.status}
                </title>
                <line
                  x1={p.x}
                  x2={p.x}
                  y1={p.y}
                  y2={p.y - 24}
                  stroke="#9aaac0"
                />
                <circle
                  cx={p.x}
                  cy={p.y - 30}
                  r={active ? 15 : 12}
                  fill={active ? "#0068ff" : "white"}
                  stroke={active ? "#b3d9ff" : "#9aaac0"}
                  strokeWidth={active ? 6 : 1}
                />
                <text
                  x={p.x}
                  y={p.y - 26}
                  textAnchor="middle"
                  fill={active ? "white" : "#243556"}
                  fontSize="12"
                  fontWeight="700"
                >
                  {index + 1}
                </text>
              </g>
            );
          })}
          {cursorPoint
            ? (() => {
                const p = geometry.project(cursorPoint);
                return (
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r="7"
                    fill="#0068ff"
                    stroke="white"
                    strokeWidth="3"
                  />
                );
              })()
            : null}
        </svg>
      ) : (
        <div className="analysis-empty">
          <CircuitIcon size={34} />
          <strong>Track shape unavailable</strong>
          <span>
            Choose a recorded attempt with position data or load a supplied
            track shape.
          </span>
        </div>
      )}
      {selected ? (
        <div className="analysis-map-caption">
          <strong>{selected.label}</strong>
          <span style={{ color: REGION_COLOURS[selected.status] }}>
            {selected.deltaS == null
              ? selected.status === "unknown"
                ? "Time comparison unavailable"
                : `${selected.status} · supplied classification`
              : `${signed(selected.deltaS)} s · ${selected.status}`}
          </span>
        </div>
      ) : null}
      <p className="analysis-source-note">
        {shape.geometryKind === "observed"
          ? "Observed driven path · gaps preserved"
          : "Supplied track shape"}{" "}
        · Similar ±{shape.similarThresholdS.toFixed(3)} s
      </p>
      {onSelectRegion && shape.regions.length ? (
        <label className="analysis-region-select">
          Turn / region
          <select
            aria-label="Turn / region"
            value={selectedRegionId ?? ""}
            onChange={(e) => onSelectRegion(e.target.value)}
          >
            {shape.regions.map((region, i) => (
              <option key={region.id} value={region.id}>
                {i + 1}. {region.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
    </div>
  );
}

const CHANNELS = [
  { key: "speed", label: "Speed", unit: "km/h" },
  { key: "delta", label: "Delta", unit: "s" },
  { key: "throttle", label: "Throttle", unit: "%" },
  { key: "brake", label: "Brake", unit: "%" },
  { key: "gear", label: "Gear", unit: "" },
  { key: "steering", label: "Steering", unit: "input" },
] as const;
export function LapComparisonTrace({
  input,
  windowM = null,
  onCursor,
  compact = false,
}: {
  input: LapTraceInput | null;
  windowM?: [number, number] | null;
  onCursor?: (distanceM: number | null) => void;
  compact?: boolean;
}) {
  const [mode, setMode] = useState("Speed"),
    [cursor, setCursor] = useState<number | null>(null),
    [zoom, setZoom] = useState<[number, number] | null>(null);
  const range = zoom ?? windowM;
  const rangeStart = range?.[0],
    rangeEnd = range?.[1];
  const chart = useMemo(() => {
    if (!input || input.distanceM.length < 2) return null;
    const domain = range ?? [input.distanceM[0], input.distanceM.at(-1)!];
    const selectedChannels = CHANNELS.filter(
      (c) =>
        (!compact && c.key === "delta") ||
        (mode === "Speed"
          ? compact
            ? c.key === "speed"
            : c.key !== "steering"
          : mode === "Inputs"
            ? c.key === "throttle" || c.key === "brake"
            : mode === "Gear"
              ? c.key === "gear"
              : c.key === "steering"),
    );
    const lanes = selectedChannels.map((channel) => {
      const key = channel.key,
        percent = key === "throttle" || key === "brake";
      const sources =
        key === "delta"
          ? [
              {
                label: "Target − reference",
                values: input.deltaS,
                mask: input.deltaMask,
                colour: "#ff172b",
              },
            ]
          : [input.target, input.reference].map((lap, i) => ({
              label: lap.label,
              values: lap.values[key] ?? [],
              mask: lap.masks?.[key],
              colour: i ? "#ff172b" : "#0068ff",
            }));
      const series = sources.map((s) => {
        const sourceRuns = traceRuns(
          input.distanceM,
          s.values,
          s.mask,
          rangeStart == null || rangeEnd == null
            ? null
            : [rangeStart, rangeEnd],
        );
        return { ...s, sourceRuns, runs: boundedTraceRuns(sourceRuns) };
      });
      let max = key === "gear" ? 8 : percent ? 1 : key === "steering" ? 1 : 0.5,
        min = key === "steering" || key === "gear" ? -1 : 0;
      if (key === "speed" || key === "delta")
        for (const s of series)
          for (const run of s.sourceRuns)
            for (const p of run) {
              max = Math.max(max, p.value);
              min = Math.min(min, p.value);
            }
      if (key === "delta") {
        max = Math.max(0.1, max * 1.1);
        min = Math.min(-0.1, min * 1.1);
      }
      if (key === "speed") max = Math.ceil(max / 50) * 50;
      return { channel, series, min, max };
    });
    return { domain, lanes };
  }, [input, rangeStart, rangeEnd, mode, compact]);
  if (!input || !chart)
    return (
      <div className="analysis-empty">
        <WaveIcon size={32} />
        <strong>Lap comparison traces unavailable</strong>
        <span>
          Choose a target and reference lap or load aligned telemetry samples.
        </span>
      </div>
    );
  const width = 1000,
    left = 86,
    right = 985,
    laneHeight = 64,
    height = chart.lanes.length * laneHeight + 38;
  const x = (distance: number) =>
    left +
    ((distance - chart.domain[0]) /
      Math.max(Number.EPSILON, chart.domain[1] - chart.domain[0])) *
      (right - left);
  const inRange = (i: number) =>
    input.distanceM[i] >= chart.domain[0] &&
    (range
      ? input.distanceM[i] < chart.domain[1]
      : input.distanceM[i] <= chart.domain[1]);
  const visibleCursor = cursor != null && inRange(cursor) ? cursor : null;
  let firstIndex = nearestSample(input.distanceM, chart.domain[0]);
  if (input.distanceM[firstIndex] < chart.domain[0]) firstIndex++;
  let lastIndex = nearestSample(input.distanceM, chart.domain[1]);
  if (
    range
      ? input.distanceM[lastIndex] >= chart.domain[1]
      : input.distanceM[lastIndex] > chart.domain[1]
  )
    lastIndex--;
  if (firstIndex > lastIndex)
    return (
      <div className="analysis-empty" role="status">
        <strong>No samples in this distance window</strong>
        <span>Choose a window containing telemetry samples.</span>
      </div>
    );
  function choose(index: number | null) {
    setCursor(index);
    onCursor?.(index == null ? null : input!.distanceM[index]);
  }
  return (
    <div className="analysis-trace">
      <div className="analysis-trace-toolbar">
        <div className="analysis-trace-legend">
          <span>
            <i className="dot-blue" />
            {input.target.label}
          </span>
          <span>
            <i className="dot-red" />
            {input.reference.label}
          </span>
        </div>
        <div className="ref-tab-pills">
          {["Speed", "Inputs", "Steering", "Gear"].map((label) => (
            <button
              key={label}
              className={`tab-pill ${mode === label ? "active" : ""}`}
              aria-pressed={mode === label}
              onClick={() => setMode(label)}
            >
              {label}
            </button>
          ))}
        </div>
        <button
          className="ref-button"
          disabled={visibleCursor == null}
          onClick={() => {
            if (visibleCursor != null) {
              const d = input.distanceM[visibleCursor],
                span = (chart.domain[1] - chart.domain[0]) / 4;
              setZoom([
                Math.max(chart.domain[0], d - span),
                Math.min(chart.domain[1], d + span),
              ]);
            }
          }}
        >
          Zoom at cursor
        </button>
        {zoom ? (
          <button className="ref-button" onClick={() => setZoom(null)}>
            Reset zoom
          </button>
        ) : null}
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        tabIndex={0}
        role="slider"
        aria-label="Shared lap trace distance cursor; use arrow keys to inspect samples"
        aria-valuemin={chart.domain[0]}
        aria-valuemax={chart.domain[1]}
        aria-valuenow={
          visibleCursor == null
            ? chart.domain[0]
            : input.distanceM[visibleCursor]
        }
        aria-valuetext={
          visibleCursor == null
            ? "No sample selected"
            : `${input.distanceM[visibleCursor].toFixed(1)} metres`
        }
        onPointerMove={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect(),
            svgX = ((event.clientX - bounds.left) / bounds.width) * width;
          if (svgX < left || svgX > right) return;
          const requested =
            chart.domain[0] +
            ((svgX - left) / (right - left)) *
              (chart.domain[1] - chart.domain[0]);
          const index = Math.max(
            firstIndex,
            Math.min(lastIndex, nearestSample(input.distanceM, requested)),
          );
          if (inRange(index)) choose(index);
        }}
        onKeyDown={(event) => {
          if (
            !["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].includes(
              event.key,
            )
          )
            return;
          event.preventDefault();
          if (event.key === "Escape") {
            choose(null);
            return;
          }
          const first = firstIndex,
            last = lastIndex;
          const index =
            event.key === "Home"
              ? first
              : event.key === "End"
                ? last
                : Math.max(
                    first,
                    Math.min(
                      last,
                      (visibleCursor ?? first) +
                        (event.key === "ArrowLeft" ? -1 : 1),
                    ),
                  );
          if (inRange(index)) choose(index);
        }}
      >
        {chart.lanes.map((lane, laneIndex) => {
          const top = laneIndex * laneHeight + 8,
            bottom = top + 48;
          const y = (value: number) =>
            bottom - ((value - lane.min) / (lane.max - lane.min)) * 48;
          return (
            <g key={lane.channel.key}>
              <text x="4" y={top + 21} className="trace-lane-label">
                {lane.channel.label}
              </text>
              <text x="4" y={top + 35}>
                ({lane.channel.unit})
              </text>
              {[0, 0.5, 1].map((f) => (
                <g key={f}>
                  <line
                    x1={left}
                    x2={right}
                    y1={top + f * 48}
                    y2={top + f * 48}
                    stroke="#e5edf7"
                  />
                  <text x={left - 10} y={top + f * 48 + 4} textAnchor="end">
                    {(
                      (lane.max - f * (lane.max - lane.min)) *
                      (lane.channel.unit === "%" ? 100 : 1)
                    ).toFixed(
                      lane.channel.key === "delta"
                        ? 2
                        : lane.channel.key === "steering"
                          ? 1
                          : 0,
                    )}
                  </text>
                </g>
              ))}
              {Array.from({ length: 9 }, (_, i) => (
                <line
                  key={i}
                  x1={left + (i / 8) * (right - left)}
                  x2={left + (i / 8) * (right - left)}
                  y1={top}
                  y2={bottom}
                  stroke="#edf2f9"
                />
              ))}
              {lane.series.map((series, si) => (
                <g key={si}>
                  {series.runs.map((run, ri) => {
                    const points = run;
                    const path = points
                      .map(
                        (p, i) =>
                          `${i ? (lane.channel.key === "gear" ? `H${x(p.distance).toFixed(2)}V` : "L") : "M"}${lane.channel.key === "gear" && i ? y(p.value).toFixed(2) : `${x(p.distance).toFixed(2)},${y(p.value).toFixed(2)}`}`,
                      )
                      .join(" ");
                    return run.length === 1 ? (
                      <circle
                        key={ri}
                        cx={x(run[0].distance)}
                        cy={y(run[0].value)}
                        r="2"
                        fill={series.colour}
                      />
                    ) : (
                      <path
                        key={ri}
                        d={path}
                        stroke={series.colour}
                        strokeWidth="1.8"
                        fill="none"
                      />
                    );
                  })}
                </g>
              ))}
              {visibleCursor != null ? (
                <line
                  x1={x(input.distanceM[visibleCursor])}
                  x2={x(input.distanceM[visibleCursor])}
                  y1={top}
                  y2={bottom}
                  stroke="#18243a"
                  strokeDasharray="4 3"
                />
              ) : null}
            </g>
          );
        })}
        {Array.from({ length: 9 }, (_, i) => (
          <text
            key={i}
            x={left + (i / 8) * (right - left)}
            y={height - 17}
            textAnchor="middle"
          >
            {(
              (chart.domain[0] +
                (i / 8) * (chart.domain[1] - chart.domain[0])) /
              1000
            ).toFixed(2)}
          </text>
        ))}
        <text x={(left + right) / 2} y={height - 2} textAnchor="middle">
          Track Distance (km)
        </text>
      </svg>
      <div className="analysis-cursor-readout" aria-live="polite">
        {visibleCursor == null ? (
          <span>
            Move over a trace or use arrow keys to inspect the same distance in
            both laps.
          </span>
        ) : (
          <>
            <strong>{input.distanceM[visibleCursor].toFixed(1)} m</strong>
            {chart.lanes.map((lane) => (
              <span key={lane.channel.key}>
                {lane.channel.label}:{" "}
                {lane.series
                  .map((s) =>
                    (s.mask && s.mask[visibleCursor] !== true) ||
                    !Number.isFinite(s.values[visibleCursor])
                      ? "—"
                      : `${(s.values[visibleCursor]! * (lane.channel.unit === "%" ? 100 : 1)).toFixed(lane.channel.key === "delta" ? 3 : lane.channel.key === "gear" ? 0 : 1)}`,
                  )
                  .join(" / ")}{" "}
                {lane.channel.unit}
              </span>
            ))}
          </>
        )}
      </div>
      <p className="analysis-source-note">
        Gaps represent unsupported samples. Large inputs are displayed with at
        most 128 runs and 2,000 points per series; cursor values use the
        original samples.
      </p>
    </div>
  );
}
export function signed(value: number) {
  return `${value > 0 ? "+" : ""}${value.toFixed(3)}`;
}
export function timeMs(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  const ms = Math.round(value);
  return `${Math.floor(ms / 60000)}:${(Math.floor(ms / 1000) % 60).toString().padStart(2, "0")}.${(ms % 1000).toString().padStart(3, "0")}`;
}
