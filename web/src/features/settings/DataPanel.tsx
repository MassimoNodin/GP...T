"use client";

import { useEffect, useState } from "react";
import { humanReason } from "@/features/review-data/reasons";
import { DataTable, type Column } from "@/features/ui-foundation/DataTable";
import { DownloadIcon, RefreshIcon } from "@/features/ui-foundation/icons";
import { ProgressBar } from "@/features/ui-foundation/ProgressBar";
import { Badge, Button, Card } from "@/features/ui-foundation/primitives";
import { EmptyState, InlineAlert } from "@/features/ui-foundation/states";
import { useToast } from "@/features/ui-foundation/Toast";
import { readRecordingSourcePage } from "@/lib/recording-catalog";
import { parseStorageUsageResponse } from "@/lib/storage-usage.mjs";
import {
  CAPTURES_PAGE_SIZE,
  capturesModel,
  capturesSummary,
  type CaptureRow,
  type CapturesModel,
} from "./captures";
import { formatUtc } from "./display";
import { barWidth } from "./shared";
import {
  DONUT_CIRCUMFERENCE,
  DONUT_RADIUS,
  buildStorageView,
  donutLabel,
  donutSegments,
  scopeColor,
  type StorageView,
} from "./storage-view";
import styles from "./Settings.module.css";
import tabStyles from "./SettingsTabs.module.css";

/* ------------------------------------------------------------------ storage */

function Swatch({ color, hatched }: { color: string; hatched: boolean }) {
  return (
    <svg className={tabStyles.swatch} viewBox="0 0 10 10" aria-hidden="true" focusable="false">
      {hatched ? (
        <>
          <rect width="10" height="10" rx="2" fill="var(--grey-soft)" />
          <path d="M-1 8L8 -1M2 11L11 2" stroke="var(--faint)" strokeWidth="1.2" />
        </>
      ) : (
        <rect width="10" height="10" rx="2" fill={color} />
      )}
    </svg>
  );
}

function Donut({ view }: { view: StorageView }) {
  const segments = donutSegments(view.scopes);
  const value = view.used.replace(/ used$/, "");
  return (
    <svg className={tabStyles.donut} viewBox="0 0 120 120" role="img" aria-label={donutLabel(view)} focusable="false">
      <g transform="rotate(-90 60 60)">
        <circle className={tabStyles.donutTrack} cx="60" cy="60" r={DONUT_RADIUS} />
        {segments.map((segment) => (
          <circle
            key={segment.key}
            className={tabStyles.donutArc}
            cx="60"
            cy="60"
            r={DONUT_RADIUS}
            stroke={segment.color}
            strokeDasharray={`${segment.length} ${DONUT_CIRCUMFERENCE - segment.length}`}
            strokeDashoffset={-segment.start}
          />
        ))}
      </g>
      <text className={tabStyles.donutValue} x="60" y="62">
        {value}
      </text>
      <text className={tabStyles.donutSub} x="60" y="76">
        {view.complete ? "used" : "used (at least)"}
      </text>
    </svg>
  );
}

