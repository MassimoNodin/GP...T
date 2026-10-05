import type {
  LiveCarStatusRecord,
  LiveLapTimingRecord,
  LiveTelemetryRecord,
  SessionContext,
} from "@/lib/api";

export type LiveSource = "recording" | "replay";

export interface PinnedLiveSnapshot {
  source: LiveSource;
  operation_id: string;
  state: string;
  context: SessionContext | null;
  live_telemetry: LiveTelemetryRecord | null;
  live_car_status: LiveCarStatusRecord | null;
  live_lap_timing: LiveLapTimingRecord | null;
  capture_name: string | null;
  speed: number | null;
}

export type PinnedLiveRead =
  | { status: "ready"; snapshot: PinnedLiveSnapshot }
  | { status: "unavailable" | "replaced" | "error"; snapshot: null };

const monitorStates = new Set([
  "waiting",
  "fresh",
  "stale",
  "unsupported",
  "unavailable",
]);
const recordingStates = new Set([
  "starting",
  "recording",
  "stopping",
  "complete",
  "failed",
  "interrupted",
]);
const replayStates = new Set([
  "starting",
  "playing",
  "pausing",
  "paused",
  "resuming",
  "stepping",
  "stopping",
  "stopped",
  "completed",
  "failed",
]);

export function readPinnedLiveCurrent(
  source: LiveSource,
  operationId: string,
  value: unknown,
): PinnedLiveRead {
  if (!isRecord(value) || value.status !== "ok") {
    return { status: "error", snapshot: null };
  }
  if (!("data" in value)) return { status: "error", snapshot: null };
  if (value.data === null) return { status: "unavailable", snapshot: null };
  if (!isRecord(value.data)) return { status: "error", snapshot: null };

  const data = value.data;
  const returnedId =
    source === "recording" ? data.recording_id : data.playback_id;
  if (typeof returnedId !== "string") {
    return { status: "error", snapshot: null };
  }
  if (returnedId !== operationId) {
    return { status: "replaced", snapshot: null };
  }

  const state = source === "recording" ? data.status : data.state;
  const validStates = source === "recording" ? recordingStates : replayStates;
  if (typeof state !== "string" || !validStates.has(state)) {
    return { status: "error", snapshot: null };
  }

  const progress = source === "recording" ? asRecord(data.progress) : null;
  const contextValue = source === "recording"
    ? progress?.latest_context
    : data.latest_context;
  const snapshot: PinnedLiveSnapshot = {
    source,
    operation_id: operationId,
    state,
    context: isRecord(contextValue)
      ? (contextValue as SessionContext)
      : null,
    live_telemetry: readMonitor(
      source === "recording"
        ? progress?.live_telemetry
        : data.live_telemetry,
    ) as LiveTelemetryRecord | null,
    live_car_status: readMonitor(
      source === "recording"
        ? progress?.live_car_status
        : data.live_car_status,
    ) as LiveCarStatusRecord | null,
    live_lap_timing: readMonitor(
      source === "recording"
        ? progress?.live_lap_timing
        : data.live_lap_timing,
    ) as LiveLapTimingRecord | null,
    capture_name:
      source === "replay" && typeof data.capture_name === "string"
        ? data.capture_name
        : null,
    speed:
      source === "replay" &&
      typeof data.speed === "number" &&
      Number.isFinite(data.speed)
        ? data.speed
        : null,
  };
  return { status: "ready", snapshot };
}

function readMonitor(value: unknown):
  | LiveTelemetryRecord
  | LiveCarStatusRecord
  | LiveLapTimingRecord
  | null {
  if (!isRecord(value)) return null;
  if (
    typeof value.status !== "string" ||
    !monitorStates.has(value.status) ||
    !(value.reason === null || typeof value.reason === "string") ||
    !(value.age_ms === null ||
      (typeof value.age_ms === "number" && Number.isFinite(value.age_ms)))
  ) {
    return null;
  }
  return value as unknown as
    | LiveTelemetryRecord
    | LiveCarStatusRecord
    | LiveLapTimingRecord;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
