import type { ShellFooter, ShellStatus } from "@/features/ui-foundation/AppShell";
import type { Tone } from "@/features/ui-foundation/primitives";

// Reference content transcribed from docs/assets/ui-reference.png. Screens use
// these values wherever the local API cannot supply the section. Arithmetic
// that was wrong in the image is corrected: Lap 28 (1:13.420) minus the
// session best Lap 57 (1:12.842) is +0.578, and sector deltas sum to it.

export const FIXTURE_SESSION_KEY = "monaco-2024-race";

export const shellFixture = {
  engineerRunning: { tone: "green", label: "Engineer running" } satisfies ShellStatus,
  telemetryLive: { tone: "green", label: "Telemetry live" } satisfies ShellStatus,
  replayMode: { tone: "amber", label: "Replay Mode" } satisfies ShellStatus,
  sessionFooter: {
    tone: "green",
    label: "Engineer running",
    lines: ["Monaco Grand Prix", "Race  •  Lap 28/78"],
  } satisfies ShellFooter,
  telemetryFooter: {
    tone: "green",
    label: "Telemetry live",
    lines: ["F1 24 (PC)", "Ready"],
  } satisfies ShellFooter,
};

/* ------------------------------------------------------------------ Drive */

export type SystemRow = {
  label: string;
  status: string;
  tone: "green" | "amber" | "red" | "grey";
  detail: string;
};

export type RadioKind = "proactive" | "question" | "response" | "issue";

export type RadioEvent = {
  id: string;
  time: string;
  timestamp: string;
  kind: RadioKind;
  text: string;
  outcome: string;
  outcomeTone: Tone;
  trigger: string;
  evidence: string[];
  output: string;
  timings: string;
  limitations?: string;
};

export const driveFixture = {
  header: { items: ["Monaco Grand Prix", "Race", "Lap 28/78"], trailing: "18:42" },
  system: [
    { label: "Engineer Runtime", status: "Running", tone: "green", detail: "Since 14:27" },
    { label: "Telemetry (Game Data)", status: "Receiving", tone: "green", detail: "120 ms" },
    { label: "Microphone Input", status: "Available", tone: "green", detail: "Shure MV7" },
    { label: "Speech Recognition", status: "Listening", tone: "green", detail: "High confidence" },
    { label: "Question Processing", status: "Ready", tone: "green", detail: "< 1 s" },
    { label: "Audio Output", status: "Available", tone: "green", detail: "MacBook Speakers" },
    { label: "Proactive Assistance", status: "Enabled", tone: "green", detail: "Medium" },
  ] as SystemRow[],
  activity: "Listening for your question...",
  context: {
    title: "Monaco Grand Prix",
    subtitle: ["Race", "4 Jun 2024"],
    track: "monaco",
    facts: [
      ["Game", "F1 24 (PC)"],
      ["Car", "Ferrari"],
      ["Driver", "Player"],
      ["Conditions", "Dry, 24°C"],
      ["Lap", "28 / 78"],
      ["Track", "78%"],
    ] as Array<[string, string]>,
    source: "Live game data",
  },
  radio: buildRadioHistory(),
};

function radio(
  id: number,
  time: string,
  seconds: string,
  kind: RadioKind,
  text: string,
  outcome: string,
  extra: Partial<RadioEvent> = {},
): RadioEvent {
  const defaults: Record<RadioKind, Omit<RadioEvent, "id" | "time" | "timestamp" | "kind" | "text" | "outcome" | "outcomeTone">> = {
    proactive: {
      trigger: "Telemetry trend",
      evidence: ["Live game telemetry"],
      output: "TTS playback",
      timings: "Generated in 290 ms  •  Played in 200 ms",
    },
    question: {
      trigger: "Push-to-talk question",
      evidence: ["Transcribed driver speech"],
      output: "Routed to question processing",
      timings: "Transcribed in 410 ms",
    },
    response: {
      trigger: "Driver question",
      evidence: ["Live game telemetry"],
      output: "TTS playback",
      timings: "Generated in 350 ms  •  Played in 190 ms",
    },
    issue: {
      trigger: "Runtime diagnostic",
      evidence: ["Engineer runtime log"],
      output: "Driver prompted to repeat",
      timings: "Detected in 120 ms",
    },
  };
  const outcomeTone: Tone =
    outcome === "Played" || outcome === "Responded"
      ? "green"
      : outcome === "Suppressed" || outcome === "Expired"
        ? "grey"
        : outcome === "Handled"
          ? "amber"
          : "red";
  return {
    id: `radio-${id}`,
    time,
    timestamp: `${time}:${seconds}`,
    kind,
    text,
    outcome,
    outcomeTone,
    ...defaults[kind],
    ...extra,
  };
}

