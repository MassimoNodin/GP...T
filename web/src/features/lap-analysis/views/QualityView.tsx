"use client";

import { formatLapTime } from "@/features/review-data/format";
import { StatusGlyph } from "@/features/ui-foundation/icons";
import { Card } from "@/features/ui-foundation/primitives";
import { ProgressBar } from "@/features/ui-foundation/ProgressBar";
import { NotAvailable } from "@/features/ui-foundation/states";
import { useAnalysis } from "../analysis-context";
import type { AnalysisLap } from "../load";
import { ALL_CHANNEL_KEYS, type ChannelKey, type LapTrace } from "../lap-telemetry";
import { LegendDot } from "./parts";
import styles from "../Views.module.css";

const CHANNEL_NAME: Record<ChannelKey, string> = {
  speed: "Speed",
  throttle: "Throttle",
  brake: "Brake",
  steering: "Steering",
  gear: "Gear",
};

export type Coverage = { percent: number; gaps: number };

/** Share of the lap covered by one channel and how many holes it has; null when not recorded. */
export function channelCoverage(trace: Extract<LapTrace, { state: "available" }>, key: ChannelKey): Coverage | null {
  const coverage = trace.coverage[key];
  if (!coverage || !(trace.length > 0)) return null;
  return { percent: Math.min(100, (coverage.metres / trace.length) * 100), gaps: Math.max(0, coverage.runs - 1) };
}

function tone(percent: number): "green" | "amber" | "red" {
  return percent >= 98 ? "green" : percent >= 80 ? "amber" : "red";
}

function LapQuality({ lap, trace, role, source, trackLengthM }: { lap: AnalysisLap; trace: LapTrace | undefined; role: "target" | "reference"; source: string; trackLengthM: number | null }) {
  const available = trace?.state === "available" ? trace : null;
  const distance = available
    ? trackLengthM
      ? `Distance ${Math.round(available.length).toLocaleString("en-US")} / ${Math.round(trackLengthM).toLocaleString("en-US")} m (${Math.min(100, (available.length / trackLengthM) * 100).toFixed(1)}%)`
      : `Distance ${Math.round(available.length).toLocaleString("en-US")} m`
    : null;
  return (
    <Card
      title={
        <span className={styles.qualityTitle}>
          <LegendDot tone={role} />
          Lap {lap.lap} ({role})
        </span>
      }
      labelledBy={`quality-${role}`}
      className={styles.qualityCard}
    >
      <ul className={styles.facts}>
        <li>
          {lap.valid === false ? <StatusGlyph tone="amber" glyph="alert" size={13} /> : lap.valid === true ? <StatusGlyph tone="green" glyph="check" size={13} /> : <StatusGlyph tone="grey" glyph="ring" size={13} />}
          {lap.valid === false ? "Invalidated by the game" : lap.valid === true ? "Valid per the game" : "Validity not reported"}
        </li>
        <li>
          {lap.startObserved === false ? <StatusGlyph tone="amber" glyph="alert" size={13} /> : <StatusGlyph tone="green" glyph="check" size={13} />}
          {lap.startObserved === false ? "Start not observed" : "Start observed"}
        </li>
        {lap.pit && (
          <li>
            <StatusGlyph tone="amber" glyph="alert" size={13} />
            Includes pit lane
          </li>
        )}
      </ul>
      {available ? (
        <>
          <p className={styles.factLine}>
            Samples {available.samples.toLocaleString("en-US")}
            {distance ? ` · ${distance}` : ""}
          </p>
          <h3 className={styles.subHeading}>Channel coverage</h3>
          <ul className={styles.coverage}>
            {ALL_CHANNEL_KEYS.map((key) => {
              const coverage = channelCoverage(available, key);
              return (
                <li key={key}>
                  <span className={styles.coverageName}>{CHANNEL_NAME[key]}</span>
                  {coverage ? (
                    <>
                      <ProgressBar
                        value={coverage.percent}
                        size="sm"
                        tone={tone(coverage.percent)}
                        label={`${CHANNEL_NAME[key]} coverage ${Math.round(coverage.percent)}% for Lap ${lap.lap}`}
                      />
                      <span className={styles.coverageValue}>
                        {Math.round(coverage.percent)}%{coverage.gaps > 0 ? ` · ${coverage.gaps} gap${coverage.gaps === 1 ? "" : "s"}` : ""}
                      </span>
                    </>
                  ) : (
                    <span className={styles.muted}>not recorded</span>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      ) : trace?.state === "unavailable" ? (
        <p className={styles.factLine}>
          <NotAvailable reason={trace.reason} /> {trace.reason}
        </p>
      ) : (
        <p className={styles.factLine}>Loading telemetry…</p>
      )}
      <p className={styles.sourceLine}>
        Source: {source}
        {lap.ms ? ` · ${formatLapTime(lap.ms)}` : ""}
      </p>
    </Card>
  );
}

/** Per-lap coverage of the compared laps and what the session data does and does not include. */
export function QualityView() {
  const { model, target, reference, targetTrace, referenceTrace, enrichment, laps } = useAnalysis();
  const source = model.source === "v2" ? "Live session evidence (v2)" : model.source === "v1" ? "Imported capture (v1)" : "Reference data";
  const sectorsKnown = laps.some((lap) => lap.sectors);
  const sectorSource = laps.find((lap) => lap.sectorsSource)?.sectorsSource;
  const sectorText = sectorsKnown
    ? sectorSource === "session_history"
      ? "Session history"
      : sectorSource === "lap_data"
        ? "Lap data"
        : "Lap timing"
    : null;
  const tyreKnown = laps.some((lap) => lap.tyre);
  return (
    <div className={styles.stack}>
      <div className={styles.twoColumns}>
        {target && <LapQuality lap={target} trace={targetTrace} role="target" source={source} trackLengthM={model.trackLengthM} />}
        {reference && <LapQuality lap={reference} trace={referenceTrace} role="reference" source={source} trackLengthM={model.trackLengthM} />}
      </div>
      <Card title="Session data" labelledBy="quality-session" className={styles.qualityCard}>
        <dl className={styles.kv}>
          <div>
            <dt>Laps read</dt>
            <dd>
              {model.lapDataNote ? model.lapDataNote : enrichment.state === "loaded" && enrichment.truncated ? "Part of the session only" : "All laps"}
            </dd>
          </div>
          <div>
            <dt>Sector times</dt>
            <dd>{sectorText ?? (enrichment.state === "deferred" ? "Checking…" : <NotAvailable reason={enrichment.state === "unavailable" ? enrichment.reason : "Sector times were not in the recorded packets"} />)}</dd>
          </div>
          <div>
            <dt>Tyre data</dt>
            <dd>{tyreKnown ? "Car status" : enrichment.state === "deferred" ? "Checking…" : <NotAvailable reason={enrichment.state === "unavailable" ? enrichment.reason : "Tyre data was not in the recorded packets"} />}</dd>
          </div>
          <div>
            <dt>Engine</dt>
            <dd>Reachable</dd>
          </div>
        </dl>
      </Card>
    </div>
  );
}
