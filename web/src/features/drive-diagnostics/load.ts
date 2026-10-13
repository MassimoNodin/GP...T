import { driveFixture, type RadioEvent } from "@/features/review-data/fixtures";
import { fetchEngineerEvents, fetchPreferences } from "@/features/review-data/backend";
import type { EngineerEvent } from "@/features/review-data/engineer-events";
import {
  ENGINE_OFFLINE_FOOTER,
  engineerShell,
  isReachable,
  parseRuntimeInfo,
  type RuntimeInfo,
} from "@/features/live-status/map";
import { parseAcquisitionStatus, type AcquisitionStatus } from "@/lib/acquisition-status";
import { forwardLocalRequest } from "@/lib/local-proxy";
import type { ShellFooter, ShellStatus } from "@/features/ui-foundation/AppShell";
import { trackKeyFromName } from "@/features/ui-foundation/TrackMap";
import { trackPhotoUrl } from "@/features/ui-foundation/track-photo-url";
import type { Interruption } from "./radio";
import type { FeedStatus } from "./radio-feed";
import {
  contextSubtitle,
  pickCurrentSession,
  sessionContextModel,
  type EvidenceSession,
  type SessionContextModel,
} from "./session";
import { parseSpeechStatus, proactiveState, type ProactiveState, type SpeechState } from "./system-rows";

export type DriveContext =
  | {
      kind: "session";
      title: string;
      subtitle: string[];
      track: string;
      facts: Array<[string, string]>;
      sessionId: string;
      /** True when the session is the one the receiver is serving now. */
      current: boolean;
    }
  | {
      /** Reference design content, shown only while the local engine is unreachable. */
      kind: "reference";
      title: string;
      subtitle: string[];
      track: string;
      facts: Array<[string, string]>;
    }
  | { kind: "empty" };

export type DriveModel = {
  /** True when the local engine did not answer at all: reference content and a banner. */
  reference: boolean;
  header: { items: string[]; trailing?: string };
  status: ShellStatus;
  footer: ShellFooter;
  context: DriveContext;
  photo: string | null;
  radio: {
    state: FeedStatus;
    reason: string | null;
    /** Oldest first. Empty on the reference path. */
    events: EngineerEvent[];
    nextAfter: number | null;
    /** Reference rows; only filled on the reference path. */
    fixture: RadioEvent[];
  };
  /** Backend interruption preference; read-aloud follows it. */
  interruption: Interruption;
  proactive: ProactiveState;
  speech: SpeechState;
  /** Server-read state the client starts from; null = the backend did not answer. */
  initial: { acquisition: AcquisitionStatus | null; runtime: RuntimeInfo | null };
  /** Epoch ms and zone the server rendered with, so the first client render matches. */
  generatedAt: number;
  timeZone: string;
};

const TIMEOUT_MS = 2500;
const RADIO_PAGE = 50;

