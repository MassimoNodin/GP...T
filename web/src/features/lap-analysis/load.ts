import type { LapRecord } from "@/lib/api";
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
  bestEvidenceLap,
  evidenceLaps,
  evidenceSessionContext,
  type EvidenceAttempt,
  type EvidenceSession,
} from "@/features/review-data/session-evidence";
import { FIXTURE_SESSION_KEY, lapAnalysisFixture, shellFixture } from "@/features/review-data/fixtures";
import { formatGame, formatSessionType } from "@/features/review-data/format";
import { fixtureLaps } from "@/features/session-overview/load";
import type { RegionModel } from "@/features/review-data/regions";
import type { ShellFooter, ShellStatus } from "@/features/ui-foundation/AppShell";
import { trackKeyFromName } from "@/features/ui-foundation/TrackMap";
import { unavailableTrace, type LapTelemetry, type LapTrace } from "./lap-telemetry";
import { fetchEvidenceTrace, fetchPreviewTrace, fetchTrackModelLabel } from "./trace-data";

export type AnalysisLap = {
  id: string;
  lap: number;
  ms: number;
  /** Sector times when the game reported them for this lap. */
  sectors: [number, number, number] | null;
  /**
   * v1 attempt key accepted by `POST /api/v1/engineer/ask`; null for reference
   * fixture laps and for v2 laps (v1 ask cannot resolve v2 attempt ids).
   */
  attemptKey: string | null;
  /**
   * v2 attempt revision id for `GET /api/v2/session-evidence/sessions/{session}/attempts/{revision}`;
   * null for fixture and v1 laps.
   */
  revisionId: string | null;
  /** Whether the v2 trace is already materialised (`detail.state === "available"`). */
  traceAvailable?: boolean;
  /** Game validity verdict; null or absent when the backend did not report one. */
  valid?: boolean | null;
  /** Lap included the pit lane. */
  pit?: boolean;
  /** False when the start of the lap was not observed. */
  startObserved?: boolean | null;
  /** Why sector times are missing (humanised); set by the v2 `/laps` enrichment. */
  sectorsReason?: string | null;
  /** Where the sector times came from. */
  sectorsSource?: "session_history" | "lap_data" | "timing_evidence" | null;
  /** Tyre fitted on this lap (v2 enrichment). */
  tyre?: LapTyre | null;
  tyreReason?: string | null;
};

export type LapTyre = { label: string; actual: string | null; ageLaps: number | null };

/** Corner regions of the circuit for this session. */
export type RegionSource =
  | { state: "available"; model: RegionModel }
  /** The client reads them through the proxy (v2 only). */
  | { state: "deferred" }
  | { state: "none"; reason: string }
  | { state: "reference" };

/** Sector and tyre enrichment of v2 laps. */
export type EnrichmentState =
  | { state: "none" }
  | { state: "loaded"; truncated: boolean }
  | { state: "deferred" }
  | { state: "unavailable"; reason: string };

export type LapAnalysisModel = {
  sessionKey: string;
  live: boolean;
  /** Header pill; real sessions keep it in sync with telemetry acquisition. */
  status: ShellStatus;
  header: string[];
  footer: ShellFooter;
  track: string;
  trackKey: string;
  laps: AnalysisLap[];
  targetId: string;
  referenceId: string | null;
  bestId: string | null;
  /** Backend that supplied the laps. */
  source: "fixture" | "v1" | "v2";
  /** Set when lap data is known to be incomplete. */
  lapDataNote: string | null;
  /** Fixture: illustrative traces. Real sessions: measured traces for the selected laps. */
  telemetry: LapTelemetry;
  /** Registered track model for the circuit of a real session; null when none matches. */
  trackModel: string | null;
  /** Track length when the recording reports it. */
  trackLengthM: number | null;
  /** Raw v1 session type (`time_trial`, ...), for the v1 comparison policy. */
  sessionType: string | null;
  /** v1 processing run id, to find the original capture. */
  runId: string | null;
  regions: RegionSource;
  enrichment: EnrichmentState;
  /** The engineer runtime reports `ready`, so a model may answer. */
  runtimeReady: boolean;
  /** Original capture download (v1 sessions whose capture is in the catalog). */
  capture: { captureId: string; version: string } | null;
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
    status: shellFixture.replayMode,
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
      revisionId: null,
    })),
    targetId: lapAnalysisFixture.target.id,
    referenceId: lapAnalysisFixture.reference.id,
    bestId: lapAnalysisFixture.reference.id,
    source: "fixture",
    lapDataNote: null,
    telemetry: { kind: "reference" },
    trackModel: null,
    trackLengthM: null,
    sessionType: null,
    runId: null,
    regions: { state: "reference" },
    enrichment: { state: "none" },
    runtimeReady: false,
    capture: null,
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

  const acquisition = await fetchAcquisition();
  const live = async (
    model: LapAnalysisModel,
    readTrace: (lap: AnalysisLap) => Promise<LapTrace>,
    trackLengthM: number | null,
  ): Promise<LapAnalysisModel> => {
    const picked = pick(model);
    return {
      ...picked,
      status: acquisition ? telemetryStatus(acquisition) : TELEMETRY_OFFLINE,
      footer: acquisition ? telemetryFooter(acquisition, picked.footer) : picked.footer,
      telemetry: await selectedTraces(picked, readTrace),
      trackModel: await fetchTrackModelLabel(picked.track === UNKNOWN_CIRCUIT ? null : picked.track, trackLengthM),
      trackLengthM,
    };
  };

  const evidenceSession = await fetchEvidenceSession(sessionKey);
  if (evidenceSession) {
    const result = await fetchEvidenceAttempts(evidenceSession.id);
    const model = evidenceAnalysisModel(evidenceSession, result?.attempts ?? [], result === null || result.truncated);
    const length = evidenceSessionContext(evidenceSession, result?.attempts ?? [])?.trackLengthM ?? null;
    return live(
      model,
      (lap) =>
        lap.revisionId && lap.traceAvailable
          ? fetchEvidenceTrace(evidenceSession.id, lap.revisionId)
          : Promise.resolve(unavailableTrace("not_processed")),
      length,
    );
  }

  const session = await fetchSession(sessionKey);
  if (!session) return null;
  const records = (await fetchLaps(session)) ?? [];
  const completed = completedLaps(records);
  const title = session.context?.track_name ?? UNKNOWN_CIRCUIT;
  const best = sessionBestLap(records);
  const laps: AnalysisLap[] = completed.map((lap) => ({
    id: lap.attempt_key,
    lap: lap.lap_number,
    ms: lap.lap_time_ms as number,
    sectors: sectorsOf(lap),
    attemptKey: lap.attempt_key,
    revisionId: null,
    valid: lap.game_valid,
    pit: lap.pit_encountered,
    startObserved: lap.start_observed,
  }));
  return live(
    {
      ...realShell(sessionKey, title, formatSessionType(session.context?.session_type), formatGame(session.packet_format)),
      laps,
      targetId: laps.at(-1)?.id ?? "",
      referenceId: best?.attempt_key ?? null,
      bestId: best?.attempt_key ?? null,
      source: "v1",
      sessionType: session.context?.session_type ?? null,
      runId: session.run_id ?? null,
    },
    (lap) => (lap.attemptKey ? fetchPreviewTrace(lap.attemptKey) : Promise.resolve(unavailableTrace(null))),
    session.context?.track_length_m ?? null,
  );
}

