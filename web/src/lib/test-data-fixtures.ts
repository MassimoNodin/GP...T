// Server-side fixture generation. Imported only by the read-only test-data API.
import type { AnalysisInput, TraceChannel } from "./analysis-display";
import type {
  TestDataScenario,
  TestDataSnapshot,
  TestLap,
  TestSession,
} from "./test-data";

const tracks = [
  {
    id: "test-session-monza",
    track: "Monza",
    lengthM: 5793,
    baseMs: 80095,
    names: [
      "Rettifilo",
      "Curva Grande",
      "Roggia",
      "Lesmo 1",
      "Lesmo 2",
      "Ascari",
      "Parabolica",
    ],
    anchors: [
      [0, 0],
      [30, -155],
      [80, -195],
      [110, -180],
      [155, -280],
      [225, -300],
      [235, -240],
      [200, -185],
      [270, -110],
      [330, -95],
      [350, -50],
      [300, 20],
      [180, 30],
      [100, 50],
      [0, 0],
    ],
  },
  {
    id: "test-session-zandvoort",
    track: "Zandvoort",
    lengthM: 4259,
    baseMs: 72904,
    names: [
      "Tarzan",
      "Hugenholtz",
      "Hunserug",
      "Scheivlak",
      "Masters",
      "Arie Luyendyk",
    ],
    anchors: [
      [0, 0],
      [10, -130],
      [90, -155],
      [120, -90],
      [80, -60],
      [160, -40],
      [200, -130],
      [260, -110],
      [275, -35],
      [210, 20],
      [160, 65],
      [60, 70],
      [0, 0],
    ],
  },
  {
    id: "test-session-spa",
    track: "Spa",
    lengthM: 7004,
    baseMs: 103227,
    names: [
      "La Source",
      "Eau Rouge",
      "Les Combes",
      "Bruxelles",
      "Pouhon",
      "Blanchimont",
      "Bus Stop",
    ],
    anchors: [
      [0, 0],
      [-35, -65],
      [55, -95],
      [210, -180],
      [270, -100],
      [225, -55],
      [280, 50],
      [185, 105],
      [130, 65],
      [45, 145],
      [-30, 70],
      [0, 0],
    ],
  },
];
const offsets = [2104, 809, 1420, 608, 1903, 1230, 622, 0, 846, 908, 513, 317];
const round = (n: number) => Math.round(n * 1000) / 1000;

function lapList(baseMs: number): TestLap[] {
  return offsets.map((offset, i) => {
    const timeMs = baseMs + offset;
    const first = Math.round(timeMs * 0.341);
    const second = Math.round(timeMs * 0.356);
    return {
      id: `test-lap-${i + 1}`,
      number: i + 1,
      timeMs,
      sectorsMs: [first, second, timeMs - first - second],
      valid: i !== 4,
      disposition: i === 4 ? "Invalid" : i === 0 ? "Out lap" : "Push",
      tyre: "Medium",
    };
  });
}