async function readJson(path: string): Promise<unknown> {
  try {
    const response = await forwardLocalRequest(path, { signal: AbortSignal.timeout(TIMEOUT_MS) });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

function evidenceSessions(body: unknown): EvidenceSession[] {
  const data = (body as { data?: unknown } | null)?.data;
  if (!Array.isArray(data)) return [];
  return data.flatMap((item): EvidenceSession[] => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    if (typeof row.id !== "string" || typeof row.lifecycle !== "string") return [];
    return [
      {
        id: row.id,
        lifecycle: row.lifecycle,
        acquisition: typeof row.acquisition === "string" ? row.acquisition : "",
        context: row.context,
      },
    ];
  });
}

function statusSessionId(body: unknown): string | null {
  const detail = (body as { detail_processing?: { session_id?: unknown } } | null)?.detail_processing;
  return typeof detail?.session_id === "string" ? detail.session_id : null;
}

/** Drive session context for the current or most recent v2 session, from raw API bodies. */
export function currentSessionContext(statusBody: unknown, sessionsBody: unknown): SessionContextModel | null {
  const sessions = evidenceSessions(sessionsBody);
  const picked = pickCurrentSession(sessions, statusSessionId(statusBody));
  const model = picked ? sessionContextModel(picked, trackKeyFromName) : null;
  if (model || !picked || picked.current) return model;
  // A past session without context (no circuit) says nothing useful; fall back
  // to the latest session that has one.
  for (const session of [...sessions].reverse()) {
    const fallback = sessionContextModel({ session, current: false }, trackKeyFromName);
    if (fallback) return fallback;
  }
  return null;
}

/** Reads the current or most recent v2 session for screens other than Drive. */
export async function loadCurrentSession(statusBody: unknown): Promise<SessionContextModel | null> {
  return currentSessionContext(statusBody, await readJson("/api/v2/session-evidence/sessions?limit=100"));
}

export async function loadDrive(): Promise<DriveModel> {
  const [statusBody, runtimeBody, sessionsBody, speechBody, events, preferences] = await Promise.all([
    readJson("/api/v2/session-evidence/status"),
    readJson("/api/v1/engineer/runtime"),
    readJson("/api/v2/session-evidence/sessions?limit=100"),
    readJson("/api/v1/engineer/transcribe"),
    fetchEngineerEvents({ limit: RADIO_PAGE }),
    fetchPreferences(),
  ]);
  const acquisition = parseAcquisitionStatus(statusBody);
  const runtime = parseRuntimeInfo(runtimeBody);

  // Reference content only when nothing at all answered.
  const engineReachable =
    isReachable(acquisition, runtime) ||
    sessionsBody !== null ||
    speechBody !== null ||
    events.state !== "unreachable" ||
    preferences.state !== "unreachable";
  const reference = !engineReachable;

  const real = reference ? null : currentSessionContext(statusBody, sessionsBody);

  let context: DriveContext;
  let header: DriveModel["header"];
  let footerLines: string[];
  let track: string | null;
  if (reference) {
    context = {
      kind: "reference",
      title: driveFixture.context.title,
      subtitle: driveFixture.context.subtitle,
      track: driveFixture.context.track,
      facts: driveFixture.context.facts,
    };
    header = driveFixture.header;
    footerLines = ["Start the local engine", ""];
    track = driveFixture.context.track;
  } else if (real) {
    context = {
      kind: "session",
      title: real.title,
      subtitle: contextSubtitle(real),
      track: real.track,
      facts: real.facts,
      sessionId: real.id,
      current: real.current,
    };
    header = { items: real.headerItems };
    footerLines = real.footerLines;
    track = real.track;
  } else {
    context = { kind: "empty" };
    header = { items: [] };
    footerLines = ["No session yet", "Waiting for the game"];
    track = null;
  }

  const shell = engineerShell(runtime, footerLines);
  const radio: DriveModel["radio"] =
    events.state === "ok"
      ? { state: "ok", reason: null, events: events.page.events, nextAfter: events.page.nextAfter, fixture: [] }
      : {
          state: events.state,
          reason: events.state === "unavailable" ? events.reason : null,
          events: [],
          nextAfter: null,
          fixture: reference ? driveFixture.radio : [],
        };

  const speechParsed = parseSpeechStatus(speechBody);
  const speech: SpeechState = speechParsed ?? (reference ? { kind: "unreachable" } : { kind: "checking" });

  return {
    reference,
    header,
    status: shell.status,
    footer: reference ? ENGINE_OFFLINE_FOOTER : shell.footer,
    context,
    photo: track ? trackPhotoUrl(track) : null,
    radio,
    interruption: preferences.state === "ok" ? preferences.stored.doc.interruption : "allow",
    proactive: proactiveState(preferences.state === "ok" ? preferences.stored.doc.topics : null),
    speech,
    initial: { acquisition, runtime },
    generatedAt: Date.now(),
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
}
