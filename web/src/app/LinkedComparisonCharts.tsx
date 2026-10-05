"use client";

import { useMemo, useRef, useState } from "react";
import type {
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from "react";

export type Series = {
  label: string;
  color: string;
  values: Array<number | null>;
  mask?: boolean[];
};

export type Difference = {
  label: string;
  left: number;
  right: number;
  unit: string;
  scale?: number;
  precision?: number;
};

export type ChartSpec = {
  id: string;
  title: string;
  subtitle: string;
  unit: string;
  series: Series[];
  optional?: boolean;
  overlayLabel?: string;
  renderMode?: "line" | "step";
  valueFormat?: "gear";
  range?: [number, number];
  zero?: boolean;
  percentAxis?: boolean;
  valueUnit: string;
  valueScale?: number;
  signedValues?: boolean;
  precision?: number;
  differences?: Difference[];
};

type RangeIndex = { first: number; last: number };

const WIDTH = 1000;
const HEIGHT = 220;
const LEFT = 58;
const RIGHT = 18;
const TOP = 17;
const BOTTOM = 30;
const PLOT_WIDTH = WIDTH - LEFT - RIGHT;
const PLOT_HEIGHT = HEIGHT - TOP - BOTTOM;
const MAX_GRID_POINTS = 100_000;
const MAX_RENDERED_POINTS = 2_000;
const MAX_RENDERED_RUNS = 256;
const GEAR_TICKS = [-1, 0, 1, 2, 3, 4, 5, 6, 7, 8];

export default function LinkedComparisonCharts({
  distance,
  charts,
  initialWindowM = null,
}: {
  distance: number[];
  charts: ChartSpec[];
  initialWindowM?: [number, number] | null;
}) {
  const initialWindow = resolveInitialRange(distance, initialWindowM);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [requestedDistance, setRequestedDistance] = useState<number | null>(
    null,
  );
  const [zoom, setZoom] = useState<RangeIndex | null>(() =>
    initialWindow?.status === "range" ? initialWindow.range : null,
  );
  const [analysisWindowView, setAnalysisWindowView] = useState(
    initialWindow?.status === "range",
  );
  const [visibleOptionalCharts, setVisibleOptionalCharts] = useState<
    Set<string>
  >(() => new Set());
  const [zoomMode, setZoomMode] = useState(false);
  const [dragStart, setDragStart] = useState<number | null>(null);
  const optionalCharts = charts.filter((chart) => chart.optional);
  const visibleCharts = charts.filter(
    (chart) => !chart.optional || visibleOptionalCharts.has(chart.id),
  );

  const invalidReason = useMemo(
    () => validateGrid(distance, charts),
    [distance, charts],
  );
  if (invalidReason) {
    return (
      <section
        id="comparison-charts"
        className="chart-stack"
        aria-label="Linked comparison charts"
        tabIndex={-1}
      >
        <section
          className="chart-card panel linked-chart-unavailable"
          role="status"
        >
          <h3>Linked distance inspection unavailable</h3>
          <p>{invalidReason}</p>
        </section>
      </section>
    );
  }

  if (distance.length === 0) {
    return (
      <section
        id="comparison-charts"
        className="chart-stack"
        aria-label="Linked comparison charts"
        tabIndex={-1}
      >
        <section
          className="chart-card panel linked-chart-unavailable"
          role="status"
        >
          <h3>Linked distance inspection unavailable</h3>
          <p>The comparison contains no distance-grid points.</p>
        </section>
      </section>
    );
  }

  if (initialWindow?.status === "empty") {
    return (
      <section
        id="comparison-charts"
        className="chart-stack"
        aria-label="Linked comparison charts"
        tabIndex={-1}
      >
        <section
          className="chart-card panel linked-chart-unavailable"
          role="status"
        >
          <h3>No grid points inside the selected interval</h3>
          <p>
            The exact interval was preserved. This comparison grid contains no
            existing distance samples within its half-open bounds, so no chart
            values are interpolated.
          </p>
        </section>
      </section>
    );
  }

  const lastIndex = distance.length - 1;
  const visibleRange = zoom ?? { first: 0, last: lastIndex };
  const selectedDistance =
    selectedIndex == null ? null : distance[selectedIndex];
  const selectedValues =
    selectedIndex == null
      ? null
      : visibleCharts.map((chart) => readoutForChart(chart, selectedIndex));

  const toggleOptionalChart = (chartId: string, visible: boolean) => {
    setVisibleOptionalCharts((current) => {
      const next = new Set(current);
      if (visible) next.add(chartId);
      else next.delete(chartId);
      return next;
    });
  };

  const chooseCursor = (index: number, requested: number | null) => {
    setSelectedIndex(index);
    setRequestedDistance(requested);
  };

  const chooseZoom = (first: number, last: number) => {
    const low = Math.min(first, last);
    const high = Math.max(first, last);
    if (high - low < 1) return;
    setZoom({ first: low, last: high });
    setAnalysisWindowView(false);
    setSelectedIndex((current) =>
      current == null ? null : Math.max(low, Math.min(high, current)),
    );
    setRequestedDistance(null);
    setZoomMode(false);
    setDragStart(null);
  };

  const adjustZoom = (factor: number) => {
    const current = zoom ?? { first: 0, last: lastIndex };
    const span = current.last - current.first;
    if (factor < 1 && span < 2) return;
    const center =
      selectedIndex != null &&
      selectedIndex >= current.first &&
      selectedIndex <= current.last
        ? selectedIndex
        : Math.round((current.first + current.last) / 2);
    const newSpan = Math.max(1, Math.min(lastIndex, Math.round(span * factor)));
    const first = Math.max(
      0,
      Math.min(lastIndex - newSpan, center - Math.floor(newSpan / 2)),
    );
    const last = first + newSpan;
    setZoom({ first, last });
    setAnalysisWindowView(false);
    setSelectedIndex((currentIndex) =>
      currentIndex == null
        ? null
        : Math.max(first, Math.min(last, currentIndex)),
    );
    setRequestedDistance(null);
    setZoomMode(false);
    setDragStart(null);
  };

  return (
    <section
      id="comparison-charts"
      className="linked-comparison"
      aria-label="Linked comparison charts"
      tabIndex={-1}
    >
      <div className="linked-chart-controls panel">
        <div className="linked-chart-control-copy">
          <strong>Shared distance cursor</strong>
          <span>
            Click any chart or focus a chart and use the arrow keys to inspect
            an existing resampled comparison-grid point.
          </span>
        </div>
        <div className="linked-chart-buttons">
          <button
            type="button"
            className={
              zoomMode ? "secondary-button active" : "secondary-button"
            }
            aria-pressed={zoomMode}
            onClick={() => {
              setZoomMode((value) => !value);
              setDragStart(null);
            }}
          >
            {zoomMode ? "Cancel zoom selection" : "Select zoom range"}
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => adjustZoom(0.5)}
            disabled={visibleRange.last - visibleRange.first < 2}
          >
            Zoom in
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => adjustZoom(2)}
            disabled={
              !zoom || visibleRange.last - visibleRange.first >= lastIndex
            }
          >
            Zoom out
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => {
              setZoom(null);
              setAnalysisWindowView(false);
              setZoomMode(false);
              setDragStart(null);
            }}
            disabled={!zoom}
          >
            Reset zoom
          </button>
        </div>
        {optionalCharts.length ? (
          <div
            className="linked-chart-overlays"
            role="group"
            aria-label="Optional comparison channels"
          >
            <strong>OPTIONAL CHANNELS</strong>
            {optionalCharts.map((chart) => (
              <label key={chart.id}>
                <input
                  type="checkbox"
                  checked={visibleOptionalCharts.has(chart.id)}
                  onChange={(event) =>
                    toggleOptionalChart(chart.id, event.currentTarget.checked)
                  }
                />
                {chart.overlayLabel ?? chart.title}
              </label>
            ))}
          </div>
        ) : null}
        <p className="linked-chart-mode" role="status">
          {zoomMode
            ? dragStart == null
              ? "Drag across any chart to zoom all charts to that distance interval."
              : "Keep dragging, then release to set the shared local zoom."
            : analysisWindowView && initialWindowM
              ? `Selected interval: [${initialWindowM[0].toFixed(2)}, ${initialWindowM[1].toFixed(2)}) m · existing grid points only`
              : zoom
                ? `Local view: ${distance[visibleRange.first].toFixed(1)}–${distance[visibleRange.last].toFixed(1)} m · analysis window unchanged`
                : `Full comparison view: ${distance[0].toFixed(1)}–${distance[lastIndex].toFixed(1)} m`}
        </p>
      </div>

      <section
        className="linked-cursor-readout panel"
        aria-label="Selected comparison grid values"
        aria-live="polite"
      >
        <div className="linked-readout-heading">
          <span className="eyebrow">GRID INSPECTION</span>
          <strong>
            {selectedDistance == null
              ? "No point selected"
              : `${selectedDistance.toFixed(2)} m`}
          </strong>
          {selectedDistance != null ? (
            <small>
              Point {selectedIndex! + 1} of {distance.length}
            </small>
          ) : null}
        </div>
        {selectedDistance != null &&
        requestedDistance != null &&
        Math.abs(requestedDistance - selectedDistance) > Number.EPSILON ? (
          <p className="linked-requested-distance">
            Requested {requestedDistance.toFixed(2)} m; nearest existing grid
            point is {selectedDistance.toFixed(2)} m. No additional values are
            interpolated.
          </p>
        ) : null}
        {selectedValues ? (
          <div className="linked-readout-grid">
            {visibleCharts.map((chart, chartIndex) => (
              <section
                key={chart.id}
                className="linked-readout-group"
                aria-label={`${chart.title} values`}
              >
                <h3>{chart.title}</h3>
                {selectedValues[chartIndex].values.map((item) => (
                  <div className="linked-readout-value" key={item.label}>
                    <i style={{ background: item.color }} />
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                  </div>
                ))}
                {selectedValues[chartIndex].differences.map((item) => (
                  <div className="linked-readout-difference" key={item.label}>
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                  </div>
                ))}
              </section>
            ))}
          </div>
        ) : (
          <p className="linked-readout-prompt">
            Choose a point on any chart to read every channel at that same
            distance.
          </p>
        )}
      </section>

      <div className="chart-stack">
        {visibleCharts.map((chart) => (
          <LinkedChart
            key={chart.id}
            chart={chart}
            distance={distance}
            visibleRange={visibleRange}
            selectedIndex={selectedIndex}
            zoomMode={zoomMode}
            dragStart={dragStart}
            onCursor={chooseCursor}
            onDragStart={setDragStart}
            onZoom={chooseZoom}
          />
        ))}
      </div>
      <p className="chart-footnote linked-chart-footnote">
        Lines stop at unsupported samples. Paths are rendered from at most{" "}
        {MAX_RENDERED_POINTS.toLocaleString()} existing points per series and{" "}
        {MAX_RENDERED_RUNS} supported runs; omissions are reported. The cursor
        reads the original resampled comparison grid.
      </p>
    </section>
  );
}

