"use client";

import { useState } from "react";
import type { AttemptRegionReport, AttemptTrajectoryPreview } from "@/lib/api";
import AttemptRegionsPanel from "./AttemptRegionsPanel";
import ObservedTrajectoryPanel from "./ObservedTrajectoryPanel";

export default function DiagnosticEvidencePanels({
  trajectoryReport,
  trajectoryUnavailableReason,
  regionReport,
  regionUnavailableReason,
  showRegionPanel,
  selectedRegionId,
  onSelectedRegionChange,
}: {
  trajectoryReport: AttemptTrajectoryPreview | null;
  trajectoryUnavailableReason: string | null;
  regionReport: AttemptRegionReport | null;
  regionUnavailableReason: string | null;
  showRegionPanel: boolean;
  selectedRegionId?: string | null;
  onSelectedRegionChange?: (identifier: string | null) => void;
}) {
  const [internalSelectedRegionId, setInternalSelectedRegionId] = useState<string | null>(null);
  const activeRegionId = selectedRegionId === undefined
    ? internalSelectedRegionId
    : selectedRegionId;
  const sourcesMatch =
    trajectoryReport !== null &&
    regionReport !== null &&
    sameSource(trajectoryReport, regionReport);
  const selectedRegion =
    sourcesMatch && regionReport
      ? regionReport.regions.find((region) => region.identifier === activeRegionId) ?? null
      : null;

  return (
    <div className="diagnostic-evidence-panels">
      {trajectoryReport && regionReport ? (
        <p
          className={`diagnostic-provenance-status ${sourcesMatch ? "is-matched" : "is-mismatched"}`}
          role="status"
        >
          {sourcesMatch
            ? "Region and path provenance match by attempt, run, session, player, trace checksum and schema. Spatial highlights preserve recorded segment breaks and remain diagnostic."
            : "Region and trajectory provenance differ. Spatial linking is disabled."}
        </p>
      ) : null}
      <ObservedTrajectoryPanel
        report={trajectoryReport}
        unavailableReason={trajectoryUnavailableReason}
        regionHighlight={selectedRegion}
      />
      {showRegionPanel ? (
        <AttemptRegionsPanel
          report={regionReport}
          unavailableReason={regionUnavailableReason}
          selectedRegionId={activeRegionId}
          onSelectRegion={sourcesMatch ? toggleRegion : undefined}
        />
      ) : null}
    </div>
  );

  function toggleRegion(identifier: string) {
    const nextRegionId = activeRegionId === identifier ? null : identifier;
    if (selectedRegionId === undefined) setInternalSelectedRegionId(nextRegionId);
    onSelectedRegionChange?.(nextRegionId);
    const shouldInspect = nextRegionId !== null;
    if (shouldInspect) {
      requestAnimationFrame(() => {
        document
          .getElementById("observed-driven-path")
          ?.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    }
  }
}

function sameSource(
  trajectory: AttemptTrajectoryPreview,
  regions: AttemptRegionReport,
): boolean {
  const left = trajectory.source;
  const right = regions.source;
  return (
    left.attempt_key === right.attempt_key &&
    left.run_id === right.run_id &&
    left.session_uid === right.session_uid &&
    left.car_index === right.car_index &&
    left.trace_sha256 === right.trace_sha256 &&
    left.trace_schema_version === right.trace_schema_version
  );
}