const UNKNOWN_CIRCUIT = "Unknown circuit";

/**
 * Measured traces for the laps currently selected as target and reference,
 * read one at a time (the backend admits a single trace read). The client
 * compares the keys with its pickers, so a lap missing here means "loading".
 */
async function selectedTraces(
  model: LapAnalysisModel,
  readTrace: (lap: AnalysisLap) => Promise<LapTrace>,
): Promise<LapTelemetry> {
  const traces: Record<string, LapTrace> = {};
  for (const id of new Set([model.targetId, model.referenceId])) {
    const lap = id ? model.laps.find((item) => item.id === id) : undefined;
    if (lap) traces[lap.id] = await readTrace(lap);
  }
  return { kind: "real", traces };
}

/** Model parts shared by real sessions; never carries reference-fixture content. */
function realShell(sessionKey: string, title: string, type: string, game: string): LapAnalysisModel {
  return {
    sessionKey,
    live: true,
    // Replaced from telemetry acquisition by loadLapAnalysis.
    status: TELEMETRY_OFFLINE,
    header: [title, type],
    footer: { ...TELEMETRY_OFFLINE, lines: [game, "Not reachable"] },
    track: title,
    trackKey: trackKeyFromName(title),
    laps: [],
    targetId: "",
    referenceId: null,
    bestId: null,
    source: "v1",
    lapDataNote: null,
    telemetry: { kind: "real", traces: {} },
    trackModel: null,
    trackLengthM: null,
    sessionType: null,
    runId: null,
    // Filled in by `extendLapAnalysis` (load-extras.ts) on the server.
    regions: { state: "none", reason: "compatible_track_model_unavailable" },
    enrichment: { state: "none" },
    runtimeReady: false,
    capture: null,
  };
}

/** Lap Analysis model for a v2 session: player laps only, no sector times in v2. */
export function evidenceAnalysisModel(
  session: EvidenceSession,
  attempts: EvidenceAttempt[],
  truncated: boolean,
): LapAnalysisModel {
  const context = evidenceSessionContext(session, attempts);
  const title = context?.trackName ?? UNKNOWN_CIRCUIT;
  const evidence = evidenceLaps(attempts);
  const best = bestEvidenceLap(evidence);
  const laps: AnalysisLap[] = evidence.map((lap) => ({
    id: lap.id,
    lap: lap.lap,
    ms: lap.ms,
    sectors: lap.sectors,
    attemptKey: null,
    revisionId: lap.revisionId,
    traceAvailable: lap.traceAvailable,
    valid: lap.valid,
    pit: lap.pit,
    startObserved: lap.startObserved,
  }));
  return {
    ...realShell(session.id, title, formatSessionType(context?.sessionType), formatGame(context?.packetFormat)),
    laps,
    targetId: laps.at(-1)?.id ?? "",
    referenceId: best?.id ?? null,
    bestId: best?.id ?? null,
    source: "v2",
    enrichment: { state: "deferred" },
    lapDataNote: truncated ? "Only the first attempts of this session could be read, so some laps may be missing." : null,
  };
}
