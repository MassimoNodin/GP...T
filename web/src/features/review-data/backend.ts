import { parseAcquisitionStatus, type AcquisitionStatus } from "@/lib/acquisition-status";
import { requestApi, type LapRecord, type SessionRecord } from "@/lib/api";
import { forwardLocalRequest } from "@/lib/local-proxy";
import { parseStorageUsageResponse, type StorageUsage } from "@/lib/storage-usage.mjs";
import type { ShellFooter, ShellStatus } from "@/features/ui-foundation/AppShell";

// Server-side reads from the local F1 Engineer API. Every reader returns null
// when the API is unreachable or reports the data unavailable, so screens can
// fall back to the reference fixture for that section.

export async function fetchSessions(): Promise<SessionRecord[] | null> {
  const response = await requestApi<SessionRecord[]>("/api/v1/sessions").catch(() => null);
  if (!response || response.status !== "ok" || !Array.isArray(response.data)) return null;
  // One entry per capture/session: the latest completed processing run wins.
  const latest = new Map<string, SessionRecord>();
  for (const item of response.data) {
    const key = `${item.capture_sha256}:${item.session_uid}`;
    const current = latest.get(key);
    const finished = item.finished_at_utc ?? item.started_at_utc;
    const currentFinished = current ? (current.finished_at_utc ?? current.started_at_utc) : "";
    const preferred =
      !current ||
      (item.run_status === "complete" && current.run_status !== "complete") ||
      (item.run_status === current.run_status && finished > currentFinished);
    if (preferred) latest.set(key, item);
  }
  return [...latest.values()].sort((a, b) => b.started_at_utc.localeCompare(a.started_at_utc));
}

export async function fetchSession(sessionKey: string): Promise<SessionRecord | null> {
  const sessions = await fetchSessions();
  return sessions?.find((item) => item.session_key === sessionKey) ?? null;
}

export async function fetchLaps(session: SessionRecord): Promise<LapRecord[] | null> {
  const query = new URLSearchParams({ run_id: session.run_id, session_uid: session.session_uid });
  const response = await requestApi<LapRecord[]>(`/api/v1/laps?${query}`).catch(() => null);
  if (!response || response.status !== "ok" || !Array.isArray(response.data)) return null;
  return response.data;
}

export async function fetchAcquisition(): Promise<AcquisitionStatus | null> {
  try {
    const response = await forwardLocalRequest("/api/v2/session-evidence/status", {
      signal: AbortSignal.timeout(2500),
    });
    if (!response.ok) return null;
    return parseAcquisitionStatus(await response.json());
  } catch {
    return null;
  }
}

export async function fetchStorage(): Promise<StorageUsage | null> {
  try {
    const response = await forwardLocalRequest("/api/v1/storage/usage", {
      signal: AbortSignal.timeout(2500),
    });
    if (!response.ok) return null;
    return parseStorageUsageResponse(await response.json());
  } catch {
    return null;
  }
}

/** Telemetry acquisition state expressed as a header pill. */
export function telemetryStatus(status: AcquisitionStatus): ShellStatus {
  switch (status.state) {
    case "receiving":
      return { tone: "green", label: "Telemetry live" };
    case "starting":
    case "listening":
      return { tone: "amber", label: "Waiting for game" };
    case "stale":
      return { tone: "amber", label: "Telemetry stale" };
    case "disabled":
      return { tone: "grey", label: "Telemetry disabled" };
    default:
      return { tone: "red", label: "Telemetry offline" };
  }
}

export function telemetryFooter(status: AcquisitionStatus, fallback: ShellFooter): ShellFooter {
  const pill = telemetryStatus(status);
  const detail =
    status.state === "receiving"
      ? "Ready"
      : status.state === "listening" || status.state === "starting"
        ? "Listening on UDP 20777"
        : status.state === "stale"
          ? "No recent packets"
          : "Not receiving";
  return { tone: pill.tone, label: pill.label, lines: [fallback.lines[0] ?? "", detail] };
}

export function completedLaps(laps: LapRecord[]): LapRecord[] {
  return laps
    .filter((lap) => lap.disposition === "completed" && typeof lap.lap_time_ms === "number")
    .sort((a, b) => a.lap_number - b.lap_number || a.attempt_number - b.attempt_number);
}

/** Fastest lap that the game reported valid and the pipeline allows as a reference. */
export function sessionBestLap(laps: LapRecord[]): LapRecord | null {
  let best: LapRecord | null = null;
  for (const lap of completedLaps(laps)) {
    if (lap.game_valid === false || !lap.reference_eligible) continue;
    if (!best || (lap.lap_time_ms as number) < (best.lap_time_ms as number)) best = lap;
  }
  return best;
}
