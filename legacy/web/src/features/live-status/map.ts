import type { AcquisitionStatus } from "@/lib/acquisition-status";
import type { ShellFooter, ShellStatus, ShellTone } from "@/features/ui-foundation/AppShell";

// Pure mappings from backend state to shell and Drive presentation. Kept free of
// React and runtime imports so server pages, client components and tests share
// one definition. `null` always means "the backend did not answer".

export type RuntimeInfo = {
  status: string;
  reason: string | null;
  runtime: string;
  model_name: string;
  last_inference_at_utc: string | null;
};

export type StatusRow = {
  label: string;
  status: string;
  tone: ShellTone;
  detail: string;
};

export const ROW_ENGINEER = "Engineer Runtime";
export const ROW_TELEMETRY = "Telemetry (Game Data)";
export const ROW_QUESTIONS = "Question Processing";

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function shortText(value: unknown, limit: number): string | null {
  return typeof value === "string" && value.length > 0 && value.length <= limit ? value : null;
}

/**
 * Lenient reader for the GET /api/v1/engineer/runtime envelope. It only needs
 * the status, so it does not reject extra or newly populated fields (for
 * example `last_inference_at_utc` once a question has been answered).
 */
export function parseRuntimeInfo(value: unknown): RuntimeInfo | null {
  const envelope = record(value);
  const data = record(envelope?.data);
  if (!envelope || !data) return null;
  if (envelope.api_version !== "v1") return null;
  const status = shortText(data.status, 48);
  if (!status) return null;
  return {
    status,
    reason: shortText(data.reason, 96),
    runtime: shortText(data.runtime, 32) ?? "Engineer",
    model_name: shortText(data.model_name, 64) ?? "",
    last_inference_at_utc: shortText(data.last_inference_at_utc, 40),
  };
}

/* ----------------------------------------------------------- shell: pill */

export function telemetryStatus(status: AcquisitionStatus | null): ShellStatus {
  if (!status) return { tone: "red", label: "Backend unavailable" };
  switch (status.state) {
    case "receiving":
      return { tone: "green", label: "Telemetry live" };
    case "starting":
    case "listening":
    case "stale":
      // "stale" is also the receiver's state while the game is not running.
      return { tone: "grey", label: "Waiting for game" };
    case "disabled":
      return { tone: "grey", label: "Telemetry disabled" };
    default:
      return { tone: "red", label: "Telemetry offline" };
  }
}

/**
 * `recentlyLive`: the browser saw packets arriving in the last few minutes, so
 * a "stale" receiver means the session went quiet rather than no game running.
 */
export function telemetryDetail(status: AcquisitionStatus | null, recentlyLive = false): string {
  if (!status) return "Not reachable";
  switch (status.state) {
    case "receiving":
      return "Ready";
    case "listening":
    case "starting":
      return "Listening on UDP 20777";
    case "stale":
      return recentlyLive ? "Packets stopped arriving" : "Listening on UDP 20777";
    default:
      return "Not receiving";
  }
}

/** Footer for telemetry screens: game on the first line, receiver detail on the second. */
export function telemetryFooter(status: AcquisitionStatus | null, firstLine: string): ShellFooter {
  const pill = telemetryStatus(status);
  return { tone: pill.tone, label: pill.label, lines: [firstLine, telemetryDetail(status)] };
}

/** Sidebar footer for every screen while the local engine cannot be reached. */
export const ENGINE_OFFLINE_FOOTER: ShellFooter = {
  tone: "red",
  label: "Engine offline",
  lines: ["Start the local engine", ""],
};

export function telemetryShell(
  status: AcquisitionStatus | null,
  firstLine: string,
): { status: ShellStatus; footer: ShellFooter } {
  return { status: telemetryStatus(status), footer: telemetryFooter(status, firstLine) };
}

export function engineerStatus(runtime: RuntimeInfo | null): ShellStatus {
  if (!runtime) return { tone: "red", label: "Engineer offline" };
  if (runtime.status === "ready") return { tone: "green", label: "Engineer running" };
  if (runtime.status === "not_configured") return { tone: "amber", label: "Engineer not configured" };
  return { tone: "red", label: "Engineer unavailable" };
}