function buildRadioHistory(): RadioEvent[] {
  return [
    radio(24, "18:42", "16", "proactive", "Tyre temps increasing, suggest lifting in Turns 6–8 to keep them in the optimal window.", "Played", {
      trigger: "Tyre temperature trend + track position",
      evidence: ["Front left tyre: 104°C (+12°C past 2 laps)", "Lap 26–28 trend: increasing", "Driver in clean air"],
      output: "TTS playback (1.8 s)",
      timings: "Generated in 320 ms  •  Played in 210 ms",
      limitations: "Only tyre surface temps available (core temps not available in game data).",
    }),
    radio(23, "18:40", "41", "question", "What's the gap to Norris?", "Responded", {
      evidence: ["Transcript confidence: high"],
    }),
    radio(22, "18:40", "43", "response", "Norris is 2.4 seconds ahead.", "Played", {
      trigger: "Driver question: gap to Norris",
      evidence: ["Lap data: car ahead interval", "Updated 0.4 s before response"],
      output: "TTS playback (1.2 s)",
    }),
    radio(21, "18:38", "05", "proactive", "Fuel target looks good for current pace.", "Played", {
      trigger: "Fuel delta vs. target",
      evidence: ["Fuel remaining vs. laps remaining", "Last 3 laps average consumption"],
      output: "TTS playback (1.6 s)",
    }),
    radio(20, "18:36", "22", "question", "Are my brakes ok?", "Responded"),
    radio(19, "18:36", "24", "response", "Brakes are in a good window, no issues.", "Played", {
      trigger: "Driver question: brakes",
      evidence: ["Brake temperatures (all four corners)", "No brake damage reported"],
      output: "TTS playback (1.5 s)",
    }),
    radio(18, "18:34", "50", "issue", "Microphone pickup was unclear.", "Handled", {
      trigger: "Low transcription confidence",
      evidence: ["Speech confidence below threshold"],
      limitations: "Audio is not retained after transcription.",
    }),
    radio(17, "18:32", "11", "proactive", "Track is getting faster, best lap 1:13.4.", "Suppressed", {
      trigger: "Track evolution",
      evidence: ["Session best lap trend"],
      output: "Not played (driver busy in sector 1)",
      timings: "Generated in 300 ms  •  Suppressed",
    }),
    radio(16, "18:30", "02", "proactive", "Gap to car behind is stable at 1.8 seconds.", "Played"),
    radio(15, "18:28", "37", "question", "How are the tyres looking?", "Responded"),
    radio(14, "18:28", "39", "response", "Tyres are in the window, front left slightly warmer.", "Played"),
    radio(13, "18:27", "14", "issue", "Speech output device changed.", "Handled", {
      trigger: "Audio device change",
      evidence: ["Default output device switched"],
      output: "Playback resumed on new device",
    }),
    radio(12, "18:25", "48", "proactive", "Safety Car ending this lap, prepare for restart.", "Played"),
    radio(11, "18:24", "09", "question", "What position am I in?", "Responded"),
    radio(10, "18:24", "11", "response", "You are P6, gained two places under the Safety Car.", "Played"),
    radio(9, "18:22", "30", "proactive", "Safety Car deployed. Delta positive, stay above it.", "Played"),
    radio(8, "18:20", "55", "issue", "Question timed out before a response was ready.", "Expired", {
      trigger: "Response deadline",
      evidence: ["Generation exceeded 4 s limit"],
      output: "Not played",
    }),
    radio(7, "18:19", "18", "question", "Is the car ahead on older tyres?", "Responded"),
    radio(6, "18:19", "21", "response", "Yes, Albon is on tyres four laps older.", "Played"),
    radio(5, "18:16", "44", "proactive", "Lost 0.8 seconds in Turn 10 this lap.", "Played"),
    radio(4, "18:14", "03", "question", "Remind me of the fuel target.", "Responded"),
    radio(3, "18:14", "05", "response", "Target is plus 0.2 kilograms at the flag.", "Played"),
    radio(2, "18:12", "40", "issue", "Telemetry gap of 0.6 seconds detected.", "Handled", {
      trigger: "Packet gap",
      evidence: ["UDP receive gap above 500 ms"],
      output: "Logged for review",
    }),
    radio(1, "18:10", "00", "proactive", "Radio check, engineer online.", "Played"),
  ];
}

