"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { lapAnalysisFixture } from "@/features/review-data/fixtures";
import { parseLapEnrichment } from "@/features/review-data/enrichment";
import { humanReason } from "@/features/review-data/reasons";
import { parseSessionTrackModels, pickRegionModel, type RegionModel } from "@/features/review-data/regions";
import { useToast } from "@/features/ui-foundation/Toast";
import { deltaSeries, type DeltaSeries } from "./delta";
import { mergeEnrichment } from "./enrich";
import {
  axisFor,
  lapDifferences,
  lapQuality,
  type Axis,
  type Difference,
  type DistanceRange,
  type LapTrace,
  type QualityItem,
} from "./lap-telemetry";
import type { AnalysisLap, EnrichmentState, LapAnalysisModel, RegionSource } from "./load";
import { processTelemetry } from "./process-telemetry";
import { findChoice, regionChoices, regionDelta, type RegionChoice } from "./regions";
import { publishExport } from "./export-store";
import { stateParams, type CompareMode, type InitialState, type View } from "./url-state";
import type { CursorSource } from "./TelemetryCharts";

export type ProcessState = {
  /** Lap ids being processed right now. */
  busy: string[];
  /** Last failure, per lap id. */
  errors: Record<string, { message: string; busy: boolean }>;
};

export type AnalysisState = {
  model: LapAnalysisModel;
  /** Laps with sector/tyre enrichment merged in. */
  laps: AnalysisLap[];
  /** Real session (measured traces); false for the reference fixture. */
  measured: boolean;
  target: AnalysisLap | undefined;
  reference: AnalysisLap | null;
  targetIndex: number;
  referenceIndex: number;
  setTargetId: (id: string) => void;
  setReferenceId: (id: string) => void;
  swap: () => void;
  targetTrace: LapTrace | undefined;
  referenceTrace: LapTrace | undefined;
  compare: CompareMode;
  setCompare: (mode: CompareMode) => void;
  view: View;
  setView: (view: View) => void;
  enrichment: EnrichmentState;
  regions: RegionSource;
  regionModel: RegionModel | null;
  choices: RegionChoice[];
  selection: string | null;
  selectedChoice: RegionChoice | null;
  setSelection: (id: string | null) => void;
  brush: DistanceRange | null;
  setBrush: (range: DistanceRange | null) => void;
  /** Brushed range, else the selected region's range, else null (full lap). */
  range: DistanceRange | null;
  cursor: number | null;
  pinned: boolean;
  onCursor: (distance: number | null, source: CursorSource) => void;
  axis: Axis | null;
  delta: DeltaSeries | null;
  regionDeltaS: number | null;
  differences: Difference[];
  quality: QualityItem[];
  lapDelta: number | null;
  processing: ProcessState;
  processLap: (lap: AnalysisLap) => void;
  canProcess: boolean;
  /** Fixture-only region name state (the reference drawing). */
  fixtureRegion: string | null;
  setFixtureRegion: (region: string | null) => void;
};

const AnalysisContext = createContext<AnalysisState | null>(null);

export function useAnalysis(): AnalysisState {
  const value = useContext(AnalysisContext);
  if (!value) throw new Error("useAnalysis must be used inside AnalysisProvider");
  return value;
}

/** Keeps a trace we already hold when a refresh could only report it unavailable. */
export function mergeTraces(previous: Record<string, LapTrace>, incoming: Record<string, LapTrace>): Record<string, LapTrace> {
  const merged = { ...previous };
  for (const [id, trace] of Object.entries(incoming)) {
    if (trace.state === "unavailable" && merged[id]?.state === "available") continue;
    merged[id] = trace;
  }
  return merged;
}

const RETRY_MS = [1000, 3000];

