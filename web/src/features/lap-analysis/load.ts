import type { LapRecord } from "@/lib/api";
import {
  completedLaps,
  fetchLaps,
  fetchSession,
  sessionBestLap,
} from "@/features/review-data/backend";
import { FIXTURE_SESSION_KEY, lapAnalysisFixture, shellFixture } from "@/features/review-data/fixtures";
import { formatSessionType } from "@/features/review-data/format";
import { fixtureLaps } from "@/features/session-overview/load";
import type { ShellFooter } from "@/features/ui-foundation/AppShell";
import { trackKeyFromName } from "@/features/ui-foundation/TrackMap";

export type AnalysisLap = {
  id: string;
  lap: number;
  ms: number;
  /** Sector times when the game reported them for this lap. */
  sectors: [number, number, number] | null;
  /** Attempt key for the local engine; null for reference fixture laps. */
  attemptKey: string | null;
};

export type LapAnalysisModel = {
  sessionKey: string;
  live: boolean;
  header: string[];
  footer: ShellFooter;
  track: string;
  trackKey: string;
  laps: AnalysisLap[];
  targetId: string;
  referenceId: string | null;
  bestId: string | null;
};

function fixtureModel(footer: ShellFooter): LapAnalysisModel {
  const sectorsByLap: Record<number, [number, number, number]> = {
    28: [18420, 35128, 19872],
    // Reference sectors chosen so each delta matches the corrected table:
    // S1 -0.210, S2 +0.942, S3 -0.154 (sum +0.578).
    57: [18630, 34186, 20026],
  };
  return {
    sessionKey: FIXTURE_SESSION_KEY,
    live: false,
    header: ["Monaco Grand Prix", "Race"],
    footer,
    track: lapAnalysisFixture.track,
    trackKey: lapAnalysisFixture.trackKey,
    laps: fixtureLaps().map((lap) => ({
      id: lap.id,
      lap: lap.lap,
      ms: lap.ms,
      sectors: sectorsByLap[lap.lap] ?? null,
      attemptKey: null,
    })),
    targetId: lapAnalysisFixture.target.id,
    referenceId: lapAnalysisFixture.reference.id,
    bestId: lapAnalysisFixture.reference.id,
  };
}

function sectorsOf(lap: LapRecord): [number, number, number] | null {
  const timing = lap.timing_evidence;
  const s1 = timing?.sector1_time_ms;
  const s2 = timing?.sector2_time_ms;
  const s3 = timing?.sector3_time_ms;
  if (typeof s1 === "number" && typeof s2 === "number" && typeof s3 === "number") return [s1, s2, s3];
  return null;
}

export async function loadLapAnalysis(
  sessionKey: string,
  target: string | undefined,
  reference: string | undefined,
): Promise<LapAnalysisModel | null> {
  const footer: ShellFooter = shellFixture.sessionFooter;
  const pick = (model: LapAnalysisModel) => {
    const has = (id: string | undefined) => id !== undefined && model.laps.some((lap) => lap.id === id);
    return {
      ...model,
      targetId: has(target) ? (target as string) : model.targetId,
      referenceId: has(reference) ? (reference as string) : model.referenceId,
    };
  };
  if (sessionKey === FIXTURE_SESSION_KEY) return pick(fixtureModel(footer));

  const session = await fetchSession(sessionKey);
  if (!session) return null;
  const records = (await fetchLaps(session)) ?? [];
  const completed = completedLaps(records);
  if (!completed.length) return pick({ ...fixtureModel(footer), sessionKey });
  const title = session.context?.track_name ?? "Unknown circuit";
  const best = sessionBestLap(records);
  const laps: AnalysisLap[] = completed.map((lap) => ({
    id: lap.attempt_key,
    lap: lap.lap_number,
    ms: lap.lap_time_ms as number,
    sectors: sectorsOf(lap),
    attemptKey: lap.attempt_key,
  }));
  const fallbackTarget = laps.at(-1)?.id ?? "";
  return pick({
    sessionKey,
    live: true,
    header: [title, formatSessionType(session.context?.session_type)],
    footer: {
      tone: "green",
      label: "Engineer running",
      lines: [title, `${formatSessionType(session.context?.session_type)}  •  ${laps.length} laps`],
    },
    track: title,
    trackKey: trackKeyFromName(title),
    laps,
    targetId: fallbackTarget,
    referenceId: best?.attempt_key ?? null,
    bestId: best?.attempt_key ?? null,
  });
}
