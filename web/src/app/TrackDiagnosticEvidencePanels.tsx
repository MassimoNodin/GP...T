"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { AttemptRegionReport, AttemptTrajectoryPreview } from "@/lib/api";
import { appScreenHref } from "@/lib/navigation";
import DiagnosticEvidencePanels from "./DiagnosticEvidencePanels";

export default function TrackDiagnosticEvidencePanels({
  trajectoryReport,
  trajectoryUnavailableReason,
  regionReport,
  regionUnavailableReason,
  showRegionPanel,
  initialSelectedRegionId,
  preservedQuery,
}: {
  trajectoryReport: AttemptTrajectoryPreview | null;
  trajectoryUnavailableReason: string | null;
  regionReport: AttemptRegionReport | null;
  regionUnavailableReason: string | null;
  showRegionPanel: boolean;
  initialSelectedRegionId: string | null;
  preservedQuery: string;
}) {
  const router = useRouter();
  const [selectedRegionId, setSelectedRegionId] = useState(initialSelectedRegionId);
  const [selectionError, setSelectionError] = useState<string | null>(null);

  function selectRegion(identifier: string | null) {
    const query = new URLSearchParams(preservedQuery);
    query.delete("engineer_region_identifier");
    const href = identifier
      ? appScreenHref("track", preservedQuery, { engineer_region_identifier: identifier })
      : appScreenHref("track", query.toString());
    if (!href) {
      setSelectionError("This selection is too large to carry safely. The current region highlight was kept.");
      return;
    }
    setSelectionError(null);
    setSelectedRegionId(identifier);
    router.push(href, { scroll: false });
  }

  return (
    <>
      {selectionError ? <p className="track-selection-warning" role="status">{selectionError}</p> : null}
      <DiagnosticEvidencePanels
        trajectoryReport={trajectoryReport}
        trajectoryUnavailableReason={trajectoryUnavailableReason}
        regionReport={regionReport}
        regionUnavailableReason={regionUnavailableReason}
        showRegionPanel={showRegionPanel}
        selectedRegionId={selectedRegionId}
        onSelectedRegionChange={selectRegion}
      />
    </>
  );
}