/* --------------------------------------------------------- Session library */

export type LibrarySession = {
  key: string;
  title: string;
  track: string;
  type: { label: string; tone: Tone };
  source: { label: string; tone: Tone };
  date: string;
  meta: string[];
  laps: number;
  notes: string;
  status: { label: string; tone: Tone };
  href: string | null;
};

const fixtureMeta = (weather: string, game = "F1 24 (PC)") => [game, "Ferrari", weather];

export const libraryFixture = {
  header: { items: ["Connected to F1 24"], trailing: "v1.22" },
  sessions: [
    {
      key: FIXTURE_SESSION_KEY,
      title: "Monaco Grand Prix",
      track: "monaco",
      type: { label: "Race", tone: "green" },
      source: { label: "Game", tone: "blue" },
      date: "4 Jun 2024, 15:00",
      meta: fixtureMeta("Dry 24°C"),
      laps: 78,
      notes: "Intense race with Safety Car. Good pace in final stint.",
      status: { label: "Complete", tone: "green" },
      href: `/review/${FIXTURE_SESSION_KEY}`,
    },
    {
      key: "imola-2024-qualifying",
      title: "Imola Grand Prix",
      track: "imola",
      type: { label: "Qualifying", tone: "grey" },
      source: { label: "Game", tone: "blue" },
      date: "17 May 2024, 16:00",
      meta: fixtureMeta("Dry 22°C"),
      laps: 15,
      notes: "Strong Q3 lap. Good balance. Room for improvement in S2.",
      status: { label: "Complete", tone: "green" },
      href: `/review/${FIXTURE_SESSION_KEY}`,
    },
    {
      key: "miami-2024-race",
      title: "Miami Grand Prix",
      track: "miami",
      type: { label: "Race", tone: "amber" },
      source: { label: "Import", tone: "blue" },
      date: "5 May 2024, 15:00",
      meta: fixtureMeta("Dry 28°C"),
      laps: 57,
      notes: "Solid race, managed tyres well. Late SC impacted strategy.",
      status: { label: "Complete", tone: "green" },
      href: `/review/${FIXTURE_SESSION_KEY}`,
    },
    {
      key: "suzuka-2024-practice-1",
      title: "Suzuka Grand Prix",
      track: "suzuka",
      type: { label: "Practice 1", tone: "amber" },
      source: { label: "Game", tone: "grey" },
      date: "7 Apr 2024, 12:00",
      meta: fixtureMeta("Dry 20°C"),
      laps: 32,
      notes: "Setup exploration. Testing different front wing levels.",
      status: { label: "Processing 68%", tone: "blue" },
      href: null,
    },
    {
      key: "bahrain-2024-race",
      title: "Bahrain Grand Prix",
      track: "bahrain",
      type: { label: "Race", tone: "purple" },
      source: { label: "Replay", tone: "blue" },
      date: "2 Mar 2024, 16:00",
      meta: fixtureMeta("Dry 26°C", "F1 23"),
      laps: 57,
      notes: "Older replay for comparison. Some data unavailable.",
      status: { label: "Partial", tone: "amber" },
      href: `/review/${FIXTURE_SESSION_KEY}`,
    },
    {
      key: "saudi-2024-race",
      title: "Saudi Arabian Grand Prix",
      track: "jeddah",
      type: { label: "Race", tone: "amber" },
      source: { label: "Game", tone: "blue" },
      date: "9 Mar 2024, 18:00",
      meta: fixtureMeta("Dry 27°C"),
      laps: 50,
      notes: "Good pace, minor mistake in final sector.",
      status: { label: "Complete", tone: "green" },
      href: `/review/${FIXTURE_SESSION_KEY}`,
    },
    {
      key: "australia-2024-practice-2",
      title: "Australian Grand Prix",
      track: "melbourne",
      type: { label: "Practice 2", tone: "grey" },
      source: { label: "Import", tone: "blue" },
      date: "24 Mar 2024, 14:00",
      meta: fixtureMeta("Wet 18°C"),
      laps: 29,
      notes: "Wet running, setup testing. Limited data (replay).",
      status: { label: "Unavailable", tone: "red" },
      href: null,
    },
  ] as LibrarySession[],
};