function LinkedChart({
  chart,
  distance,
  visibleRange,
  selectedIndex,
  zoomMode,
  dragStart,
  onCursor,
  onDragStart,
  onZoom,
}: {
  chart: ChartSpec;
  distance: number[];
  visibleRange: RangeIndex;
  selectedIndex: number | null;
  zoomMode: boolean;
  dragStart: number | null;
  onCursor: (index: number, requested: number | null) => void;
  onDragStart: (distance: number | null) => void;
  onZoom: (first: number, last: number) => void;
}) {
  const suppressZoomClick = useRef(false);
  const renderData = useMemo(
    () =>
      chart.series.map((series) =>
        buildRenderRuns(series, visibleRange, chart.renderMode ?? "line"),
      ),
    [
      chart.series,
      chart.renderMode,
      distance,
      visibleRange.first,
      visibleRange.last,
    ],
  );
  const hasSupportedRun = renderData.some((item) => item.runs.length > 0);
  const [min, max] = useMemo(() => {
    let low = chart.range?.[0] ?? (chart.zero ? 0 : Infinity);
    let high = chart.range?.[1] ?? (chart.zero ? 0 : -Infinity);
    if (!chart.range) {
      for (const series of chart.series) {
        for (let index = 0; index < series.values.length; index += 1) {
          if (!supported(series, index)) continue;
          const value = series.values[index]!;
          low = Math.min(low, value);
          high = Math.max(high, value);
        }
      }
    }
    if (!Number.isFinite(low) || !Number.isFinite(high)) {
      low = 0;
      high = 1;
    }
    if (low === high) {
      low -= 1;
      high += 1;
    }
    const padding = chart.range ? 0 : (high - low) * 0.08;
    return [low - padding, high + padding];
  }, [chart.series, chart.range, chart.zero]);
  const y = (value: number) =>
    TOP + ((max - value) / (max - min)) * PLOT_HEIGHT;
  const minDistance = distance[visibleRange.first];
  const maxDistance = distance[visibleRange.last];
  const span = Math.max(Number.EPSILON, maxDistance - minDistance);
  const x = (value: number) =>
    LEFT + ((value - minDistance) / span) * PLOT_WIDTH;
  const ticks =
    chart.valueFormat === "gear"
      ? GEAR_TICKS
      : Array.from(
          { length: 4 },
          (_, index) => min + ((max - min) * index) / 3,
        );
  const cursorDistance = selectedIndex == null ? null : distance[selectedIndex];

  const requestedAt = (event: ReactPointerEvent<SVGRectElement>) => {
    const svg = event.currentTarget.ownerSVGElement;
    const bounds = svg?.getBoundingClientRect();
    if (!svg || !bounds || bounds.width <= 0) return minDistance;
    const localX = ((event.clientX - bounds.left) / bounds.width) * WIDTH;
    const clampedX = Math.max(LEFT, Math.min(WIDTH - RIGHT, localX));
    return minDistance + ((clampedX - LEFT) / PLOT_WIDTH) * span;
  };

  const selectNearest = (requested: number) =>
    nearestIndex(distance, requested, visibleRange);

  const onPointerDown = (event: ReactPointerEvent<SVGRectElement>) => {
    if (!zoomMode) return;
    event.preventDefault();
    suppressZoomClick.current = false;
    event.currentTarget.setPointerCapture(event.pointerId);
    onDragStart(requestedAt(event));
  };

  const onPointerUp = (event: ReactPointerEvent<SVGRectElement>) => {
    if (!zoomMode || dragStart == null) return;
    const end = requestedAt(event);
    const first = selectNearest(dragStart);
    const last = selectNearest(end);
    event.currentTarget.releasePointerCapture(event.pointerId);
    if (first !== last) {
      suppressZoomClick.current = true;
      onZoom(first, last);
    }
    onDragStart(null);
  };

  const onClick = (event: ReactPointerEvent<SVGRectElement>) => {
    if (suppressZoomClick.current) {
      suppressZoomClick.current = false;
      return;
    }
    if (zoomMode) return;
    const requested = requestedAt(event);
    onCursor(selectNearest(requested), requested);
  };

  const onKeyDown = (event: ReactKeyboardEvent<SVGRectElement>) => {
    let index =
      selectedIndex == null
        ? Math.round((visibleRange.first + visibleRange.last) / 2)
        : selectedIndex;
    if (event.key === "ArrowLeft" || event.key === "ArrowDown") index -= 1;
    else if (event.key === "ArrowRight" || event.key === "ArrowUp") index += 1;
    else if (event.key === "Home") index = visibleRange.first;
    else if (event.key === "End") index = visibleRange.last;
    else return;
    event.preventDefault();
    index = Math.max(visibleRange.first, Math.min(visibleRange.last, index));
    onCursor(index, null);
  };

  return (
    <section className="chart-card panel">
      <div className="chart-header">
        <div>
          <h3>{chart.title}</h3>
          <p>{chart.subtitle}</p>
        </div>
        <span className="chart-unit">{chart.unit}</span>
      </div>
      <div className="chart-wrap">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="group"
          aria-label={`${chart.title} over lap distance`}
        >
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                className="grid-line"
                x1={LEFT}
                x2={WIDTH - RIGHT}
                y1={y(tick)}
                y2={y(tick)}
              />
              <text
                className="axis-label"
                x={LEFT - 9}
                y={y(tick) + 4}
                textAnchor="end"
              >
                {chart.valueFormat === "gear"
                  ? gearLabel(tick)
                  : chart.percentAxis
                    ? Math.round(tick * 100)
                    : tick.toFixed(1)}
              </text>
            </g>
          ))}
          {chart.zero && min < 0 && max > 0 ? (
            <line
              className="zero-line"
              x1={LEFT}
              x2={WIDTH - RIGHT}
              y1={y(0)}
              y2={y(0)}
            />
          ) : null}
          {renderData.map((rendered, seriesIndex) => (
            <g key={chart.series[seriesIndex].label}>
              {rendered.runs.map((run, runIndex) => {
                if (run.length === 1) {
                  const index = run[0];
                  const value = chart.series[seriesIndex].values[index]!;
                  return (
                    <circle
                      key={runIndex}
                      cx={x(distance[index])}
                      cy={y(value)}
                      r="2.2"
                      fill={chart.series[seriesIndex].color}
                    />
                  );
                }
                return (
                  <path
                    key={runIndex}
                    d={renderRunPath(
                      run,
                      chart.series[seriesIndex],
                      distance,
                      x,
                      y,
                      chart.renderMode ?? "line",
                    )}
                    fill="none"
                    stroke={chart.series[seriesIndex].color}
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    vectorEffect="non-scaling-stroke"
                  />
                );
              })}
            </g>
          ))}
          {cursorDistance != null &&
          cursorDistance >= minDistance &&
          cursorDistance <= maxDistance ? (
            <line
              className="linked-cursor-line"
              x1={x(cursorDistance)}
              x2={x(cursorDistance)}
              y1={TOP}
              y2={HEIGHT - BOTTOM}
              pointerEvents="none"
            />
          ) : null}
          <text className="axis-label" x={LEFT} y={HEIGHT - 6}>
            {minDistance.toFixed(0)} m
          </text>
          <text
            className="axis-label"
            x={WIDTH - RIGHT}
            y={HEIGHT - 6}
            textAnchor="end"
          >
            {maxDistance.toFixed(0)} m
          </text>
          <rect
            className="linked-chart-hit-area"
            x={LEFT}
            y={TOP}
            width={PLOT_WIDTH}
            height={PLOT_HEIGHT}
            fill="transparent"
            role="slider"
            tabIndex={0}
            aria-label={`${chart.title} shared distance cursor`}
            aria-valuemin={minDistance}
            aria-valuemax={maxDistance}
            aria-valuenow={cursorDistance ?? (minDistance + maxDistance) / 2}
            aria-valuetext={
              cursorDistance == null
                ? "No grid point selected"
                : `${cursorDistance.toFixed(2)} metres`
            }
            style={{ cursor: "crosshair", touchAction: "none" }}
            onClick={onClick}
            onPointerDown={onPointerDown}
            onPointerUp={onPointerUp}
            onPointerCancel={() => {
              suppressZoomClick.current = false;
              onDragStart(null);
            }}
            onKeyDown={onKeyDown}
          />
        </svg>
      </div>
      {!hasSupportedRun ? (
        <p className="linked-chart-empty" role="status">
          No supported samples in this interval. Missing values and gaps remain
          unavailable.
        </p>
      ) : null}
      <div className="chart-legend">
        {chart.series.map((series) => (
          <span key={series.label}>
            <i style={{ background: series.color }} />
            {series.label}
          </span>
        ))}
        <span className="legend-gap">Gaps break the line</span>
      </div>
      <RenderOmissions data={renderData} />
    </section>
  );
}

