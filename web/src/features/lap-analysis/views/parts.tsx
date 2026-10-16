"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import { Bone } from "@/features/route-states/Skeleton";
import { EmptyState, InlineAlert } from "@/features/ui-foundation/states";
import { Button } from "@/features/ui-foundation/primitives";
import { useAnalysis } from "../analysis-context";
import type { AnalysisLap } from "../load";
import type { LapTrace } from "../lap-telemetry";
import styles from "../Views.module.css";

/** Small filled circle used as a legend key (SVG, decorative). */
export function LegendDot({ tone }: { tone: "target" | "reference" }) {
  return (
    <svg className={styles.legendDot} viewBox="0 0 10 10" width="10" height="10" aria-hidden="true" focusable="false">
      <circle cx="5" cy="5" r="5" fill={`var(--${tone})`} />
    </svg>
  );
}

/** Square colour key for a legend (SVG, decorative). */
export function Swatch({ color }: { color: string }) {
  return (
    <svg className={styles.legendDot} viewBox="0 0 10 10" width="10" height="10" aria-hidden="true" focusable="false">
      <rect width="10" height="10" rx="2" fill={color} />
    </svg>
  );
}

/** Width in px of an element, tracked with a ResizeObserver; `fallback` until measured. */
export function useElementWidth<T extends HTMLElement>(fallback = 720): [RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.round(entry.contentRect.width));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

export const PROCESS_BODY = "Processing reads this lap from the recorded journal. It takes a few seconds and does not change the recording.";

/** The "process this lap" action for a deferred v2 lap, with its busy and error states. */
export function ProcessAction({ lap }: { lap: AnalysisLap }) {
  const { processing, processLap } = useAnalysis();
  const busy = processing.busy.includes(lap.id);
  const error = processing.errors[lap.id];
  return (
    <>
      <Button variant="primary" disabled={busy} aria-busy={busy || undefined} onClick={() => processLap(lap)}>
        {busy ? "Processing…" : error ? "Try again" : "Process telemetry"}
      </Button>
      {error && (
        <div className={styles.processError}>
          <InlineAlert tone={error.busy ? "warning" : "error"}>{error.message}</InlineAlert>
        </div>
      )}
    </>
  );
}

/** Why a lap has no telemetry and, when it can be processed on request, how. */
export function UnavailableLap({ lap, trace }: { lap: AnalysisLap; trace: Extract<LapTrace, { state: "unavailable" }> }) {
  const { canProcess } = useAnalysis();
  if (trace.processable && canProcess && lap.revisionId) {
    return (
      <EmptyState
        title={`Telemetry for Lap ${lap.lap} has not been processed`}
        body={PROCESS_BODY}
        action={<ProcessAction lap={lap} />}
      />
    );
  }
  return <EmptyState title={`No telemetry for Lap ${lap.lap}`} body={trace.reason} />;
}

/** Placeholder rows inside a chart frame while the server loads the traces. */
export function ChartLoading({ lap }: { lap: AnalysisLap | undefined }) {
  return (
    <div className={styles.chartLoading} role="status" aria-live="polite">
      <p>{lap ? `Loading telemetry for Lap ${lap.lap}…` : "Loading telemetry…"}</p>
      {[112, 82, 76, 92].map((height, index) => (
        <Bone key={index} height={height} />
      ))}
    </div>
  );
}
