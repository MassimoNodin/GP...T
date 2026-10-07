"use client";

import { useState } from "react";
import type { AnalysisInput, TrackRegion } from "@/lib/analysis-display";
import { REGION_COLOURS } from "@/lib/analysis-display";
import {
  AnalysisFileInput,
  AnalysisPanel,
  LapComparisonTrace,
  signed,
  TrackAnalysisMap,
} from "./AnalysisWidgets";
import { CircuitIcon, LightbulbIcon, TableIcon, TargetIcon } from "./Icons";

export default function TrackAnalysisScreen({
  initialInput,
  selectionHref,
  initialRegionId = null,
}: {
  initialInput: AnalysisInput;
  selectionHref: string | null;
  initialRegionId?: string | null;
}) {
  const [loaded, setLoaded] = useState<AnalysisInput | null>(null),
    [selectedId, setSelectedId] = useState<string | null>(initialRegionId),
    [cursor, setCursor] = useState<number | null>(null);
  const input = loaded ?? initialInput;
  const [inputRevision, setInputRevision] = useState(0);
  const shape = input.track ?? {
    name: "No track selected",
    lengthM: 0,
    geometryKind: "supplied" as const,
    similarThresholdS: 0.01,
    segments: [],
    regions: [],
  };
  const region =
    shape.regions.find((r) => r.id === selectedId) ?? shape.regions[0] ?? null;
  function select(id: string) {
    setSelectedId(id);
    setCursor(null);
  }
  function load(value: AnalysisInput) {
    setInputRevision((current) => current + 1);
    setLoaded(value);
    setSelectedId(value.track?.regions[0]?.id ?? null);
    setCursor(null);
  }
  return (
    <section
      className="reference-screen reference-track dynamic-track"
      aria-label="Track analysis screen"
    >
      <AnalysisPanel
        title="Track & Analysis Configuration"
        icon={<CircuitIcon size={22} />}
        action={
          <AnalysisFileInput
            onLoad={load}
            loaded={loaded !== null}
            onReset={() => {
              setInputRevision((current) => current + 1);
              setLoaded(null);
              setSelectedId(initialRegionId);
              setCursor(null);
            }}
          />
        }
        className="analysis-configuration"
      >
        <div className="analysis-configuration-details">
          <strong>{shape.name}</strong>
          <span>
            {shape.lengthM
              ? `${(shape.lengthM / 1000).toFixed(3)} km · ${shape.regions.length} named regions`
              : "Choose an attempt and track model"}
          </span>
          <span>
            {loaded
              ? "Local input file"
              : "Selected recorded attempt · diagnostic regions"}
          </span>
          {selectionHref ? (
            <a className="ref-button" href={selectionHref}>
              Choose laps and reference
            </a>
          ) : null}
          <a className="ref-button" href="#track-evidence-trigger">
            Track model & attempt controls
          </a>
        </div>
      </AnalysisPanel>
      <div className="ref-grid track-main">
        <AnalysisPanel
          title="Track Map & Region Analysis"
          icon={<CircuitIcon size={23} />}
        >
          <TrackAnalysisMap
            shape={shape}
            selectedRegionId={region?.id}
            onSelectRegion={select}
            cursorDistanceM={cursor}
          />
        </AnalysisPanel>
        <div className="ref-stack">
          <AnalysisPanel
            title={
              region ? `Region Metrics – ${region.label}` : "Region Metrics"
            }
            icon={<LightbulbIcon size={22} />}
          >
            <div className="analysis-region-metrics">
              <RegionMetric
                label="Minimum Speed"
                value={region?.minimumSpeedKph}
                unit="km/h"
                reference={region?.referenceMinimumSpeedKph}
              />
              <RegionMetric
                label="Throttle On Distance"
                value={region?.throttlePickupM}
                unit="m"
                reference={region?.referenceThrottlePickupM}
              />
              <div className="analysis-metric">
                <span>Time Delta</span>
                <strong
                  style={{ color: REGION_COLOURS[region?.status ?? "unknown"] }}
                >
                  {region?.deltaS == null ? "—" : `${signed(region.deltaS)} s`}
                </strong>
                <small>
                  {region?.status === "unknown" || !region
                    ? "Timing unavailable"
                    : `${region.status} vs reference`}
                </small>
              </div>
            </div>
          </AnalysisPanel>
          <div className="ref-grid track-insights">
            <AnalysisPanel
              title={
                region ? `Region Evidence – ${region.label}` : "Region Evidence"
              }
              icon={<TargetIcon size={21} />}
            >
              <RegionEvidence region={region} />
            </AnalysisPanel>
            <AnalysisPanel
              title="Region List – Comparisons"
              icon={<TableIcon size={21} />}
            >
              <div className="ref-table-wrap">
                <table className="ref-table analysis-region-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Turn / Region</th>
                      <th>Time Delta</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shape.regions.map((r, i) => (
                      <tr
                        key={r.id}
                        className={r.id === region?.id ? "is-selected" : ""}
                      >
                        <td>{i + 1}</td>
                        <td>
                          <button
                            onClick={() => select(r.id)}
                            aria-pressed={r.id === region?.id}
                          >
                            {r.label}
                          </button>
                        </td>
                        <td>
                          {r.deltaS == null ? "—" : `${signed(r.deltaS)} s`}
                        </td>
                        <td>
                          <span
                            className="analysis-status-dot"
                            style={{ background: REGION_COLOURS[r.status] }}
                          />
                          {r.status === "unknown" ? "Unavailable" : r.status}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!shape.regions.length ? (
                  <p className="analysis-source-note">
                    No named regions were supplied.
                  </p>
                ) : null}
              </div>
            </AnalysisPanel>
          </div>
        </div>
      </div>
      <AnalysisPanel
        title={
          region
            ? `Region Comparison – ${region.label}`
            : "Lap Comparison Trace"
        }
        icon={<TableIcon size={22} />}
        className="analysis-region-trace"
      >
        <LapComparisonTrace
          compact
          key={JSON.stringify([
            inputRevision,
            region?.id,
            input.comparison?.target.label,
          ])}
          input={input.comparison ?? null}
          windowM={region ? [region.startM, region.endM] : null}
          onCursor={setCursor}
        />
      </AnalysisPanel>
    </section>
  );
}
function RegionMetric({
  label,
  value,
  unit,
  reference,
}: {
  label: string;
  value?: number | null;
  unit: string;
  reference?: number | null;
}) {
  return (
    <div className="analysis-metric">
      <span>{label}</span>
      <strong>{value == null ? "—" : `${Math.round(value)} ${unit}`}</strong>
      <small>
        Ref: {reference == null ? "—" : `${Math.round(reference)} ${unit}`}
      </small>
    </div>
  );
}
function RegionEvidence({ region }: { region: TrackRegion | null }) {
  if (!region)
    return (
      <p className="analysis-source-note">
        Select a region to inspect its supplied evidence.
      </p>
    );
  const speed =
    region.minimumSpeedKph != null && region.referenceMinimumSpeedKph != null
      ? region.minimumSpeedKph - region.referenceMinimumSpeedKph
      : null;
  const pickup =
    region.throttlePickupM != null && region.referenceThrottlePickupM != null
      ? region.throttlePickupM - region.referenceThrottlePickupM
      : null;
  return (
    <ul className="analysis-evidence">
      <li>
        Distance window: {region.startM.toFixed(0)}–{region.endM.toFixed(0)} m.
      </li>
      <li>
        {region.deltaS == null
          ? region.status === "unknown"
            ? "Connected timing comparison is unavailable in this region."
            : `Supplied classification: ${region.status}. No time difference was supplied.`
          : `Target is ${Math.abs(region.deltaS).toFixed(3)} s ${region.deltaS < 0 ? "quicker" : region.deltaS > 0 ? "slower" : "equal"} across this region.`}
      </li>
      <li>
        {speed == null
          ? "Minimum speed comparison is unavailable."
          : `Minimum speed is ${Math.abs(speed).toFixed(1)} km/h ${speed < 0 ? "lower" : "higher"} than reference.`}
      </li>
      <li>
        {pickup == null
          ? "Throttle pickup comparison is unavailable."
          : `50% throttle pickup is ${Math.abs(pickup).toFixed(1)} m ${pickup < 0 ? "earlier" : "later"} than reference.`}
      </li>
    </ul>
  );
}