function analysisFor(
  track: (typeof tracks)[number],
  target: TestLap,
  reference: TestLap,
  partial: boolean,
): AnalysisInput {
  const distances = Array.from({ length: 241 }, (_, i) =>
    round((i * track.lengthM) / 240),
  );
  function trace(lap: TestLap) {
    const values: Record<TraceChannel, (number | null)[]> = {
      speed: [],
      throttle: [],
      brake: [],
      gear: [],
      steering: [],
    };
    for (let i = 0; i < distances.length; i++) {
      const phase = i / 240;
      const corner = Math.max(
        0,
        Math.sin(phase * Math.PI * 14 + lap.number * 0.025),
      );
      const speed = round(
        322 -
          190 * corner ** 6 -
          (lap.number % 4) * Math.sin(phase * Math.PI * 8),
      );
      const missing = partial && i >= 95 && i <= 110;
      values.speed.push(missing ? null : speed);
      values.throttle.push(missing ? null : round(1 - 0.94 * corner ** 8));
      values.brake.push(
        missing
          ? null
          : round(
              Math.max(0, Math.sin(phase * Math.PI * 14 + 0.35)) ** 14 * 0.93,
            ),
      );
      values.gear.push(
        missing ? null : Math.max(2, Math.min(8, Math.floor(speed / 43) + 1)),
      );
      values.steering.push(
        missing ? null : round(Math.sin(phase * Math.PI * 14) * corner * 0.55),
      );
    }
    // Normalize accumulated sample time to each fixture lap time so endpoint delta agrees.
    const elapsed = [0];
    for (let i = 1; i < distances.length; i++) {
      const speed = values.speed[i] ?? 180;
      elapsed.push(
        elapsed[i - 1] + (distances[i] - distances[i - 1]) / (speed / 3.6),
      );
    }
    const scale = lap.timeMs / 1000 / elapsed[elapsed.length - 1];
    return {
      values,
      elapsed: elapsed.map((t) => t * scale),
      label: `Test lap ${lap.number}`,
    };
  }
  const a = trace(target),
    b = trace(reference);
  const deltaS = a.elapsed.map((t, i) =>
    partial && i >= 95 && i <= 110 ? null : round(t - b.elapsed[i]),
  );
  const regions = track.names.map((name, i) => {
    const start = Math.round((i * 240) / track.names.length),
      end = Math.round(((i + 1) * 240) / track.names.length);
    const supported = !partial || end < 95 || start > 110;
    const delta = supported
      ? round(
          a.elapsed[end] -
            b.elapsed[end] -
            (a.elapsed[start] - b.elapsed[start]),
        )
      : null;
    const minimum = (values: (number | null)[]) =>
      Math.min(
        ...values.slice(start, end).filter((v): v is number => v !== null),
      );
    return {
      id: `test-region-${i + 1}`,
      label: name,
      startM: distances[start],
      endM: distances[end],
      status:
        delta === null
          ? ("unknown" as const)
          : Math.abs(delta) <= 0.01
            ? ("similar" as const)
            : delta < 0
              ? ("faster" as const)
              : ("slower" as const),
      deltaS: delta,
      minimumSpeedKph: supported ? minimum(a.values.speed) : null,
      referenceMinimumSpeedKph: supported ? minimum(b.values.speed) : null,
      throttlePickupM: supported
        ? distances[Math.min(end - 1, start + 8)]
        : null,
      referenceThrottlePickupM: supported
        ? distances[Math.min(end - 1, start + 7)]
        : null,
    };
  });
  const lengths = [0];
  for (let i = 1; i < track.anchors.length; i++)
    lengths.push(
      lengths[i - 1] +
        Math.hypot(
          track.anchors[i][0] - track.anchors[i - 1][0],
          track.anchors[i][1] - track.anchors[i - 1][1],
        ),
    );
  const points = track.anchors.map(([x, z], i) => ({
    x,
    z,
    distanceM: round(
      (lengths[i] / lengths[lengths.length - 1]) * track.lengthM,
    ),
  }));
  const segments = partial ? [points.slice(0, 5), points.slice(6)] : [points];
  return {
    version: 1,
    track: {
      name: `${track.track} · synthetic supplied shape`,
      lengthM: track.lengthM,
      geometryKind: "supplied",
      similarThresholdS: 0.01,
      segments,
      regions,
    },
    comparison: {
      distanceM: distances,
      target: { label: a.label, values: a.values },
      reference: { label: b.label, values: b.values },
      deltaS,
      deltaMask: deltaS.map((v) => v !== null),
    },
  };
}

