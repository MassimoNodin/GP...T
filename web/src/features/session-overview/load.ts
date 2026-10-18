import type { LapRecord, SessionRecord } from "@/lib/api";
import {
  completedLaps,
  fetchAcquisition,
  fetchEvidenceAttempts,
  fetchEvidenceSession,
  fetchLapEnrichment,
  fetchLaps,
  fetchSession,
  cachedSummaryDate,
  fetchSummary,
  sessionBestLap,
  telemetryFooter,
  telemetryStatus,
  TELEMETRY_OFFLINE,
  type ReadResult,
} from "@/features/review-data/backend";
import type { LapEnrichment } from "@/features/review-data/enrichment";
import {
  FIXTURE_SESSION_KEY,
  overviewFixture,
  shellFixture,
} from "@/features/review-data/fixtures";
import {
  formatDayTime,
  formatGame,
  formatSessionType,
} from "@/features/review-data/format";
import { humanReason } from "@/features/review-data/reasons";
import {
  bestEvidenceLap,
  conditionsLabel,
  EMPTY_PROGRESSION,
  evidenceLaps,
  evidenceSessionContext,
  lifecycleStatus,
  progressionFrom,
  timeDrivenMs,
  type EvidenceAttempt,
  type EvidenceSession,
} from "@/features/review-data/session-evidence";
import type { SessionSummary } from "@/features/review-data/summary";
import type { ShellFooter } from "@/features/ui-foundation/AppShell";
import { trackKeyFromName } from "@/features/ui-foundation/TrackMap";
import { trackPhotoUrl } from "@/features/ui-foundation/track-photo-url";
import {
  applyEnrichment,
  conditionsText,
  heroDateLine,
  lapEvents,
  notCompletedEvents,
  realHeroFields,
  referenceDetailFields,
  referenceHeroFields,
  sectionFromRead,
  setupFromContexts,
  setupGroups,
} from "./overview-data";
import type {
  OverviewContext,
  OverviewEvent,
  OverviewLap,
  OverviewModel,
  OverviewStint,
} from "./types";

export type {
  Compound,
  DeferredSection,
  OverviewContext,
  OverviewEvent,
  OverviewLap,
  OverviewModel,
  OverviewStint,
  SetupView,
} from "./types";

/** Time the server loader waits for `/laps` and `/summary` before the client takes over. */
export const DEFERRED_BUDGET_MS = 1500;

const NO_TYRE_V1 = "Tyre data was not recorded for this session.";

function emptyLap(): Pick<
  OverviewLap,
  "sectors" | "sectorsReason" | "tyre" | "tyreReason" | "pit" | "startObserved" | "traceAvailable"
> {
  return {
    sectors: null,
    sectorsReason: null,
    tyre: null,
    tyreReason: null,
    pit: false,
    startObserved: null,
    traceAvailable: true,
  };
}

// A deterministic 78-lap list consistent with the reference numbers:
// Lap 28 is 1:13.420 and the session best is Lap 57 at 1:12.842.
export function fixtureLaps(): OverviewLap[] {
  const laps: OverviewLap[] = [];
  for (let lap = 1; lap <= 78; lap += 1) {
    const stintStart = lap <= 12 ? 1 : lap <= 54 ? 13 : 55;
    const jitter = ((lap * 37) % 17) / 100 - 0.08;
    let seconds = 73.55 - 0.006 * lap + 0.018 * (lap - stintStart) + jitter;
    if (lap === 1) seconds = 78.214;
    if (lap >= 20 && lap <= 23) seconds = 96.1 + jitter;
    if (lap === 28) seconds = 73.42;
    if (lap === 57) seconds = 72.842;
    if (lap !== 57 && seconds <= 72.95) seconds = 72.95 + ((lap * 13) % 7) / 100;
    laps.push({ id: String(lap), lap, ms: Math.round(seconds * 1000), valid: true, ...emptyLap() });
  }
  return laps;
}

const FIXTURE_STINTS: OverviewStint[] = [
  { index: 1, compound: "soft", label: "Soft", actual: "C5", startLap: 1, endLap: 12, lapCount: 12, startAge: 0, endAge: 12 },
  { index: 2, compound: "medium", label: "Medium", actual: "C4", startLap: 13, endLap: 54, lapCount: 42, startAge: 0, endAge: 42 },
  { index: 3, compound: "hard", label: "Hard", actual: "C3", startLap: 55, endLap: 78, lapCount: 24, startAge: 0, endAge: 24 },
];

