"use client";

import { Card, DeltaPill } from "@/features/ui-foundation/primitives";
import { StatTile } from "@/features/ui-foundation/StatTile";
import { useAnalysis } from "../analysis-context";
import { ChartCard, INPUT_ROWS } from "../ChartCard";
import { inputStats, type InputStats } from "../inputs-stats";
import { formatRange } from "../regions";
import styles from "../Views.module.css";

type Tile = {
  key: keyof InputStats;
  label: string;
  missing: string;
  format: (value: number) => string;
  /** Which direction counts as better, when that is unambiguous. */
  better?: "higher" | "lower";
  unit: string;
};

const TILES: Tile[] = [
  { key: "fullThrottlePct", label: "Full throttle", missing: "Throttle was not recorded", format: (v) => `${Math.round(v)}%`, better: "higher", unit: "%" },
  { key: "coastingPct", label: "Coasting", missing: "Throttle and brake were not both recorded", format: (v) => `${Math.round(v)}%`, better: "lower", unit: "%" },
  { key: "brakingZones", label: "Braking zones", missing: "Brake was not recorded", format: (v) => String(v), unit: "" },
  { key: "brakingDistanceM", label: "Braking distance", missing: "Brake was not recorded", format: (v) => `${Math.round(v).toLocaleString("en-US")} m`, unit: " m" },
  { key: "peakBrakePct", label: "Peak brake", missing: "Brake was not recorded", format: (v) => `${Math.round(v)}%`, unit: "%" },
  { key: "steeringPer100m", label: "Steering activity", missing: "Steering was not recorded", format: (v) => `${Math.round(v)}`, unit: "" },
];

function difference(tile: Tile, target: number | null, reference: number | null) {
  if (target === null || reference === null) return null;
  const diff = Math.round(target) - Math.round(reference);
  if (diff === 0) return { text: "0", ms: 0 };
  const good = tile.better === undefined ? null : tile.better === "higher" ? diff > 0 : diff < 0;
  // DeltaPill colours positive red and negative green; map "better" onto that.
  return { text: `${diff > 0 ? "+" : ""}${diff}`, ms: good === null ? 0 : good ? -1 : 1 };
}

/** Driver-input summary of the compared laps over the selected range, plus the input channels. */
export function InputsView() {
  const { target, reference, targetTrace, referenceTrace, range, selectedChoice, model } = useAnalysis();
  const targetStats = inputStats(targetTrace, range);
  const referenceStats = referenceTrace ? inputStats(referenceTrace, range) : null;
  const scope = range ? (selectedChoice ? `${selectedChoice.label} (${formatRange(range)})` : formatRange(range)) : "Full lap";
  const hasTarget = targetTrace?.state === "available";

  return (
    <div className={styles.stack}>
      <Card title={`Inputs over ${scope}`} labelledBy="input-summary" className={styles.tableCard}>
        {!hasTarget ? (
          <p className={styles.muted}>Input statistics need telemetry for Lap {target?.lap ?? ""}. See the chart below.</p>
        ) : (
          <>
            <p className={styles.legendLine}>
              Values read Lap {target?.lap}
              {reference ? ` / Lap ${reference.lap}` : ""}.
              {model.source === "v1" ? " Gear is not available for imported captures." : ""}
            </p>
            <div className={styles.tiles}>
              {TILES.map((tile) => {
                const t = targetStats[tile.key];
                const r = referenceStats ? referenceStats[tile.key] : null;
                const diff = difference(tile, t, r);
                return (
                  <StatTile
                    key={tile.key}
                    label={tile.label}
                    value={t === null ? "" : r === null || !reference ? tile.format(t) : `${tile.format(t)} / ${tile.format(r)}`}
                    unavailableReason={t === null ? tile.missing : undefined}
                    sub={diff ? <DeltaPill ms={diff.ms}>{diff.text}</DeltaPill> : undefined}
                  />
                );
              })}
              {targetStats.gearChanges !== null && (
                <StatTile
                  label="Gear changes"
                  value={
                    referenceStats && referenceStats.gearChanges !== null
                      ? `${targetStats.gearChanges} / ${referenceStats.gearChanges}`
                      : String(targetStats.gearChanges)
                  }
                />
              )}
            </div>
          </>
        )}
      </Card>
      <ChartCard rows={INPUT_ROWS} />
    </div>
  );
}
