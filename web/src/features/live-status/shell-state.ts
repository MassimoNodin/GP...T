import { fetchAcquisition } from "@/features/review-data/backend";
import type { ShellFooter, ShellStatus } from "@/features/ui-foundation/AppShell";
import { forwardLocalRequest } from "@/lib/local-proxy";
import type { AcquisitionStatus } from "@/lib/acquisition-status";
import { combinedStatus, parseRuntimeInfo, sessionFooter, telemetryDetail, type RuntimeInfo } from "./map";

// Server-side read of the two states behind the shared header pill, so every
// screen renders the same pill the browser poller will keep up to date.

export type ShellState = {
  acquisition: AcquisitionStatus | null;
  runtime: RuntimeInfo | null;
  status: ShellStatus;
};

async function fetchRuntime(): Promise<RuntimeInfo | null> {
  try {
    const response = await forwardLocalRequest("/api/v1/engineer/runtime", {
      signal: AbortSignal.timeout(2500),
    });
    // The runtime endpoint answers 503 with a full envelope while unavailable.
    return parseRuntimeInfo(await response.json());
  } catch {
    return null;
  }
}

export async function loadShellState(): Promise<ShellState> {
  const [acquisition, runtime] = await Promise.all([fetchAcquisition(), fetchRuntime()]);
  return { acquisition, runtime, status: combinedStatus(acquisition, runtime) };
}

/**
 * The pill and footer a live page renders on the server; mirrors what
 * LiveShellProvider computes in the browser so hydration matches.
 */
export function liveShellProps(
  state: ShellState,
  mode: "telemetry" | "engineer",
  footer: ShellFooter,
): { status: ShellStatus; footer: ShellFooter } {
  const lines = mode === "telemetry" ? [footer.lines[0] ?? "", telemetryDetail(state.acquisition)] : footer.lines;
  return { status: state.status, footer: sessionFooter(state.acquisition, state.runtime, lines) };
}