function RenderOmissions({
  data,
}: {
  data: ReturnType<typeof buildRenderRuns>[];
}) {
  const omittedRuns = data.reduce((total, item) => total + item.omittedRuns, 0);
  const omittedPoints = data.reduce(
    (total, item) => total + item.omittedPoints,
    0,
  );
  const omittedTransitions = data.reduce(
    (total, item) => total + item.omittedTransitions,
    0,
  );
  const omittedTailEndpoints = data.reduce(
    (total, item) => total + item.omittedTailEndpoints,
    0,
  );
  const omissions = [
    omittedRuns
      ? countLabel(omittedRuns, "supported run", "supported runs")
      : null,
    omittedPoints
      ? countLabel(omittedPoints, "grid point", "grid points")
      : null,
    omittedTransitions
      ? countLabel(omittedTransitions, "gear transition", "gear transitions")
      : null,
    omittedTailEndpoints
      ? countLabel(
          omittedTailEndpoints,
          "gear trace tail endpoint",
          "gear trace tail endpoints",
        )
      : null,
  ].filter((item): item is string => item != null);
  if (!omissions.length) return null;
  return (
    <p className="linked-chart-omissions" role="status">
      Rendered view omits {omissions.join(", ")} across plotted series to stay
      within display limits. Cursor values still use the full resampled grid.
      {omittedTransitions
        ? " Any capped gear trace stops before omitted shifts and does not carry values across them."
        : null}
    </p>
  );
}

