import type { LiveTelemetryRecord } from "./api";

export interface BrowserTelemetryPoint {
  epoch: string;
  sessionUid: string;
  packetFormat: number;
  playerCarIndex: number;
  frameIdentifier: number;
  sessionTimeS: number;
  speedKph: number | null;
  throttle: number | null;
  brake: number | null;
  gear: number | null;
}

export interface BrowserTelemetryHistory {
  points: readonly BrowserTelemetryPoint[];
  epoch: string | null;
  sessionUid: string | null;
  packetFormat: number | null;
  playerCarIndex: number | null;
  watermarkFrameIdentifier: number | null;
  watermarkSessionTimeS: number | null;
  awaitingMonotonicResume: boolean;
}

export const LIVE_CHART_MAX_POINTS: number;
export const LIVE_CHART_WINDOW_SECONDS: number;
export const EMPTY_BROWSER_TELEMETRY_HISTORY: BrowserTelemetryHistory;
export function collectBrowserTelemetryPoint(
  previous: BrowserTelemetryHistory,
  monitor: LiveTelemetryRecord,
  sourceState: string,
): BrowserTelemetryHistory;