/* -------------------------------------------------------- Session overview */

export type ProgressionPoint = {
  lap: number;
  ms: number;
  tone: "blue" | "green" | "purple" | "orange" | "navy";
};

export type NotableEvent = {
  lap: number;
  text: string;
  icon: "ring-green" | "alert-red" | "ring-amber" | "check-green";
  highlight?: boolean;
};

export type Stint = { compound: "Soft" | "Medium" | "Hard"; laps: number };

const T = (seconds: number) => Math.round(seconds * 1000);

// Lap time progression points positioned as plotted in the reference chart.
const progression: ProgressionPoint[] = [
  [0, 72.23, "blue"], [2, 71.5, "blue"], [3, 71.28, "blue"], [5, 70.6, "blue"],
  [7, 70.8, "purple"], [9, 70.0, "green"], [11, 70.4, "green"], [12, 70.05, "green"],
  [14, 70.0, "green"], [16, 69.67, "green"], [18, 69.8, "purple"], [19, 69.5, "purple"],
  [21, 69.56, "purple"], [23, 69.75, "purple"], [25, 69.94, "purple"], [28, 69.9, "blue"],
  [30, 70.0, "purple"], [31, 70.1, "orange"], [33, 70.27, "orange"], [35, 70.4, "orange"],
  [37, 70.24, "orange"], [38, 70.24, "orange"], [40, 70.6, "orange"], [42, 70.38, "orange"],
  [43, 70.46, "navy"], [45, 70.6, "orange"], [47, 70.73, "orange"], [48, 71.03, "orange"],
  [50, 71.0, "orange"], [52, 71.4, "navy"], [53, 71.36, "orange"], [55, 71.55, "orange"],
  [57, 71.63, "orange"], [58, 72.23, "orange"], [60, 71.74, "orange"], [62, 71.9, "orange"],
  [64, 72.15, "orange"], [66, 72.78, "orange"], [67, 72.45, "orange"], [70, 72.73, "orange"],
  [72, 72.67, "orange"], [74, 72.45, "orange"], [76, 73.66, "orange"],
].map(([lap, seconds, tone]) => ({
  lap: lap as number,
  ms: T(seconds as number),
  tone: tone as ProgressionPoint["tone"],
}));

export const overviewFixture = {
  header: { items: ["Monaco Grand Prix", "Race", "4 Jun 2024"] },
  hero: {
    title: "Monaco Grand Prix",
    track: "monaco",
    lines: [
      ["Race", "4 Jun 2024, 15:00"],
      ["F1 24 (PC)", "Ferrari", "Player"],
    ],
    status: { label: "Complete", tone: "green" as Tone },
    stats: [
      ["78", "Laps"],
      ["1:42:18", "Duration"],
      ["Dry 24°C", "Conditions"],
      ["Medium", "Track Grip"],
    ] as Array<[string, string]>,
    details: [
      ["Game Build", "1.22"],
      ["Assists", "Off"],
      ["Damage", "Full"],
      ["Rule Set", "2024"],
      ["Data Source", "Game (Live)"],
    ] as Array<[string, string]>,
  },
  progression: {
    points: progression,
    yMin: T(68),
    yMax: T(76),
    xMax: 77,
    selectedLap: 28,
  },
  summary: {
    text: "A strong and consistent race with good tyre management. Pace improved in the final stint after the Safety Car, with a best lap of 1:12.842 on lap 57.",
    items: [
      { tone: "green", text: "Good tyre management" },
      { tone: "green", text: "Strong final stint pace" },
      { tone: "amber", text: "Minor time loss in Turn 10 (laps 12–16)" },
      { tone: "green", text: "Gained 4 positions after Safety Car" },
    ] as Array<{ tone: "green" | "amber" | "red"; text: string }>,
  },
  events: {
    total: 8,
    items: [
      { lap: 1, text: "Race start", icon: "ring-green" },
      { lap: 12, text: "Lost 0.8s in Turn 10", icon: "alert-red" },
      { lap: 20, text: "Safety Car deployed", icon: "ring-amber" },
      { lap: 24, text: "Safety Car ended", icon: "check-green" },
      { lap: 57, text: "Personal best (1:12.842)", icon: "check-green", highlight: true },
      { lap: 63, text: "Gap to car ahead under 1s", icon: "ring-green" },
      { lap: 71, text: "Yellow flag in sector 2", icon: "ring-amber" },
      { lap: 78, text: "Chequered flag (P4)", icon: "check-green" },
    ] as NotableEvent[],
  },
  stints: [
    { compound: "Soft", laps: 12 },
    { compound: "Medium", laps: 42 },
    { compound: "Hard", laps: 24 },
  ] as Stint[],
  laps: {
    selected: { lap: 28, ms: T(73.42) },
    best: { lap: 57, ms: T(72.842) },
    total: 78,
  },
};