function countLabel(count: number, singular: string, plural: string) {
  return `${count.toLocaleString()} ${count === 1 ? singular : plural}`;
}

function buildRenderRuns(
  series: Series,
  visibleRange: RangeIndex,
  renderMode: "line" | "step",
) {
  if (renderMode === "step") return buildStepRuns(series, visibleRange);
  const runs: number[][] = [];
  let current: number[] = [];
  for (let index = visibleRange.first; index <= visibleRange.last; index += 1) {
    if (supported(series, index)) current.push(index);
    else if (current.length) {
      runs.push(current);
      current = [];
    }
  }
  if (current.length) runs.push(current);
  const omittedRuns = Math.max(0, runs.length - MAX_RENDERED_RUNS);
  const retainedRuns = runs.slice(0, MAX_RENDERED_RUNS);
  const totalRetained = retainedRuns.reduce(
    (total, run) => total + run.length,
    0,
  );
  const mandatory = retainedRuns.map((run) => Math.min(2, run.length));
  const mandatoryTotal = mandatory.reduce((total, points) => total + points, 0);
  const extraBudget = Math.max(0, MAX_RENDERED_POINTS - mandatoryTotal);
  const extraCapacity = retainedRuns.reduce(
    (total, run, index) => total + run.length - mandatory[index],
    0,
  );
  const allocations = retainedRuns.map((run, index) => {
    if (run.length <= mandatory[index] || extraCapacity === 0)
      return mandatory[index];
    const extra = Math.floor(
      (extraBudget * (run.length - mandatory[index])) / extraCapacity,
    );
    return Math.min(run.length, mandatory[index] + extra);
  });
  let assigned = allocations.reduce((total, value) => total + value, 0);
  for (
    let index = 0;
    assigned < Math.min(MAX_RENDERED_POINTS, totalRetained);
    index = (index + 1) % allocations.length
  ) {
    if (allocations[index] < retainedRuns[index].length) {
      allocations[index] += 1;
      assigned += 1;
    }
  }
  const renderedRuns = retainedRuns.map((run, index) =>
    sampleRun(run, allocations[index]),
  );
  const renderedPoints = allocations.reduce((total, value) => total + value, 0);
  return {
    runs: renderedRuns,
    omittedRuns,
    omittedPoints: Math.max(0, totalRetained - renderedPoints),
    omittedTransitions: 0,
    omittedTailEndpoints: 0,
  };
}