/** Footer for engineer screens: label from the runtime, context lines supplied by the page. */
export function engineerFooter(runtime: RuntimeInfo | null, lines: string[]): ShellFooter {
  const pill = engineerStatus(runtime);
  return { tone: pill.tone, label: pill.label, lines };
}

export function engineerShell(
  runtime: RuntimeInfo | null,
  lines: string[],
): { status: ShellStatus; footer: ShellFooter } {
  return { status: engineerStatus(runtime), footer: engineerFooter(runtime, lines) };
}

/* ------------------------------------------------- shell: combined status */

type Part = { tone: ShellTone; label: string; detail: string };

const TONE_RANK: Record<ShellTone, number> = { green: 0, grey: 1, amber: 2, red: 3 };

/** The game-connection half of the header pill. */
export function telemetryPart(status: AcquisitionStatus | null, recentlyLive = false): Part {
  if (!status) return { tone: "red", label: "Telemetry unknown", detail: "Receiver status unavailable" };
  switch (status.state) {
    case "receiving":
      return { tone: "green", label: "Telemetry live", detail: "Receiving game data" };
    case "starting":
    case "listening":
      return { tone: "grey", label: "Waiting for game", detail: "Listening on UDP 20777" };
    case "stale":
      return recentlyLive
        ? { tone: "amber", label: "Telemetry stale", detail: "Packets stopped arriving" }
        : { tone: "grey", label: "Waiting for game", detail: "Listening on UDP 20777" };
    case "disabled":
      return { tone: "grey", label: "Telemetry off", detail: "Acquisition is disabled" };
    default:
      return { tone: "red", label: "Telemetry offline", detail: "Receiver is not running" };
  }
}

/** The engineer half of the header pill. */
export function engineerPart(runtime: RuntimeInfo | null): Part {
  if (!runtime) return { tone: "red", label: "Engineer offline", detail: "Runtime status unavailable" };
  if (runtime.status === "ready") {
    return { tone: "green", label: "Engineer ready", detail: runtime.model_name || runtime.runtime };
  }
  if (runtime.status === "not_configured") {
    return { tone: "amber", label: "Engineer not set up", detail: humanReason(runtime.reason) };
  }
  return { tone: "red", label: "Engineer unavailable", detail: humanReason(runtime.reason) };
}

/**
 * One header pill for every screen: game connection and engineer together, in
 * the tone of whichever is worse, with both halves listed in its details.
 */
export function combinedStatus(
  acquisition: AcquisitionStatus | null,
  runtime: RuntimeInfo | null,
  recentlyLive = false,
): ShellStatus {
  const telemetry = telemetryPart(acquisition, recentlyLive);
  const engineer = engineerPart(runtime);
  const details = [
    { label: "Telemetry", value: `${telemetry.label.replace(/^Telemetry /, "")} · ${telemetry.detail}`, tone: telemetry.tone },
    { label: "Engineer", value: `${engineer.label.replace(/^Engineer /, "")} · ${engineer.detail}`, tone: engineer.tone },
  ];
  if (!acquisition && !runtime) {
    return { tone: "red", label: "Engine offline", details: [{ label: "Local engine", value: "Not reachable. Start the local engine.", tone: "red" }] };
  }
  const tone = TONE_RANK[telemetry.tone] >= TONE_RANK[engineer.tone] ? telemetry.tone : engineer.tone;
  return { tone, label: `${telemetry.label} · ${engineer.label}`, details };
}

/**
 * Sidebar footer shared by every live screen: the game connection on the head
 * line and the page's session lines below.
 */
export function sessionFooter(
  acquisition: AcquisitionStatus | null,
  runtime: RuntimeInfo | null,
  lines: string[],
  recentlyLive = false,
): ShellFooter {
  if (!acquisition && !runtime) return ENGINE_OFFLINE_FOOTER;
  const telemetry = telemetryPart(acquisition, recentlyLive);
  return { tone: telemetry.tone, label: telemetry.label, lines };
}

/* ------------------------------------------------------ drive: status rows */

export function isReachable(
  acquisition: AcquisitionStatus | null,
  runtime: RuntimeInfo | null,
): boolean {
  return acquisition !== null || runtime !== null;
}

const UNAVAILABLE_DETAIL = "Service unreachable";

