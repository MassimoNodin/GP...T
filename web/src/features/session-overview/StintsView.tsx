"use client";

import { useMemo } from "react";
import { Bone } from "@/features/route-states/Skeleton";
import { formatAxisLapTime, formatLapTime } from "@/features/review-data/format";
import { TyreIcon } from "@/features/ui-foundation/icons";
import { DataTable, type Column } from "@/features/ui-foundation/DataTable";
import { Card } from "@/features/ui-foundation/primitives";
import { EmptyState, InlineAlert, NotAvailable } from "@/features/ui-foundation/states";
import {
  compoundColor,
  degradationTone,
  formatDegradation,
  formatLapRanges,
  isCleanLap,
  lapsInStint,
  lapsWithoutStint,
  niceScale,
  pitMarkers,
  stintStats,
  totalLapsOf,
} from "./overview-data";
import { StintBar } from "./shared";
import type { OverviewLap, OverviewStint } from "./types";
import type { ViewData } from "./view-types";
import styles from "./SessionOverview.module.css";
import viewStyles from "./OverviewViews.module.css";

type StintRow = { stint: OverviewStint; stats: ReturnType<typeof stintStats> };

export function StintsView({ data }: { data: ViewData }) {
  const { enrichment, laps } = data;

  if (enrichment.status === "loading") {
    return (
      <div className={`${viewStyles.stack}`}>
        <Card title="Stint Structure" labelledBy="stint-structure" className={styles.stintCard}>
          <div aria-hidden="true">
            <Bone width="60%" height={12} style={{ marginBottom: 22 }} />
            <Bone height={20} />
          </div>
        </Card>
        <div className={viewStyles.split}>
          <Card title="Stints" labelledBy="stints-table">
            <DataTable
              caption="Stints"
              columns={[{ key: "stint", header: "#", render: () => null }, { key: "compound", header: "Compound", render: () => null }]}
              rows={[]}
              rowKey={() => ""}
              loading
              loadingRows={3}
            />
          </Card>
          <Card title="Stint Pace" labelledBy="stint-pace">
            <Bone height={200} />
          </Card>
        </div>
      </div>
    );
  }

  if (enrichment.status === "unavailable" || enrichment.stints.length === 0) {
    return (
      <div className={viewStyles.stack}>
        <Card title="Stints" labelledBy="stints-table">
          <EmptyState
            title="Tyre data was not recorded for this session."
            body={
              enrichment.status === "unavailable" && data.model.source === "v2"
                ? enrichment.reason
                : "Stints come from the game's car status and session history packets."
            }
          />
        </Card>
      </div>
    );
  }

  const { stints, partialReason } = enrichment;
  const missing = lapsWithoutStint(laps, stints);
  const pits = pitMarkers(stints, laps);
  return (
    <div className={viewStyles.stack}>
      {(partialReason || missing.length > 0) && (
        <InlineAlert tone="warning">
          {partialReason
            ? "Only part of this session could be read, so later stints may be missing."
            : `Tyre data is missing for laps ${formatLapRanges(missing)}.`}
        </InlineAlert>
      )}
      <Card title="Stint Structure" labelledBy="stint-structure" className={styles.stintCard}>
        <StintBar stints={stints} total={totalLapsOf(laps, stints)} pits={pits} />
      </Card>
      <div className={viewStyles.split}>
        <StintTable stints={stints} laps={laps} />
        <Card title="Stint Pace" labelledBy="stint-pace">
          <StintPaceChart stints={stints} laps={laps} />
        </Card>
      </div>
    </div>
  );
}

