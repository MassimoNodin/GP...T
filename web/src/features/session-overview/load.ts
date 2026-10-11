import type { LapRecord, SessionRecord } from "@/lib/api";
import {
  completedLaps,
  fetchAcquisition,
  fetchEvidenceAttempts,
  fetchEvidenceSession,
  fetchLaps,
  fetchSession,
  sessionBestLap,
  telemetryFooter,
  telemetryStatus,
  TELEMETRY_OFFLINE,
} from "@/features/review-data/backend";
import {
  FIXTURE_SESSION_KEY,
  overviewFixture,
  shellFixture,
  type NotableEvent,
  type ProgressionPoint,
  type Stint,
} from "@/features/review-data/fixtures";
import {
  formatDay,
  formatDayTime,
  formatDuration,
  formatGame,
  formatLapTime,
  formatSessionType,
  formatWeather,
} from "@/features/review-data/format";
import {
  bestEvidenceLap,
  conditionsLabel,
  EMPTY_PROGRESSION,
  evidenceEvents,
  evidenceLaps,
  evidenceSessionContext,
  formatTrackLength,
  lifecycleStatus,
  progressionFrom,
  timeDrivenMs,
  type EvidenceAttempt,
  type EvidenceSession,
} from "@/features/review-data/session-evidence";
import type { ShellFooter, ShellStatus } from "@/features/ui-foundation/AppShell";
import type { Tone } from "@/features/ui-foundation/primitives";
import { trackKeyFromName } from "@/features/ui-foundation/TrackMap";
import { trackPhotoUrl } from "@/features/ui-foundation/track-photo-url";

export type OverviewLap = {
  id: string;
  lap: number;
  ms: number;
  valid: boolean;
  /** v2 attempt revision id, for fetching the lap's trace. */
  revisionId?: string;
};

/** Shown where a real session has no derivable content for a section. */
export const NOT_AVAILABLE = "—";

export type OverviewModel = {
  sessionKey: string;
  header: string[];
  /** Header pill; `live` keeps it in sync with telemetry acquisition. */
  shell: { live: boolean; status: ShellStatus };
  footer: ShellFooter;
  photo: string | null;
  hero: {
    title: string;
    track: string;
    lines: string[][];
    status: { label: string; tone: Tone };
    stats: Array<[string, string]>;
    details: Array<[string, string]>;
  };
  progression: {
    points: ProgressionPoint[];
    yMin: number;
    yMax: number;
    yStep: number;
    xMax: number;
    /** Fixture charts follow the reference drawing rather than the lap list. */
    illustrative: boolean;
  };
  /** Null on real sessions that cannot supply the section. */
  summary: typeof overviewFixture.summary | null;
  events: { total: number; items: NotableEvent[] } | null;
  stints: Stint[] | null;
  /** Set when lap data is known to be incomplete. */
  lapDataNote: string | null;
  laps: OverviewLap[];
  selectedId: string;
  bestId: string | null;
};

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
    laps.push({ id: String(lap), lap, ms: Math.round(seconds * 1000), valid: true });
  }
  return laps;
}

function fixtureModel(footer: ShellFooter): OverviewModel {
  return {
    sessionKey: FIXTURE_SESSION_KEY,
    header: overviewFixture.header.items,
    shell: { live: false, status: shellFixture.replayMode },
    footer,
    photo: trackPhotoUrl(overviewFixture.hero.track),
    hero: overviewFixture.hero,
    progression: { ...overviewFixture.progression, yStep: 2000, illustrative: true },
    summary: overviewFixture.summary,
    events: overviewFixture.events,
    stints: overviewFixture.stints,
    lapDataNote: null,
    laps: fixtureLaps(),
    selectedId: "28",
    bestId: "57",
  };
}

/** Placeholder model parts for real sessions: no other circuit's reference content. */
function realBase(footer: ShellFooter) {
  const fixture = fixtureModel(footer);
  return {
    ...fixture,
    summary: null,
    events: null,
    stints: null,
    lapDataNote: null,
  };
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
  }));
  const best = sessionBestLap(records);
  const bestId = best?.attempt_key ?? null;
  const selectedId = laps.at(-1)?.id ?? "";
  const weather = formatWeather(context?.weather_name);
  const started = Date.parse(session.started_at_utc);
  const finished = session.finished_at_utc ? Date.parse(session.finished_at_utc) : Number.NaN;
  return {
    ...base,
    sessionKey: session.session_key,
    header: [title, formatSessionType(context?.session_type), formatDay(session.started_at_utc)],
    photo: trackPhotoUrl(track),
    hero: {
      ...base.hero,
      title,
      track,
      lines: [
        [formatSessionType(context?.session_type), formatDayTime(session.started_at_utc)],
        [formatGame(session.packet_format), "Player"],
      ],
      status:
        session.run_status === "complete"
          ? { label: "Complete", tone: "green" }
          : { label: formatSessionType(session.run_status), tone: "amber" },
      stats: [
        [String(laps.length), "Laps"],
        [formatDuration(finished - started), "Duration"],
        [weather ?? NOT_AVAILABLE, "Conditions"],
        [NOT_AVAILABLE, "Track Grip"],
      ],
      details: [
        ["Game Build", NOT_AVAILABLE],
        ["Assists", NOT_AVAILABLE],
        ["Damage", NOT_AVAILABLE],
        ["Rule Set", context?.rule_set ? formatSessionType(context.rule_set) : NOT_AVAILABLE],
        ["Data Source", "Game (Live)"],
      ],
    },
    progression: laps.length ? progressionFrom(laps, selectedId, bestId) : EMPTY_PROGRESSION,
    laps,
    selectedId: laps.length ? selectedId : "",
    bestId: laps.length ? bestId : null,
  };
}

