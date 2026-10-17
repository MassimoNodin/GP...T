// Connections tab: the telemetry connection test result and the speech
// recognition card. Pure mapping so the exact copy can be tested.

import { parseLocalSpeechStatus, speechPlacementLabel } from "@/lib/local-speech-draft";
import { humanReason } from "@/features/review-data/reasons";
import type { DisplayRow, DisplayTone } from "./display";

/* -------------------------------------------------------- connection test */

export const CONNECTION_TEST_SECONDS = 10;

export type ConnectionTestInput = {
  /** `received` when the test started; null when the engine did not report it. */
  baseline: number | null;
  /** `received` at the last successful poll; null when none succeeded. */
  latest: number | null;
  /** Receiver state at the last poll (e.g. `receiving`, `disabled`). */
  state: string | null;
  host: string | null;
  port: number | null;
  seconds?: number;
};

export type ConnectionTestResult = {
  tone: "success" | "warning" | "error";
  text: string;
  packets: number | null;
};

function formatPackets(count: number): string {
  return new Intl.NumberFormat("en-US").format(count);
}

/** Address to type into the game: a concrete host, or a hint when the receiver binds every interface. */
export function gameAddress(host: string | null): string {
  return host && host !== "0.0.0.0" && host !== "::" ? host : "this computer's IP address";
}

/**
 * Outcome of watching the packet counter for `seconds`. The counter can reset
 * when the receiver restarts, in which case the latest value is the number
 * seen since the restart.
 */
export function connectionTestOutcome(input: ConnectionTestInput): ConnectionTestResult {
  const seconds = input.seconds ?? CONNECTION_TEST_SECONDS;
  if (input.state === null || input.latest === null) {
    return {
      tone: "error",
      text: "The connection test could not read the receiver. Start the local engine and try again.",
      packets: null,
    };
  }
  if (input.state === "disabled") {
    return {
      tone: "warning",
      text: "Automatic telemetry acquisition is disabled, so the engine is not listening for the game.",
      packets: null,
    };
  }
  const baseline = input.baseline ?? input.latest;
  const packets = input.latest >= baseline ? input.latest - baseline : input.latest;
  if (packets > 0) {
    return {
      tone: "success",
      text: `Receiving data: ${formatPackets(packets)} ${packets === 1 ? "packet" : "packets"} in ${seconds} s.`,
      packets,
    };
  }
  const port = input.port === null ? "20777" : String(input.port);
  return {
    tone: "warning",
    text: `No packets in ${seconds} s. In F1, set UDP Telemetry On, IP ${gameAddress(input.host)}, port ${port}, send rate 20 Hz or higher.`,
    packets: 0,
  };
}

/* ------------------------------------------------------ speech recognition */

export type SpeechView = {
  state: "ready" | "unavailable" | "unreachable" | "invalid";
  tone: DisplayTone;
  /** Chip text for the card header. */
  statusLabel: string;
  rows: DisplayRow[];
  /** Help line under the card. */
  footnote: string;
  /** Runtime id used to verify a microphone test; null unless ready. */
  runtimeId: string | null;
};

const SPEECH_REASONS: Record<string, string> = {
  runtime_unavailable: "The speech runtime is not installed.",
  temporary_directory_unavailable: "The speech runtime has no usable temporary folder.",
};

/** Plain-language reason for a speech status or transcription failure. */
export function speechReasonText(reason: string | null | undefined): string {
  if (!reason) return "Speech recognition is not set up on the engine.";
  if (Object.hasOwn(SPEECH_REASONS, reason)) return SPEECH_REASONS[reason];
  const known = humanReason(reason);
  return known || "Speech recognition is not set up on the engine.";
}

/** Reads the `/api/engineer/transcribe` GET body (null when the request failed). */
export function describeSpeech(body: unknown): SpeechView {
  const envelope = body !== null && typeof body === "object" ? (body as Record<string, unknown>) : null;
  // The engine (or the proxy) answered with an unavailable envelope: nothing to read.
  if (body === null || body === undefined || envelope?.status === "unavailable") {
    return {
      state: "unreachable",
      tone: "red",
      statusLabel: "Unavailable",
      rows: [
        { label: "Status", value: "Local engine unavailable", tone: "red" },
        { label: "Runtime", value: "Unavailable" },
        { label: "Model", value: "Unavailable" },
        { label: "Placement", value: "Unavailable" },
      ],
      footnote: "Speech recognition status could not be read because the local engine did not answer.",
      runtimeId: null,
    };
  }
  const status = parseLocalSpeechStatus(body);
  if (status === null) {
    return {
      state: "invalid",
      tone: "amber",
      statusLabel: "Unrecognised",
      rows: [{ label: "Status", value: "The engine sent a speech status this app does not recognise", tone: "amber" }],
      footnote: "Update the app and the engine together, then reload this page.",
      runtimeId: null,
    };
  }
  if (status.status === "unavailable") {
    return {
      state: "unavailable",
      tone: "amber",
      statusLabel: "Not set up",
      rows: [
        { label: "Status", value: "Not set up", tone: "amber" },
        { label: "Reason", value: speechReasonText(status.reason) },
      ],
      footnote: "Choose a ready speech provider in Voice & Controls to use voice questions.",
      runtimeId: null,
    };
  }
  return {
    state: "ready",
    tone: "green",
    statusLabel: "Ready",
    rows: [
      { label: "Status", value: "Ready", tone: "green" },
      { label: "Runtime", value: status.runtime === "OpenAI" ? "OpenAI" : `whisper.cpp ${status.runtime_version}` },
      { label: "Model", value: status.model_name },
      { label: "Placement", value: speechPlacementLabel(status.placement) },
      { label: "Clip length", value: `Up to ${status.maximum_clip_seconds} s` },
      { label: "Audio", value: "Audio is not stored" },
    ],
    footnote: status.runtime === "OpenAI"
      ? "Audio is sent to OpenAI for recognition. OpenAI API billing applies."
      : "Recognition runs on this computer. Nothing is sent to a cloud service. Use Test microphone on Voice & Controls to try it.",
    runtimeId: status.runtime_id,
  };
}