function buildStepRuns(series: Series, visibleRange: RangeIndex) {
  const sourceRuns: number[][] = [];
  let current: number[] | null = null;
  let previousValue: number | null = null;
  let totalTransitions = 0;

  const finishRun = (endIndex: number) => {
    if (!current) return;
    if (current.at(-1) !== endIndex) current.push(endIndex);
    sourceRuns.push(current);
    current = null;
    previousValue = null;
  };

  for (let index = visibleRange.first; index <= visibleRange.last; index += 1) {
    const value = series.values[index];
    if (
      !supported(series, index) ||
      typeof value !== "number" ||
      !Number.isInteger(value) ||
      value < -1 ||
      value > 8
    ) {
      finishRun(index - 1);
      continue;
    }
    if (!current) {
      current = [index];
    } else if (value !== previousValue) {
      current.push(index);
      totalTransitions += 1;
    }
    previousValue = value;
    if (index === visibleRange.last) finishRun(index);
  }

  const renderedRuns: number[][] = [];
  let remainingPoints = MAX_RENDERED_POINTS;
  let retainedTransitions = 0;
  let omittedTailEndpoints = 0;
  for (const run of sourceRuns.slice(0, MAX_RENDERED_RUNS)) {
    if (remainingPoints === 0) break;
    const retained = run.slice(0, remainingPoints);
    if (!retained.length) continue;
    renderedRuns.push(retained);
    remainingPoints -= retained.length;
    if (
      retained.length < run.length &&
      run.length > 1 &&
      series.values[run[run.length - 1]] === series.values[run[run.length - 2]]
    ) {
      omittedTailEndpoints += 1;
    }
    for (let index = 1; index < retained.length; index += 1) {
      if (
        series.values[retained[index]] !== series.values[retained[index - 1]]
      ) {
        retainedTransitions += 1;
      }
    }
  }

  return {
    runs: renderedRuns,
    omittedRuns: Math.max(0, sourceRuns.length - renderedRuns.length),
    omittedPoints: 0,
    omittedTransitions: Math.max(0, totalTransitions - retainedTransitions),
    omittedTailEndpoints,
  };
}

