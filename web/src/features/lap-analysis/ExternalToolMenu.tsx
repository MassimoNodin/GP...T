"use client";

import { ChevronDownIcon, OpenDocumentIcon } from "@/features/ui-foundation/icons";
import { Menu, MenuItem } from "@/features/ui-foundation/primitives";
import { isDelta } from "./delta";
import { lapTimesCsv, slug, telemetryCsv, type ExportLap } from "./export";
import { useExportSnapshot } from "./export-store";
import type { AnalysisLap } from "./load";
import styles from "./ExportLapsButton.module.css";

function download(text: string, fileName: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function exportLaps(laps: readonly AnalysisLap[]): ExportLap[] {
  return laps.map((lap) => ({
    lap: lap.lap,
    ms: lap.ms,
    sectors: lap.sectors,
    valid: lap.valid,
    pit: lap.pit,
    tyre: lap.tyre ? { label: lap.tyre.label, ageLaps: lap.tyre.ageLaps } : null,
  }));
}

/**
 * "Open in External Tool": an export menu. Lap times always; the telemetry of
 * the compared laps once their traces are loaded; the original capture for
 * imported sessions that still have it.
 */
export function ExternalToolMenu({ laps, fileName }: { laps: AnalysisLap[]; fileName: string }) {
  const snapshot = useExportSnapshot();
  const allLaps = snapshot?.laps ?? laps;
  const base = slug(snapshot?.fileName ?? fileName);
  const target = snapshot?.target ?? null;
  const reference = snapshot?.reference ?? null;
  const targetTrace = snapshot?.targetTrace ?? null;
  const telemetryReady = targetTrace?.state === "available";
  const telemetryLabel = target
    ? `Telemetry for Lap ${target.lap}${reference ? ` vs ${reference.lap}` : ""} (CSV)`
    : "Telemetry (CSV)";
  const capture = snapshot?.capture ?? null;

  return (
    <Menu
      label="Open in External Tool"
      className={styles.menu}
      triggerClassName={styles.trigger}
      panelClassName={styles.panel}
      trigger={
        <>
          <OpenDocumentIcon size={14} />
          Open in External Tool
          <ChevronDownIcon size={13} />
        </>
      }
    >
      <MenuItem onSelect={() => download(lapTimesCsv(exportLaps(allLaps)), `${base}-laps.csv`)}>Lap times (CSV)</MenuItem>
      <MenuItem
        disabled={!telemetryReady}
        onSelect={() => {
          const csv = telemetryCsv(targetTrace ?? undefined, snapshot?.referenceTrace, snapshot?.delta);
          if (csv && target) download(csv, `${base}-lap-${target.lap}${reference ? `-vs-${reference.lap}` : ""}-telemetry.csv`);
        }}
      >
        {telemetryLabel}
        {!telemetryReady && <span className={styles.reason}>Telemetry for the selected lap is not loaded.</span>}
        {telemetryReady && snapshot && isDelta(snapshot.delta) && <span className={styles.reason}>Includes the derived time delta.</span>}
      </MenuItem>
      {capture && (
        <a href={`/api/recording-sources/${capture.captureId}/download?version=${capture.version}`} download>
          Original capture (.f1ecap)
        </a>
      )}
      <p className={styles.note}>Files open in spreadsheet or telemetry tools.</p>
    </Menu>
  );
}
