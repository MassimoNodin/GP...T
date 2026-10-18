import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const typescript = require("typescript");
const featuresRoot = new URL("../src/features/", import.meta.url);

function transpile(source, fileName) {
  return typescript.transpileModule(source, {
    fileName,
    compilerOptions: {
      module: typescript.ModuleKind.ESNext,
      target: typescript.ScriptTarget.ES2022,
    },
  }).outputText;
}

function toDataUrl(code) {
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}

/** Transpiles a source file and points the listed import specifiers at data URLs. */
async function load(relativePath, imports = {}) {
  let code = transpile(await readFile(new URL(relativePath, featuresRoot), "utf8"), relativePath);
  for (const [specifier, url] of Object.entries(imports)) {
    code = code.replaceAll(`from "${specifier}"`, `from ${JSON.stringify(url)}`);
  }
  return code;
}

const urlOf = async (relativePath, imports) => toDataUrl(await load(relativePath, imports));

const telemetry = await import(await urlOf("lap-analysis/lap-telemetry.ts"));

// load.ts is exercised with the real fixtures, formatting and evidence mapping;
// only the modules that need the network or React are stubbed.
const formatUrl = await urlOf("review-data/format.ts");
const evidenceUrl = await urlOf("review-data/session-evidence.ts", { "./format": formatUrl });
const fixturesUrl = await urlOf("review-data/fixtures.ts");
const noop = "() => null";
const backendUrl = toDataUrl(`
  export const completedLaps = () => [];
  export const fetchAcquisition = async () => null;
  export const fetchEvidenceAttempts = async () => null;
  export const fetchEvidenceSession = async () => null;
  export const fetchLaps = async () => null;
  export const fetchSession = async () => null;
  export const sessionBestLap = ${noop};
  export const telemetryFooter = (_status, footer) => footer;
  export const telemetryStatus = () => ({ tone: "green", label: "Telemetry live" });
  export const TELEMETRY_OFFLINE = { tone: "red", label: "Telemetry offline", lines: [] };
`);
const overviewUrl = toDataUrl(`export const fixtureLaps = () => [{ id: "28", lap: 28, ms: 73420 }, { id: "57", lap: 57, ms: 72842 }];`);
const trackMapUrl = toDataUrl(`export const trackKeyFromName = (name) => String(name).toLowerCase();`);
const traceDataUrl = toDataUrl(`
  export const fetchEvidenceTrace = async () => ({ state: "unavailable", reason: "stub" });
  export const fetchPreviewTrace = async () => ({ state: "unavailable", reason: "stub" });
  export const fetchTrackModelLabel = async () => null;
`);
const analysis = await import(
  await urlOf("lap-analysis/load.ts", {
    "@/features/review-data/backend": backendUrl,
    "@/features/review-data/session-evidence": evidenceUrl,
    "@/features/review-data/fixtures": fixturesUrl,
    "@/features/review-data/format": formatUrl,
    "@/features/session-overview/load": overviewUrl,
    "@/features/ui-foundation/TrackMap": trackMapUrl,
    "./lap-telemetry": await urlOf("lap-analysis/lap-telemetry.ts"),
    "./trace-data": traceDataUrl,
  })
);

/* ---------------------------------------------------------------- payloads */

/** A v2 attempt-detail response with `count` samples along `length` metres. */
function attemptDetail(count, { length = 1000, withSteering = true, overrides = (_i, sample) => sample } = {}) {
  const samples = [];
  for (let i = 0; i < count; i += 1) {
    const distance = (i / (count - 1)) * length;
    samples.push(
      overrides(i, {
        session_uid: "1",
        car_index: 0,
        lap_number: 3,
        frame_ordinal: 1000 + i,
        lap_distance_m: distance,
        speed_mps: 50 + (i % 10),
        throttle: i % 2 ? 1 : 0.5,
        brake: i % 5 === 0 ? 1 : 0,
        steering: withSteering ? -0.25 : null,
        car_telemetry_available: true,
      }),
    );
  }
  return { data: { attempt: { id: "rev-3" }, samples } };
}

const lap = (id, number, ms, extra = {}) => ({
  id,
  lap: number,
  ms,
  sectors: null,
  attemptKey: null,
  revisionId: `rev-${id}`,
  valid: true,
  pit: false,
  startObserved: true,
  ...extra,
});

/* ------------------------------------------------------------------- tests */