function renderRunPath(
  run: number[],
  series: Series,
  distance: number[],
  x: (value: number) => number,
  y: (value: number) => number,
  renderMode: "line" | "step",
) {
  const first = run[0];
  let path = `M${x(distance[first]).toFixed(2)},${y(series.values[first]!).toFixed(2)}`;
  for (let point = 1; point < run.length; point += 1) {
    const previousIndex = run[point - 1];
    const currentIndex = run[point];
    const previousValue = series.values[previousIndex]!;
    const currentValue = series.values[currentIndex]!;
    const currentX = x(distance[currentIndex]).toFixed(2);
    if (renderMode === "step") {
      path += ` L${currentX},${y(previousValue).toFixed(2)}`;
      if (currentValue !== previousValue) {
        path += ` L${currentX},${y(currentValue).toFixed(2)}`;
      }
    } else {
      path += ` L${currentX},${y(currentValue).toFixed(2)}`;
    }
  }
  return path;
}

function sampleRun(run: number[], count: number) {
  if (count >= run.length) return run;
  if (count <= 1) return [run[0]];
  return Array.from(
    { length: count },
    (_, index) => run[Math.round((index * (run.length - 1)) / (count - 1))],
  );
}

function supported(series: Series, index: number) {
  const value = series.values[index];
  return (
    value != null && Number.isFinite(value) && (series.mask?.[index] ?? true)
  );
}