function StintTable({ stints, laps }: { stints: OverviewStint[]; laps: OverviewLap[] }) {
  const rows = useMemo<StintRow[]>(() => stints.map((stint) => ({ stint, stats: stintStats(stint, laps) })), [stints, laps]);
  const columns: Column<StintRow>[] = [
    { key: "index", header: "#", width: "36px", render: (row) => <span className={viewStyles.stintIndex}>{row.stint.index}</span> },
    {
      key: "compound",
      header: "Compound",
      width: "minmax(100px, 1fr)",
      render: ({ stint }) => (
        <span className={viewStyles.tyreCell} title={stint.actual ?? undefined}>
          <TyreIcon compound={stint.compound} size={13} />
          {stint.label}
        </span>
      ),
    },
    {
      key: "laps",
      header: "Laps",
      width: "110px",
      render: ({ stint }) => `${stint.startLap}–${stint.endLap} (${stint.lapCount})`,
    },
    {
      key: "age",
      header: "Tyre age",
      width: "84px",
      render: ({ stint }) =>
        stint.startAge === null || stint.endAge === null ? (
          <NotAvailable compact reason="Tyre age was not reported" />
        ) : (
          `${stint.startAge} to ${stint.endAge}`
        ),
    },
    {
      key: "best",
      header: "Best",
      width: "84px",
      numeric: true,
      render: ({ stats }) =>
        stats.best === null ? <NotAvailable compact reason="No clean laps in this stint" /> : formatLapTime(stats.best),
    },
    {
      key: "average",
      header: "Average",
      width: "84px",
      numeric: true,
      render: ({ stats }) =>
        stats.average === null ? <NotAvailable compact reason="No clean laps in this stint" /> : formatLapTime(Math.round(stats.average)),
    },
    {
      key: "degradation",
      header: "Degradation",
      width: "110px",
      numeric: true,
      footnote: "— Degradation needs at least four clean laps in a stint.",
      render: ({ stats }) =>
        stats.degradation === null ? (
          <NotAvailable compact reason="Needs at least four clean laps" />
        ) : (
          <span className={viewStyles.degradation} data-tone={degradationTone(stats.degradation)}>
            {formatDegradation(stats.degradation)}
          </span>
        ),
    },
  ];
  return (
    <Card title="Stints" labelledBy="stints-table">
      <DataTable caption="Stints" columns={columns} rows={rows} rowKey={(row) => String(row.stint.index)} />
    </Card>
  );
}

const W = 470;
const H = 232;
const PAD = { left: 38, right: 10, top: 8, bottom: 30 };

/** Lap time against lap in stint, one line per stint in its compound colour (clean laps only). */
function StintPaceChart({ stints, laps }: { stints: OverviewStint[]; laps: OverviewLap[] }) {
  const series = stints.map((stint) => ({
    stint,
    points: lapsInStint(stint, laps)
      .filter(isCleanLap)
      .map((lap) => ({ x: lap.lap - stint.startLap + 1, ms: lap.ms })),
  }));
  const all = series.flatMap((entry) => entry.points);
  if (all.length === 0) {
    return <EmptyState size="inline" title="There are no clean laps to chart." />;
  }
  const sorted = all.map((point) => point.ms).sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  const plotted = all.filter((point) => point.ms <= median + 6000);
  const { yMin, yMax, yStep } = niceScale(Math.min(...plotted.map((p) => p.ms)), Math.max(...plotted.map((p) => p.ms)));
  const xMax = Math.max(5, ...series.map((entry) => entry.stint.lapCount));
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const x = (lap: number) => PAD.left + ((lap - 1) / Math.max(xMax - 1, 1)) * plotW;
  const y = (ms: number) => PAD.top + (1 - (Math.min(Math.max(ms, yMin), yMax) - yMin) / (yMax - yMin)) * plotH;
  const yTicks: number[] = [];
  for (let value = yMin; value <= yMax + 1; value += yStep) yTicks.push(value);
  const xStep = xMax > 40 ? 10 : 5;
  const xTicks = [1, ...Array.from({ length: Math.floor(xMax / xStep) }, (_, i) => (i + 1) * xStep)].filter((tick) => tick <= xMax);
  return (
    <div className={styles.chartWrap}>
      <svg viewBox={`0 0 ${W} ${H}`} className={styles.chart} role="img" aria-label="Lap times by stint">
        {yTicks.map((tick) => (
          <g key={tick}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(tick)} y2={y(tick)} stroke="var(--chart-grid)" />
            <text x={PAD.left - 8} y={y(tick) + 3.5} textAnchor="end" className={styles.axis}>
              {formatAxisLapTime(tick)}
            </text>
          </g>
        ))}
        {xTicks.map((tick) => (
          <text key={tick} x={x(tick)} y={H - 10} textAnchor="middle" className={styles.axis}>
            {tick}
          </text>
        ))}
        {series.map(({ stint, points }) => {
          const drawn = points.filter((point) => point.ms <= median + 6000);
          return (
            <g key={stint.index}>
              <polyline
                fill="none"
                stroke={compoundColor(stint.compound)}
                strokeWidth="1.6"
                strokeLinejoin="round"
                points={drawn.map((point) => `${x(point.x)},${y(point.ms)}`).join(" ")}
              />
              {drawn.map((point) => (
                <circle key={point.x} cx={x(point.x)} cy={y(point.ms)} r="2.9" fill={compoundColor(stint.compound)} />
              ))}
            </g>
          );
        })}
      </svg>
      <div className={viewStyles.paceLegend}>
        {stints.map((stint) => (
          <span key={stint.index} className={styles.key}>
            <i style={{ background: compoundColor(stint.compound), borderRadius: "50%" }} />
            Stint {stint.index}: {stint.label}
          </span>
        ))}
      </div>
    </div>
  );
}
