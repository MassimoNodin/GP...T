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
      return { tone: "amber", label: "Waiting for game" };
    case "stale":
      return { tone: "amber", label: "Telemetry stale" };
    case "disabled":
      return { tone: "grey", label: "Telemetry disabled" };
    default:
      return { tone: "red", label: "Telemetry offline" };
  }
}

export function telemetryDetail(status: AcquisitionStatus | null): string {
  if (!status) return "Not reachable";
  switch (status.state) {
    case "receiving":
      return "Ready";
    case "listening":
    case "starting":
      return "Listening on UDP 20777";
    case "stale":
      return "No recent packets";
    default:
      return "Not receiving";
  }
}

/** Footer for telemetry screens: game on the first line, receiver detail on the second. */
export function telemetryFooter(status: AcquisitionStatus | null, firstLine: string): ShellFooter {
  const pill = telemetryStatus(status);
  return { tone: pill.tone, label: pill.label, lines: [firstLine, telemetryDetail(status)] };
}

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

export function telemetryRow(acquisition: AcquisitionStatus | null): StatusRow {
  if (!acquisition) return unavailableRow(ROW_TELEMETRY, "No receiver status");
  const delayMs = Math.round((acquisition.last_publication_delay_s ?? 0) * 1000);
  switch (acquisition.state) {
    case "receiving":
      return { label: ROW_TELEMETRY, status: "Receiving", tone: "green", detail: `${delayMs} ms` };
    case "listening":
      return { label: ROW_TELEMETRY, status: "Listening", tone: "amber", detail: "UDP 20777" };
    case "starting":
      return { label: ROW_TELEMETRY, status: "Starting", tone: "amber", detail: "UDP 20777" };
    case "stale":
      return { label: ROW_TELEMETRY, status: "Stale", tone: "amber", detail: "No recent packets" };
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
