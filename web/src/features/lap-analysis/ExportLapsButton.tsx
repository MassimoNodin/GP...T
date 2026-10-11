"use client";

import { OpenDocumentIcon } from "@/features/ui-foundation/icons";
import { formatLapTime, formatSectorTime } from "@/features/review-data/format";
import type { AnalysisLap } from "./load";
import styles from "./ExportLapsButton.module.css";

/** Exports the session's lap and sector times as CSV for spreadsheets and other tools. */
export function ExportLapsButton({ laps, fileName }: { laps: AnalysisLap[]; fileName: string }) {
  function download() {
    const rows = [
      ["lap", "lap_time", "lap_time_ms", "s1", "s2", "s3"],
      ...laps.map((lap) => [
        String(lap.lap),
        formatLapTime(lap.ms),
        String(lap.ms),
        ...[0, 1, 2].map((i) => (lap.sectors ? formatSectorTime(lap.sectors[i]) : "")),
      ]),
    ];
    const csv = rows.map((row) => row.join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${fileName}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }
  return (
    <button type="button" className={styles.button} onClick={download} title="Download lap and sector times as CSV">
      <OpenDocumentIcon size={14} />
      Open in External Tool
    </button>
  );
}