function unavailableRow(label: string, detail = UNAVAILABLE_DETAIL): StatusRow {
  return { label, status: "Unavailable", tone: "grey", detail };
}

function humanReason(reason: string | null): string {
  if (!reason) return "Runtime unavailable";
  const text = reason.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function telemetryRow(acquisition: AcquisitionStatus | null, recentlyLive = false): StatusRow {
  if (!acquisition) return unavailableRow(ROW_TELEMETRY, "No receiver status");
  const delayMs = Math.round((acquisition.last_publication_delay_s ?? 0) * 1000);
  switch (acquisition.state) {
    case "receiving":
      return { label: ROW_TELEMETRY, status: "Receiving", tone: "green", detail: `${delayMs} ms` };
    case "listening":
      return { label: ROW_TELEMETRY, status: "Waiting", tone: "grey", detail: "Listening on UDP 20777" };
    case "starting":
      return { label: ROW_TELEMETRY, status: "Starting", tone: "grey", detail: "UDP 20777" };
    case "stale":
      return recentlyLive
        ? { label: ROW_TELEMETRY, status: "Stale", tone: "amber", detail: "Packets stopped arriving" }
        : { label: ROW_TELEMETRY, status: "Waiting", tone: "grey", detail: "Listening on UDP 20777" };
    case "stopped":
      return { label: ROW_TELEMETRY, status: "Stopped", tone: "red", detail: "Receiver stopped" };
    case "failed":
      return { label: ROW_TELEMETRY, status: "Failed", tone: "red", detail: "Receiver failed" };
    default:
      return { label: ROW_TELEMETRY, status: "Disabled", tone: "grey", detail: "Not enabled" };
  }
}

export function engineerRuntimeRow(runtime: RuntimeInfo | null): StatusRow {
  if (!runtime) return unavailableRow(ROW_ENGINEER, "No runtime status");
  if (runtime.status === "ready") {
    return { label: ROW_ENGINEER, status: "Running", tone: "green", detail: runtime.model_name || runtime.runtime };
  }
  if (runtime.status === "not_configured") {
    return { label: ROW_ENGINEER, status: "Not configured", tone: "amber", detail: humanReason(runtime.reason) };
  }
  return { label: ROW_ENGINEER, status: "Unavailable", tone: "red", detail: humanReason(runtime.reason) };
}

export function questionProcessingRow(runtime: RuntimeInfo | null): StatusRow {
  if (!runtime) return unavailableRow(ROW_QUESTIONS, "No runtime status");
  if (runtime.status === "ready") {
    return { label: ROW_QUESTIONS, status: "Ready", tone: "green", detail: runtime.runtime };
  }
  return { label: ROW_QUESTIONS, status: "Unavailable", tone: "red", detail: humanReason(runtime.reason ?? runtime.status) };
}

/**
 * Reference rows with the three backend-backed rows replaced by live state.
 * When neither endpoint answers, every row (including those with no backend
 * source) is shown as unavailable rather than keeping reference "Running".
 */
export function buildSystemRows(
  reference: StatusRow[],
  acquisition: AcquisitionStatus | null,
  runtime: RuntimeInfo | null,
): StatusRow[] {
  if (!isReachable(acquisition, runtime)) {
    return reference.map((row) => unavailableRow(row.label));
  }
  return reference.map((row) => {
    if (row.label === ROW_ENGINEER) return engineerRuntimeRow(runtime);
    if (row.label === ROW_TELEMETRY) return telemetryRow(acquisition);
    if (row.label === ROW_QUESTIONS) return questionProcessingRow(runtime);
    return row;
  });
}

export function currentActivity(
  reference: string,
  acquisition: AcquisitionStatus | null,
  runtime: RuntimeInfo | null,
): { text: string; tone: ShellTone } {
  if (!isReachable(acquisition, runtime)) return { text: "Unavailable", tone: "grey" };
  return { text: reference, tone: "green" };
}

/** Pill under Session Context: red "live" only while packets are actually arriving. */
export function liveDataPill(acquisition: AcquisitionStatus | null): { live: boolean; label: string } {
  return acquisition?.state === "receiving"
    ? { live: true, label: "Live game data" }
    : { live: false, label: "No live data" };
}
