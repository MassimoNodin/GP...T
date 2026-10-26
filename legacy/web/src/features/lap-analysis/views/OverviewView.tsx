"use client";

import { ChartCard, OVERVIEW_ROWS } from "../ChartCard";
import { SideColumn } from "../SideColumn";
import { TrackCard } from "../TrackCard";

/** Track card, chart and the three summary cards, as the reference draws them. */
export function OverviewView() {
  return (
    <>
      <TrackCard />
      <ChartCard rows={OVERVIEW_ROWS} />
      <SideColumn show={{ sectors: true, differences: true, quality: true }} />
    </>
  );
}