test("v2 samples map to km/h, percent and distance-ordered runs", () => {
  const trace = telemetry.traceFromEvidence(attemptDetail(400));
  assert.equal(trace.state, "available");
  assert.equal(trace.samples, 400);
  assert.equal(trace.length, 1000);
  assert.deepEqual(Object.keys(trace.channels).sort(), ["brake", "speed", "steering", "throttle"]);

  const [speedRun] = trace.channels.speed;
  assert.deepEqual(speedRun[0], [0, 50 * 3.6]); // 50 m/s -> 180 km/h at the first sample
  assert.equal(trace.channels.steering[0][0][1], -25); // -0.25 of full lock -> -25 %
  assert.equal(trace.channels.brake[0][0][1], 100);
  assert.equal(trace.channels.throttle[0][0][1], 50);

  for (const runs of Object.values(trace.channels)) {
    for (const run of runs) {
      for (let i = 1; i < run.length; i += 1) assert.ok(run[i][0] >= run[i - 1][0], "distance never decreases");
    }
  }
  // 400 samples over 1,000 m are thinned to roughly one per five metres.
  assert.ok(trace.channels.speed[0].length < 250, `thinned, got ${trace.channels.speed[0].length}`);
  assert.equal(trace.channels.speed[0].at(-1)[0], 1000, "last sample is kept");
});

test("missing channel values break the run and a fully missing channel is dropped", () => {
  const gap = telemetry.traceFromEvidence(
    attemptDetail(200, { overrides: (i, sample) => (i >= 90 && i < 110 ? { ...sample, throttle: null } : sample) }),
  );
  assert.equal(gap.state, "available");
  assert.equal(gap.channels.throttle.length, 2);
  assert.equal(gap.channels.speed.length, 1);

  const noSteering = telemetry.traceFromEvidence(attemptDetail(200, { withSteering: false }));
  assert.equal(noSteering.state, "available");
  assert.equal(noSteering.channels.steering, undefined);
});

test("samples that jump backwards, lack a distance or lack car telemetry are ignored", () => {
  const trace = telemetry.traceFromEvidence(
    attemptDetail(200, {
      overrides: (i, sample) => {
        if (i === 50) return { ...sample, lap_distance_m: 3 };
        if (i === 60) return { ...sample, lap_distance_m: null };
        if (i === 70) return { ...sample, car_telemetry_available: false };
        return sample;
      },
    }),
  );
  assert.equal(trace.state, "available");
  assert.equal(trace.samples, 197);
});

test("missing or insufficient samples give an unavailable state with a reason", () => {
  for (const body of [null, {}, { data: null }, { data: { samples: "nope" } }, { data: { samples: [] } }]) {
    const trace = telemetry.traceFromEvidence(body);
    assert.equal(trace.state, "unavailable");
    assert.match(trace.reason, /no telemetry samples/i);
  }
  const few = telemetry.traceFromEvidence(attemptDetail(5));
  assert.equal(few.state, "unavailable");
  assert.match(few.reason, /too few/i);

  const noDistance = telemetry.traceFromEvidence(attemptDetail(100, { overrides: (_i, sample) => ({ ...sample, lap_distance_m: null }) }));
  assert.equal(noDistance.state, "unavailable");

  assert.match(telemetry.traceUnavailableReason("trace_not_selected"), /not selected/i);
  assert.match(telemetry.traceUnavailableReason("comparison_read_budget_busy"), /busy/i);
  assert.match(telemetry.traceUnavailableReason("something_unknown"), /could not be read/i);
  assert.equal(telemetry.unavailableTrace("not_processed").state, "unavailable");
});

test("v1 trace previews map onto the same series", () => {
  const points = Array.from({ length: 60 }, (_, i) => ({ lap_distance_m: i * 10, value: i }));
  const body = {
    data: {
      status: "observed",
      channels: {
        speed: { segments: [{ points }] },
        steering: { segments: [{ points: points.map((p) => ({ ...p, value: -0.5 })) }] },
        throttle: { segments: [] },
      },
    },
  };
  const trace = telemetry.traceFromPreview(body);
  assert.equal(trace.state, "available");
  assert.equal(trace.length, 590);
  assert.equal(trace.channels.speed[0].length > 2, true);
  assert.equal(trace.channels.steering[0][0][1], -50);
  assert.equal(trace.channels.throttle, undefined);
  assert.equal(telemetry.traceFromPreview({ data: null }).state, "unavailable");
});

test("the distance axis covers the longest available trace", () => {
  const long = telemetry.traceFromEvidence(attemptDetail(300, { length: 5412 }));
  const short = telemetry.traceFromEvidence(attemptDetail(300, { length: 5000 }));
  const axis = telemetry.axisFor([short, long, telemetry.unavailableTrace(null), undefined]);
  assert.equal(axis.from, 0);
  assert.equal(axis.to, 5450);
  assert.ok(axis.ticks.length <= 9 && axis.ticks[0] === 0);
  assert.equal(telemetry.axisFor([telemetry.unavailableTrace(null), undefined]), null);
});

