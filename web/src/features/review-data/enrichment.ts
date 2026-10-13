import { asRecord, bool, envelopeData, finite, integer, text } from "./parse-utils";

// Parser for `GET /api/v2/session-evidence/sessions/{id}/laps` (sector and
// tyre enrichment of v2 player attempts). Payload shape verified against
// `f1_engineer/processing/lap_enrichment.py`:
//   data.laps[]   revision_id, attempt_number, lap_number, lap_time_ms,
//                 disposition, readiness, superseded, start_observed, valid,
//                 game_valid, pit, sectors_ms, sectors_source, sectors_reason,
//                 tyre, tyre_reason
//   data.stints[] stint_index, visual/actual_compound, visual/actual_label,
//                 start_lap, end_lap, lap_count, start_age_laps, end_age_laps
//   data.truncated, data.source.truncation (reason code; not top-level)

export const MAX_ENRICHED_LAPS = 500; // the backend never lists more
export const MAX_ENRICHED_STINTS = 100;

export type TyreInfo = {
  visualCompound: number;
  actualCompound: number;
  visualLabel: string | null;
  actualLabel: string | null;
  ageLaps: number | null;
  source: "car_status" | "session_history_stints";
};

export type EnrichedLap = {
  revisionId: string;
  lapNumber: number | null;
  attemptNumber: number | null;
  lapTimeMs: number | null;
  disposition: string;
  /** Attempt readiness as reported by the backend (e.g. "available", "deferred"). */
  readiness: string | null;
  superseded: boolean;
  valid: boolean;
  gameValid: boolean | null;
  pit: boolean;
  startObserved: boolean | null;
  sectorsMs: [number, number, number] | null;
  sectorsSource: "session_history" | "lap_data" | null;
  sectorsReason: string | null;
  tyre: TyreInfo | null;
  tyreReason: string | null;
};

export type EnrichedStint = {
  index: number;
  visualLabel: string | null;
  actualLabel: string | null;
  visualCompound: number | null;
  actualCompound: number | null;
  startLap: number;
  endLap: number;
  lapCount: number;
  startAge: number | null;
  endAge: number | null;
};

export type LapEnrichment = {
  sessionId: string | null;
  laps: EnrichedLap[];
  stints: EnrichedStint[];
  truncated: boolean;
  /** Reason code for the truncation (`source.truncation`), when the scan stopped early. */
  truncation: string | null;
};

function parseSectors(value: unknown): [number, number, number] | null {
  if (!Array.isArray(value) || value.length !== 3) return null;
  const sectors = value.map(finite);
  if (sectors.some((item) => item === null || item <= 0)) return null;
  return sectors as [number, number, number];
}

function parseTyre(value: unknown): TyreInfo | null {
  const record = asRecord(value);
  if (!record) return null;
  const visual = integer(record.visual_compound);
  const actual = integer(record.actual_compound);
  const source = record.source;
  if (visual === null || actual === null) return null;
  if (source !== "car_status" && source !== "session_history_stints") return null;
  return {
    visualCompound: visual,
    actualCompound: actual,
    visualLabel: text(record.visual_label, 32),
    actualLabel: text(record.actual_label, 32),
    ageLaps: integer(record.age_laps),
    source,
  };
}

function parseLap(value: unknown): EnrichedLap | null {
  const record = asRecord(value);
  const revisionId = text(record?.revision_id, 128);
  if (!record || !revisionId) return null;
  const sectorsSource = record.sectors_source;
  const sectors = parseSectors(record.sectors_ms);
  const gameValid = bool(record.game_valid);
  return {
    revisionId,
    lapNumber: integer(record.lap_number),
    attemptNumber: integer(record.attempt_number),
    lapTimeMs: finite(record.lap_time_ms),
    disposition: text(record.disposition, 32) ?? "unknown",
    readiness: text(record.readiness, 32),
    superseded: record.superseded === true,
    // `valid` is derived by the backend as "game_valid is not false".
    valid: typeof record.valid === "boolean" ? record.valid : gameValid !== false,
    gameValid,
    pit: record.pit === true,
    startObserved: bool(record.start_observed),
    sectorsMs: sectors,
    sectorsSource:
      sectors !== null && (sectorsSource === "session_history" || sectorsSource === "lap_data")
        ? sectorsSource
        : null,
    sectorsReason: text(record.sectors_reason, 128),
    tyre: parseTyre(record.tyre),
    tyreReason: text(record.tyre_reason, 128),
  };
}

function parseStint(value: unknown): EnrichedStint | null {
  const record = asRecord(value);
  const index = integer(record?.stint_index);
  const startLap = integer(record?.start_lap);
  const endLap = integer(record?.end_lap);
  if (!record || index === null || startLap === null || endLap === null || endLap < startLap) return null;
  return {
    index,
    visualLabel: text(record.visual_label, 32),
    actualLabel: text(record.actual_label, 32),
    visualCompound: integer(record.visual_compound),
    actualCompound: integer(record.actual_compound),
    startLap,
    endLap,
    lapCount: integer(record.lap_count) ?? endLap - startLap + 1,
    startAge: integer(record.start_age_laps),
    endAge: integer(record.end_age_laps),
  };
}

/** Parses a full `/laps` response body (`{ data: { laps, stints, ... } }`). Null when malformed. */
export function parseLapEnrichment(body: unknown): LapEnrichment | null {
  const data = asRecord(envelopeData(body));
  if (!data || !Array.isArray(data.laps)) return null;
  const laps = data.laps
    .slice(0, MAX_ENRICHED_LAPS)
    .map(parseLap)
    .filter((lap): lap is EnrichedLap => lap !== null);
  const stints = (Array.isArray(data.stints) ? data.stints : [])
    .slice(0, MAX_ENRICHED_STINTS)
    .map(parseStint)
    .filter((stint): stint is EnrichedStint => stint !== null);
  const source = asRecord(data.source);
  return {
    sessionId: text(data.session_id, 128),
    laps,
    stints,
    truncated: data.truncated === true,
    truncation: text(source?.truncation, 128),
  };
}

/** Index laps by attempt revision id so v2 attempts can pick up sectors and tyres. */
export function enrichmentByRevision(enrichment: LapEnrichment): Map<string, EnrichedLap> {
  return new Map(enrichment.laps.map((lap) => [lap.revisionId, lap]));
}