/** Session overview for a v2 session-evidence session; only derivable content is shown. */
export function evidenceModel(
  session: EvidenceSession,
  attempts: EvidenceAttempt[],
  truncated: boolean,
  footer: ShellFooter,
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
  const laps: OverviewLap[] = evidence.map((lap) => ({
    id: lap.id,
    lap: lap.lap,
    ms: lap.ms,
    valid: lap.valid,
    revisionId: lap.revisionId,
  }));
  const best = bestEvidenceLap(evidence);
  const bestId = best?.id ?? null;
  const selectedId = laps.at(-1)?.id ?? "";
  const events = evidenceEvents(attempts, evidence);
  const invalid = evidence.filter((lap) => !lap.valid).length;
  const shortId = session.id.slice(0, 8);
  const trackTemperature =
    context?.trackTemperatureC !== null && context?.trackTemperatureC !== undefined
      ? `${Math.round(context.trackTemperatureC)}°C`
      : NOT_AVAILABLE;
  return {
    ...base,
    sessionKey: session.id,
    header: [title, type, `Session #${shortId}`],
    photo: trackPhotoUrl(track),
    hero: {
      ...base.hero,
      title,
      track,
      lines: [
        [type, "Date not recorded", `#${shortId}`],
        [formatGame(context?.packetFormat), "Player"],
      ],
      status: lifecycleStatus(session.lifecycle),
      stats: [
        [String(laps.length), "Laps"],
        [best ? formatLapTime(best.ms) : NOT_AVAILABLE, "Best Lap"],
        [conditionsLabel(context) ?? NOT_AVAILABLE, "Conditions"],
        [trackTemperature, "Track Temp"],
      ],
      details: [
        ["Game", context?.packetFormat ? formatGame(context.packetFormat) : NOT_AVAILABLE],
        ["Track Length", formatTrackLength(context?.trackLengthM)],
        ["Time Driven", formatDuration(timeDrivenMs(evidence))],
        ["Rule Set", context?.ruleSet ? formatSessionType(context.ruleSet) : NOT_AVAILABLE],
        ["Data Source", truncated ? "Game (Live, partial)" : "Game (Live)"],
      ],
    },
    progression: laps.length ? progressionFrom(laps, selectedId, bestId) : EMPTY_PROGRESSION,
    summary: best
      ? {
          text: `${laps.length} timed ${laps.length === 1 ? "lap" : "laps"} recorded. Best lap ${formatLapTime(best.ms)} on lap ${best.lap}.`,
          items: invalid
            ? [{ tone: "amber" as const, text: `${invalid} ${invalid === 1 ? "lap" : "laps"} invalidated by the game` }]
            : [],
        }
      : null,
    events: { total: events.length, items: events },
    stints: null,
    lapDataNote: truncated ? "Only the first attempts of this session could be read, so some laps may be missing." : null,
    laps,
    selectedId,
    bestId,
  };
}

export async function loadOverview(sessionKey: string): Promise<OverviewModel | null> {
  const acquisition = await fetchAcquisition();
  const footer = acquisition
    ? telemetryFooter(acquisition, shellFixture.telemetryFooter)
    : shellFixture.telemetryFooter;
  if (sessionKey === FIXTURE_SESSION_KEY) return fixtureModel(footer);
  const shell = { live: true, status: acquisition ? telemetryStatus(acquisition) : TELEMETRY_OFFLINE };
  const evidenceSession = await fetchEvidenceSession(sessionKey);
  if (evidenceSession) {
    const result = await fetchEvidenceAttempts(evidenceSession.id);
    return {
      ...evidenceModel(evidenceSession, result?.attempts ?? [], result === null || result.truncated, footer),
      shell,
    };
  }
  const session = await fetchSession(sessionKey);
  if (!session) return null;
  const laps = (await fetchLaps(session)) ?? [];
  return { ...realModel(session, laps, footer), shell };
}