function validateGrid(distance: number[], charts: ChartSpec[]) {
  if (distance.length > MAX_GRID_POINTS)
    return `The comparison grid exceeds the ${MAX_GRID_POINTS.toLocaleString()}-point inspection limit.`;
  for (let index = 0; index < distance.length; index += 1) {
    if (
      !Number.isFinite(distance[index]) ||
      (index > 0 && distance[index] <= distance[index - 1])
    ) {
      return "The comparison distance grid is not finite and strictly increasing.";
    }
  }
  for (const chart of charts) {
    for (const series of chart.series) {
      if (
        series.values.length !== distance.length ||
        (series.mask && series.mask.length !== distance.length)
      ) {
        return "A comparison channel does not match the shared distance grid.";
      }
    }
  }
  return null;
}

function lowerBound(
  values: number[],
  target: number,
  first: number,
  last: number,
) {
  let low = first;
  let high = last + 1;
  while (low < high) {
    const middle = low + Math.floor((high - low) / 2);
    if (values[middle] < target) low = middle + 1;
    else high = middle;
  }
  return low;
}

function resolveInitialRange(
  distance: number[],
  windowM: [number, number] | null,
): { status: "range"; range: RangeIndex } | { status: "empty" } | null {
  if (!windowM) return null;
  const [start, end] = windowM;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return null;
  }
  if (distance.length === 0) return { status: "empty" };

  const first = lowerBound(distance, start, 0, distance.length - 1);
  const endExclusive = lowerBound(distance, end, 0, distance.length - 1);
  const last = Math.min(distance.length - 1, endExclusive - 1);
  if (first > last || first >= distance.length) return { status: "empty" };
  return { status: "range", range: { first, last } };
}

