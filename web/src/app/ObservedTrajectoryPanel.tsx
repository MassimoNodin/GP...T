"use client";

import { useState } from "react";
import type {
  AttemptRegionWindow,
  AttemptTrajectoryPreview,
  ObservedTrajectoryPoint,
} from "@/lib/api";

const PLOT_WIDTH = 1000;
const PLOT_HEIGHT = 620;
const PLOT_PADDING = 44;

type ProjectedPoint = {
  point: ObservedTrajectoryPoint;
  x: number;
  y: number;
  segmentIndex: number;
};

export default function ObservedTrajectoryPanel({
  report,
  unavailableReason,
  regionHighlight,
}: {
  report: AttemptTrajectoryPreview | null;
  unavailableReason: string | null;
  regionHighlight: AttemptRegionWindow | null;
}) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [zoom, setZoom] = useState(1);

  if (!report) {
    return (
      <section className="observed-trajectory panel">
        <div className="observed-trajectory-heading">
          <div>
            <span className="eyebrow">MOTION / DIAGNOSTIC ONLY</span>
            <h2>Observed driven path</h2>
          </div>
          <span className="trajectory-badge">UNAVAILABLE</span>
        </div>
        <p className="quality-unavailable">
          {unavailableReason
            ? humanize(unavailableReason)
            : "The local API did not return trajectory evidence for this attempt."}
        </p>
        <p className="trajectory-footnote">
          Older traces without Motion and attempts without usable world-position samples cannot be plotted.
        </p>
      </section>
    );
  }

  const flattened = report.segments.flatMap((segment) =>
    segment.points.map((point) => ({ point, segmentIndex: segment.segment_index })),
  );
  const projectedSegments = projectSegments(report, zoom);
  const safeSelectedIndex = Math.min(selectedIndex, flattened.length - 1);
  const selected = flattened[safeSelectedIndex];
  const selectedProjected = selected
    ? projectedSegments
        .find((segment) => segment.segmentIndex === selected.segmentIndex)
        ?.points.find((item) => item.point.frame_identifier === selected.point.frame_identifier)
    : null;
  const [regionStartM, regionEndM] = regionHighlight?.analysis_window_m ?? [0, 0];
  const highlightedFragments = regionHighlight
    ? regionHighlight.position_evidence.fragments.flatMap((fragment) => {
        const segment = projectedSegments.find(
          (item) => item.segmentIndex === fragment.segment_index,
        );
        if (!segment) return [];
        const points = segment.points.filter(
          ({ point }) =>
            point.lap_distance_m >= regionStartM &&
            point.lap_distance_m < regionEndM &&
            isFrameBetweenInclusive(
              point.frame_identifier,
              fragment.start_anchor.frame_identifier,
              fragment.end_anchor.frame_identifier,
            ),
        );
        return points.length ? [{ fragment, segment, points }] : [];
      })
    : [];
  const retainedPreviewPointCount = projectedSegments.reduce(
    (count, segment) =>
      count +
      segment.points.filter(
        ({ point }) => point.lap_distance_m >= regionStartM && point.lap_distance_m < regionEndM,
      ).length,
    0,
  );
  const highlightedPreviewPointCount = highlightedFragments.reduce(
    (count, fragment) => count + fragment.points.length,
    0,
  );
  const coveragePercent = report.coverage.source_sample_count
    ? `${(report.coverage.position_sample_coverage * 100).toFixed(1)}%`
    : "0.0%";
  const source = report.source;

  return (
    <section className="observed-trajectory panel" id="observed-driven-path">
      <div className="observed-trajectory-heading">
        <div>
          <span className="eyebrow">MOTION / DIAGNOSTIC ONLY</span>
          <h2>Observed driven path</h2>
        </div>
        <span className="trajectory-badge">NOT A CENTRELINE</span>
      </div>
      <p className="trajectory-description">
        World X/Z coordinates · metres · equal spatial scale. Segment breaks preserve gaps in the recorded evidence.
      </p>

      <div className="trajectory-provenance-grid">
        <div><span>ATTEMPT</span><strong>{source.disposition.replaceAll("_", " ").toUpperCase()}</strong></div>
        <div><span>GAME VALIDITY</span><strong>{source.game_valid === true ? "VALID" : source.game_valid === false ? "INVALID" : "UNKNOWN"}</strong></div>
        <div><span>REFERENCE</span><strong>{source.reference_eligible ? "ELIGIBLE" : "NOT ELIGIBLE"}</strong></div>
        <div><span>POSITION COVERAGE</span><strong>{coveragePercent}</strong></div>
        <div><span>SOURCE CHECKSUM</span><strong title={source.trace_sha256}>{source.trace_sha256.slice(0, 12)}…</strong></div>
        <div><span>TRACE SCHEMA</span><strong>V{source.trace_schema_version}</strong></div>
      </div>

      {source.exclusion_reasons.length > 0 && (
        <p className="trajectory-exclusions">
          Reference exclusions: {source.exclusion_reasons.map(humanize).join(" · ")}
        </p>
      )}

      <div className="trajectory-controls" aria-label="Path plot controls">
        <button type="button" onClick={() => setZoom((value) => Math.min(5, value * 1.5))} aria-label="Zoom in on observed path">+</button>
        <button type="button" onClick={() => setZoom((value) => Math.max(0.5, value / 1.5))} aria-label="Zoom out on observed path">−</button>
        <button type="button" onClick={() => setZoom(1)}>Fit path</button>
        <span>{zoom.toFixed(2)}×</span>
      </div>

      <div className="trajectory-plot-wrap">
        <svg
          className="trajectory-plot"
          viewBox={`0 0 ${PLOT_WIDTH} ${PLOT_HEIGHT}`}
          role="img"
          aria-label="Observed world X and Z vehicle positions, plotted in metres with equal spatial scale and discontinuities kept separate"
          preserveAspectRatio="xMidYMid meet"
        >
          <rect x="0" y="0" width={PLOT_WIDTH} height={PLOT_HEIGHT} className="trajectory-plot-background" />
          <text x={PLOT_WIDTH / 2} y={PLOT_HEIGHT - 9} className="trajectory-axis-label" textAnchor="middle">WORLD X · M</text>
          <text x="16" y={PLOT_HEIGHT / 2} className="trajectory-axis-label" textAnchor="middle" transform={`rotate(-90 16 ${PLOT_HEIGHT / 2})`}>WORLD Z · M</text>
          {projectedSegments.map((segment) => (
            <g
              key={segment.segmentIndex}
              role="group"
              aria-label={`Observed segment ${segment.segmentIndex + 1}; break before: ${segment.breakBeforeReasons.map(humanize).join(", ")}`}
            >
              {segment.points.length > 1 ? (
                <polyline
                  className="trajectory-path-segment"
                  points={segment.points.map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ")}
                  aria-hidden="true"
                />
              ) : segment.points.length === 1 ? (
                <circle
                  className="trajectory-single-point"
                  cx={segment.points[0].x}
                  cy={segment.points[0].y}
                  r="3"
                  aria-hidden="true"
                />
              ) : null}
            </g>
          ))}
          {highlightedFragments.map(({ fragment, segment, points }) => (
            <g
              key={`region-${regionHighlight?.identifier}-${fragment.fragment_index}`}
              role="group"
              aria-label={`Retained preview positions in ${regionHighlight?.label}, source fragment ${fragment.fragment_index + 1}, segment ${segment.segmentIndex + 1}`}
            >
              {points.length > 1 ? (
                <polyline
                  className="trajectory-region-highlight"
                  points={points.map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ")}
                  aria-hidden="true"
                />
              ) : (
                <circle
                  className="trajectory-region-highlight-point"
                  cx={points[0].x}
                  cy={points[0].y}
                  r="4"
                  aria-hidden="true"
                />
              )}
            </g>
          ))}
          {selectedProjected && (
            <circle
              className="trajectory-selected-point"
              cx={selectedProjected.x}
              cy={selectedProjected.y}
              r="7"
              aria-hidden="true"
            />
          )}
        </svg>
      </div>

      {regionHighlight ? (
        <RegionPositionEvidence
          region={regionHighlight}
          retainedPreviewPointCount={retainedPreviewPointCount}
          highlightedPreviewPointCount={highlightedPreviewPointCount}
        />
      ) : null}

      <div className="trajectory-sample-control">
        <label htmlFor="trajectory-point-range">INSPECT RETAINED SAMPLE</label>
        <input
          id="trajectory-point-range"
          type="range"
          min={0}
          max={Math.max(0, flattened.length - 1)}
          value={Math.max(0, safeSelectedIndex)}
          onChange={(event) => setSelectedIndex(Number(event.currentTarget.value))}
          aria-valuetext={selected ? `Frame ${selected.point.frame_identifier}` : "No selected point"}
          disabled={flattened.length === 0}
        />
        <span>{selected ? `FRAME ${selected.point.frame_identifier}` : "NO POINTS"}</span>
      </div>

      {selected && (
        <dl className="trajectory-selected-details">
          <div><dt>SESSION TIME</dt><dd>{selected.point.session_time_s.toFixed(3)} s</dd></div>
          <div><dt>LAP TIME</dt><dd>{selected.point.lap_time_s.toFixed(3)} s</dd></div>
          <div><dt>LAP DISTANCE</dt><dd>{selected.point.lap_distance_m.toFixed(1)} m</dd></div>
          <div><dt>WORLD X</dt><dd>{selected.point.world_position_m.x.toFixed(2)} m</dd></div>
          <div><dt>WORLD Y</dt><dd>{selected.point.world_position_m.y.toFixed(2)} m</dd></div>
          <div><dt>WORLD Z</dt><dd>{selected.point.world_position_m.z.toFixed(2)} m</dd></div>
          <div><dt>SEGMENT</dt><dd>{selected.segmentIndex + 1} / {report.coverage.segment_count}</dd></div>
        </dl>
      )}

      <div className="trajectory-coverage-note">
        Rendered {report.preview.rendered_point_count.toLocaleString()} of {report.preview.source_position_point_count.toLocaleString()} observed position samples across {report.preview.rendered_segment_count} segments.
        {report.preview.omitted_position_point_count > 0 && " Preview points were thinned; source coverage and segment anchors remain unchanged."}
      </div>
      <BreakExamples report={report} />
      <p className="trajectory-footnote">
        This is a sampled world-coordinate projection with no calibrated circuit orientation. It does not show an ideal line, lateral error, apex, or coaching conclusion. Source: run {source.run_id.slice(0, 8)} · session {source.session_uid} · player index {source.car_index}.
      </p>
    </section>
  );
}

function RegionPositionEvidence({
  region,
  retainedPreviewPointCount,
  highlightedPreviewPointCount,
}: {
  region: AttemptRegionWindow;
  retainedPreviewPointCount: number;
  highlightedPreviewPointCount: number;
}) {
  const evidence = region.position_evidence;
  const [startM, endM] = region.analysis_window_m;
  return (
    <details className="trajectory-region-evidence" open aria-live="polite">
      <summary>
        {region.label} path evidence · {startM.toFixed(0)}–{endM.toFixed(0)} m
      </summary>
      <p>
        Window includes its start and excludes its end. Full-source positions: {evidence.source_position_sample_count?.toLocaleString() ?? "unavailable"} of {evidence.source_sample_count_in_window.toLocaleString()} recorded samples. This plot retains {retainedPreviewPointCount.toLocaleString()} preview positions in the window; {highlightedPreviewPointCount.toLocaleString()} are highlighted across the listed source fragments. Preview thinning can omit source positions from the highlight.
      </p>
      {evidence.status === "motion_unavailable_for_trace_schema" ? (
        <p>World positions are unavailable in this trace schema.</p>
      ) : evidence.status === "no_source_samples_in_window" ? (
        <p>No source telemetry samples have lap distance inside this window.</p>
      ) : evidence.status === "positions_unavailable_in_window" ? (
        <p>Telemetry samples exist in the window, but none has usable source position evidence.</p>
      ) : null}
      {evidence.source_position_sample_count !== null &&
      evidence.source_position_sample_count > 0 &&
      evidence.fragments.length > 0 ? (
        <ul>
          {evidence.fragments.map((fragment) => (
            <li key={`${region.identifier}:${fragment.fragment_index}`}>
              Source fragment {fragment.fragment_index + 1} · segment {fragment.segment_index + 1}
              {fragment.break_before_reasons.length
                ? ` · break before: ${fragment.break_before_reasons.map(humanize).join(", ")}`
                : ""}
              {fragment.window_membership_break_before && !fragment.clipped_at_window_start
                ? " · re-enters after an out-of-window position"
                : ""}
              {fragment.window_membership_break_after && !fragment.clipped_at_window_end
                ? " · separates before an out-of-window position"
                : ""}
              {fragment.clipped_at_window_start ? " · clipped at window start" : ""}
              {fragment.clipped_at_window_end ? " · clipped at window end" : ""}
              {` · ${fragment.sample_count.toLocaleString()} source positions`}
              <br />
              {formatPositionAnchor(fragment.start_anchor)}
              {fragment.sample_count > 1 ? ` → ${formatPositionAnchor(fragment.end_anchor)}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
      {evidence.omitted_fragment_count > 0 ? (
        <p>{evidence.omitted_fragment_count} source position {evidence.omitted_fragment_count === 1 ? "fragment was" : "fragments were"} omitted from both this bounded anchor list and the highlight.</p>
      ) : null}
    </details>
  );
}

const FRAME_ID_MODULUS = 0x1_0000_0000;

function isFrameBetweenInclusive(frame: number, start: number, end: number): boolean {
  const span = (end - start + FRAME_ID_MODULUS) % FRAME_ID_MODULUS;
  const offset = (frame - start + FRAME_ID_MODULUS) % FRAME_ID_MODULUS;
  return offset <= span;
}

function formatPositionAnchor(anchor: AttemptRegionWindow["position_evidence"]["fragments"][number]["start_anchor"]) {
  const { world_position_m: position } = anchor;
  return `Frame ${anchor.frame_identifier} · ${anchor.lap_distance_m.toFixed(1)} m · ${anchor.session_time_s.toFixed(3)} s · X/Y/Z ${position.x.toFixed(2)} / ${position.y.toFixed(2)} / ${position.z.toFixed(2)} m`;
}

function BreakExamples({ report }: { report: AttemptTrajectoryPreview }) {
  const examples = report.break_examples;
  const omitted = report.preview.break_examples_omitted_count;
  if (examples.length === 0) return null;
  return (
    <details className="trajectory-breaks">
      <summary>
        {report.coverage.discontinuity_count} discontinuities · {examples.length} examples
        {omitted > 0 ? ` shown · ${omitted} omitted` : " shown"}
      </summary>
      <ul>
        {examples.map((example, index) => {
          const frame = example.before?.frame_identifier ?? example.frame_identifier ?? null;
          return <li key={`${example.reason}-${frame}-${index}`}>{humanize(example.reason)}{frame == null ? "" : ` · frame ${frame}`}</li>;
        })}
      </ul>
    </details>
  );
}

function projectSegments(report: AttemptTrajectoryPreview, zoom: number) {
  const allPoints = report.segments.flatMap((segment) => segment.points);
  if (allPoints.length === 0) return [];
  const xs = allPoints.map((point) => point.world_position_m.x);
  const zs = allPoints.map((point) => point.world_position_m.z);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minZ = Math.min(...zs);
  const maxZ = Math.max(...zs);
  const rangeX = Math.max(1, maxX - minX);
  const rangeZ = Math.max(1, maxZ - minZ);
  const scale = Math.min(
    (PLOT_WIDTH - PLOT_PADDING * 2) / rangeX,
    (PLOT_HEIGHT - PLOT_PADDING * 2) / rangeZ,
  ) * zoom;
  const centerX = (minX + maxX) / 2;
  const centerZ = (minZ + maxZ) / 2;
  return report.segments.map((segment) => ({
    segmentIndex: segment.segment_index,
    breakBeforeReasons: segment.break_before_reasons,
    points: segment.points.map((point) => ({
      point,
      x: PLOT_WIDTH / 2 + (point.world_position_m.x - centerX) * scale,
      y: PLOT_HEIGHT / 2 - (point.world_position_m.z - centerZ) * scale,
    })),
  }));
}

function humanize(value: string) {
  return value.replaceAll("_", " ");
}