test("differences and quality come from lap times, flags and measured traces", () => {
  const slow = telemetry.traceFromEvidence(attemptDetail(300, { overrides: (i, s) => ({ ...s, speed_mps: 40 + (i % 10), throttle: 0.6 }) }));
  const fast = telemetry.traceFromEvidence(attemptDetail(300, { overrides: (i, s) => ({ ...s, speed_mps: 50 + (i % 10), throttle: 1 }) }));
  const target = lap("5", 5, 93943, { pit: true });
  const reference = lap("3", 3, 92252);

  const differences = telemetry.lapDifferences(target, reference, slow, fast);
  assert.equal(differences[0].text, "+1.691s (Lap 5 slower)");
  assert.equal(differences[0].emphasis, true);
  assert.ok(differences.some((item) => /Lower minimum speed \(-36 km\/h\)/.test(item.text)), JSON.stringify(differences));
  assert.ok(differences.some((item) => /Less time at full throttle/.test(item.text)));

  // Without traces only the lap-time difference is known.
  const timesOnly = telemetry.lapDifferences(target, reference, telemetry.unavailableTrace(null), undefined);
  assert.deepEqual(timesOnly.map((item) => item.text), ["+1.691s (Lap 5 slower)"]);
  assert.deepEqual(telemetry.lapDifferences(target, null, slow, undefined), []);
  assert.equal(telemetry.lapDifferences(target, target, slow, slow).length, 1);

  const quality = telemetry.lapQuality({
    target,
    reference,
    targetTrace: slow,
    referenceTrace: telemetry.unavailableTrace("not_processed"),
    lapDataNote: "Only the first attempts of this session could be read, so some laps may be missing.",
    trackModel: null,
  });
  const texts = quality.map((item) => item.text);
  assert.ok(texts.includes("Lap 5 is valid per the game"));
  assert.ok(texts.includes("Lap 5 includes the pit lane"));
  assert.ok(texts.includes("Complete telemetry for Lap 5"));
  assert.ok(texts.includes("No telemetry samples for Lap 3"));
  assert.ok(texts.every((text) => !/track model/i.test(text)));
  assert.ok(texts.some((text) => /could be read/.test(text)));
  assert.doesNotMatch(JSON.stringify({ differences, timesOnly, quality }), /monaco/i);
});

test("track model labels match the circuit by name and length", () => {
  const models = [
    { track_name: "Melbourne", track_length_m: 5276, revision: 1, validation_status: "draft" },
    { track_name: "Jeddah", track_length_m: 6175, revision: 1, validation_status: "draft" },
    { track_name: "Jeddah", track_length_m: 6175, revision: 2, validation_status: "validated" },
    { track_name: "Jeddah", track_length_m: 3000, revision: 9, validation_status: "validated" },
  ];
  assert.equal(telemetry.trackModelLabel(models, "jeddah", 6180), "Jeddah r2 (validated)");
  assert.equal(telemetry.trackModelLabel(models, "Sakhir", 5412), null);
  assert.equal(telemetry.trackModelLabel(null, "Jeddah", 6175), null);
  assert.equal(telemetry.trackModelLabel(models, null, null), null);
});

const SESSION = {
  id: "4a09407f07885540b29221e7636ec37f",
  generation: "58c75388",
  uid: "1748447935434161293",
  lifecycle: "ended",
  acquisition: "receiving",
  occurrence: 1,
  context: {
    trackName: "Jeddah",
    sessionType: "practice_1",
    weatherName: "clear",
    airTemperatureC: 26,
    trackTemperatureC: 36,
    packetFormat: 2025,
    ruleSet: "practice_qualifying",
    trackLengthM: 6175,
    totalLaps: 1,
  },
};

function attempt(id, lapNumber, ms) {
  return {
    id,
    role: "player",
    publishedSequence: lapNumber,
    attemptNumber: lapNumber,
    lapNumber,
    lapTimeMs: ms,
    disposition: "completed",
    gameValid: true,
    pitEncountered: false,
    startObserved: true,
    superseded: false,
    traceAvailable: lapNumber !== 2,
    context: null,
  };
}

test("a real v2 session model never carries Monaco reference content", async () => {
  const model = analysis.evidenceAnalysisModel(
    SESSION,
    [attempt("rev-1", 1, 95367), attempt("rev-2", 2, 93834), attempt("rev-3", 3, 93996)],
    false,
  );
  assert.equal(model.source, "v2");
  assert.equal(model.track, "Jeddah");
  assert.deepEqual(model.telemetry, { kind: "real", traces: {} });
  assert.equal(model.trackModel, null);
  assert.equal(model.laps[1].traceAvailable, false);
  assert.equal(model.laps[0].valid, true);
  assert.equal(model.laps[0].attemptKey, null);
  assert.doesNotMatch(JSON.stringify(model), /monaco/i);

  const unknown = analysis.evidenceAnalysisModel({ ...SESSION, context: null }, [attempt("rev-1", 1, 95000)], true);
  assert.equal(unknown.track, "Unknown circuit");
  assert.match(unknown.lapDataNote, /first attempts/);
  assert.doesNotMatch(JSON.stringify(unknown), /monaco/i);
});

test("the fixture session keeps its illustrative reference telemetry", async () => {
  const model = await analysis.loadLapAnalysis("monaco-2024-race", undefined, undefined);
  assert.equal(model.source, "fixture");
  assert.deepEqual(model.telemetry, { kind: "reference" });
  assert.equal(model.targetId, "28");
  assert.equal(model.referenceId, "57");
  assert.equal(model.trackModel, null);
});
