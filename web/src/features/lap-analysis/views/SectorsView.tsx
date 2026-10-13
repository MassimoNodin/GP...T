"use client";

import { useMemo, useState, type ReactNode } from "react";
import { Bone } from "@/features/route-states/Skeleton";
import { formatDelta, formatLapTime, formatSectorTime } from "@/features/review-data/format";
import { ChevronDownIcon, ChevronRightIcon, ExpandIcon } from "@/features/ui-foundation/icons";
import { DataTable, type Column } from "@/features/ui-foundation/DataTable";
import { Badge, Button, Card, DeltaPill, IconButton } from "@/features/ui-foundation/primitives";
import { EmptyState, InlineAlert, NotAvailable } from "@/features/ui-foundation/states";
import { useAnalysis } from "../analysis-context";
import { SECTOR_MISSING } from "../SideColumn";
import type { AnalysisLap } from "../load";
import {
  cornerMetrics,
  distanceDifferenceLabel,
  formatRange,
  type CornerMetrics,
  type RegionChoice,
} from "../regions";
import styles from "../Views.module.css";

/* ------------------------------------------------------------ sectors */

type SectorRow = {
  key: string;
  label: string;
  target: number | null;
  reference: number | null;
  best: { ms: number; lap: number } | null;
  isLap: boolean;
};

/** Best value of one sector (or the lap) over the laps the game accepted. */
export function sessionBest(laps: readonly AnalysisLap[], pick: (lap: AnalysisLap) => number | null): { ms: number; lap: number } | null {
  let best: { ms: number; lap: number } | null = null;
  for (const lap of laps) {
    if (lap.valid === false) continue;
    const value = pick(lap);
    if (value !== null && (best === null || value < best.ms)) best = { ms: value, lap: lap.lap };
  }
  return best;
}

function signed(ms: number | null) {
  return ms === null ? null : <DeltaPill ms={ms}>{formatDelta(ms)}</DeltaPill>;
}

function SectorsCard() {
  const { laps, target, reference, enrichment, measured } = useAnalysis();
  const loading = enrichment.state === "deferred";
  const rows = useMemo<SectorRow[]>(() => {
    if (!target) return [];
    const sector = (index: number): SectorRow => ({
      key: `s${index + 1}`,
      label: `S${index + 1}`,
      target: target.sectors?.[index] ?? null,
      reference: reference?.sectors?.[index] ?? null,
      best: sessionBest(laps, (lap) => lap.sectors?.[index] ?? null),
      isLap: false,
    });
    return [
      sector(0),
      sector(1),
      sector(2),
      { key: "lap", label: "Lap", target: target.ms, reference: reference?.ms ?? null, best: sessionBest(laps, (lap) => lap.ms), isLap: true },
    ];
  }, [laps, reference, target]);

  const format = (row: SectorRow, ms: number | null, reason: string): ReactNode =>
    loading && !row.isLap ? <Bone width={48} height={12} /> : ms === null ? <NotAvailable reason={reason} compact /> : row.isLap ? formatLapTime(ms) : formatSectorTime(ms);
  const columns: Column<SectorRow>[] = [
    { key: "label", header: "Sector", width: "64px", render: (row) => <strong>{row.label}</strong> },
    {
      key: "target",
      header: `Lap ${target?.lap ?? ""}`,
      numeric: true,
      render: (row) => (
        <span className={row.best && row.target === row.best.ms ? styles.bestValue : undefined}>{format(row, row.target, target?.sectorsReason ?? SECTOR_MISSING)}</span>
      ),
      footnote: !loading && target && !target.sectors && measured ? `— ${target.sectorsReason ?? SECTOR_MISSING}` : undefined,
    },
    {
      key: "reference",
      header: reference ? `Lap ${reference.lap}` : "Reference",
      numeric: true,
      render: (row) => format(row, row.reference, reference ? (reference.sectorsReason ?? SECTOR_MISSING) : "Select a reference lap."),
    },
    {
      key: "delta",
      header: "Δ",
      numeric: true,
      render: (row) =>
        row.target !== null && row.reference !== null ? signed(row.target - row.reference) : <NotAvailable reason="Needs both laps." compact />,
    },
    {
      key: "best",
      header: "Session best",
      numeric: true,
      render: (row) =>
        row.best ? (
          <span className={styles.bestValue}>
            {row.isLap ? formatLapTime(row.best.ms) : formatSectorTime(row.best.ms)} <span className={styles.muted}>(L{row.best.lap})</span>
          </span>
        ) : (
          <NotAvailable reason="No lap has this sector." compact />
        ),
    },
    {
      key: "toBest",
      header: "Δ to best",
      numeric: true,
      render: (row) => (row.best && row.target !== null ? signed(row.target - row.best.ms) : <NotAvailable reason="Needs a session best." compact />),
    },
  ];
  return (
    <Card title="Sectors" labelledBy="sectors-table" className={styles.tableCard}>
      {enrichment.state === "unavailable" && (
        <InlineAlert tone="info">Sector and tyre details are not available for this session. {enrichment.reason}</InlineAlert>
      )}
      <DataTable
        caption="Sector times of the target and reference laps"
        columns={columns}
        rows={rows}
        rowKey={(row) => row.key}
        density="compact"
        empty={<EmptyState size="inline" title="No lap is selected." />}
      />
    </Card>
  );
}

