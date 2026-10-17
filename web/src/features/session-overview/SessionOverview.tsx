"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { parseLapEnrichment } from "@/features/review-data/enrichment";
import { parseSessionSummary } from "@/features/review-data/summary";
import { MoreIcon, OpenDocumentIcon } from "@/features/ui-foundation/icons";
import { Menu, Tabs } from "@/features/ui-foundation/primitives";
import { EventsView } from "./EventsView";
import { Hero } from "./Hero";
import { LapSelection } from "./LapSelection";
import { LapsView } from "./LapsView";
import { OverviewView } from "./OverviewView";
import { SetupView } from "./SetupView";
import { StintsView } from "./StintsView";
import {
  analysisHref,
  applySummaryToHero,
  eventsFor,
  overviewQuery,
  parseOverviewParams,
  resolveCompare,
  resolveEnrichment,
  resolveSummary,
  type ChartMetric,
  type LapMode,
  type OverviewUrlState,
  type OverviewView as View,
} from "./overview-data";
import type { OverviewModel } from "./types";
import { useDeferredRead } from "./use-deferred-read";
import type { ViewData } from "./view-types";
import styles from "./SessionOverview.module.css";
import viewStyles from "./OverviewViews.module.css";

const VIEWS: Array<{ key: View; label: string }> = [
  { key: "overview", label: "Overview" },
  { key: "laps", label: "Laps" },
  { key: "stints", label: "Stints" },
  { key: "events", label: "Events" },
  { key: "setup", label: "Setup & Conditions" },
];

const tabId = (key: View) => `overview-tab-${key}`;
const panelId = (key: View) => `overview-panel-${key}`;

export function SessionOverview({
  model,
  initial,
}: {
  model: OverviewModel;
  /** View, lap and compare pair from the URL, parsed on the server so the first render matches. */
  initial?: OverviewUrlState;
}) {
  const start = initial ?? parseOverviewParams(new URLSearchParams(), model.laps, { selectedId: model.selectedId });
  const [view, setView] = useState<View>(start.view);
  const [mode, setMode] = useState<LapMode>(model.laps.length >= 2 ? start.mode : "single");
  const [selectedId, setSelectedId] = useState(start.selectedId);
  const [referenceChoice, setReferenceChoice] = useState<string | null>(start.referenceId);
  const [metric, setMetric] = useState<ChartMetric>("lap");

  // Deferred sections: when the server's budget ran out the browser reads them.
  const base = model.evidenceId ? `/api/review/sessions/${encodeURIComponent(model.evidenceId)}` : null;
  const enrichmentLive = useDeferredRead(
    base && model.enrichment.state === "deferred" ? `${base}/laps` : null,
    parseLapEnrichment,
  );
  const summaryLive = useDeferredRead(
    base && model.summaryData.state === "deferred" ? `${base}/summary` : null,
    parseSessionSummary,
  );
  const enrichment = useMemo(() => resolveEnrichment(model, enrichmentLive), [model, enrichmentLive]);
  const summary = useMemo(() => resolveSummary(model, summaryLive), [model, summaryLive]);
  const laps = enrichment.laps;
  const best = laps.find((lap) => lap.id === model.bestId) ?? null;
  const events = useMemo(() => eventsFor(model, summary), [model, summary]);

  const compare = useMemo(
    () => resolveCompare({ laps, bestId: model.bestId, targetId: selectedId, referenceId: referenceChoice }),
    [laps, model.bestId, referenceChoice, selectedId],
  );
  const comparing = mode === "compare";
  const target = laps.find((lap) => lap.id === selectedId) ?? laps[0] ?? null;

  const data: ViewData = {
    model,
    laps,
    enrichment,
    summary,
    events,
    selectedId: target?.id ?? "",
    referenceId: comparing ? compare.referenceId : null,
    best,
    metric,
  };

  const hero = useMemo(
    () =>
      applySummaryToHero(
        model.hero,
        summary.status === "loaded" ? summary.value : null,
        model.source,
        model.evidenceId ? model.evidenceId.slice(0, 8) : null,
      ),
    [model.evidenceId, model.hero, model.source, summary],
  );

  const analysis = comparing
    ? analysisHref(model.sessionKey, compare.target?.id ?? null, compare.referenceId)
    : analysisHref(model.sessionKey, target?.id ?? null, best?.id ?? null);

  // Mirror view, lap and compare pair in the URL without a server round trip.
  const resolvedReferenceId = compare.referenceId;
  useEffect(() => {
    const query = overviewQuery(
      window.location.search,
      { view, mode, selectedId, referenceId: referenceChoice, resolvedReferenceId },
      { selectedId: model.selectedId },
    );
    if (query !== window.location.search) {
      window.history.replaceState(null, "", `${window.location.pathname}${query}${window.location.hash}`);
    }
  }, [mode, model.selectedId, referenceChoice, resolvedReferenceId, selectedId, view]);

  const selectLap = useCallback((id: string) => {
    setSelectedId(id);
    setReferenceChoice((current) => (current === id ? null : current));
  }, []);
  const chooseReference = useCallback(
    (id: string) => {
      if (id !== selectedId) setReferenceChoice(id);
    },
    [selectedId],
  );
  /** Shift-click on the chart: compare the selected lap against this one. */
  const compareWith = useCallback(
    (id: string) => {
      if (id === selectedId || model.laps.length < 2) return;
      setMode("compare");
      setReferenceChoice(id);
    },
    [model.laps.length, selectedId],
  );
  const swap = useCallback(() => {
    if (!compare.target || !compare.reference) return;
    setSelectedId(compare.reference.id);
    setReferenceChoice(compare.target.id);
  }, [compare.reference, compare.target]);

  const showHero = view !== "laps";

  return (
    <>
      <div className={styles.toolbar}>
        <Tabs
          items={VIEWS}
          value={view}
          onChange={setView}
          label="Session sections"
          idPrefix="overview-tab"
          controls={panelId}
        />
        <div className={styles.toolbarActions}>
          <Link href={analysis} className={styles.openButton}>
            <OpenDocumentIcon size={15} />
            Open in Lap Analysis
          </Link>
          <Menu
            label="More session actions"
            trigger={<MoreIcon size={16} />}
            triggerClassName={styles.moreButton}
            panelClassName={styles.morePanel}
          >
            <Link href={analysis}>Open in Lap Analysis</Link>
            <Link href="/review">Back to Session Library</Link>
          </Menu>
        </div>
      </div>

      <div
        role="tabpanel"
        id={panelId(view)}
        aria-labelledby={tabId(view)}
        tabIndex={0}
        className={viewStyles.panel}
      >
        {showHero && <Hero hero={hero} photo={model.photo} sessionId={model.evidenceId} chevron={model.source === "fixture"} />}
        {view === "overview" && (
          <OverviewView
            data={data}
            onMetric={setMetric}
            onShowEvents={() => setView("events")}
            onSelectLap={selectLap}
            onCompareLap={compareWith}
          />
        )}
        {view === "laps" && (
          <LapsView
            data={data}
            compare={comparing}
            onSelect={selectLap}
            onReference={chooseReference}
            onMetric={setMetric}
          />
        )}
        {view === "stints" && <StintsView data={data} />}
        {view === "events" && <EventsView data={data} />}
        {view === "setup" && <SetupView data={data} />}
      </div>

      <LapSelection
        laps={laps}
        bestId={model.bestId}
        mode={mode}
        onModeChange={setMode}
        selectedId={target?.id ?? ""}
        onSelect={selectLap}
        compare={compare}
        onReference={chooseReference}
        onSwap={swap}
        compareHref={analysis}
      />
    </>
  );
}