// Reference sectors for laps 28 and 57 match the corrected table in the Lap
// Analysis fixture; other laps split their time in fixed proportions.
const FIXTURE_SECTORS: Record<number, [number, number, number]> = {
  28: [18420, 35128, 19872],
  57: [18630, 34186, 20026],
};
const FIXTURE_PIT_LAPS = new Set([12, 54]);

function fixtureOverviewLaps(): OverviewLap[] {
  return fixtureLaps().map((lap) => {
    const stint = FIXTURE_STINTS.find((item) => lap.lap >= item.startLap && lap.lap <= item.endLap);
    const first = Math.round(lap.ms * 0.2509);
    const second = Math.round(lap.ms * 0.4785);
    return {
      ...lap,
      sectors: FIXTURE_SECTORS[lap.lap] ?? [first, second, lap.ms - first - second],
      tyre: stint
        ? { compound: stint.compound, label: stint.label, actual: stint.actual, ageLaps: lap.lap - stint.startLap + 1 }
        : null,
      pit: FIXTURE_PIT_LAPS.has(lap.lap),
      startObserved: true,
    };
  });
}

/** The reference's events with their drawn icons; kinds and categories feed the Events filters. */
function fixtureEvents(): OverviewEvent[] {
  const meta: Array<Pick<OverviewEvent, "kind" | "category" | "isPlayer">> = [
    { kind: "lights_out", category: "race_control", isPlayer: false },
    { kind: "time_loss", category: "my_laps", isPlayer: true },
    { kind: "safety_car", category: "race_control", isPlayer: false },
    { kind: "safety_car", category: "race_control", isPlayer: false },
    { kind: "fastest_lap", category: "my_laps", isPlayer: true },
    { kind: "gap_ahead", category: "other", isPlayer: true },
    { kind: "yellow_flag", category: "race_control", isPlayer: false },
    { kind: "chequered_flag", category: "race_control", isPlayer: false },
  ];
  return overviewFixture.events.items.map((event, index) => ({
    id: `reference-${index}`,
    ...meta[index],
    lap: event.lap,
    timeS: null,
    label: event.text,
    icon: event.icon,
    highlight: event.highlight,
    source: "reference" as const,
  }));
}

function fixtureSummary(): SessionSummary {
  return {
    startedAtUtc: "2024-06-04T15:00:00Z",
    endedAtUtc: "2024-06-04T16:42:18Z",
    lastObservedAtUtc: "2024-06-04T16:42:18Z",
    timeReason: null,
    lifecycle: "ended",
    durationS: 6138,
    scheduledDurationS: 7200,
    totalLaps: 78,
    pitStopCount: 2,
    lastLapNumber: 78,
    weather: [
      { sessionTimeS: 0, weather: "clear", trackTemperatureC: 38, airTemperatureC: 24 },
      { sessionTimeS: 2470, weather: "light_cloud", trackTemperatureC: 35, airTemperatureC: 23 },
    ],
    events: [],
    totals: { listed: 8, observed: 8, omittedMinor: 0, omittedBudget: 0, truncated: false, byKind: {} },
    scan: { complete: true, stopReason: null },
  };
}