/* ------------------------------------------------------------ corners */

type CornerRow = { choice: RegionChoice; metrics: CornerMetrics; child: boolean };

function pair(values: { target: number | null; reference: number | null }, digits = 0, unit = "") {
  const f = (value: number | null) => (value === null ? "—" : value.toFixed(digits));
  return `${f(values.target)} / ${f(values.reference)}${unit}`;
}

function CornersCard() {
  const { regions, regionModel, choices, targetTrace, referenceTrace, delta, reference, target, selectedChoice, setSelection, setView, setBrush } =
    useAnalysis();
  const [expanded, setExpanded] = useState<string[]>([]);

  const rows = useMemo<CornerRow[]>(() => {
    const complexMembers = new Set(choices.filter((choice) => choice.complex).flatMap((choice) => choice.members.map((member) => member.id)));
    const top = choices.filter((choice) => choice.complex || !complexMembers.has(choice.members[0].id));
    top.sort((a, b) => a.range.from - b.range.from);
    const metrics = (choice: RegionChoice) =>
      cornerMetrics(choice, targetTrace?.state === "available" ? targetTrace : undefined, referenceTrace?.state === "available" ? referenceTrace : undefined, delta);
    const list: CornerRow[] = [];
    for (const choice of top) {
      list.push({ choice, metrics: metrics(choice), child: false });
      if (choice.complex && expanded.includes(choice.id)) {
        for (const member of choice.members) {
          const single = choices.find((item) => item.id === `r:${member.id}`);
          if (single) list.push({ choice: single, metrics: metrics(single), child: true });
        }
      }
    }
    return list;
  }, [choices, delta, expanded, referenceTrace, targetTrace]);

  const tLabel = target ? target.lap : "T";
  const rLabel = reference ? reference.lap : "R";
  const columns: Column<CornerRow>[] = [
    {
      key: "corner",
      header: "Corner",
      width: "120px",
      render: (row) => <span className={row.child ? styles.childRow : undefined}>{row.choice.label}</span>,
    },
    { key: "range", header: "Range", width: "130px", render: (row) => formatRange(row.choice.range) },
    {
      key: "min",
      header: `Min speed ${tLabel}/${rLabel}`,
      numeric: true,
      render: (row) => `${pair(row.metrics.minKph)} km/h`,
    },
    {
      key: "minDelta",
      header: "Δ",
      numeric: true,
      render: (row) => {
        const { target: t, reference: r } = row.metrics.minKph;
        return t === null || r === null ? <NotAvailable reason="Needs both laps." compact /> : `${t - r > 0 ? "+" : ""}${Math.round(t - r)}`;
      },
    },
    {
      key: "time",
      header: `Time in corner ${tLabel}/${rLabel}`,
      numeric: true,
      render: (row) => (row.metrics.timeS.target === null ? <NotAvailable reason="Needs complete speed traces." compact /> : `${pair(row.metrics.timeS, 2)} s`),
    },
    {
      key: "timeDelta",
      header: "Δ time",
      numeric: true,
      render: (row) => {
        const value = row.metrics.deltaTimeS;
        return value === null ? <NotAvailable reason="Needs complete speed traces for both laps." compact /> : signed(value * 1000);
      },
    },
    {
      key: "brake",
      header: "Brake point",
      render: (row) =>
        distanceDifferenceLabel(row.metrics.brakePointM.target, row.metrics.brakePointM.reference) ?? <NotAvailable reason="No braking found in this region for both laps." compact />,
    },
    {
      key: "throttle",
      header: "Throttle 90%",
      render: (row) =>
        distanceDifferenceLabel(row.metrics.throttlePickupM.target, row.metrics.throttlePickupM.reference) ?? <NotAvailable reason="No throttle pickup found for both laps." compact />,
    },
    {
      key: "actions",
      header: "Actions",
      width: "88px",
      align: "end",
      render: (row) => (
        <span className={styles.rowActions}>
          {row.choice.complex && (
            <IconButton
              label={`${expanded.includes(row.choice.id) ? "Hide" : "Show"} corners in ${row.choice.label}`}
              aria-expanded={expanded.includes(row.choice.id)}
              onClick={() => setExpanded((current) => (current.includes(row.choice.id) ? current.filter((id) => id !== row.choice.id) : [...current, row.choice.id]))}
            >
              {expanded.includes(row.choice.id) ? <ChevronDownIcon size={13} /> : <ChevronRightIcon size={13} />}
            </IconButton>
          )}
          <IconButton
            label={`Show ${row.choice.label} in telemetry`}
            onClick={() => {
              setSelection(row.choice.id);
              setView("telemetry");
            }}
          >
            <ExpandIcon size={13} />
          </IconButton>
        </span>
      ),
    },
  ];

  if (regions.state === "deferred") {
    return (
      <Card title="Corners" labelledBy="corners-table" className={styles.tableCard}>
        <div aria-busy="true">
          <Bone height={14} />
          <Bone height={14} style={{ marginTop: 10 }} />
        </div>
      </Card>
    );
  }
  if (regions.state !== "available" || !regionModel) {
    return (
      <Card title="Corners" labelledBy="corners-table" className={styles.tableCard}>
        <EmptyState
          title="No corner regions for this circuit"
          body={regions.state === "none" ? regions.reason : undefined}
          action={
            <Button
              onClick={() => {
                setBrush(null);
                setView("telemetry");
              }}
            >
              Measure a distance range
            </Button>
          }
        />
      </Card>
    );
  }
  return (
    <Card
      title={
        <span className={styles.cornersTitle}>
          Corners ({regionModel.trackName} r{regionModel.revision} · {regionModel.validation === "draft" ? "Draft" : "Validated"})
          {regionModel.validation === "draft" && <Badge tone="amber">Draft</Badge>}
        </span>
      }
      labelledBy="corners-table"
      className={styles.tableCard}
    >
      {regionModel.validation === "draft" && (
        <InlineAlert tone="info">Draft regions are approximate and have not been reviewed. Use them as distance ranges, not corner names.</InlineAlert>
      )}
      {!reference && <InlineAlert tone="info">Select a reference lap to compare corners.</InlineAlert>}
      <DataTable
        caption="Corner metrics for the target and reference laps"
        columns={columns}
        rows={rows}
        rowKey={(row) => row.choice.id}
        selectedKey={selectedChoice?.id ?? null}
        onSelect={(row) => setSelection(row.choice.id)}
        density="compact"
        empty={<EmptyState size="inline" title="This track model has no regions." />}
      />
    </Card>
  );
}

export function SectorsView() {
  return (
    <div className={styles.stack}>
      <SectorsCard />
      <CornersCard />
    </div>
  );
}