function StorageCard({ initial }: { initial: StorageView | null }) {
  const [view, setView] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function refresh() {
    setBusy(true);
    setFailed(false);
    try {
      const response = await fetch("/api/storage/usage", { cache: "no-store" });
      const parsed = response.ok ? parseStorageUsageResponse(await response.json()) : null;
      if (parsed) setView(buildStorageView(parsed));
      else setFailed(true);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Storage" labelledBy="storage-usage">
      <p className={styles.cardDesc}>Space used by recorded sessions and analysis data.</p>
      {view ? (
        <>
          <div className={tabStyles.donutRow}>
            <Donut view={view} />
            <div className={tabStyles.donutText}>
              <div className={styles.storageUsed}>{view.used}</div>
              <div className={styles.help}>
                {view.available}
                {view.capacity ? ` · ${view.capacity}` : ""}
              </div>
              {view.volumeShare !== null && (
                <div className={tabStyles.mt}>
                  <ProgressBar value={view.volumeShare * 100} label="Volume in use" size="sm" />
                </div>
              )}
            </div>
          </div>
          <ul className={styles.scopes}>
            {view.scopes.map((scope, index) => (
              <li key={scope.key} className={styles.scope} data-unavailable={scope.unavailable || undefined}>
                <div className={`${styles.scopeHead} ${tabStyles.legendHead}`}>
                  <Swatch color={scopeColor(index)} hatched={scope.unavailable} />
                  <span>{scope.label}</span>
                  <span className={styles.scopeValue}>{scope.value}</span>
                </div>
                {scope.share !== null && (
                  <div className={styles.meter} data-small="" aria-hidden="true">
                    <span style={{ width: barWidth(scope.share) }} />
                  </div>
                )}
                {scope.detail && !scope.unavailable && <div className={styles.scopeDetail}>{scope.detail}</div>}
              </li>
            ))}
          </ul>
          <p className={styles.footnote}>
            {view.complete ? "" : "Some locations could not be measured, so the total is a lower bound. "}
            {view.note}
          </p>
          <div className={tabStyles.measured}>
            <span>Measured {formatUtc(view.measuredAtUtc) ?? "at an unknown time"}</span>
            <Button onClick={() => void refresh()} disabled={busy} aria-busy={busy || undefined}>
              <RefreshIcon size={13} className={tabStyles.buttonIcon} />
              {busy ? "Measuring…" : "Refresh"}
            </Button>
          </div>
        </>
      ) : (
        <>
          <p className={styles.unavailable}>
            Storage usage is unavailable. Start the local engine to measure recorded sessions.
          </p>
          <div className={tabStyles.row}>
            <Button onClick={() => void refresh()} disabled={busy} aria-busy={busy || undefined}>
              <RefreshIcon size={13} className={tabStyles.buttonIcon} />
              {busy ? "Measuring…" : "Try again"}
            </Button>
          </div>
        </>
      )}
      {failed && (
        <div className={tabStyles.mt}>
          <InlineAlert tone="warning">Storage could not be measured just now. The previous figures are shown.</InlineAlert>
        </div>
      )}
    </Card>
  );
}

/* ----------------------------------------------------------------- captures */

type CapturesState =
  | { status: "loading" }
  | { status: "unreachable" }
  | { status: "ready"; model: CapturesModel; loadingMore: boolean; moreFailed: boolean };

async function fetchCapturePage(offset: number) {
  try {
    const response = await fetch(`/api/recording-sources/page?limit=${CAPTURES_PAGE_SIZE}&offset=${offset}`, {
      cache: "no-store",
    });
    if (!response.ok) return null;
    const body = (await response.json()) as { status?: unknown; data?: unknown } | null;
    if (!body || body.status !== "ok") return null;
    return readRecordingSourcePage(body.data);
  } catch {
    return null;
  }
}

function CapturesCard() {
  const [state, setState] = useState<CapturesState>({ status: "loading" });
  const [importing, setImporting] = useState<ReadonlySet<string>>(new Set());
  const toast = useToast();

  useEffect(() => {
    let cancelled = false;
    void fetchCapturePage(0).then((page) => {
      if (cancelled) return;
      setState(
        page
          ? { status: "ready", model: capturesModel(page), loadingMore: false, moreFailed: false }
          : { status: "unreachable" },
      );
    });
    return () => {
      cancelled = true;
    };
  }, []);

  async function showMore() {
    if (state.status !== "ready" || state.loadingMore) return;
    const current = state.model;
    setState({ ...state, loadingMore: true, moreFailed: false });
    const page = await fetchCapturePage(current.rows.length);
    setState(
      page
        ? { status: "ready", model: capturesModel(page, current.rows), loadingMore: false, moreFailed: false }
        : { status: "ready", model: current, loadingMore: false, moreFailed: true },
    );
  }

  async function importCapture(row: CaptureRow) {
    setImporting((current) => new Set(current).add(row.captureId));
    let reason: string | null = null;
    try {
      const response = await fetch("/api/import-jobs", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ capture_id: row.captureId, queue_if_busy: true }),
      });
      const body = (await response.json().catch(() => null)) as { reason?: unknown } | null;
      if (!response.ok) reason = typeof body?.reason === "string" ? body.reason : "unavailable";
    } catch {
      reason = "local_engine_unavailable";
    }
    setImporting((current) => {
      const next = new Set(current);
      next.delete(row.captureId);
      return next;
    });
    if (reason !== null) {
      toast.push({ tone: "error", title: "Import could not be started", body: humanReason(reason) });
      return;
    }
    toast.push({
      tone: "success",
      title: "Import queued",
      body: `${row.name} will appear in the session library when it finishes.`,
      action: { label: "Open library", href: "/review" },
    });
    setState((current) =>
      current.status === "ready"
        ? {
            ...current,
            model: {
              ...current.model,
              rows: current.model.rows.map((candidate) =>
                candidate.captureId === row.captureId
                  ? { ...candidate, canImport: false, badge: { label: "Queued", tone: "amber" as const } }
                  : candidate,
              ),
            },
          }
        : current,
    );
  }

  const columns: Column<CaptureRow>[] = [
    {
      key: "name",
      header: "Name",
      width: "minmax(140px, 1fr)",
      render: (row) => (
        <span className={tabStyles.cellText} title={row.name}>
          {row.name}
        </span>
      ),
    },
    { key: "size", header: "Size", width: "84px", numeric: true, render: (row) => row.size },
    {
      key: "imported",
      header: "Imported",
      width: "110px",
      render: (row) => <Badge tone={row.badge.tone}>{row.badge.label}</Badge>,
    },
    {
      key: "actions",
      header: "Actions",
      width: "130px",
      align: "end",
      render: (row) => (
        <span className={tabStyles.actions}>
          {row.canImport && (
            <Button
              className={tabStyles.small}
              onClick={() => void importCapture(row)}
              disabled={importing.has(row.captureId)}
              aria-label={`Import ${row.name}`}
            >
              Import
            </Button>
          )}
          {row.downloadHref && (
            <a
              className={tabStyles.iconLink}
              href={row.downloadHref}
              download
              aria-label={`Download ${row.name}`}
              title="Download capture"
            >
              <DownloadIcon size={14} />
            </a>
          )}
        </span>
      ),
    },
  ];

  const ready = state.status === "ready" ? state : null;
  return (
    <Card
      title="Captures"
      labelledBy="captures"
      aside={ready ? <span className={tabStyles.hint}>{capturesSummary(ready.model)}</span> : undefined}
    >
      <p className={styles.cardDesc}>Capture files in the recordings folder and whether they were imported.</p>
      {state.status === "unreachable" ? (
        <EmptyState
          size="inline"
          tone="warning"
          title="Captures cannot be listed because the local engine is not reachable."
        />
      ) : (
        <DataTable
          caption="Capture files"
          columns={columns}
          rows={ready?.model.rows ?? []}
          rowKey={(row) => row.captureId}
          loading={state.status === "loading"}
          loadingRows={4}
          density="compact"
          empty={<EmptyState size="inline" title="No capture files are in the recordings folder." />}
        />
      )}
      {ready?.model.hasMore && (
        <div className={tabStyles.tableFoot}>
          <Button onClick={() => void showMore()} disabled={ready.loadingMore} aria-busy={ready.loadingMore || undefined}>
            {ready.loadingMore ? "Loading…" : "Show more"}
          </Button>
          {ready.moreFailed && <span className={tabStyles.err}>More captures could not be loaded.</span>}
        </div>
      )}
    </Card>
  );
}

export function DataPanel({ storage }: { storage: StorageView | null }) {
  return (
    <div className={styles.grid}>
      <StorageCard initial={storage} />
      <div className={styles.rightCol}>
        <CapturesCard />
        <Card title="Retention" labelledBy="retention">
          <p className={styles.cardDesc}>How long recorded sessions are kept.</p>
          <p className={tabStyles.text}>
            Keep everything. Nothing is deleted automatically. Recordings stay in the recordings folder until you
            remove them.
          </p>
          <p className={styles.footnote}>Nothing on this screen deletes or renames data.</p>
        </Card>
      </div>
    </div>
  );
}