function fixtureModel(footer: ShellFooter): OverviewModel {
  return {
    sessionKey: FIXTURE_SESSION_KEY,
    source: "fixture",
    evidenceId: null,
    header: overviewFixture.header.items,
    shell: { live: false, status: shellFixture.replayMode },
    footer,
    photo: trackPhotoUrl(overviewFixture.hero.track),
    hero: {
      title: overviewFixture.hero.title,
      track: overviewFixture.hero.track,
      lines: overviewFixture.hero.lines,
      status: overviewFixture.hero.status,
      stats: referenceHeroFields(overviewFixture.hero.stats),
      details: referenceDetailFields(overviewFixture.hero.details),
    },
    progression: { ...overviewFixture.progression, yStep: 2000, illustrative: true },
    summary: {
      text: overviewFixture.summary.text,
      items: overviewFixture.summary.items,
    },
    events: fixtureEvents(),
    lapDataNote: null,
    laps: fixtureOverviewLaps(),
    selectedId: "28",
    bestId: "57",
    enrichment: { state: "loaded", value: { stints: FIXTURE_STINTS }, partialReason: null },
    summaryData: { state: "loaded", value: fixtureSummary(), partialReason: null },
    setup: {
      groups: setupGroups({
        front_wing: 24,
        rear_wing: 18,
        on_throttle_differential: 55,
        off_throttle_differential: 50,
        front_camber: -3.5,
        rear_camber: -2,
        front_toe: 0.05,
        rear_toe: 0.4,
        front_suspension: 41,
        rear_suspension: 21,
        front_anti_roll_bar: 16,
        rear_anti_roll_bar: 9,
        front_suspension_height: 30,
        rear_suspension_height: 50,
        brake_pressure_percent: 95,
        brake_bias_percent: 56,
        engine_braking_percent: 60,
        front_left_tyre_pressure_psi: 23.5,
        front_right_tyre_pressure_psi: 23.5,
        rear_left_tyre_pressure_psi: 21.5,
        rear_right_tyre_pressure_psi: 21.5,
        ballast: 6,
        fuel_load: 100,
        invalid_fields: [],
      }),
      changeCount: 0,
    },
    context: {
      sessionType: "race",
      ruleSet: "2024",
      packetFormat: 2024,
      trackName: "Monaco",
      trackLengthM: 3337,
      totalLaps: 78,
      weather: "clear",
      airTemperatureC: 24,
      trackTemperatureC: 38,
      startedAtUtc: null,
      endedAtUtc: null,
      durationS: null,
    },
  };
}

/** Placeholder model parts for real sessions: no other circuit's reference content. */
function realBase(footer: ShellFooter): OverviewModel {
  return {
    ...fixtureModel(footer),
    summary: null,
    events: [],
    lapDataNote: null,
    setup: null,
    evidenceId: null,
  };
}

function v1Sectors(record: LapRecord): Pick<OverviewLap, "sectors" | "sectorsReason"> {
  const evidence = record.timing_evidence;
  const parts = [evidence?.sector1_time_ms, evidence?.sector2_time_ms, evidence?.sector3_time_ms];
  if (
    evidence?.status === "matched" &&
    parts.every((value): value is number => typeof value === "number" && Number.isFinite(value) && value > 0)
  ) {
    return { sectors: parts as [number, number, number], sectorsReason: null };
  }
  const reason = humanReason(evidence?.reasons?.[0]);
  return { sectors: null, sectorsReason: reason || "Sector times were not reported for this lap." };
}

