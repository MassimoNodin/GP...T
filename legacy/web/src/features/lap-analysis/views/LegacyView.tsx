"use client";

import { ChartCard } from "../ChartCard";
import { SideColumn } from "../SideColumn";
import { TrackCard } from "../TrackCard";
import type { View } from "../url-state";

/** The reference layouts of the fixture session: one drawing, re-filtered per tab. */
export function LegacyView({ view }: { view: View }) {
  const channels = view === "inputs" ? (["throttle", "brake", "steering"] as const) : (["speed", "throttle", "brake", "steering"] as const);
  return (
    <>
      {(view === "overview" || view === "map") && <TrackCard />}
      {(view === "overview" || view === "telemetry" || view === "inputs") && <ChartCard rows={[...channels]} />}
      {(view === "overview" || view === "sectors" || view === "quality") && (
        <SideColumn
          show={{
            sectors: view === "overview" || view === "sectors",
            differences: view === "overview" || view === "sectors",
            quality: view === "overview" || view === "quality",
          }}
        />
      )}
    </>
  );
}