function nearestIndex(values: number[], target: number, range: RangeIndex) {
  const right = Math.min(
    range.last,
    lowerBound(values, target, range.first, range.last),
  );
  const left = Math.max(range.first, right - 1);
  if (right > range.last) return range.last;
  return target - values[left] <= values[right] - target ? left : right;
}

function readValue(
  series: Series,
  index: number,
  scale = 1,
  unit: string,
  signed = false,
  precision = 1,
  valueFormat?: "gear",
) {
  if (!supported(series, index)) return "Unavailable";
  const value = series.values[index]! * scale;
  if (valueFormat === "gear") return gearLabel(value);
  const sign = value > 0 && signed ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toFixed(precision)} ${unit}`;
}

function gearLabel(value: number) {
  if (!Number.isInteger(value) || value < -1 || value > 8) return "Unavailable";
  if (value === -1) return "R";
  if (value === 0) return "N";
  return String(value);
}

function readoutForChart(chart: ChartSpec, index: number) {
  const values = chart.series.map((series) => ({
    label: series.label,
    color: series.color,
    value: readValue(
      series,
      index,
      chart.valueScale ?? 1,
      chart.valueUnit,
      chart.signedValues,
      chart.precision ?? 1,
      chart.valueFormat,
    ),
  }));
  const differences = (chart.differences ?? []).map((difference) => {
    const left = chart.series[difference.left];
    const right = chart.series[difference.right];
    const supportedBoth =
      left && right && supported(left, index) && supported(right, index);
    const delta = supportedBoth
      ? (left.values[index]! - right.values[index]!) * (difference.scale ?? 1)
      : null;
    const sign =
      delta != null && delta > 0 ? "+" : delta != null && delta < 0 ? "−" : "";
    const value =
      delta == null
        ? "Unavailable"
        : `${sign}${Math.abs(delta).toFixed(difference.precision ?? 1)} ${difference.unit}`;
    return { label: difference.label, value };
  });
  return { values, differences };
}
