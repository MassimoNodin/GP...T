import type { DeviceTile } from "@/features/settings/devices";
import type { AcquisitionStatus } from "@/lib/acquisition-status";
import {
  ROW_ENGINEER,
  ROW_QUESTIONS,
  ROW_TELEMETRY,
  engineerRuntimeRow,
  isReachable,
  questionProcessingRow,
  telemetryRow,
  type RuntimeInfo,
  type StatusRow,
} from "@/features/live-status/map";
import { humanReason } from "@/features/review-data/reasons";

// Pure System Status and Current Activity logic for the Drive screen. Engine
// rows come from live-status/map.ts; the rest are built here from browser
// state (microphone, audio output), the speech status endpoint and saved
// preferences. Every source has an explicit "not known yet" state so server
// and first client render agree.

export { ROW_ENGINEER, ROW_QUESTIONS, ROW_TELEMETRY };
export const ROW_MICROPHONE = "Microphone Input";
export const ROW_SPEECH = "Speech Recognition";
export const ROW_AUDIO = "Audio Output";
export const ROW_PROACTIVE = "Proactive Assistance";

export const BROWSER_FOOTNOTE = "Microphone input is captured by the standalone radio, never by this browser. Optional browser playback is separate from the radio voice.";

export type MicrophoneState =
  | { kind: "radio"; device: DeviceTile }
  | { kind: "checking" }
  | { kind: "allowed"; label: string | null }
  | { kind: "not_set_up" }
  | { kind: "blocked" }
  | { kind: "unsupported" };

export type SpeechState =
  | { kind: "checking" }
  | { kind: "ready"; placement: string | null }
  | { kind: "unavailable"; reason: string | null }
  | { kind: "unreachable" };

export type AudioState =
  | { kind: "checking" }
  | { kind: "available"; voice: string; speaking: boolean }
  | { kind: "no_voice" }
  | { kind: "unsupported" };

export type ProactiveState =
  | { kind: "enabled"; enabled: number; total: number }
  | { kind: "off" }
  | { kind: "unknown" };

/* ------------------------------------------------------------ source mapping */

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

/**
 * Reads the `GET /api/v1/engineer/transcribe` envelope. Null when the body is
 * not a status envelope (a web-proxy error counts as unreachable).
 */
export function parseSpeechStatus(body: unknown): SpeechState | null {
  const data = record(record(body)?.data);
  const status = typeof data?.status === "string" ? data.status : null;
  if (!data || !status) return null;
  if (status === "ready") {
    const placement = typeof data.placement === "string" && data.placement.length <= 16 ? data.placement : null;
    return { kind: "ready", placement };
  }
  const reason = typeof data.reason === "string" && data.reason.length <= 96 ? data.reason : null;
  return { kind: "unavailable", reason };
}

/** Microphone row input from `permissions.query` (state or null) and the audio input labels. */
export function microphoneState(
  permission: "granted" | "denied" | "prompt" | null,
  labels: readonly string[],
): MicrophoneState {
  if (permission === "denied") return { kind: "blocked" };
  if (permission === "granted") {
    const label = labels.map((item) => item.trim()).find((item) => item !== "") ?? null;
    return { kind: "allowed", label };
  }
  return { kind: "not_set_up" };
}

/** Preferences summary for the Proactive Assistance row. */
export function proactiveState(topics: Readonly<Record<string, boolean>> | null): ProactiveState {
  if (!topics) return { kind: "unknown" };
  const values = Object.values(topics);
  const enabled = values.filter(Boolean).length;
  return enabled > 0 ? { kind: "enabled", enabled, total: values.length } : { kind: "off" };
}

/* ---------------------------------------------------------------------- rows */

const CHECKING = "Checking…";

function unavailableRow(label: string, detail = "Service unreachable"): StatusRow {
  return { label, status: "Unavailable", tone: "grey", detail };
}

export function microphoneRow(state: MicrophoneState): StatusRow {
  switch (state.kind) {
    case "radio":
      return { label: ROW_MICROPHONE, status: state.device.state,
        tone: state.device.tone === "green-text" ? "green" : state.device.tone === "red-text" ? "red" : "grey",
        detail: state.device.name };
    case "allowed":
      return { label: ROW_MICROPHONE, status: "Allowed", tone: "green", detail: state.label ?? "Default microphone" };
    case "not_set_up":
      return { label: ROW_MICROPHONE, status: "Not set up", tone: "grey", detail: "Allow in Settings" };
    case "blocked":
      return { label: ROW_MICROPHONE, status: "Blocked", tone: "red", detail: "Blocked by the browser" };
    case "unsupported":
      return { label: ROW_MICROPHONE, status: "Unsupported", tone: "grey", detail: "This browser has no microphone access" };
    default:
      return { label: ROW_MICROPHONE, status: CHECKING, tone: "grey", detail: "" };
  }
}

