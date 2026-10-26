import {
  fetchLapEnrichment,
  fetchRecordingSources,
  fetchRegions,
  fetchSessionTrackModels,
  fetchTrackModels,
  readLocalJson,
} from "@/features/review-data/backend";
import { humanReason } from "@/features/review-data/reasons";
import { pickRegionModel } from "@/features/review-data/regions";
import { mergeEnrichment } from "./enrich";
import type { LapAnalysisModel, RegionSource } from "./load";
import { matchTrackModel } from "./regions";

// Server-side additions to the Lap Analysis model that need the network and so
// stay out of load.ts (which is testable without a backend): sector and tyre
// enrichment, corner regions, whether a model may answer questions, and the
// original capture of a v1 session.

/** The server waits this long for the scan-backed reads; slower ones are deferred to the client. */
const ENRICHMENT_BUDGET_MS = 1500;
const REGION_BUDGET_MS = 2500;

async function runtimeReady(): Promise<boolean> {
  const read = await readLocalJson("/api/v1/engineer/runtime", { timeoutMs: 1500, maxBytes: 16 * 1024 });
  if (read.kind !== "response" || read.status !== 200) return false;
  const data = (read.body as { data?: { status?: unknown } } | null)?.data;
  return data?.status === "ready";
}

async function v2Regions(sessionId: string): Promise<RegionSource> {
  const result = await fetchSessionTrackModels(sessionId, { timeoutMs: REGION_BUDGET_MS });
  switch (result.state) {
    case "ok": {
      const model = pickRegionModel(result.value.models);
      return model
        ? { state: "available", model }
        : { state: "none", reason: humanReason(result.value.reason ?? "compatible_track_model_unavailable") };
    }
    case "timeout":
    case "busy":
      return { state: "deferred" };
    case "unavailable":
      return { state: "none", reason: humanReason(result.reason) };
    default:
      return { state: "none", reason: humanReason("local_engine_unavailable") };
  }
}

async function v1Regions(model: LapAnalysisModel): Promise<RegionSource> {
  const list = await fetchTrackModels();
  if (list.state !== "ok") return { state: "none", reason: humanReason("local_engine_unavailable") };
  const match = matchTrackModel(list.value, model.track === "Unknown circuit" ? null : model.track, model.trackLengthM);
  if (!match) return { state: "none", reason: humanReason("compatible_track_model_unavailable") };
  const regions = await fetchRegions(match.modelId, match.revision);
  return regions.state === "ok" && regions.value.regions.length > 0
    ? { state: "available", model: regions.value }
    : { state: "none", reason: humanReason("compatible_track_model_unavailable") };
}

async function capture(runId: string | null): Promise<LapAnalysisModel["capture"]> {
  if (!runId) return null;
  const page = await fetchRecordingSources({ limit: 50, availability: "available" });
  const item = page?.items.find((source) => source.latest_job_run_id === runId && source.available);
  return item ? { captureId: item.capture_id, version: item.download_version } : null;
}

/** Adds enrichment, regions, runtime readiness and the capture link. Never throws. */
export async function extendLapAnalysis(model: LapAnalysisModel): Promise<LapAnalysisModel> {
  if (model.source === "fixture") return model;
  const ready = runtimeReady().catch(() => false);
  if (model.source === "v2") {
    const [enrichment, regions, runtime] = await Promise.all([
      fetchLapEnrichment(model.sessionKey, { timeoutMs: ENRICHMENT_BUDGET_MS }),
      v2Regions(model.sessionKey).catch((): RegionSource => ({ state: "deferred" })),
      ready,
    ]);
    const base = { ...model, regions, runtimeReady: runtime };
    switch (enrichment.state) {
      case "ok":
        return {
          ...base,
          laps: mergeEnrichment(model.laps, enrichment.value),
          enrichment: { state: "loaded", truncated: enrichment.value.truncated },
        };
      case "timeout":
      case "busy":
        return { ...base, enrichment: { state: "deferred" } };
      case "unavailable":
        return { ...base, enrichment: { state: "unavailable", reason: humanReason(enrichment.reason) } };
      default:
        return { ...base, enrichment: { state: "unavailable", reason: humanReason("local_engine_unavailable") } };
    }
  }
  const [regions, runtime, source] = await Promise.all([
    v1Regions(model).catch((): RegionSource => ({ state: "none", reason: humanReason("local_engine_unavailable") })),
    ready,
    capture(model.runId).catch(() => null),
  ]);
  return { ...model, regions, runtimeReady: runtime, capture: source };
}
