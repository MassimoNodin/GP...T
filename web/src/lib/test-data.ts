import type { AnalysisInput } from "./analysis-display";

export const TEST_DATA_SCENARIOS = [
  "populated",
  "empty",
  "partial",
  "stale",
  "unavailable",
] as const;
export type TestDataScenario = (typeof TEST_DATA_SCENARIOS)[number];
export type TestLap = {
  id: string;
  number: number;
  timeMs: number;
  sectorsMs: number[];
  valid: boolean;
  disposition: string;
  tyre: string;
};
export type TestSession = {
  id: string;
  track: string;
  lengthM: number;
  type: string;
  startedAt: string;
  status: string;
  lapCount: number;
  bestTimeMs: number;
  runId: string;
};
export type TestDataSnapshot = {
  schema_version: 1;
  source: {
    kind: "synthetic";
    fixture_version: "ui-test-v1";
    scenario: TestDataScenario;
    tick: number;
    at_utc: string;
  };
  diagnostic_only: true;
  coaching_eligible: false;
  ranking_eligible: false;
  sessions: TestSession[];
  laps: TestLap[];
  selected: {
    sessionId: string | null;
    targetId: string | null;
    referenceId: string | null;
  };
  recording: {
    status: string;
    elapsedMs: number;
    packets: number;
    queueDrops: number;
    bytes: number;
    rate: number;
  } | null;
  live: {
    status: "fresh" | "stale";
    ageMs: number;
    lap: number;
    lapTimeMs: number;
    speedKph: number | null;
    rpm: number | null;
    gear: number | null;
    throttle: number | null;
    brake: number | null;
    fuelReported: number | null;
    ersPercent: number | null;
    drs: boolean | null;
    airC: number | null;
    trackC: number | null;
    tyreWearPercent: (number | null)[];
    tyreTemperatureC: (number | null)[];
    distanceM: number;
  } | null;
  captures: {
    id: string;
    name: string;
    sessionId: string;
    bytes: number;
    laps: number;
    durationMs: number;
    status: string;
    packetLossPercent: number;
  }[];
  imports: {
    id: string;
    name: string;
    track: string;
    progress: number | null;
    status: string;
  }[];
  lifecycle: { label: string; at: string; status: string }[];
  inventory: { label: string; count: number | null; status: string }[];
  storage: {
    usedBytes: number;
    budgetBytes: number;
    categories: { label: string; bytes: number }[];
  } | null;
  runtime: {
    model: string;
    status: string;
    provider: string;
    voice: string;
  } | null;
  settings: {
    udpHost: string;
    udpPort: number;
    queueSize: number;
    hudPosition: string;
    hudOpacity: number;
    hudTheme: string;
    voiceSpeed: number;
    voiceVolume: number;
    devices: string[];
  } | null;
  engineer: { question: string; answer: string; limitations: string[] } | null;
  analysis: AnalysisInput;
};

export function isTestDataScenario(value: unknown): value is TestDataScenario {
  return (
    typeof value === "string" &&
    TEST_DATA_SCENARIOS.some((scenario) => scenario === value)
  );
}
