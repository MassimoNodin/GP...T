"use client";

import { lapAnalysisFixture } from "@/features/review-data/fixtures";
import { Card } from "@/features/ui-foundation/primitives";
import { useAnalysis } from "./analysis-context";
import { MeasuredCharts } from "./MeasuredCharts";
import { paddedWindow } from "./regions";
import { fixtureBand } from "./TrackCard";
import { TelemetryCharts, type RowKey } from "./TelemetryCharts";
import styles from "./LapAnalysis.module.css";

/** Row sets per view; the Time Delta row is added on top for real sessions in Time Delta mode. */
export const OVERVIEW_ROWS: RowKey[] = ["delta", "speed", "throttle", "brake", "steering"];
export const INPUT_ROWS: RowKey[] = ["throttle", "brake", "steering", "gear"];

/**
 * The chart card of the overview and inputs layouts. Fixture sessions draw the
 * reference's illustrative traces; real sessions the measured ones, zoomed to
 * the selected region or brushed range.
 */
export function ChartCard({ rows }: { rows: RowKey[] }) {
  const { measured, compare, fixtureRegion, selectedChoice, brush, axis } = useAnalysis();
  if (!measured) {
    const chart = lapAnalysisFixture.chart;
    const ticks = compare === "distance" ? [2400, 2500, 2600, 2700, 2800, 2900, 3000] : chart.ticks;
    return (
      <Card className={styles.chartCard}>
        <TelemetryCharts
          from={chart.from}
          to={chart.to}
          ticks={ticks}
          band={fixtureBand(fixtureRegion)}
          channels={rows.filter((row) => row !== "delta" && row !== "gear")}
        />
      </Card>
    );
  }
  const zoom = brush ?? (selectedChoice && axis ? paddedWindow(selectedChoice.range, axis.to) : null);
  return (
    <Card className={styles.chartCard}>
      <MeasuredCharts rows={rows} zoom={zoom} band={brush ? null : (selectedChoice?.range ?? null)} hover />
    </Card>
  );
}
