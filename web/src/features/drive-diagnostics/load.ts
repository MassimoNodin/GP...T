import { driveFixture, shellFixture, type RadioEvent } from "@/features/review-data/fixtures";
import {
  buildSystemRows,
  currentActivity,
  engineerShell,
  parseRuntimeInfo,
  type RuntimeInfo,
  type StatusRow,
} from "@/features/live-status/map";
import { parseAcquisitionStatus, type AcquisitionStatus } from "@/lib/acquisition-status";
import { forwardLocalRequest } from "@/lib/local-proxy";
import type { ShellFooter, ShellStatus } from "@/features/ui-foundation/AppShell";
import { trackKeyFromName } from "@/features/ui-foundation/TrackMap";
import { trackPhotoUrl } from "@/features/ui-foundation/track-photo-url";
import {
  pickCurrentSession,
  sessionContextModel,
  type EvidenceSession,
  type SessionContextModel,
} from "./session";

export type DriveModel = {
  header: { items: string[]; trailing?: string };
  status: ShellStatus;
  footer: ShellFooter;
  /** System rows as rendered on the server (live rows already applied). */
  system: StatusRow[];
  /** Reference rows the client re-applies live state to; rows without a backend source keep these values. */
  referenceSystem: StatusRow[];
  activity: string;
  context: {
    title: string;
    subtitle: string[];
    track: string;
    facts: Array<[string, string]>;
    /** "session" when read from a v2 session, "reference" for the fixture. */
    origin: "session" | "reference";
  };
  photo: string | null;
  radio: RadioEvent[];
  /** Server-read state the client starts from; null = the backend did not answer. */
  initial: { acquisition: AcquisitionStatus | null; runtime: RuntimeInfo | null };
};

const TIMEOUT_MS = 2500;

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
  const picked = pickCurrentSession(evidenceSessions(sessionsBody), statusSessionId(statusBody));
  return picked ? sessionContextModel(picked, trackKeyFromName) : null;
}

/** Reads the current or most recent v2 session for screens other than Drive. */
export async function loadCurrentSession(statusBody: unknown): Promise<SessionContextModel | null> {
  return currentSessionContext(statusBody, await readJson("/api/v2/session-evidence/sessions?limit=100"));
}

export async function loadDrive(): Promise<DriveModel> {
  const [statusBody, runtimeBody, sessionsBody] = await Promise.all([
    readJson("/api/v2/session-evidence/status"),
    readJson("/api/v1/engineer/runtime"),
    readJson("/api/v2/session-evidence/sessions?limit=100"),
  ]);
  const acquisition = parseAcquisitionStatus(statusBody);
  const runtime = parseRuntimeInfo(runtimeBody);

  const real = currentSessionContext(statusBody, sessionsBody);

  const system = buildSystemRows(driveFixture.system, acquisition, runtime);
  const activity = currentActivity(driveFixture.activity, acquisition, runtime).text;

  const context = real
    ? {
        title: real.title,
        subtitle: real.subtitle,
        track: real.track,
        facts: real.facts,
        origin: "session" as const,
      }
    : {
        title: driveFixture.context.title,
        subtitle: driveFixture.context.subtitle,
        track: driveFixture.context.track,
        facts: driveFixture.context.facts,
        origin: "reference" as const,
      };

  const footerLines = real ? real.footerLines : shellFixture.sessionFooter.lines;
  const shell = engineerShell(runtime, footerLines);

  return {
    header: real ? { items: real.headerItems } : driveFixture.header,
    status: shell.status,
    footer: shell.footer,
    system,
    referenceSystem: driveFixture.system,
    activity,
    context,
    photo: trackPhotoUrl(context.track),
    radio: driveFixture.radio,
    initial: { acquisition, runtime },
  };
}
