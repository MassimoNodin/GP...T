import type { SessionSummary } from "@/features/review-data/summary";
import type { ProgressionPoint } from "@/features/review-data/fixtures";
import type { HeaderItem, ShellFooter, ShellStatus } from "@/features/ui-foundation/AppShell";
import type { Tone } from "@/features/ui-foundation/primitives";

// Types shared by the Session Overview loader (server), the pure helpers in
// `overview-data.ts` and the client views. Everything here is plain data so a
// model can cross the server/client boundary as props.

export type Compound = "soft" | "medium" | "hard" | "inter" | "wet" | "super_soft" | "dry" | "unknown";

export type OverviewLap = {
  /** v2: lap number as text; v1: attempt key. Used in URLs. */
  id: string;
  lap: number;
  ms: number;
  valid: boolean;
  /** v2 attempt revision id, for fetching the lap's trace and joining `/laps` enrichment. */
  revisionId?: string;
  /** Sector times in ms; null when the game did not report them. */
  sectors: [number, number, number] | null;
  /** Plain-language reason sectors are missing. */
  sectorsReason: string | null;
  tyre: { compound: Compound; label: string; actual: string | null; ageLaps: number | null } | null;
  /** Plain-language reason the tyre is missing. */
  tyreReason: string | null;
  pit: boolean;
  startObserved: boolean | null;
  /** False: the lap's detailed telemetry has not been processed ("Not processed" flag). */
  traceAvailable: boolean;
};

export type OverviewStint = {
  index: number;
  compound: Compound;
  label: string;
  actual: string | null;
  startLap: number;
  endLap: number;
  lapCount: number;
  startAge: number | null;
  endAge: number | null;
};

/** Content that may arrive after the page: loaded now, fetched by the client, or not obtainable. */
export type DeferredSection<T> =
  | { state: "loaded"; value: T; partialReason: string | null }
  | { state: "deferred" }
  | { state: "unavailable"; reason: string };

export type EventCategory = "race_control" | "penalties" | "pit" | "my_laps" | "incidents" | "other";

export type EventIcon =
  | "ring-green"
  | "ring-amber"
  | "alert-red"
  | "alert-amber"
  | "check-green"
  | "flag"
  | "pit"
  | "car";

export type OverviewEvent = {
  id: string;
  kind: string;
  category: EventCategory;
  lap: number | null;
  /** Game session time in seconds; the Events view shows it relative to the first event. */
  timeS: number | null;
  label: string;
  isPlayer: boolean;
  icon: EventIcon;
  highlight?: boolean;
  /** Where the event came from; reference events are the fixture session's. */
  source: "summary" | "lap" | "reference";
};

export type SummaryItem = {
  tone: "green" | "amber" | "red" | "blue" | "grey";
  glyph?: "check" | "alert" | "info";
  text: string;
};

export type HeroField = {
  label: string;
  /** Null renders `NotAvailable` with `reason`. */
  value: string | null;
  reason?: string;
};

export type HeroPart = string | { text: string; title: string; /** ISO instant behind `text`, shown in the viewer's time zone. */ iso?: string };

export type SetupRow = { label: string; value: string | null };
export type SetupGroup = { title: string; rows: SetupRow[] };

export type SetupView = {
  groups: SetupGroup[];
  /** Setup changes the engine observed during the session. */
  changeCount: number;
};

/** What the session itself says about where and how it was run. */
export type OverviewContext = {
  sessionType: string | null;
  ruleSet: string | null;
  packetFormat: number | null;
  trackName: string | null;
  trackLengthM: number | null;
  totalLaps: number | null;
  weather: string | null;
  airTemperatureC: number | null;
  trackTemperatureC: number | null;
  /** Wall-clock times known without `/summary` (v1 sessions). */
  startedAtUtc: string | null;
  endedAtUtc: string | null;
  /** Wall-clock duration in seconds known without `/summary` (v1 sessions). */
  durationS: number | null;
};

export type OverviewModel = {
  sessionKey: string;
  source: "fixture" | "v1" | "v2";
  /** v2 session id used to fetch deferred sections; null for fixture and v1. */
  evidenceId: string | null;
  header: HeaderItem[];
  /** Header pill; `live` keeps it in sync with telemetry acquisition. */
  shell: { live: boolean; status: ShellStatus };
  footer: ShellFooter;
  photo: string | null;
  hero: {
    title: string;
    track: string;
    lines: HeroPart[][];
    status: { label: string; tone: Tone };
    stats: HeroField[];
    details: HeroField[];
  };
  progression: {
    points: ProgressionPoint[];
    yMin: number;
    yMax: number;
    yStep: number;
    xMax: number;
    /** Fixture charts follow the reference drawing rather than the lap list. */
    illustrative: boolean;
  };
  /** Reference summary (fixture only); real sessions derive theirs from laps and `/summary`. */
  summary: { text: string; items: SummaryItem[] } | null;
  /** Events known without `/summary`: the reference's (fixture) or derived from laps. */
  events: OverviewEvent[];
  /** Set when lap data is known to be incomplete. */
  lapDataNote: string | null;
  laps: OverviewLap[];
  selectedId: string;
  bestId: string | null;
  /** Stints from `/laps`; the laps in `laps` already carry sectors and tyres when loaded. */
  enrichment: DeferredSection<{ stints: OverviewStint[] }>;
  summaryData: DeferredSection<SessionSummary>;
  /** Car setup (v1 and fixture only). */
  setup: SetupView | null;
  context: OverviewContext;
};
