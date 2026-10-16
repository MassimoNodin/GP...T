// Track model catalog as the Advanced tab and the status tile show it.
// Pure mapping from the parsed `/api/v1/track-models` list.

import type { ReadResult } from "@/features/review-data/backend";
import type { RegionModel, TrackModelSummary, TrackRegion } from "@/features/review-data/regions";

export const DRAFT_FOOTNOTE = "Draft models give approximate distance ranges; they are not reviewed corner definitions.";

export type TrackModelRow = {
  key: string;
  modelId: string;
  revision: number;
  circuit: string;
  regionCount: number;
  reviewed: boolean;
  statusLabel: "Reviewed" | "Draft";
  originLabel: "Packaged" | "Local draft" | "Reviewed bundle" | "Unknown";
  lengthLabel: string | null;
  lengthM: number | null;
};

const ORIGINS: Record<string, TrackModelRow["originLabel"]> = {
  packaged: "Packaged",
  local_draft: "Local draft",
  reviewed: "Reviewed bundle",
};

export function originLabel(origin: string | null): TrackModelRow["originLabel"] {
  return origin !== null && Object.hasOwn(ORIGINS, origin) ? ORIGINS[origin] : "Unknown";
}

/** `3337` -> `3,337 m`. */
export function formatLengthM(meters: number | null): string | null {
  if (meters === null || !Number.isFinite(meters) || meters <= 0) return null;
  return `${new Intl.NumberFormat("en-US").format(Math.round(meters))} m`;
}

/** Rows ordered by circuit, then newest revision first, then model id. */
export function trackModelRows(models: readonly TrackModelSummary[]): TrackModelRow[] {
  return models
    .map((model): TrackModelRow => {
      const reviewed = model.validation === "validated";
      return {
        key: `${model.modelId}@${model.revision}`,
        modelId: model.modelId,
        revision: model.revision,
        circuit: model.trackName,
        regionCount: model.regionCount,
        reviewed,
        statusLabel: reviewed ? "Reviewed" : "Draft",
        originLabel: originLabel(model.origin),
        lengthLabel: formatLengthM(model.trackLengthM),
        lengthM: model.trackLengthM,
      };
    })
    .sort(
      (a, b) =>
        a.circuit.localeCompare(b.circuit) ||
        b.revision - a.revision ||
        (a.modelId < b.modelId ? -1 : a.modelId > b.modelId ? 1 : 0),
    );
}

export type TrackModelsView =
  | { state: "ok"; rows: TrackModelRow[]; reviewed: number; draft: number }
  | { state: "unavailable"; reason: string }
  | { state: "unreachable" };

export function describeTrackModels(read: ReadResult<TrackModelSummary[]>): TrackModelsView {
  if (read.state === "ok") {
    const rows = trackModelRows(read.value);
    const reviewed = rows.filter((row) => row.reviewed).length;
    return { state: "ok", rows, reviewed, draft: rows.length - reviewed };
  }
  if (read.state === "unavailable") return { state: "unavailable", reason: read.reason };
  if (read.state === "busy") return { state: "unavailable", reason: read.reason };
  return { state: "unreachable" };
}

export type TrackModelsTile = { name: string; badge: string };

/** Status tile text: `{n} models` and `{k} reviewed · {d} draft`, or an honest unavailable state. */
export function trackModelsTile(view: TrackModelsView): TrackModelsTile {
  if (view.state !== "ok") return { name: "Unavailable", badge: "Not measured" };
  const count = view.rows.length;
  if (count === 0) return { name: "No models", badge: "None installed" };
  return {
    name: `${count} ${count === 1 ? "model" : "models"}`,
    badge: `${view.reviewed} reviewed · ${view.draft} draft`,
  };
}

/* ---------------------------------------------------------------- regions */

export type RegionRow = {
  id: string;
  label: string;
  range: string;
  apex: string | null;
  direction: string | null;
};

function meters(value: number): string {
  return `${new Intl.NumberFormat("en-US").format(Math.round(value))} m`;
}

export function regionRows(regions: readonly TrackRegion[]): RegionRow[] {
  return regions.map((region) => ({
    id: region.id,
    label: region.label,
    range: `${meters(region.startM)} to ${meters(region.endM)}`,
    apex: region.apexM === null ? null : meters(region.apexM),
    direction: region.direction ? region.direction.charAt(0).toUpperCase() + region.direction.slice(1) : null,
  }));
}

export function regionsHeading(model: RegionModel): string {
  const status = model.validation === "validated" ? "Reviewed" : "Draft";
  return `${model.trackName} · revision ${model.revision} · ${status}`;
}
