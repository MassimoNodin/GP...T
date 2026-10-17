"use client";

import type { ReactNode } from "react";
import { useMemo, useState } from "react";
import { StatusGlyph } from "@/features/ui-foundation/icons";
import { Card, Checkbox, Select } from "@/features/ui-foundation/primitives";
import { EmptyState } from "@/features/ui-foundation/states";
import { LapTimeChart } from "./LapTimeChart";
import { SessionQuestion } from "./SessionQuestion";
import {
  buildSeries,
  deriveSummary,
  METRIC_LABELS,
  metricOptions,
  scanStoppedText,
  lapMarkers,
  outlierLapIds,
  scopeEvents,
  compoundBands,
  compoundColor,
  COMPOUND_TINT,
  type ChartMetric,
} from "./overview-data";
import { BoneLines, EventGlyph, Unavailable } from "./shared";
import type { ViewData } from "./view-types";
import styles from "./SessionOverview.module.css";
import viewStyles from "./OverviewViews.module.css";

const COMPACT_EVENTS = 8;

export function OverviewView({
  data,
  onMetric,
  onShowEvents,
  onSelectLap,
  onCompareLap,
}: {
  data: ViewData;
  onMetric: (metric: ChartMetric) => void;
  onShowEvents: () => void;
  /** Click on a chart point. */
  onSelectLap?: (id: string) => void;
  /** Shift-click on a chart point: compare against that lap. */
  onCompareLap?: (id: string) => void;
}) {
  return (
    <div className={styles.grid}>
      <div className={styles.col}>
        <ProgressionCard data={data} onMetric={onMetric} onSelectLap={onSelectLap} onCompareLap={onCompareLap} />
      </div>
      <div className={styles.col}>
        <SummaryCard data={data} />
        <EventsCard data={data} onShowAll={onShowEvents} />
        {data.model.source !== "fixture" && <SessionQuestion data={data} />}
      </div>
    </div>
  );
}

function ProgressionCard({
  data,
  onMetric,
  onSelectLap,
  onCompareLap,
}: {
  data: ViewData;
  onMetric: (metric: ChartMetric) => void;
  onSelectLap?: (id: string) => void;
  onCompareLap?: (id: string) => void;
}) {
  const { model, laps, best, selectedId, referenceId, metric, summary, enrichment } = data;
  const [excludeOutliers, setExcludeOutliers] = useState(true);
  const summaryEvents = summary.status === "loaded" ? summary.value.events : null;
  const markers = useMemo(
    () => (model.progression.illustrative ? [] : lapMarkers(laps, summaryEvents)),
    [laps, model.progression.illustrative, summaryEvents],
  );
  const outliers = useMemo(() => outlierLapIds(laps, markers), [laps, markers]);
  const selected = laps.find((lap) => lap.id === selectedId) ?? null;
  const reference = referenceId ? (laps.find((lap) => lap.id === referenceId) ?? null) : null;
  const illustrative = model.progression.illustrative;
  const options = metricOptions(laps, illustrative);
  const activeMetric = options.includes(metric) ? metric : "lap";
  const series = useMemo(
    () =>
      illustrative
        ? null
        : buildSeries(laps, activeMetric, selectedId, model.bestId, excludeOutliers ? outliers : new Set<string>()),
    [activeMetric, excludeOutliers, illustrative, laps, model.bestId, outliers, selectedId],
  );
  const chart =
    series ??
    ({
      ...model.progression,
      values: Object.fromEntries(model.progression.points.map((point) => [point.lap, point.ms])),
    } as NonNullable<typeof series>);
  const tyres = useMemo(
    () =>
      enrichment.status === "loaded" && !illustrative && enrichment.stints.length > 0
        ? compoundBands(enrichment.stints, laps, chart.xMax)
        : null,
    [chart.xMax, enrichment, illustrative, laps],
  );
  const legendCompounds = tyres ? [...new Map(tyres.bands.map((band) => [band.compound, band])).values()] : [];
  return (
    <Card title="Lap Time Progression" labelledBy="lap-progression" className={styles.progressionCard}>
      <div className={styles.legend}>
        <span className={styles.showLabel}>Show:</span>
        <Select
          aria-label="Chart metric"
          value={activeMetric}
          onChange={(event) => onMetric(event.target.value as ChartMetric)}
          className={styles.metric}
        >
          {options.map((option) => (
            <option key={option} value={option}>
              {METRIC_LABELS[option]}
            </option>
          ))}
        </Select>
        <span className={styles.key}>
          <i style={{ background: "#22a36b" }} />
          Personal Best
        </span>
        <span className={styles.key}>
          <i style={{ background: "#9b34ef" }} />
          Session Best
        </span>
        <span className={styles.key}>
          <i style={{ background: "#2563eb" }} />
          Selected Lap
        </span>
        {legendCompounds.map((band) => (
          <span key={band.compound} className={styles.key}>
            <i className={styles.tintKey} style={{ background: compoundColor(band.compound), opacity: COMPOUND_TINT * 2.5 }} />
            {band.label}
          </span>
        ))}
        {tyres && tyres.stops.length > 0 && (
          <span className={styles.key}>
            <i className={styles.pitKey} />
            Pit stop
          </span>
        )}
        {reference && (
          <span className={styles.key}>
            <i className={viewStyles.keyRing} />
            Reference Lap
          </span>
        )}
        {!illustrative && outliers.size > 0 && (
          <span className={viewStyles.outlierToggle}>
            <Checkbox
              label={`Exclude outliers (${outliers.size})`}
              checked={excludeOutliers}
              onChange={setExcludeOutliers}
            />
          </span>
        )}
      </div>
      {selected ? (
        <LapTimeChart
          {...chart}
          selected={{ lap: selected.lap, ms: selected.ms }}
          bestMs={best?.ms ?? null}
          metric={activeMetric}
          selectedValue={chart.values[selected.lap]}
          reference={reference && chart.values[reference.lap] !== undefined ? { lap: reference.lap, value: chart.values[reference.lap] } : null}
          markers={markers}
          bands={tyres?.bands}
          stops={tyres?.stops}
          onPick={
            onSelectLap
              ? (lapNumber, compareWith) => {
                  const picked = laps.find((lap) => lap.lap === lapNumber);
                  if (!picked) return;
                  if (compareWith && onCompareLap) onCompareLap(picked.id);
                  else onSelectLap(picked.id);
                }
              : undefined
          }
        />
      ) : (
        <EmptyState size="inline" title="No timed laps were recorded for this session." />
      )}
      {selected && onSelectLap && (
        <p className={viewStyles.chartHint}>Click a lap to select it. Shift-click to compare against it.</p>
      )}
      {model.lapDataNote && <p className={styles.dataNote}>{model.lapDataNote}</p>}
    </Card>
  );
}

