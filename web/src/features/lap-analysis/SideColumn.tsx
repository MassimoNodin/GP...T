"use client";

import { Bone } from "@/features/route-states/Skeleton";
import { lapAnalysisFixture } from "@/features/review-data/fixtures";
import { formatDelta, formatLapTime, formatSectorTime } from "@/features/review-data/format";
import { StatusGlyph } from "@/features/ui-foundation/icons";
import { Card, DeltaPill } from "@/features/ui-foundation/primitives";
import { NotAvailable } from "@/features/ui-foundation/states";
import { useAnalysis } from "./analysis-context";
import styles from "./LapAnalysis.module.css";

export const SECTOR_MISSING = "Sector times were not reported for this lap.";

/** Sector Comparison, Key Differences and Data Quality cards of the overview layout. */
export function SideColumn({ show }: { show: { sectors: boolean; differences: boolean; quality: boolean } }) {
  const { measured, target, reference, enrichment, differences, quality, lapDelta, range } = useAnalysis();
  const loadingSectors = measured && enrichment.state === "deferred";
  const sectors = target
    ? [0, 1, 2].map((i) => {
        const value = target.sectors?.[i] ?? null;
        const ref = reference?.sectors?.[i] ?? null;
        return { label: `S${i + 1}`, ms: value, deltaMs: value !== null && ref !== null ? value - ref : null };
      })
    : [];
  const sectorReason = target?.sectorsReason ?? SECTOR_MISSING;
  const missing = (reason: string) => (measured ? <NotAvailable reason={reason} /> : <span className={styles.na}>—</span>);
  return (
    <div className={styles.sideCol}>
      {show.sectors && (
        <Card title="Sector Comparison" labelledBy="sector-comparison" className={styles.sectorCard}>
          <table className={styles.sectorTable}>
            <tbody>
              {sectors.map((sector) => (
                <tr key={sector.label}>
                  <th scope="row">{sector.label}</th>
                  <td className="tabular">{loadingSectors ? <Bone width={48} height={12} /> : sector.ms === null && measured ? missing(sectorReason) : formatSectorTime(sector.ms)}</td>
                  <td>
                    {sector.deltaMs !== null ? (
                      <DeltaPill ms={sector.deltaMs}>{formatDelta(sector.deltaMs)}</DeltaPill>
                    ) : loadingSectors ? null : (
                      missing(reference ? "A sector time is missing for one of the laps." : "Select a reference lap to compare sectors.")
                    )}
                  </td>
                </tr>
              ))}
              <tr className={styles.lapRow}>
                <th scope="row">Lap</th>
                <td className="tabular">{formatLapTime(target?.ms)}</td>
                <td>{lapDelta !== null ? <DeltaPill ms={lapDelta}>{formatDelta(lapDelta)}</DeltaPill> : <span className={styles.na}>—</span>}</td>
              </tr>
            </tbody>
          </table>
          {measured && enrichment.state === "unavailable" && <p className={styles.unavailable}>{enrichment.reason}</p>}
          {measured && !loadingSectors && target && !target.sectors && enrichment.state !== "unavailable" && (
            <p className={styles.unavailable}>{sectorReason}</p>
          )}
        </Card>
      )}
      {show.differences && (
        <Card
          title={measured ? (range ? "Key Differences (Selected Region)" : "Key Differences (Full Lap)") : "Key Differences (Selected Region)"}
          labelledBy="key-differences"
          className={styles.diffCard}
        >
          {measured && differences.length === 0 ? (
            <p className={styles.unavailable}>
              {reference ? "Differences are not available for these laps." : "Select a reference lap to see differences."}
            </p>
          ) : (
            <ul className={styles.diffList}>
              {(measured ? differences : lapAnalysisFixture.differences).map((item) => (
                <li key={item.text} data-emphasis={item.emphasis ? "true" : undefined}>
                  <StatusGlyph tone="red" glyph="alert" size={13} />
                  {item.text}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
      {show.quality && (
        <Card title="Data Quality" labelledBy="data-quality" className={styles.qualityCard}>
          <ul className={styles.qualityList}>
            {(measured ? quality : lapAnalysisFixture.quality).map((item) => (
              <li key={item.text}>
                <StatusGlyph tone={item.tone} glyph="ring" size={12} />
                {item.text}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
