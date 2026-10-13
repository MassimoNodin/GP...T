"use client";

import { useSyncExternalStore } from "react";
import type { DeltaSeries } from "./delta";
import type { LapTrace } from "./lap-telemetry";
import type { AnalysisLap, LapAnalysisModel } from "./load";

// "Open in External Tool" lives in the page header, outside the Lap Analysis
// client tree. This tiny store lets the menu see the laps and traces the page
// currently holds, so the telemetry export needs no second fetch.

export type ExportSnapshot = {
  fileName: string;
  laps: AnalysisLap[];
  target: AnalysisLap | null;
  reference: AnalysisLap | null;
  targetTrace: LapTrace | null;
  referenceTrace: LapTrace | null;
  delta: DeltaSeries | null;
  capture: LapAnalysisModel["capture"];
};

let snapshot: ExportSnapshot | null = null;
const listeners = new Set<() => void>();

export function publishExport(next: ExportSnapshot | null) {
  snapshot = next;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useExportSnapshot(): ExportSnapshot | null {
  return useSyncExternalStore(
    subscribe,
    () => snapshot,
    () => null,
  );
}
