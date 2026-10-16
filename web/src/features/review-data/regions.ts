import { asRecord, envelopeData, finite, integer, text } from "./parse-utils";

// Parsers for track-model regions. Payload shapes verified against
// `f1_engineer/api/track_region_routes.py`:
//   GET /api/v1/track-models/{model_id}/regions?revision=N
//     data = metadata (model_id, revision, track_name, validation_status,
//            origin, region_count, track_length_m, ...) + diagnostic_only,
//            coaching_eligible, ranking_eligible + regions[]
//            (id, label, start_distance_m, end_distance_m, nominal_apex_m,
//            direction, complex_id; distances already include the model origin)
//   GET /api/v2/session-evidence/sessions/{id}/track-models
//     data = { session_id, models: [<regions payload>], reason }
//   GET /api/v1/track-models -> list of TrackModelRecord

export const MAX_REGIONS_PER_MODEL = 128;
export const MAX_TRACK_MODELS = 64;

export type TrackRegion = {
  id: string;
  label: string;
  startM: number;
  endM: number;
  apexM: number | null;
  direction: string | null;
  complexId: string | null;
};

export type RegionModel = {
  modelId: string;
  revision: number;
  trackName: string;
  validation: "draft" | "validated";
  origin: string | null;
  regionCount: number;
  trackLengthM: number | null;
  regions: TrackRegion[];
};

export type SessionRegionModels = {
  sessionId: string | null;
  models: RegionModel[];
  /** e.g. `compatible_track_model_unavailable` when the list is empty. */
  reason: string | null;
};

export type TrackModelSummary = {
  modelId: string;
  revision: number;
  trackName: string;
  trackId: number | null;
  layoutId: string | null;
  trackLengthM: number | null;
  validation: "draft" | "validated";
  origin: string | null;
  regionCount: number;
};

function parseRegion(value: unknown): TrackRegion | null {
  const record = asRecord(value);
  const id = text(record?.id, 64);
  const startM = finite(record?.start_distance_m);
  const endM = finite(record?.end_distance_m);
  if (!record || !id || startM === null || endM === null) return null;
  return {
    id,
    label: text(record.label, 64) ?? id,
    startM,
    endM,
    apexM: finite(record.nominal_apex_m),
    direction: text(record.direction, 16),
    complexId: text(record.complex_id, 64),
  };
}

function validation(value: unknown): "draft" | "validated" {
  // Anything the UI cannot positively identify as validated is treated as a draft.
  return value === "validated" ? "validated" : "draft";
}

/** One model entry (the `data` of the regions route, or an item of `models`). */
export function parseRegionModel(value: unknown): RegionModel | null {
  const record = asRecord(value);
  const modelId = text(record?.model_id, 128);
  const revision = integer(record?.revision);
  if (!record || !modelId || revision === null || revision < 1 || !Array.isArray(record.regions)) return null;
  const regions = record.regions
    .slice(0, MAX_REGIONS_PER_MODEL)
    .map(parseRegion)
    .filter((region): region is TrackRegion => region !== null);
  return {
    modelId,
    revision,
    trackName: text(record.track_name, 128) ?? "Unknown circuit",
    validation: validation(record.validation_status),
    origin: text(record.origin, 32),
    regionCount: integer(record.region_count) ?? regions.length,
    trackLengthM: finite(record.track_length_m),
    regions,
  };
}

/** Parses the `/api/v1/track-models/{id}/regions` body. Null when malformed. */
export function parseRegionsResponse(body: unknown): RegionModel | null {
  return parseRegionModel(envelopeData(body));
}

/** Parses the v2 `/sessions/{id}/track-models` body. Null when malformed. */
export function parseSessionTrackModels(body: unknown): SessionRegionModels | null {
  const data = asRecord(envelopeData(body));
  if (!data || !Array.isArray(data.models)) return null;
  const models = data.models
    .slice(0, MAX_TRACK_MODELS)
    .map(parseRegionModel)
    .filter((model): model is RegionModel => model !== null);
  return {
    sessionId: text(data.session_id, 128),
    models,
    reason: text(data.reason, 128),
  };
}

/** Parses the `/api/v1/track-models` list body. Null when malformed. */
export function parseTrackModelList(body: unknown): TrackModelSummary[] | null {
  const data = envelopeData(body);
  if (!Array.isArray(data)) return null;
  const models: TrackModelSummary[] = [];
  for (const item of data.slice(0, MAX_TRACK_MODELS * 4)) {
    const record = asRecord(item);
    const modelId = text(record?.model_id, 128);
    const revision = integer(record?.revision);
    if (!record || !modelId || revision === null || revision < 1) continue;
    models.push({
      modelId,
      revision,
      trackName: text(record.track_name, 128) ?? "Unknown circuit",
      trackId: integer(record.track_id),
      layoutId: text(record.layout_id, 64),
      trackLengthM: finite(record.track_length_m),
      validation: validation(record.validation_status),
      origin: text(record.origin, 32),
      regionCount: integer(record.region_count) ?? 0,
    });
  }
  return models;
}

/**
 * The model to show for a session: validated over draft, then the highest
 * revision, then the model id for a stable order. Null when there is none.
 */
export function pickRegionModel(models: readonly RegionModel[]): RegionModel | null {
  let best: RegionModel | null = null;
  for (const model of models) {
    if (model.regions.length === 0) continue;
    if (
      best === null ||
      (model.validation === "validated" && best.validation !== "validated") ||
      (model.validation === best.validation &&
        (model.revision > best.revision ||
          (model.revision === best.revision && model.modelId < best.modelId)))
    ) {
      best = model;
    }
  }
  return best;
}