export function generateTestData(options: {
  scenario: TestDataScenario;
  session?: string;
  target?: string;
  reference?: string;
  tick: number;
}): TestDataSnapshot {
  const { scenario, tick } = options;
  const sessions: TestSession[] = tracks.map((t, i) => ({
    id: t.id,
    track: t.track,
    lengthM: t.lengthM,
    type: "Practice",
    startedAt: `2026-09-0${8 - i}T14:02:00Z`,
    status: "Processed",
    lapCount: 12,
    bestTimeMs: t.baseMs,
    runId: `test-run-${i + 1}`,
  }));
  const track =
    options.session === undefined
      ? tracks[0]
      : tracks.find((t) => t.id === options.session);
  if (!track) throw new Error("Unknown test session.");
  const laps = lapList(track.baseMs);
  const target =
    options.target === undefined
      ? laps[11]
      : laps.find((lap) => lap.id === options.target);
  const reference =
    options.reference === undefined
      ? laps[7]
      : laps.find((lap) => lap.id === options.reference);
  if (!target || !reference) throw new Error("Unknown test lap.");
  const empty = scenario === "empty",
    partial = scenario === "partial",
    stale = scenario === "stale";
  const analysis = empty
    ? { version: 1 as const }
    : analysisFor(track, target, reference, partial);
  const sample = Math.floor(((tick % 120) / 120) * 240);
  const sampleValue = (channel: TraceChannel) =>
    analysis.comparison?.target.values[channel]?.[sample] ?? null;
  const elapsedMs = 1122000 + tick * 1000;
  return {
    schema_version: 1,
    source: {
      kind: "synthetic",
      fixture_version: "ui-test-v1",
      scenario,
      tick,
      at_utc: new Date(Date.UTC(2026, 8, 8, 14, 2) + tick * 1000).toISOString(),
    },
    diagnostic_only: true,
    coaching_eligible: false,
    ranking_eligible: false,
    sessions: empty ? [] : sessions,
    laps: empty ? [] : laps,
    selected: {
      sessionId: empty ? null : track.id,
      targetId: empty ? null : target.id,
      referenceId: empty ? null : reference.id,
    },
    recording: empty
      ? null
      : {
          status: stale ? "Paused fixture" : "Simulated capture",
          elapsedMs,
          packets: 1248592 + tick * 124,
          queueDrops: partial ? 32 : 0,
          bytes: 2400000000 + tick * 16384,
          rate: 124,
        },
    live: empty
      ? null
      : {
          status: stale ? "stale" : "fresh",
          ageMs: stale ? 15000 + tick * 1000 : 25,
          lap: target.number,
          lapTimeMs: (tick % 120) * 1000,
          speedKph: sampleValue("speed"),
          rpm:
            sampleValue("speed") === null
              ? null
              : Math.round(4500 + sampleValue("speed")! * 25),
          gear: sampleValue("gear"),
          throttle: sampleValue("throttle"),
          brake: sampleValue("brake"),
          fuelReported: partial ? null : round(22.4 - tick * 0.003),
          ersPercent: partial ? null : 78,
          drs: partial ? null : tick % 20 > 12,
          airC: 28,
          trackC: partial ? null : 33,
          tyreWearPercent: partial ? [18, null, 21, 19] : [18, 17, 21, 19],
          tyreTemperatureC: partial ? [92, null, 94, 93] : [92, 91, 94, 93],
          distanceM: round(((tick % 120) / 120) * track.lengthM),
        },
    captures: empty
      ? []
      : sessions.map((s, i) => ({
          id: `test-capture-${i + 1}`,
          name: `${s.track}_Practice_test_capture`,
          sessionId: s.id,
          bytes: 1800000000 + i * 210000000,
          laps: 12,
          durationMs: s.bestTimeMs * 12,
          status: i === 1 ? "Imported" : "Completed",
          packetLossPercent: partial ? 2.1 : 0.3,
        })),
    imports: empty
      ? []
      : sessions.map((s, i) => ({
          id: `test-import-${i + 1}`,
          name: `${s.track}_test.f1ecap`,
          track: s.track,
          progress:
            partial && i === 2
              ? null
              : i === 0
                ? Math.min(99, 65 + (tick % 35))
                : i === 1
                  ? 100
                  : 0,
          status: i === 0 ? "Importing" : i === 1 ? "Complete" : "Queued",
        })),
    lifecycle: empty
      ? []
      : [
          "Imported",
          "Parsing",
          "Processing",
          "Generating attempts",
          "Completed",
        ].map((label, i) => ({
          label,
          at: `14:${String(2 + i * 2).padStart(2, "0")}:17`,
          status: "Complete",
        })),
    inventory: empty
      ? []
      : [
          "Lap charts",
          "Telemetry traces",
          "Track map data",
          "Event logs",
          "Analysis notes",
        ].map((label, i) => ({
          label,
          count: partial && i === 2 ? null : [12, 12, 1, 1, 3][i],
          status: partial && i === 2 ? "Unavailable" : "Available",
        })),
    storage: empty
      ? null
      : {
          usedBytes: 42600000000,
          budgetBytes: 200000000000,
          categories: [
            { label: "Recordings", bytes: 28400000000 },
            { label: "Database", bytes: 8100000000 },
            { label: "Logs", bytes: 3200000000 },
            { label: "Temporary", bytes: 2900000000 },
          ],
        },
    runtime: empty
      ? null
      : {
          model: "Qwen3 4B · fixture",
          provider: "Deterministic test API",
          status: partial ? "Unavailable fixture" : "Simulated ready",
          voice: "Synthetic voice profile (no audio)",
        },
    settings: empty
      ? null
      : {
          udpHost: "127.0.0.1",
          udpPort: 20777,
          queueSize: 1000,
          hudPosition: "Top right",
          hudOpacity: 80,
          hudTheme: "Light",
          voiceSpeed: 1,
          voiceVolume: 80,
          devices: partial ? [] : ["Test wheel", "Test button box"],
        },
    engineer: empty
      ? null
      : {
          question: `How does test lap ${target.number} compare with test lap ${reference.number}?`,
          answer: `This API-generated example has a lap-time difference of ${round((target.timeMs - reference.timeMs) / 1000)} s. Select a named region to inspect its supplied timing and telemetry.`,
          limitations: [
            "Synthetic fixture output; no model inference or coaching.",
            "Track shape and telemetry are generated examples, not recorded game evidence.",
            ...(partial
              ? [
                  "The partial scenario deliberately omits telemetry and geometry intervals.",
                ]
              : []),
          ],
        },
    analysis,
  };
}