function realModel(session: SessionRecord, records: LapRecord[], footer: ShellFooter): OverviewModel {
  const base = realBase({ ...footer, lines: [formatGame(session.packet_format), footer.lines[1] ?? ""] });
  const context = session.context;
  const title = context?.track_name ?? "Unknown circuit";
  const track = trackKeyFromName(title);
  const completed = completedLaps(records);
  const laps: OverviewLap[] = completed.map((lap) => ({
    id: lap.attempt_key,
    lap: lap.lap_number,
    ms: lap.lap_time_ms as number,
    valid: lap.game_valid !== false,
    ...emptyLap(),
    ...v1Sectors(lap),
    tyreReason: NO_TYRE_V1,
    pit: lap.pit_encountered === true,
    startObserved: typeof lap.start_observed === "boolean" ? lap.start_observed : null,
  }));
  const best = sessionBestLap(records);
  const bestId = best?.attempt_key ?? null;
  const selectedId = laps.at(-1)?.id ?? "";
  const started = Date.parse(session.started_at_utc);
  const finished = session.finished_at_utc ? Date.parse(session.finished_at_utc) : Number.NaN;
  const bestOverview = laps.find((lap) => lap.id === bestId) ?? null;
  const type = formatSessionType(context?.session_type);
  const durationS = Number.isFinite(finished - started) && finished >= started ? (finished - started) / 1000 : null;
  const fields = realHeroFields({
    source: "v1",
    lapCount: laps.length,
    bestMs: bestOverview?.ms ?? null,
    conditions: conditionsText(context?.weather_name, null),
    trackTemperatureC: null,
    packetFormat: session.packet_format || null,
    trackLengthM: context?.track_length_m ?? null,
    timeDrivenMs: laps.length ? laps.reduce((sum, lap) => sum + lap.ms, 0) : null,
    ruleSet: context?.rule_set ?? null,
    partial: false,
  });
  const ordered = [...records].sort((a, b) => a.lap_number - b.lap_number || a.attempt_number - b.attempt_number);
  return {
    ...base,
    sessionKey: session.session_key,
    source: "v1",
    header: [title, type, { iso: session.started_at_utc }],
    photo: trackPhotoUrl(track),
    hero: {
      title,
      track,
      lines: [
        [type, formatDayTime(session.started_at_utc)],
        [formatGame(session.packet_format), "Player"],
      ],
      status:
        session.run_status === "complete"
          ? { label: "Complete", tone: "green" }
          : { label: formatSessionType(session.run_status), tone: "amber" },
      stats: fields.stats,
      details: fields.details,
    },
    progression: laps.length ? progressionFrom(laps, selectedId, bestId) : EMPTY_PROGRESSION,
    events: lapEvents(laps, bestId),
    laps,
    selectedId: laps.length ? selectedId : "",
    bestId: laps.length ? bestId : null,
    enrichment: { state: "unavailable", reason: NO_TYRE_V1 },
    summaryData: { state: "unavailable", reason: "A timeline is not available for imported captures." },
    setup: setupFromContexts(ordered.map((record) => record.player_car_setup_context)),
    context: {
      sessionType: context?.session_type ?? null,
      ruleSet: context?.rule_set ?? null,
      packetFormat: session.packet_format || null,
      trackName: context?.track_name ?? null,
      trackLengthM: context?.track_length_m ?? null,
      totalLaps: null,
      weather: context?.weather_name ?? null,
      airTemperatureC: null,
      trackTemperatureC: null,
      startedAtUtc: session.started_at_utc,
      endedAtUtc: session.finished_at_utc,
      durationS,
    },
  };
}

export type EvidenceExtras = {
  enrichment?: ReadResult<LapEnrichment>;
  summary?: ReadResult<SessionSummary>;
};