/* ------------------------------------------------------------ Lap analysis */

export type LapRef = { id: string; lap: number; ms: number; note?: string };

export const lapAnalysisFixture = {
  track: "Monaco",
  trackKey: "monaco",
  target: { id: "28", lap: 28, ms: T(73.42) } satisfies LapRef,
  reference: { id: "57", lap: 57, ms: T(72.842), note: "Session Best" } satisfies LapRef,
  region: { label: "Turn 6 – Turn 8", from: 2340, to: 2980 },
  regions: ["Turn 1 – Turn 5", "Turn 6 – Turn 8", "Turn 9 – Turn 10", "Turn 11 – Turn 19"],
  quickJumps: ["S1", "S2", "S3", "T1-5", "T6-10"],
  sectors: [
    { label: "S1", ms: T(18.42), deltaMs: -210 },
    { label: "S2", ms: T(35.128), deltaMs: 942 },
    { label: "S3", ms: T(19.872), deltaMs: -154 },
  ],
  differences: [
    { text: "+0.318s (Lap 28 slower)", emphasis: true },
    { text: "Lower minimum speed (-12 km/h)" },
    { text: "Later throttle application" },
    { text: "More steering input (less stable)" },
  ],
  quality: [
    { text: "Complete telemetry for this lap", tone: "green" as const },
    { text: "All channels available", tone: "grey" as const },
    { text: "Track model: v2.1 (Monaco)", tone: "grey" as const },
  ],
  question: {
    prompt: "Why did I lose time in Turns 6 to 8 on this lap?",
    askedAt: "18:45",
    answer:
      "You lost 0.318 seconds in Turns 6–8 compared to your reference lap. The main factors were a lower minimum speed, later throttle application, and more steering input, which suggests understeer on corner exit.",
    sections: [
      ["Scope", ["Turn 6 – Turn 8 (2,340 m – 2,980 m)"]],
      ["Evidence", ["Speed trace, throttle input, steering input, time delta"]],
      ["Related Laps", ["Target:  Lap 28 (1:13.420)", "Reference:  Lap 57  (1:12.842)"]],
      ["Limitations", ["Only in-game telemetry available. No tyre core temperatures or suspension data."]],
    ] as Array<[string, string[]]>,
  },
  chart: {
    from: 2300,
    to: 3060,
    ticks: [2400, 2600, 2800, 3000],
    band: { from: 2570, to: 2760, inner: 2640 },
  },
};

/* ---------------------------------------------------------------- Settings */

export const settingsFixture = {
  header: { items: ["F1 24 (PC)", "Connected"] },
  topics: [
    ["Tyre management", true],
    ["Track conditions", true],
    ["Race strategy", true],
    ["Driving technique", true],
    ["Gap information", true],
    ["Rule reminders", true],
    ["Setup suggestions", false],
    ["General information", false],
  ] as Array<[string, boolean]>,
  focusAreas: [
    ["Driving consistency", true],
    ["Comparative analysis", true],
    ["Corner analysis", true],
    ["Setup impact", true],
    ["Tyre performance", true],
    ["Data quality notes", false],
  ] as Array<[string, boolean]>,
  verbosity: ["Concise", "Balanced", "Detailed"],
  interruption: ["Allow interruptions", "Queue until finished", "Never interrupt"],
  depth: ["Quick", "Standard", "In-depth"],
  status: {
    microphone: { name: "Shure MV7", state: "Ready" },
    audio: { name: "MacBook Speakers", state: "Ready" },
    game: { name: "F1 24 (PC)", state: "Connected (120 ms)" },
    storage: { used: "42.6 GB used", available: "128 GB available" },
    trackModels: { version: "v2.1", state: "Up to date" },
  },
};
