import type { LapRecord, SessionRecord } from "@/lib/api";
import {
  completedLaps,
  fetchAcquisition,
  fetchLaps,
  fetchSession,
  sessionBestLap,
  telemetryFooter,
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
  formatSessionType,
  formatWeather,
} from "@/features/review-data/format";
import type { ShellFooter } from "@/features/ui-foundation/AppShell";
import type { Tone } from "@/features/ui-foundation/primitives";
import { trackKeyFromName } from "@/features/ui-foundation/TrackMap";
import { trackPhotoUrl } from "@/features/ui-foundation/track-photo-url";

export type OverviewLap = { id: string; lap: number; ms: number; valid: boolean };

export type OverviewModel = {
  sessionKey: string;
  header: string[];
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
  summary: typeof overviewFixture.summary;
  events: { total: number; items: NotableEvent[] };
  stints: Stint[];
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
    footer,
    photo: trackPhotoUrl(overviewFixture.hero.track),
    hero: overviewFixture.hero,
    progression: { ...overviewFixture.progression, yStep: 2000, illustrative: true },
    summary: overviewFixture.summary,
    events: overviewFixture.events,
    stints: overviewFixture.stints,
    laps: fixtureLaps(),
    selectedId: "28",
    bestId: "57",
  };
}

function progressionFrom(laps: OverviewLap[], selectedId: string, bestId: string | null) {
  const timed = laps.filter((lap) => lap.valid);
  const sorted = [...timed].map((lap) => lap.ms).sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)] ?? 0;
  // Exclude neutralised/pit laps that would flatten the racing laps.
  const plotted = timed.filter((lap) => lap.ms <= median + 6000);
  const min = Math.min(...plotted.map((lap) => lap.ms));
  const max = Math.max(...plotted.map((lap) => lap.ms));
  const yStep = max - min > 6000 ? 2000 : 1000;
  const yMin = Math.floor(min / yStep) * yStep;
  const yMax = Math.max(yMin + 4 * yStep, Math.ceil(max / yStep) * yStep);
  let personalBest = Number.POSITIVE_INFINITY;
  const points: ProgressionPoint[] = plotted.map((lap) => {
    let tone: ProgressionPoint["tone"] = "orange";
    if (lap.ms < personalBest) {
      personalBest = lap.ms;
      tone = "green";
    }
    if (lap.id === bestId) tone = "purple";
    if (lap.id === selectedId) tone = "blue";
    return { lap: lap.lap, ms: lap.ms, tone };
  });
  return {
    points,
    yMin,
    yMax,
    yStep,
    xMax: Math.max(10, ...plotted.map((lap) => lap.lap)),
    illustrative: false,
  };
}

function realModel(session: SessionRecord, records: LapRecord[], footer: ShellFooter): OverviewModel {
  const fixture = fixtureModel(footer);
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
    ...fixture,
    sessionKey: session.session_key,
    header: [title, formatSessionType(context?.session_type), formatDay(session.started_at_utc)],
    photo: trackPhotoUrl(track),
    hero: {
      ...fixture.hero,
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
        [weather ?? "—", "Conditions"],
        fixture.hero.stats[3],
      ],
      details: [
        fixture.hero.details[0],
        fixture.hero.details[1],
        fixture.hero.details[2],
        ["Rule Set", context?.rule_set ? formatSessionType(context.rule_set) : "—"],
        ["Data Source", "Game (Live)"],
      ],
    },
    progression: laps.length >= 2 ? progressionFrom(laps, selectedId, bestId) : fixture.progression,
    laps: laps.length ? laps : fixture.laps,
    selectedId: laps.length ? selectedId : fixture.selectedId,
    bestId: laps.length ? bestId : fixture.bestId,
  };
}

export async function loadOverview(sessionKey: string): Promise<OverviewModel | null> {
  const acquisition = await fetchAcquisition();
  const footer = acquisition
    ? telemetryFooter(acquisition, shellFixture.telemetryFooter)
    : shellFixture.telemetryFooter;
  if (sessionKey === FIXTURE_SESSION_KEY) return fixtureModel(footer);
  const session = await fetchSession(sessionKey);
  if (!session) return null;
  const laps = (await fetchLaps(session)) ?? [];
  return realModel(session, laps, footer);
}
