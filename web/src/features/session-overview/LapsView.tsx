"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Bone } from "@/features/route-states/Skeleton";
import { formatDelta, formatLapTime, formatSectorTime } from "@/features/review-data/format";
import { OpenDocumentIcon, TyreIcon } from "@/features/ui-foundation/icons";
import { DataTable, type Column } from "@/features/ui-foundation/DataTable";
import { FilterChips } from "@/features/ui-foundation/FilterChips";
import { Badge, Card, Checkbox, DeltaPill, Select } from "@/features/ui-foundation/primitives";
import { EmptyState, InlineAlert, NotAvailable } from "@/features/ui-foundation/states";
import { StatTile } from "@/features/ui-foundation/StatTile";
import {
  analysisHref,
  compareDeltaToBest,
  compareLapNumber,
  compareLapTime,
  compareSector,
  filterLaps,
  lapCounts,
  METRIC_LABELS,
  metricOptions,
  reasonFootnote,
  sessionBestSectors,
  theoreticalBest,
  tyreLabel,
  type ChartMetric,
  type LapFilter,
} from "./overview-data";
import type { OverviewLap } from "./types";
import type { ViewData } from "./view-types";
import viewStyles from "./OverviewViews.module.css";

export function LapsView({
  data,
  compare,
  onSelect,
  onReference,
  onMetric,
}: {
  data: ViewData;
  /** Compare mode: rows offer "Set reference" and carry Target/Reference flags. */
  compare: boolean;
  onSelect: (id: string) => void;
  onReference: (id: string) => void;
  onMetric: (metric: ChartMetric) => void;
}) {
  const { model, laps, enrichment, best, selectedId, referenceId, metric } = data;
  const [filter, setFilter] = useState<LapFilter>("all");
  const [hideInOut, setHideInOut] = useState(false);
  const counts = useMemo(() => lapCounts(laps), [laps]);
  const rows = useMemo(() => filterLaps(laps, filter, hideInOut), [laps, filter, hideInOut]);
  const bestSectors = useMemo(() => sessionBestSectors(laps), [laps]);
  const theory = useMemo(() => theoreticalBest(laps, best?.ms ?? null), [best, laps]);
  const loading = enrichment.status === "loading";
  const hasSectors = loading || laps.some((lap) => lap.sectors);
  const hasTyres = loading || laps.some((lap) => lap.tyre);
  const options = metricOptions(laps, model.progression.illustrative);
  const activeMetric = options.includes(metric) ? metric : "lap";

  const columns = useMemo(() => {
    const cols: Column<OverviewLap>[] = [
      {
        key: "lap",
        header: "Lap",
        width: "64px",
        sort: compareLapNumber,
        render: (lap) => <>Lap {lap.lap}</>,
      },
      {
        key: "time",
        header: "Time",
        width: "96px",
        numeric: true,
        sort: compareLapTime,
        render: (lap) => formatLapTime(lap.ms),
      },
      {
        key: "delta",
        header: "Δ Best",
        width: "84px",
        numeric: true,
        sort: compareDeltaToBest(best?.ms ?? null),
        footnote: laps.some((lap) => !lap.valid) ? "— Invalid laps are not compared." : undefined,
        render: (lap) => {
          if (lap.id === best?.id) return <span className={viewStyles.bestText}>Best</span>;
          if (!lap.valid) return <NotAvailable compact reason="Invalid laps are not compared" />;
          if (!best) return <NotAvailable compact reason="No valid timed lap to compare with" />;
          return <DeltaPill ms={lap.ms - best.ms}>{formatDelta(lap.ms - best.ms)}</DeltaPill>;
        },
      },
    ];
    if (hasSectors) {
      ([0, 1, 2] as const).forEach((index) => {
        cols.push({
          key: `s${index + 1}`,
          header: `S${index + 1}`,
          width: "72px",
          numeric: true,
          sort: compareSector(index),
          footnote:
            index === 0
              ? reasonFootnote(laps, (lap) => lap.sectorsReason, (lap) => lap.sectors === null)
              : undefined,
          render: (lap) => {
            const value = lap.sectors?.[index];
            if (value === undefined) {
              return loading ? (
                <Bone width={44} height={12} />
              ) : (
                <NotAvailable compact reason={lap.sectorsReason ?? "Sector times were not reported"} />
              );
            }
            return (
              <span className={value === bestSectors[index] && lap.valid ? viewStyles.sectorBest : undefined}>
                {formatSectorTime(value)}
              </span>
            );
          },
        });
      });
    }
    if (hasTyres) {
      cols.push({
        key: "tyre",
        header: "Tyre",
        width: "minmax(110px, 1fr)",
        footnote: reasonFootnote(laps, (lap) => lap.tyreReason, (lap) => lap.tyre === null),
        render: (lap) =>
          lap.tyre ? (
            <span className={viewStyles.tyreCell} title={lap.tyre.actual ?? undefined}>
              <TyreIcon compound={lap.tyre.compound} size={13} />
              {tyreLabel(lap.tyre)}
            </span>
          ) : loading ? (
            <Bone width={80} height={12} />
          ) : (
            <NotAvailable compact reason={lap.tyreReason ?? "Tyre data was not recorded"} />
          ),
      });
    }
    cols.push(
      {
        key: "flags",
        header: "Flags",
        width: "minmax(90px, 1fr)",
        render: (lap) => (
          <span className={viewStyles.flags}>
            {compare && lap.id === selectedId && <Badge tone="blue">Target</Badge>}
            {compare && lap.id === referenceId && <Badge tone="purple">Reference</Badge>}
            {!lap.valid && <Badge tone="amber">Invalid</Badge>}
            {lap.pit && <Badge tone="grey">Pit</Badge>}
            {lap.startObserved === false && <Badge tone="grey">Start not observed</Badge>}
            {!lap.traceAvailable && <Badge tone="grey">Not processed</Badge>}
          </span>
        ),
      },
      {
        key: "actions",
        header: "Actions",
        width: compare ? "176px" : "76px",
        align: "end",
        render: (lap) => (
          <span className={viewStyles.rowActions}>
            {compare && (
              <button
                type="button"
                className={viewStyles.rowTextButton}
                disabled={lap.id === selectedId || lap.id === referenceId}
                onClick={() => onReference(lap.id)}
                aria-label={`Set lap ${lap.lap} as reference`}
              >
                Set reference
              </button>
            )}
            <Link
              href={analysisHref(model.sessionKey, lap.id, compare ? (referenceId ?? best?.id ?? null) : (best?.id ?? null))}
              className={viewStyles.rowAction}
              aria-label={`Analyse lap ${lap.lap}`}
              title={`Analyse lap ${lap.lap}`}
            >
              <OpenDocumentIcon size={14} />
            </Link>
          </span>
        ),
      },
    );
    return cols;
  }, [bestSectors, best, compare, hasSectors, hasTyres, laps, loading, model.sessionKey, onReference, referenceId, selectedId]);

  if (laps.length === 0) {
    return (
      <Card title="Laps (0)" labelledBy="laps-table">
        <EmptyState title="No laps were completed in this session." />
      </Card>
    );
  }

  return (
    <Card title={`Laps (${laps.length})`} labelledBy="laps-table">
      {enrichment.status === "unavailable" && !laps.some((lap) => lap.sectors || lap.tyre) && (
        <div className={viewStyles.alertGap}>
          <InlineAlert tone="info">Sector and tyre details are not available for this session.</InlineAlert>
        </div>
      )}
      {enrichment.status === "loaded" && enrichment.partialReason && (
        <div className={viewStyles.alertGap}>
          <InlineAlert tone="warning">Sector and tyre details are partial. {enrichment.partialReason}</InlineAlert>
        </div>
      )}
      <div className={viewStyles.paceStrip} role="group" aria-label="Pace summary">
        <StatTile label="Best lap" value={best ? formatLapTime(best.ms) : "—"} sub={best ? `Lap ${best.lap}` : "No valid lap"} />
        <StatTile
          label="Theoretical best"
          value={theory ? formatLapTime(theory.ms) : "—"}
          sub={
            theory
              ? theory.gainMs !== null && theory.gainMs >= 1
                ? `${(theory.gainMs / 1000).toFixed(3)} s from your best sectors`
                : "Your best lap holds every best sector"
              : loading
                ? "Waiting for sector times"
                : "Needs a valid time in all three sectors"
          }
        />
        {theory && (
          <StatTile
            label="Best sectors"
            value={theory.sectors.map((value) => formatSectorTime(value)).join(" · ")}
            sub="S1 · S2 · S3"
          />
        )}
      </div>
      <div className={viewStyles.tools}>
        <FilterChips
          label="Filter laps"
          value={filter}
          onChange={setFilter}
          items={[
            { key: "all", label: "All", count: counts.all },
            { key: "valid", label: "Valid", count: counts.valid },
            { key: "invalid", label: "Invalid", count: counts.invalid },
            { key: "pit", label: "Pit", count: counts.pit },
          ]}
        />
        <div className={viewStyles.toolsRight}>
          <span>Show:</span>
          <Select
            aria-label="Chart metric"
            value={activeMetric}
            onChange={(event) => onMetric(event.target.value as ChartMetric)}
            className={viewStyles.metricSelect}
          >
            {options.map((option) => (
              <option key={option} value={option}>
                {METRIC_LABELS[option]}
              </option>
            ))}
          </Select>
          <Checkbox label="Hide in/out laps" checked={hideInOut} onChange={setHideInOut} />
        </div>
      </div>
      <DataTable
        caption="Laps"
        columns={columns}
        rows={rows}
        rowKey={(lap) => lap.id}
        selectedKey={selectedId}
        onSelect={(lap) => onSelect(lap.id)}
        rowTone={(lap) => (lap.valid ? undefined : "muted")}
        defaultSort={{ key: "lap", dir: "asc" }}
        stickyHeader
        maxHeight={600}
        empty={<EmptyState size="inline" title="No laps match this filter." />}
      />
    </Card>
  );
}
