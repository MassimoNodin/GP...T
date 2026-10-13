"use client";

import type { ReactNode } from "react";
import { useMemo } from "react";
import { Bone } from "@/features/route-states/Skeleton";
import { StatusGlyph } from "@/features/ui-foundation/icons";
import { Card, Select } from "@/features/ui-foundation/primitives";
import { EmptyState, InlineAlert } from "@/features/ui-foundation/states";
import { LapTimeChart } from "./LapTimeChart";
import {
  buildSeries,
  deriveSummary,
  lapsWithoutStint,
  formatLapRanges,
  METRIC_LABELS,
  metricOptions,
  scanStoppedText,
  scopeEvents,
  totalLapsOf,
  type ChartMetric,
} from "./overview-data";
import { BoneLines, EventGlyph, StintBar, Unavailable } from "./shared";
import type { ViewData } from "./view-types";
import styles from "./SessionOverview.module.css";
import viewStyles from "./OverviewViews.module.css";

const COMPACT_EVENTS = 8;

export function OverviewView({
  data,
  onMetric,
  onShowEvents,
}: {
  data: ViewData;
  onMetric: (metric: ChartMetric) => void;
  onShowEvents: () => void;
}) {
  return (
    <div className={styles.grid}>
      <div className={styles.col}>
        <ProgressionCard data={data} onMetric={onMetric} />
        <StintCard data={data} />
      </div>
      <div className={styles.col}>
        <SummaryCard data={data} />
        <EventsCard data={data} onShowAll={onShowEvents} />
      </div>
    </div>
  );
}

function ProgressionCard({ data, onMetric }: { data: ViewData; onMetric: (metric: ChartMetric) => void }) {
  const { model, laps, best, selectedId, referenceId, metric } = data;
  const selected = laps.find((lap) => lap.id === selectedId) ?? null;
  const reference = referenceId ? (laps.find((lap) => lap.id === referenceId) ?? null) : null;
  const illustrative = model.progression.illustrative;
  const options = metricOptions(laps, illustrative);
  const activeMetric = options.includes(metric) ? metric : "lap";
  const series = useMemo(
    () => (illustrative ? null : buildSeries(laps, activeMetric, selectedId, model.bestId)),
    [activeMetric, illustrative, laps, model.bestId, selectedId],
  );
  const chart =
    series ??
    ({
      ...model.progression,
      values: Object.fromEntries(model.progression.points.map((point) => [point.lap, point.ms])),
    } as NonNullable<typeof series>);
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
        {reference && (
          <span className={styles.key}>
            <i className={viewStyles.keyRing} />
            Reference Lap
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
        />
      ) : (
        <EmptyState size="inline" title="No timed laps were recorded for this session." />
      )}
      {model.lapDataNote && <p className={styles.dataNote}>{model.lapDataNote}</p>}
    </Card>
  );
}

function StintCard({ data }: { data: ViewData }) {
  const { enrichment, laps, model } = data;
  const title = (children: ReactNode) => (
    <Card title="Stint Structure" labelledBy="stint-structure" className={styles.stintCard}>
      {children}
    </Card>
  );
  if (enrichment.status === "loading") {
    return title(
      <div aria-hidden="true">
        <Bone width="60%" height={12} style={{ marginBottom: 22 }} />
        <Bone height={20} />
      </div>,
    );
  }
  if (enrichment.status === "unavailable") {
    return title(
      <Unavailable>
        {model.source === "v1" ? enrichment.reason : "Tyre stint data is not available for this session."}
      </Unavailable>,
    );
  }
  if (enrichment.stints.length === 0) {
    return title(<EmptyState size="inline" title="Tyre data was not recorded for this session." />);
  }
  const missing = lapsWithoutStint(laps, enrichment.stints);
  return title(
    <>
      {missing.length > 0 && (
        <div className={viewStyles.alertGap}>
          <InlineAlert tone="warning">Tyre data is missing for laps {formatLapRanges(missing)}.</InlineAlert>
        </div>
      )}
      <StintBar stints={enrichment.stints} total={totalLapsOf(laps, enrichment.stints)} />
    </>,
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