function SummaryCard({ data }: { data: ViewData }) {
  const { model, laps, enrichment, summary } = data;
  const card = (children: ReactNode) => (
    <Card title="Session Summary" labelledBy="session-summary" className={styles.summaryCard}>
      {children}
    </Card>
  );
  const reference = model.source === "fixture" ? model.summary : null;
  if (!reference && summary.status === "loading") return card(<BoneLines rows={3} />);
  const derived =
    reference ??
    deriveSummary({
      laps,
      bestId: model.bestId,
      stintCount: enrichment.status === "loaded" ? enrichment.stints.length : null,
      summary: summary.status === "loaded" ? summary.value : null,
      limitations: [
        ...(model.lapDataNote ? [model.lapDataNote] : []),
        ...(enrichment.status === "loaded" && enrichment.partialReason ? [enrichment.partialReason] : []),
        ...(summary.status === "loaded" && !summary.value.scan.complete ? [scanStoppedText(summary.value.scan.stopReason)] : []),
      ],
    });
  if (!derived) {
    return card(
      laps.length === 0 ? (
        <EmptyState size="inline" title="No timed laps were recorded, so there is nothing to summarise yet." />
      ) : (
        <Unavailable>A summary is not available for this session.</Unavailable>
      ),
    );
  }
  return card(
    <>
      <p className={styles.summaryText}>{derived.text}</p>
      {derived.items.length > 0 && (
        <ul className={styles.summaryList}>
          {derived.items.map((item) => (
            <li key={item.text}>
              <StatusGlyph tone={item.tone} glyph={item.glyph ?? (item.tone === "green" ? "check" : "alert")} size={15} />
              {item.text}
            </li>
          ))}
        </ul>
      )}
    </>,
  );
}

function EventsCard({ data, onShowAll }: { data: ViewData; onShowAll: () => void }) {
  const { model, summary } = data;
  const events = scopeEvents(data.events, false);
  const loading = model.source === "v2" && summary.status === "loading";
  const shown = events.slice(0, COMPACT_EVENTS);
  return (
    <Card title={`Notable Events (${loading ? 0 : events.length})`} labelledBy="notable-events" className={styles.eventsCard}>
      {loading ? (
        <BoneLines rows={4} />
      ) : events.length === 0 ? (
        <EmptyState size="inline" title="No notable events were found in this session." />
      ) : (
        <>
          <ul className={styles.events} data-compact="true">
            {shown.map((event) => (
              <li key={event.id}>
                <EventGlyph icon={event.icon} />
                <span className={styles.eventLap}>{event.lap === null ? "" : `Lap ${event.lap}`}</span>
                <span className={styles.eventText} data-highlight={event.highlight ? "true" : undefined}>
                  {event.label}
                </span>
              </li>
            ))}
          </ul>
          {events.length > shown.length && (
            <p className={viewStyles.note}>
              Showing the first {shown.length} of {events.length} events.{" "}
              <button type="button" className={viewStyles.textButton} onClick={onShowAll}>
                View all events
              </button>
            </p>
          )}
        </>
      )}
    </Card>
  );
}