export function speechRow(state: SpeechState): StatusRow {
  switch (state.kind) {
    case "ready":
      return {
        label: ROW_SPEECH,
        status: "Ready",
        tone: "green",
        detail: state.placement === "cloud" ? "OpenAI · cloud" : state.placement === "CUDA"
          ? "Local · GPU (CUDA)" : state.placement ? `Local · ${state.placement}` : "Local",
      };
    case "unavailable":
      return {
        label: ROW_SPEECH,
        status: "Unavailable",
        tone: "amber",
        detail: state.reason ? humanReason(state.reason).replace(/\.$/, "") : "Speech runtime unavailable",
      };
    case "unreachable":
      return unavailableRow(ROW_SPEECH);
    default:
      return { label: ROW_SPEECH, status: CHECKING, tone: "grey", detail: "" };
  }
}

export function audioRow(state: AudioState): StatusRow {
  switch (state.kind) {
    case "available":
      return {
        label: ROW_AUDIO,
        status: state.speaking ? "Speaking" : "Available",
        tone: "green",
        detail: state.voice,
      };
    case "no_voice":
      return { label: ROW_AUDIO, status: "No local voice", tone: "grey", detail: "Install a system voice" };
    case "unsupported":
      return { label: ROW_AUDIO, status: "Unsupported", tone: "grey", detail: "This browser cannot speak" };
    default:
      return { label: ROW_AUDIO, status: CHECKING, tone: "grey", detail: "" };
  }
}

export function proactiveRow(state: ProactiveState): StatusRow {
  switch (state.kind) {
    case "enabled":
      return {
        label: ROW_PROACTIVE,
        status: "Enabled",
        tone: "green",
        detail: `${state.enabled} of ${state.total} topics`,
      };
    case "off":
      return { label: ROW_PROACTIVE, status: "Off", tone: "grey", detail: "All topics are off" };
    default:
      return { label: ROW_PROACTIVE, status: "Unknown", tone: "grey", detail: "Preferences unreadable" };
  }
}

export type DriveRowsInput = {
  acquisition: AcquisitionStatus | null;
  /** Packets arrived in the last few minutes, so "stale" means the session went quiet. */
  recentlyLive?: boolean;
  runtime: RuntimeInfo | null;
  microphone: MicrophoneState;
  speech: SpeechState;
  audio: AudioState;
  proactive: ProactiveState;
};

/**
 * The seven System Status rows in reference order. When neither the receiver
 * nor the runtime answers, rows whose source is the engine read as
 * unavailable; microphone and audio are this browser's and stay real.
 */
export function buildDriveRows(input: DriveRowsInput): StatusRow[] {
  const reachable = isReachable(input.acquisition, input.runtime);
  return [
    reachable ? engineerRuntimeRow(input.runtime) : unavailableRow(ROW_ENGINEER),
    reachable ? telemetryRow(input.acquisition, input.recentlyLive) : unavailableRow(ROW_TELEMETRY),
    microphoneRow(input.microphone),
    reachable ? speechRow(input.speech) : unavailableRow(ROW_SPEECH),
    reachable ? questionProcessingRow(input.runtime) : unavailableRow(ROW_QUESTIONS),
    audioRow(input.audio),
    reachable ? proactiveRow(input.proactive) : unavailableRow(ROW_PROACTIVE),
  ];
}

/* ----------------------------------------------------------- current activity */

export type ActivityWave = {
  kind: "animated" | "static" | "flat" | "typing" | "none";
  tone: "red" | "green" | "amber" | "blue" | "grey";
  /** Static bars at 40% opacity. */
  dim?: boolean;
};

export type Activity = {
  text: string;
  tone: "green" | "amber" | "red" | "grey" | "blue";
  wave: ActivityWave;
};

export type ActivityInput = {
  acquisition: AcquisitionStatus | null;
  recentlyLive?: boolean;
  runtime: RuntimeInfo | null;
  /** Preview text of the call being read aloud, or null. */
  speaking: string | null;
  /** A question younger than 30 s without a response. */
  answering: boolean;
};

export function driveActivity(input: ActivityInput): Activity {
  if (input.speaking !== null) {
    return {
      text: `Speaking: ${input.speaking}`,
      tone: "green",
      wave: { kind: "animated", tone: "red" },
    };
  }
  if (!isReachable(input.acquisition, input.runtime)) {
    return { text: "Unavailable", tone: "grey", wave: { kind: "none", tone: "grey" } };
  }
  if (input.answering) {
    return { text: "Answering your question…", tone: "blue", wave: { kind: "typing", tone: "blue" } };
  }
  const state = input.acquisition?.state ?? null;
  if (state === "receiving") {
    return input.runtime?.status === "ready"
      ? { text: "Following live telemetry", tone: "green", wave: { kind: "static", tone: "green", dim: true } }
      : { text: "Telemetry live, engineer not ready", tone: "amber", wave: { kind: "flat", tone: "amber" } };
  }
  if (state === "listening" || state === "starting" || (state === "stale" && !input.recentlyLive)) {
    return { text: "Waiting for the game", tone: "grey", wave: { kind: "flat", tone: "grey" } };
  }
  if (state === "stale") {
    return { text: "Telemetry went quiet", tone: "amber", wave: { kind: "flat", tone: "amber" } };
  }
  if (state === null) {
    return { text: "Telemetry unavailable", tone: "grey", wave: { kind: "flat", tone: "grey" } };
  }
  return { text: "Idle — no live session", tone: "grey", wave: { kind: "flat", tone: "grey" } };
}