function wait(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** GET a proxy JSON route, retrying twice on the engine's 409 busy answer. */
async function fetchJsonWithRetry(url: string, signal: AbortSignal): Promise<{ status: number; body: unknown } | null> {
  for (let attempt = 0; attempt <= RETRY_MS.length; attempt += 1) {
    try {
      const response = await fetch(url, { signal, cache: "no-store" });
      const body: unknown = await response.json().catch(() => null);
      if (response.status === 409 && attempt < RETRY_MS.length) {
        await wait(RETRY_MS[attempt]);
        continue;
      }
      return { status: response.status, body };
    } catch {
      return null;
    }
  }
  return null;
}

export function AnalysisProvider({
  model,
  initial,
  children,
}: {
  model: LapAnalysisModel;
  initial: InitialState;
  children: ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const toast = useToast();
  const measured = model.telemetry.kind === "real";

  /* ---------------------------------------------------------------- URL */

  const writeParams = useCallback(
    (patch: Record<string, string | null>, mode: "router" | "history") => {
      const params = new URLSearchParams(window.location.search);
      for (const [key, value] of Object.entries(patch)) {
        if (value === null) params.delete(key);
        else params.set(key, value);
      }
      const url = params.size ? `${pathname}?${params}` : pathname;
      if (mode === "router") router.replace(url, { scroll: false });
      else window.history.replaceState(null, "", url);
    },
    [pathname, router],
  );

  /* ------------------------------------------------------- lap selection */

  const [targetId, setTargetIdState] = useState(model.targetId);
  const [referenceId, setReferenceIdState] = useState(model.referenceId);
  const setTargetId = (id: string) => {
    setTargetIdState(id);
    writeParams({ target: id }, "router");
  };
  const setReferenceId = (id: string) => {
    setReferenceIdState(id);
    writeParams({ reference: id }, "router");
  };
  const swap = () => {
    if (!referenceId) return;
    setTargetIdState(referenceId);
    setReferenceIdState(targetId);
    writeParams({ target: referenceId, reference: targetId }, "router");
  };

  /* ------------------------------------------------- enrichment (deferred) */

  const [clientEnrichment, setClientEnrichment] = useState<ReturnType<typeof parseLapEnrichment>>(null);
  const [enrichmentFailed, setEnrichmentFailed] = useState<string | null>(null);
  const needsEnrichment = model.enrichment.state === "deferred" && !clientEnrichment && !enrichmentFailed;
  useEffect(() => {
    if (!needsEnrichment) return;
    const controller = new AbortController();
    void (async () => {
      const result = await fetchJsonWithRetry(`/api/review/sessions/${encodeURIComponent(model.sessionKey)}/laps`, controller.signal);
      if (controller.signal.aborted) return;
      const parsed = result && result.status === 200 ? parseLapEnrichment(result.body) : null;
      if (parsed) setClientEnrichment(parsed);
      else {
        const reason = (result?.body as { reason?: unknown } | null)?.reason;
        setEnrichmentFailed(humanReason(typeof reason === "string" ? reason : "local_engine_unavailable"));
      }
    })();
    return () => controller.abort();
  }, [needsEnrichment, model.sessionKey]);

  const laps = useMemo(
    () => (clientEnrichment ? mergeEnrichment(model.laps, clientEnrichment) : model.laps),
    [clientEnrichment, model.laps],
  );
  const enrichment: EnrichmentState = clientEnrichment
    ? { state: "loaded", truncated: clientEnrichment.truncated }
    : enrichmentFailed
      ? { state: "unavailable", reason: enrichmentFailed }
      : model.enrichment;

  /* ------------------------------------------------------- regions (deferred) */

  const [clientRegions, setClientRegions] = useState<RegionSource | null>(null);
  const needsRegions = model.regions.state === "deferred" && !clientRegions;
  useEffect(() => {
    if (!needsRegions) return;
    const controller = new AbortController();
    void (async () => {
      const result = await fetchJsonWithRetry(`/api/review/sessions/${encodeURIComponent(model.sessionKey)}/track-models`, controller.signal);
      if (controller.signal.aborted) return;
      const parsed = result && result.status === 200 ? parseSessionTrackModels(result.body) : null;
      const picked = parsed ? pickRegionModel(parsed.models) : null;
      setClientRegions(
        picked
          ? { state: "available", model: picked }
          : { state: "none", reason: humanReason(parsed?.reason ?? "compatible_track_model_unavailable") },
      );
    })();
    return () => controller.abort();
  }, [needsRegions, model.sessionKey]);
  const regions: RegionSource = clientRegions ?? model.regions;
  const regionModel = regions.state === "available" ? regions.model : null;
  const choices = useMemo(() => regionChoices(regionModel), [regionModel]);

  /* --------------------------------------------------------------- traces */

  const incoming = model.telemetry.kind === "real" ? model.telemetry.traces : null;
  const [serverTraces, setServerTraces] = useState<Record<string, LapTrace>>(incoming ?? {});
  const [seenIncoming, setSeenIncoming] = useState(incoming);
  if (incoming !== seenIncoming) {
    setSeenIncoming(incoming);
    if (incoming) setServerTraces((previous) => mergeTraces(previous, incoming));
  }
  const [processed, setProcessed] = useState<Record<string, LapTrace>>({});
  const traces = useMemo(() => ({ ...serverTraces, ...processed }), [serverTraces, processed]);

  const targetIndex = Math.max(0, laps.findIndex((lap) => lap.id === targetId));
  const target = laps[targetIndex];
  const referenceIndex = laps.findIndex((lap) => lap.id === referenceId);
  const reference = referenceIndex >= 0 ? laps[referenceIndex] : null;
  const targetTrace = measured && target ? traces[target.id] : undefined;
  const referenceTrace = measured && reference ? traces[reference.id] : undefined;

  /* ----------------------------------------------------------------- view */

  const [view, setViewState] = useState<View>(initial.view);
  const [compare, setCompare] = useState<CompareMode>("delta");
  const [selection, setSelectionState] = useState<string | null>(initial.region);
  const [brush, setBrushState] = useState<DistanceRange | null>(initial.brush);
  const [fixtureRegion, setFixtureRegion] = useState<string | null>(lapAnalysisFixture.region.label);

  const persist = (next: { view?: View; selection?: string | null; brush?: DistanceRange | null }) => {
    writeParams(
      stateParams({
        view: next.view ?? view,
        region: next.selection !== undefined ? next.selection : selection,
        brush: next.brush !== undefined ? next.brush : brush,
      }),
      "history",
    );
  };
  const setView = (next: View) => {
    setViewState(next);
    persist({ view: next });
  };
  const selectedChoice = findChoice(choices, selection);
  const setSelection = (id: string | null) => {
    setSelectionState(id);
    setBrushState(null);
    persist({ selection: id, brush: null });
  };
  const setBrush = (range: DistanceRange | null) => {
    setBrushState(range);
    if (range) setSelectionState(null);
    persist({ brush: range, selection: range ? null : selection });
  };
  const range = brush ?? selectedChoice?.range ?? null;

  /* --------------------------------------------------------------- cursor */

  const [cursorState, setCursorState] = useState<{ d: number | null; pinned: boolean }>({ d: null, pinned: false });
  const onCursor = useCallback((distance: number | null, source: CursorSource) => {
    setCursorState((current) => {
      if (source === "pin") {
        return current.pinned && distance !== null && current.d !== null && Math.abs(current.d - distance) < 1
          ? { d: current.d, pinned: false }
          : { d: distance, pinned: true };
      }
      if (source === "key") return { d: distance, pinned: true };
      if (current.pinned) return current;
      return { d: distance, pinned: false };
    });
  }, []);

  /* ----------------------------------------------------------- derivations */

  const axis = useMemo(() => axisFor([targetTrace, referenceTrace]), [targetTrace, referenceTrace]);
  const delta = useMemo<DeltaSeries | null>(
    () => (measured && reference ? deltaSeries(targetTrace, referenceTrace, target?.ms, reference.ms, model.trackLengthM) : null),
    [measured, reference, targetTrace, referenceTrace, target?.ms, model.trackLengthM],
  );
  const regionDeltaS = range && delta ? regionDelta(range, delta) : null;
  const differences = useMemo(
    () =>
      measured
        ? lapDifferences(target, reference, targetTrace, referenceTrace, {
            range,
            timeDeltaS: compare === "delta" ? regionDeltaS : null,
          })
        : [],
    [measured, reference, referenceTrace, target, targetTrace, range, compare, regionDeltaS],
  );
  const quality = useMemo(
    () =>
      measured
        ? lapQuality({
            target,
            reference,
            targetTrace,
            referenceTrace,
            lapDataNote: model.lapDataNote,
            trackModel: regionModel ? `${regionModel.trackName} r${regionModel.revision} (${regionModel.validation})` : model.trackModel,
          })
        : [],
    [measured, model.lapDataNote, model.trackModel, reference, referenceTrace, regionModel, target, targetTrace],
  );
  const lapDelta = target && reference ? target.ms - reference.ms : null;

  /* ---------------------------------------------------- process telemetry */

  const [processing, setProcess] = useState<ProcessState>({ busy: [], errors: {} });
  const canProcess = model.source === "v2";
  const processLap = (lap: AnalysisLap) => {
    if (!lap.revisionId || processing.busy.includes(lap.id)) return;
    const revisionId = lap.revisionId;
    setProcess((current) => {
      const errors = { ...current.errors };
      delete errors[lap.id];
      return { busy: [...current.busy, lap.id], errors };
    });
    void processTelemetry(model.sessionKey, revisionId).then((result) => {
      setProcess((current) => {
        const errors = { ...current.errors };
        delete errors[lap.id];
        if (result.state === "busy") errors[lap.id] = { message: result.message, busy: true };
        if (result.state === "failed") errors[lap.id] = { message: result.message, busy: false };
        return { busy: current.busy.filter((id) => id !== lap.id), errors };
      });
      if (result.state === "ok") {
        setProcessed((current) => ({ ...current, [lap.id]: result.trace }));
        toast.push({ tone: "success", title: `Lap ${lap.lap} telemetry processed for this view.` });
      }
    });
  };

  /* -------------------------------------------------------------- exports */

  useEffect(() => {
    publishExport({
      fileName: model.track,
      laps,
      target: target ?? null,
      reference,
      targetTrace: targetTrace ?? null,
      referenceTrace: referenceTrace ?? null,
      delta: compare === "delta" ? delta : null,
      capture: model.capture,
    });
    return () => {
      publishExport(null);
    };
  }, [model.track, model.capture, laps, target, reference, targetTrace, referenceTrace, delta, compare]);

  const value: AnalysisState = {
    model,
    laps,
    measured,
    target,
    reference,
    targetIndex,
    referenceIndex,
    setTargetId,
    setReferenceId,
    swap,
    targetTrace,
    referenceTrace,
    compare,
    setCompare,
    view,
    setView,
    enrichment,
    regions,
    regionModel,
    choices,
    selection,
    selectedChoice,
    setSelection,
    brush,
    setBrush,
    range,
    cursor: cursorState.d,
    pinned: cursorState.pinned,
    onCursor,
    axis,
    delta,
    regionDeltaS,
    differences,
    quality,
    lapDelta,
    processing,
    processLap,
    canProcess,
    fixtureRegion,
    setFixtureRegion,
  };
  return <AnalysisContext.Provider value={value}>{children}</AnalysisContext.Provider>;
}