/** Session overview for a v2 session-evidence session; only derivable content is shown. */
export function evidenceModel(
  session: EvidenceSession,
  attempts: EvidenceAttempt[],
  truncated: boolean,
  footer: ShellFooter,
  extras: EvidenceExtras = {},
): OverviewModel {
  const context = evidenceSessionContext(session, attempts);
  const base = realBase({
    ...footer,
    lines: [formatGame(context?.packetFormat), footer.lines[1] ?? ""],
  });
  const title = context?.trackName ?? "Unknown circuit";
  const track = trackKeyFromName(title);
  const type = formatSessionType(context?.sessionType);
  const evidence = evidenceLaps(attempts);
  const baseLaps: OverviewLap[] = evidence.map((lap) => ({
    id: lap.id,
    lap: lap.lap,
    ms: lap.ms,
    valid: lap.valid,
    revisionId: lap.revisionId,
    sectors: null,
    sectorsReason: null,
    tyre: null,
    tyreReason: null,
    pit: lap.pit,
    startObserved: lap.startObserved,
    traceAvailable: lap.traceAvailable,
  }));

  const enrichmentRead = extras.enrichment ? sectionFromRead(extras.enrichment) : ({ state: "deferred" } as const);
  let laps = baseLaps;
  let enrichment: OverviewModel["enrichment"];
  if (enrichmentRead.state === "loaded") {
    const applied = applyEnrichment(baseLaps, enrichmentRead.value);
    laps = applied.laps;
    enrichment = { state: "loaded", value: { stints: applied.stints }, partialReason: applied.partialReason };
  } else {
    enrichment = enrichmentRead;
  }
  const summaryData = extras.summary ? sectionFromRead(extras.summary) : ({ state: "deferred" } as const);
  const startedAt =
    (summaryData.state === "loaded" ? summaryData.value.startedAtUtc : null) ?? cachedSummaryDate(session.id);

  const best = bestEvidenceLap(evidence);
  const bestId = best?.id ?? null;
  const selectedId = laps.at(-1)?.id ?? "";
  const shortId = session.id.slice(0, 8);
  const unfinished = [
    ...new Set(
      attempts.flatMap((attempt) =>
        attempt.role === "player" &&
        !attempt.superseded &&
        attempt.disposition !== "completed" &&
        attempt.lapNumber !== null
          ? [attempt.lapNumber]
          : [],
      ),
    ),
  ];
  const fields = realHeroFields({
    source: "v2",
    lapCount: laps.length,
    bestMs: best?.ms ?? null,
    conditions: conditionsLabel(context),
    trackTemperatureC: context?.trackTemperatureC ?? null,
    packetFormat: context?.packetFormat ?? null,
    trackLengthM: context?.trackLengthM ?? null,
    timeDrivenMs: timeDrivenMs(evidence),
    ruleSet: context?.ruleSet ?? null,
    partial: truncated,
  });
  const overviewContext: OverviewContext = {
    sessionType: context?.sessionType ?? null,
    ruleSet: context?.ruleSet ?? null,
    packetFormat: context?.packetFormat ?? null,
    trackName: context?.trackName ?? null,
    trackLengthM: context?.trackLengthM ?? null,
    totalLaps: context?.totalLaps ?? null,
    weather: context?.weatherName ?? null,
    airTemperatureC: context?.airTemperatureC ?? null,
    trackTemperatureC: context?.trackTemperatureC ?? null,
    startedAtUtc: null,
    endedAtUtc: null,
    durationS: null,
  };
  return {
    ...base,
    sessionKey: session.id,
    source: "v2",
    evidenceId: session.id,
    header: [title, type, startedAt ? { iso: startedAt } : `Session #${shortId}`],
    photo: trackPhotoUrl(track),
    hero: {
      title,
      track,
      lines: [heroDateLine(type, startedAt, shortId), [formatGame(context?.packetFormat), "Player"]],
      status: lifecycleStatus(session.lifecycle),
      stats: fields.stats,
      details: fields.details,
    },
    progression: laps.length ? progressionFrom(laps, selectedId, bestId) : EMPTY_PROGRESSION,
    events: [...lapEvents(laps, bestId), ...notCompletedEvents(unfinished)],
    lapDataNote: truncated ? "Only the first attempts of this session could be read, so some laps may be missing." : null,
    laps,
    selectedId,
    bestId,
    enrichment,
    summaryData,
    setup: null,
    context: overviewContext,
  };
}

export async function loadOverview(sessionKey: string): Promise<OverviewModel | null> {
  // The fixture session is a replay of the reference: its footer is the
  // reference session, never live receiver state (same as Lap Analysis).
  if (sessionKey === FIXTURE_SESSION_KEY) return fixtureModel(shellFixture.sessionFooter);
  const acquisition = await fetchAcquisition();
  // Receiver state unreadable: say so instead of showing a green reference footer.
  const footer: ShellFooter = acquisition
    ? telemetryFooter(acquisition, shellFixture.telemetryFooter)
    : { tone: TELEMETRY_OFFLINE.tone, label: TELEMETRY_OFFLINE.label, lines: ["F1", "Not reachable"] };
  const shell = { live: true, status: acquisition ? telemetryStatus(acquisition) : TELEMETRY_OFFLINE };
  const evidenceSession = await fetchEvidenceSession(sessionKey);
  if (evidenceSession) {
    // Attempts are required; `/laps` and `/summary` get a short budget and
    // otherwise continue in the browser (deferred sections).
    const [result, enrichment, summary] = await Promise.all([
      fetchEvidenceAttempts(evidenceSession.id),
      fetchLapEnrichment(evidenceSession.id, { timeoutMs: DEFERRED_BUDGET_MS }),
      fetchSummary(evidenceSession.id, { timeoutMs: DEFERRED_BUDGET_MS }),
    ]);
    return {
      ...evidenceModel(evidenceSession, result?.attempts ?? [], result === null || result.truncated, footer, {
        enrichment,
        summary,
      }),
      shell,
    };
  }
  const session = await fetchSession(sessionKey);
  if (!session) return null;
  const laps = (await fetchLaps(session)) ?? [];
  return { ...realModel(session, laps, footer), shell };
}

