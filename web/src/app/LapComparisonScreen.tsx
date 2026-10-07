"use client";

import { useMemo, useState } from "react";
import type { AnalysisInput } from "@/lib/analysis-display";
import {
  AnalysisFileInput,
  AnalysisPanel,
  LapComparisonTrace,
  signed,
  timeMs,
  TrackAnalysisMap,
} from "./AnalysisWidgets";
import { BarChartIcon, CircuitIcon, TableIcon } from "./Icons";

export type ComparisonSummary = {
  targetTimeMs: number | null;
  referenceTimeMs: number | null;
  officialDeltaS: number | null;
  sectors: Array<{
    label: string;
    targetMs: number | null;
    referenceMs: number | null;
    deltaS: number | null;
  }>;
};
export default function LapComparisonScreen({
  initialInput,
  summary,
  windowM,
  trackHref,
}: {
  initialInput: AnalysisInput;
  summary: ComparisonSummary | null;
  windowM: [number, number] | null;
  trackHref: string | null;
}) {
  const [loaded, setLoaded] = useState<AnalysisInput | null>(null),
    [cursor, setCursor] = useState<number | null>(null);
  const [regionId, setRegionId] = useState<string | null>(null),
    [start, setStart] = useState(windowM?.[0]?.toString() ?? ""),
    [end, setEnd] = useState(windowM?.[1]?.toString() ?? "");
  const input = loaded ?? initialInput,
    comparison = input.comparison ?? null;
  const resolvedSummary = loaded ? null : summary;
  const peakSpeed = useMemo(
    () =>
      topSpeed(
        comparison?.target.values.speed,
        comparison?.target.masks?.speed,
      ),
    [comparison],
  );
  const [inputRevision, setInputRevision] = useState(0);
  const validWindow =
    (start === "" && end === "") ||
    (start !== "" &&
      end !== "" &&
      Number.isFinite(Number(start)) &&
      Number.isFinite(Number(end)) &&
      Number(start) >= 0 &&
      Number(end) > Number(start));
  const range: [number, number] | null =
    validWindow && start !== "" ? [Number(start), Number(end)] : null;
  const sectors = resolvedSummary?.sectors ?? [];
  return (
    <section
      className="reference-screen reference-compare dynamic-compare"
      aria-label="Lap comparison screen"
    >
      <AnalysisPanel
        title="Lap Comparison"
        icon={<BarChartIcon size={24} />}
        action={
          <AnalysisFileInput
            loaded={loaded !== null}
            onLoad={(value) => {
              setInputRevision((current) => current + 1);
              setLoaded(value);
              setCursor(null);
              setStart("");
              setEnd("");
              setRegionId(null);
            }}
            onReset={() => {
              setInputRevision((current) => current + 1);
              setLoaded(null);
              setCursor(null);
              setStart(windowM?.[0]?.toString() ?? "");
              setEnd(windowM?.[1]?.toString() ?? "");
            }}
          />
        }
        className="analysis-configuration"
      >
        <div className="analysis-comparison-controls">
          <label>
            Target Lap
            <strong>
              <i className="dot-blue" />
              {comparison?.target.label ?? "Choose a target lap"}
            </strong>
          </label>
          <label>
            Reference Lap
            <strong>
              <i className="dot-red" />
              {comparison?.reference.label ?? "Choose a reference lap"}
            </strong>
          </label>
          <label>
            Start (m)
            <input
              type="number"
              min="0"
              value={start}
              placeholder="Full lap"
              onChange={(e) => setStart(e.target.value)}
            />
          </label>
          <label>
            End (m)
            <input
              type="number"
              min="0"
              value={end}
              placeholder="Full lap"
              onChange={(e) => setEnd(e.target.value)}
            />
          </label>
          <button
            className="ref-button"
            onClick={() => {
              setStart("");
              setEnd("");
            }}
          >
            Full Lap
          </button>
          <a className="ref-button is-primary" href="#comparison-workflow">
            Choose laps
          </a>
        </div>
        {!validWindow ? (
          <p role="alert" className="analysis-source-note">
            Enter both distances, with end greater than start.
          </p>
        ) : null}
      </AnalysisPanel>
      <div className="ref-grid compare-summary">
        <SummaryMetric
          label="Target Lap"
          value={timeMs(resolvedSummary?.targetTimeMs)}
        />
        <SummaryMetric
          label="Reference Lap"
          value={timeMs(resolvedSummary?.referenceTimeMs)}
        />
        <SummaryMetric
          label="Official Lap Delta"
          value={
            resolvedSummary?.officialDeltaS == null
              ? "—"
              : `${signed(resolvedSummary.officialDeltaS)} s`
          }
        />
        <SummaryMetric label="Top Speed" value={peakSpeed} />
        {sectors.map((s) => (
          <SummaryMetric
            key={s.label}
            label={s.label}
            value={s.deltaS == null ? "—" : `${signed(s.deltaS)} s`}
          />
        ))}
      </div>
      <div className="ref-grid compare-middle">
        <AnalysisPanel
          title="Telemetry Comparison"
          icon={<BarChartIcon size={22} />}
        >
          <LapComparisonTrace
            key={JSON.stringify([
              inputRevision,
              start,
              end,
              comparison?.target.label,
            ])}
            input={validWindow ? comparison : null}
            windowM={range}
            onCursor={setCursor}
          />
        </AnalysisPanel>
        <AnalysisPanel
          title="Track Map – Delta to Reference"
          icon={<CircuitIcon size={23} />}
          action={
            trackHref ? (
              <a className="ref-button" href={trackHref}>
                Track analysis
              </a>
            ) : null
          }
        >
          {input.track ? (
            <TrackAnalysisMap
              shape={input.track}
              selectedRegionId={regionId}
              onSelectRegion={setRegionId}
              cursorDistanceM={cursor}
            />
          ) : (
            <div className="analysis-empty">
              <CircuitIcon size={32} />
              <strong>Track shape unavailable</strong>
              <span>Position data or a supplied shape is required.</span>
            </div>
          )}
        </AnalysisPanel>
      </div>
      <AnalysisPanel
        title="Sector Comparison"
        icon={<TableIcon size={22} />}
        className="analysis-sector-panel"
      >
        <table className="ref-table">
          <thead>
            <tr>
              <th>Sector</th>
              <th>Target Lap</th>
              <th>Reference Lap</th>
              <th>Delta</th>
            </tr>
          </thead>
          <tbody>
            {sectors.map((s) => (
              <tr key={s.label}>
                <td>{s.label}</td>
                <td>
                  {s.targetMs == null ? "—" : (s.targetMs / 1000).toFixed(3)}
                </td>
                <td>
                  {s.referenceMs == null
                    ? "—"
                    : (s.referenceMs / 1000).toFixed(3)}
                </td>
                <td>{s.deltaS == null ? "—" : `${signed(s.deltaS)} s`}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!sectors.length ? (
          <p className="analysis-source-note">
            Reported sector times are unavailable for this input.
          </p>
        ) : null}
      </AnalysisPanel>
    </section>
  );
}
function SummaryMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="ref-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
function topSpeed(values?: Array<number | null>, mask?: boolean[]) {
  let max: number | null = null;
  for (let i = 0; i < (values?.length ?? 0); i++) {
    const v = values![i];
    if (v != null && Number.isFinite(v) && (!mask || mask[i] === true))
      max = Math.max(max ?? 0, v);
  }
  return max == null ? "—" : `${Math.round(max)} km/h`;
}
