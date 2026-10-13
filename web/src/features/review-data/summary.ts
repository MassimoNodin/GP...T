import { asRecord, bool, envelopeData, finite, integer, text } from "./parse-utils";

// Parser for `GET /api/v2/session-evidence/sessions/{id}/summary`. Payload shape
// verified against `f1_engineer/processing/session_summary.py`:
//   data.started_at_utc / ended_at_utc / last_observed_at_utc / time_reason
//   data.session            id, lifecycle, track_name, session_type, total_laps
//   data.session_time       start_s, end_s, duration_s (game session time)
//   data.scheduled_duration_s
//   data.player             car_index, last_lap_number, pit_stop_count
//   data.weather[]          session_time_s, weather, track_temperature_c, air_temperature_c
//   data.events[]           kind, label, session_time_s, lap, car_index, is_player, code, detail
//   data.totals             by_kind, observed, listed, omitted_minor, omitted_budget, truncated
//   data.scan               complete, stop_reason

export const MAX_SUMMARY_EVENTS = 400;
export const MAX_SUMMARY_WEATHER = 200;

export type SummaryEvent = {
  kind: string;
  label: string;
  sessionTimeS: number | null;
  lap: number | null;
  carIndex: number | null;
  isPlayer: boolean;
  code: string | null;
  detail: Record<string, unknown>;
};

export type WeatherPoint = {
  sessionTimeS: number | null;
  weather: string | null;
  trackTemperatureC: number | null;
  airTemperatureC: number | null;
};

export type SessionSummary = {
  startedAtUtc: string | null;
  endedAtUtc: string | null;
  lastObservedAtUtc: string | null;
  timeReason: string | null;
  lifecycle: string | null;
  durationS: number | null;
  scheduledDurationS: number | null;
  totalLaps: number | null;
  pitStopCount: number | null;
  lastLapNumber: number | null;
  weather: WeatherPoint[];
  events: SummaryEvent[];
  totals: {
    listed: number;
    observed: number;
    omittedMinor: number;
    omittedBudget: number;
    truncated: boolean;
    byKind: Record<string, number>;
  };
  scan: { complete: boolean; stopReason: string | null };
};

/** Labels are templated with `{car}`/`{other}`; a surviving placeholder reads naturally. */
export function cleanEventLabel(label: string): string {
  return label.replace(/\{car\}/g, "A driver").replace(/\{other\}/g, "another driver");
}

function isoUtc(value: unknown): string | null {
  const candidate = text(value, 40);
  if (!candidate || !/^\d{4}-\d{2}-\d{2}T/.test(candidate)) return null;
  return Number.isFinite(Date.parse(candidate)) ? candidate : null;
}

function parseEvent(value: unknown): SummaryEvent | null {
  const record = asRecord(value);
  const kind = text(record?.kind, 64);
  const label = text(record?.label, 200);
  if (!record || !kind || !label) return null;
  return {
    kind,
    label: cleanEventLabel(label),
    sessionTimeS: finite(record.session_time_s),
    lap: integer(record.lap),
    carIndex: integer(record.car_index),
    isPlayer: record.is_player === true,
    code: text(record.code, 16),
    detail: asRecord(record.detail) ?? {},
  };
}

function parseWeather(value: unknown): WeatherPoint | null {
  const record = asRecord(value);
  if (!record) return null;
  return {
    sessionTimeS: finite(record.session_time_s),
    weather: text(record.weather, 32),
    trackTemperatureC: finite(record.track_temperature_c),
    airTemperatureC: finite(record.air_temperature_c),
  };
}

function parseByKind(value: unknown): Record<string, number> {
  const record = asRecord(value);
  const result: Record<string, number> = {};
  if (!record) return result;
  for (const [key, count] of Object.entries(record).slice(0, 64)) {
    const parsed = integer(count);
    if (parsed !== null && parsed >= 0) result[key.slice(0, 64)] = parsed;
  }
  return result;
}

/**
 * Names the player's car "You" in event labels the engine wrote with a car
 * number ("Car 7 overtook Car 4" -> "You overtook Car 4"). Labels that carry a
 * driver name are left as they are.
 */
export function personaliseLabel(label: string, playerCarIndex: number): string {
  return label.replace(new RegExp(`\\bCar ${playerCarIndex}\\b`, "g"), (_match, offset: number) =>
    offset === 0 ? "You" : "you",
  );
}

/** Parses a full `/summary` response body (`{ data: {...} }`). Null when malformed. */
export function parseSessionSummary(body: unknown): SessionSummary | null {
  const data = asRecord(envelopeData(body));
  if (!data || !Array.isArray(data.events) || !asRecord(data.scan)) return null;
  const session = asRecord(data.session);
  const sessionTime = asRecord(data.session_time);
  const player = asRecord(data.player);
  const totals = asRecord(data.totals);
  const scan = asRecord(data.scan);
  const playerCarIndex = integer(player?.car_index);
  const events = data.events
    .slice(0, MAX_SUMMARY_EVENTS)
    .map(parseEvent)
    .filter((event): event is SummaryEvent => event !== null)
    .map((event) => (playerCarIndex === null ? event : { ...event, label: personaliseLabel(event.label, playerCarIndex) }));
  const weather = (Array.isArray(data.weather) ? data.weather : [])
    .slice(0, MAX_SUMMARY_WEATHER)
    .map(parseWeather)
    .filter((point): point is WeatherPoint => point !== null);
  return {
    startedAtUtc: isoUtc(data.started_at_utc),
    endedAtUtc: isoUtc(data.ended_at_utc),
    lastObservedAtUtc: isoUtc(data.last_observed_at_utc),
    timeReason: text(data.time_reason, 128),
    lifecycle: text(session?.lifecycle, 32),
    durationS: finite(sessionTime?.duration_s),
    scheduledDurationS: finite(data.scheduled_duration_s),
    totalLaps: integer(session?.total_laps),
    pitStopCount: integer(player?.pit_stop_count),
    lastLapNumber: integer(player?.last_lap_number),
    weather,
    events,
    totals: {
      listed: integer(totals?.listed) ?? events.length,
      observed: integer(totals?.observed) ?? events.length,
      omittedMinor: integer(totals?.omitted_minor) ?? 0,
      omittedBudget: integer(totals?.omitted_budget) ?? 0,
      truncated: totals?.truncated === true,
      byKind: parseByKind(totals?.by_kind),
    },
    scan: {
      complete: bool(scan?.complete) ?? false,
      stopReason: text(scan?.stop_reason, 64),
    },
  };
}
