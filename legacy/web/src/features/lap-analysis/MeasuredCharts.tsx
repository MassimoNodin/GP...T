"use client";

import { useAnalysis } from "./analysis-context";
import { axisFor, ticksBetween, type DistanceRange } from "./lap-telemetry";
import { TelemetryCharts, type RowKey } from "./TelemetryCharts";
import { ChartLoading, ProcessAction, UnavailableLap } from "./views/parts";
import { InlineAlert } from "@/features/ui-foundation/states";
import styles from "./LapAnalysis.module.css";

/**
 * Chart card body for real sessions: measured traces for the target (and the
 * reference when it has them), or why there are none and how to get them.
 */
export function MeasuredCharts({
  rows,
  zoom,
  band = null,
  interactive = false,
  hover = false,
  rowHeights,
}: {
  /** Rows to draw, top to bottom; delta and gear are dropped when there is no data for them. */
  rows: ReadonlyArray<RowKey>;
  /** Zoom window; defaults to the full lap. */
  zoom?: DistanceRange | null;
  /** Shaded region of the zoom. */
  band?: DistanceRange | null;
  interactive?: boolean;
  /** Hover moves the shared cursor (and the track map dot) without brushing a range. */
  hover?: boolean;
  rowHeights?: Partial<Record<RowKey, number>>;
}) {
  const { target, reference, targetTrace, referenceTrace, compare, delta, cursor, onCursor, setBrush } = useAnalysis();
  if (!target) return <p className={styles.chartEmpty}>No lap is selected.</p>;
  // The server sends traces for the laps in the URL; the pickers can move ahead of it.
  if (!targetTrace || (reference && !referenceTrace)) return <ChartLoading lap={target} />;
  if (targetTrace.state === "unavailable") return <UnavailableLap lap={target} trace={targetTrace} />;

  const referenceReady = referenceTrace?.state === "available" ? referenceTrace : null;
  const full = axisFor([targetTrace, referenceReady ?? undefined]);
  if (!full) return <p className={styles.chartEmpty}>Telemetry traces are not available for this comparison.</p>;
  const shown = zoom ?? full;
  const hasGear = !!targetTrace.channels.gear || !!referenceReady?.channels.gear;
  const wantsDelta = rows.includes("delta") && compare === "delta" && !!reference;
  const channels = rows.filter((row) => (row === "gear" ? hasGear : row === "delta" ? wantsDelta : true));

  return (
    <>
      <TelemetryCharts
        from={shown.from}
        to={shown.to}
        ticks={ticksBetween(shown.from, shown.to)}
        band={band}
        channels={channels}
        measured={{ target: targetTrace.channels, reference: referenceReady?.channels ?? null }}
        delta={wantsDelta ? (referenceReady ? delta : { unavailable: "Delta needs complete speed traces for both laps." }) : null}
        rowHeights={rowHeights}
        cursor={interactive || hover ? cursor : null}
        onCursor={interactive || hover ? onCursor : undefined}
        onBrush={interactive ? setBrush : undefined}
      />
      {reference && referenceTrace?.state === "unavailable" && (
        <div className={styles.chartNote}>
          <InlineAlert tone="info" actions={referenceTrace.processable && reference.revisionId ? <ProcessAction lap={reference} /> : undefined}>
            Lap {reference.lap} has no telemetry, so only Lap {target.lap} is drawn. {referenceTrace.reason}
          </InlineAlert>
        </div>
      )}
      {!reference && <p className={styles.chartNote}>No reference lap is selected, so only the target lap is drawn.</p>}
    </>
  );
}
