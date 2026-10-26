import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

// Pure modules behind the Lap Analysis views: delta, corner metrics, input
// statistics, trace parsing (gear, coverage, path), regions and URL state.
// Transpiled with the same pattern as lap-analysis.test.mjs; dependencies are
// wired by specifier so no network or React is needed.

const require = createRequire(import.meta.url);
const typescript = require("typescript");
const featuresRoot = new URL("../src/features/", import.meta.url);

const cache = new Map();

/** Loads a feature module; `imports` maps import specifiers to other feature modules (loaded the same way). */
async function load(relativePath, imports = {}) {
  if (cache.has(relativePath)) return cache.get(relativePath);
  const source = await readFile(new URL(relativePath, featuresRoot), "utf8");
  let code = typescript.transpileModule(source, {
    fileName: relativePath,
    compilerOptions: { module: typescript.ModuleKind.ESNext, target: typescript.ScriptTarget.ES2022 },
  }).outputText;
  for (const [specifier, target] of Object.entries(imports)) {
    const url = `data:text/javascript;base64,${Buffer.from(await moduleCode(target)).toString("base64")}`;
    code = code.replaceAll(`from "${specifier}"`, `from ${JSON.stringify(url)}`);
  }
  const module = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
  cache.set(relativePath, module);
  return module;
}

// A data-URL module cannot import another by path, so dependencies are inlined as nested data URLs.
async function moduleCode(spec) {
  const [relativePath, imports = {}] = Array.isArray(spec) ? spec : [spec];
  const source = await readFile(new URL(relativePath, featuresRoot), "utf8");
  let code = typescript.transpileModule(source, {
    fileName: relativePath,
    compilerOptions: { module: typescript.ModuleKind.ESNext, target: typescript.ScriptTarget.ES2022 },
  }).outputText;
  for (const [specifier, target] of Object.entries(imports)) {
    const url = `data:text/javascript;base64,${Buffer.from(await moduleCode(target)).toString("base64")}`;
    code = code.replaceAll(`from "${specifier}"`, `from ${JSON.stringify(url)}`);
  }
  return code;
}

const TELEMETRY = "lap-analysis/lap-telemetry.ts";
const DELTA = ["lap-analysis/delta.ts", { "./lap-telemetry": TELEMETRY }];
const telemetry = await load(TELEMETRY);
const delta = await load(...DELTA);
const regions = await load("lap-analysis/regions.ts", {
  "./delta": DELTA,
  "./lap-telemetry": TELEMETRY,
});
const inputs = await load("lap-analysis/inputs-stats.ts", { "./lap-telemetry": TELEMETRY });
const geometry = await load("lap-analysis/track-geometry.ts");
const urlState = await load("lap-analysis/url-state.ts");
const reviewRegions = await load("review-data/regions.ts", { "./parse-utils": "review-data/parse-utils.ts" });
const exporter = await load("lap-analysis/export.ts", {
  "@/features/review-data/format": "review-data/format.ts",
  "./delta": DELTA,
  "./lap-telemetry": TELEMETRY,
});
const enrich = await load("lap-analysis/enrich.ts", { "@/features/review-data/reasons": "review-data/reasons.ts" });

/* ----------------------------------------------------------------- builders */

/** An available trace whose channels are given as arrays of [distance, value] runs. */
function trace(channels, { length = 1000, coverage, path = null } = {}) {
  const covered =
    coverage ??
    Object.fromEntries(
      Object.entries(channels).map(([key, runs]) => [
        key,
        { metres: runs.reduce((total, run) => total + (run.at(-1)[0] - run[0][0]), 0), runs: runs.length },
      ]),
    );
  return { state: "available", samples: 200, length, channels, coverage: covered, path };
}

/** Constant-speed run every 5 m over `length` metres. */
function flat(value, length = 1000, from = 0) {
  const run = [];
  for (let d = from; d <= length; d += 5) run.push([d, value]);
  return run;
}

/** Speed in km/h as a function of distance, every 5 m. */
function profile(f, length = 1000) {
  const run = [];
  for (let d = 0; d <= length; d += 5) run.push([d, f(d)]);
  return run;
}

/* -------------------------------------------------------------- tests: delta */

test("delta of identical laps is zero and normalised to the lap time", () => {
  const speed = trace({ speed: [flat(180)] });
  const series = delta.deltaSeries(speed, speed, 20000, 20000, 1000);
  assert.ok(delta.isDelta(series));
  assert.equal(series.endMs, 0);
  assert.ok(series.points.every(([, value]) => Math.abs(value) < 1e-9));
  assert.equal(series.points[0][0], 0);
  assert.equal(series.points.at(-1)[0], 1000);
});

test("delta end equals the official lap time difference whatever the speeds integrate to", () => {
  const slow = trace({ speed: [flat(180)] });
  const fast = trace({ speed: [flat(200)] });
  // Official times disagree with the integrated ones on purpose; normalisation must win.
  const series = delta.deltaSeries(slow, fast, 21000, 19500, 1000);
  assert.ok(delta.isDelta(series));
  assert.equal(series.endMs, 1500);
  assert.ok(series.points.at(-1)[1] > 1.49 && series.points.at(-1)[1] < 1.51);
  // Cumulative times end exactly at the official lap times.
  assert.ok(Math.abs(series.targetTime.at(-1)[1] - 21) < 1e-9);
  assert.ok(Math.abs(series.referenceTime.at(-1)[1] - 19.5) < 1e-9);
  // The target loses time steadily: the delta never decreases for constant speeds.
  for (let i = 1; i < series.points.length; i += 1) assert.ok(series.points[i][1] >= series.points[i - 1][1] - 1e-9);
  assert.ok(Math.abs(delta.deltaAt(series, 500) - 0.75) < 0.01);
  assert.ok(Math.abs(delta.deltaBetween(series, 250, 750) - 0.75) < 0.01);
});

test("slow stretches are floored at 5 km/h instead of dividing by zero", () => {
  const stopped = trace({ speed: [profile((d) => (d > 400 && d < 420 ? 0 : 150))] });
  const series = delta.deltaSeries(stopped, stopped, 30000, 30000, 1000);
  assert.ok(delta.isDelta(series));
  assert.ok(series.targetTime.every(([, t]) => Number.isFinite(t)));
});

test("delta is unavailable without a reference, lap times or enough speed coverage", () => {
  const full = trace({ speed: [flat(180)] });
  assert.match(delta.deltaSeries(full, undefined, 20000, 20000).unavailable, /reference lap/i);
  assert.match(delta.deltaSeries(full, full, null, 20000, 1000).unavailable, /lap time/i);
  const partial = trace({ speed: [flat(180, 600)] }, { length: 1000 });
  assert.equal(delta.deltaSeries(partial, full, 20000, 20000, 1000).unavailable, delta.DELTA_INCOMPLETE);
  const unavailable = telemetry.unavailableTrace("not_processed");
  assert.equal(delta.deltaSeries(unavailable, full, 20000, 20000, 1000).unavailable, delta.DELTA_INCOMPLETE);
  // Coverage counts metres with data, not the span: a 40% hole fails even over a full-length trace.
  const holes = trace({ speed: [flat(180, 300), flat(180, 1000, 700)] }, { length: 1000 });
  assert.equal(delta.deltaSeries(holes, full, 20000, 20000, 1000).unavailable, delta.DELTA_INCOMPLETE);
});

test("signed areas split at the zero crossings and chart extents stay symmetric", () => {
  const points = [
    [0, 0.1],
    [10, 0.3],
    [20, -0.1],
    [30, -0.3],
    [40, 0.2],
  ];
  const areas = delta.signedAreas(points);
  assert.equal(areas.positive.length, 2);
  assert.equal(areas.negative.length, 1);
  const crossing = areas.negative[0][0];
  assert.equal(crossing[1], 0);
  assert.ok(crossing[0] > 10 && crossing[0] < 20);
  assert.ok(Math.abs(crossing[0] - 17.5) < 1e-9); // 0.3 -> -0.1 crosses three quarters of the way
  assert.equal(delta.deltaExtent(points, 0, 40), 0.5);
  assert.equal(delta.deltaExtent([[0, 0]], 0, 10), 0.25);
  assert.equal(delta.deltaExtent([[0, 4]], 0, 10), 5);
});

/* --------------------------------------------------------- tests: corner metrics */

/** A lap with one corner at 400-600 m: brakes at `brakeAt`, slowest 80 km/h at 500 m, flat out again from `throttleAt`. */
function cornerLap({ brakeAt, throttleAt, minKph = 80 }) {
  const speed = profile((d) => {
    if (d < brakeAt) return 220;
    if (d < 500) return 220 - ((d - brakeAt) / (500 - brakeAt)) * (220 - minKph);
    if (d < 600) return minKph + ((d - 500) / 100) * (200 - minKph);
    return 200;
  });
  const brake = profile((d) => (d >= brakeAt && d < brakeAt + 60 ? 80 : 0));
  const throttle = profile((d) => (d >= brakeAt && d < throttleAt ? 0 : 100));
  return trace({ speed: [speed], brake: [brake], throttle: [throttle] });
}

test("corner metrics compare minimum speed, time, brake point and throttle pickup", () => {
  const target = cornerLap({ brakeAt: 340, throttleAt: 540, minKph: 76 });
  const reference = cornerLap({ brakeAt: 330, throttleAt: 530, minKph: 80 });
  const series = delta.deltaSeries(target, reference, 21000, 20500, 1000);
  const metrics = regions.cornerMetrics({ range: { from: 400, to: 600 }, apexM: 500 }, target, reference, series);
  assert.equal(Math.round(metrics.minKph.target), 76);
  assert.equal(Math.round(metrics.minKph.reference), 80);
  assert.equal(metrics.minAtM.target, 500);
  assert.ok(metrics.entryKph.target > 100 && metrics.exitKph.target > 150);
  assert.ok(metrics.timeS.target > metrics.timeS.reference, "the slower corner takes longer");
  assert.ok(metrics.deltaTimeS > 0);
  assert.ok(Math.abs(metrics.deltaTimeS - (metrics.timeS.target - metrics.timeS.reference)) < 0.2);
  assert.equal(Math.round(metrics.brakePointM.target - metrics.brakePointM.reference), 10);
  assert.equal(regions.distanceDifferenceLabel(metrics.brakePointM.target, metrics.brakePointM.reference), "10 m later");
  assert.equal(regions.distanceDifferenceLabel(metrics.throttlePickupM.target, metrics.throttlePickupM.reference), "10 m later");
});

test("corner metrics tolerate a missing reference and missing channels", () => {
  const target = cornerLap({ brakeAt: 340, throttleAt: 540 });
  const metrics = regions.cornerMetrics({ range: { from: 400, to: 600 }, apexM: null }, target, undefined, null);
  assert.ok(metrics.minKph.target !== null);
  assert.equal(metrics.minKph.reference, null);
  assert.equal(metrics.timeS.target, null);
  assert.equal(metrics.deltaTimeS, null);
  assert.equal(regions.distanceDifferenceLabel(metrics.brakePointM.target, metrics.brakePointM.reference), null);
  const speedOnly = trace({ speed: [flat(150)] });
  const bare = regions.cornerMetrics({ range: { from: 400, to: 600 }, apexM: 500 }, speedOnly, speedOnly, null);
  assert.equal(bare.brakePointM.target, null);
  assert.equal(bare.throttlePickupM.target, null);
  assert.equal(regions.distanceDifferenceLabel(100, 101), "same");
  assert.equal(regions.distanceDifferenceLabel(90, 100), "10 m earlier");
});

/* ------------------------------------------------------------ tests: input stats */

test("input statistics measure throttle, coasting, braking zones and steering", () => {
  const throttle = profile((d) => (d < 400 ? 100 : d < 600 ? 0 : 100));
  const brake = profile((d) => {
    if (d >= 380 && d < 420) return 60;
    if (d >= 430 && d < 450) return 100; // within 20 m of the previous zone: the same braking zone
    if (d >= 700 && d < 720) return 30;
    return 0;
  });
  const steering = profile((d) => (d < 500 ? 0 : 10)); // one 10% step in 1,000 m
  const stats = inputs.inputStats(trace({ speed: [flat(100)], throttle: [throttle], brake: [brake], steering: [steering] }), null);
  assert.ok(stats.fullThrottlePct > 75 && stats.fullThrottlePct < 85);
  assert.equal(stats.brakingZones, 2);
  assert.equal(stats.peakBrakePct, 100);
  assert.ok(stats.brakingDistanceM > 50 && stats.brakingDistanceM < 100);
  assert.ok(stats.coastingPct > 10 && stats.coastingPct < 25);
  assert.ok(Math.abs(stats.steeringPer100m - 1) < 0.05);
  assert.equal(stats.gearChanges, null, "no gear channel, no gear statistic");
});

test("input statistics respect the selected range and missing channels", () => {
  const throttle = profile((d) => (d < 400 ? 100 : 0));
  const lap = trace({ speed: [flat(100)], throttle: [throttle] });
  const early = inputs.inputStats(lap, { from: 0, to: 300 });
  const late = inputs.inputStats(lap, { from: 500, to: 900 });
  assert.equal(Math.round(early.fullThrottlePct), 100);
  assert.equal(Math.round(late.fullThrottlePct), 0);
  assert.equal(early.brakingZones, null);
  assert.equal(early.peakBrakePct, null);
  assert.equal(early.coastingPct, null, "coasting needs the brake channel");
  assert.equal(inputs.inputStats(telemetry.unavailableTrace(null)).fullThrottlePct, null);
  const geared = trace({ speed: [flat(100)], gear: [profile((d) => (d < 300 ? 3 : d < 600 ? 4 : 5))] });
  assert.equal(inputs.inputStats(geared).gearChanges, 2);
});

/* ------------------------------------------------- tests: trace parsing and helpers */

function evidenceBody(count, overrides = (_i, sample) => sample, length = 1000) {
  const samples = [];
  for (let i = 0; i < count; i += 1) {
    samples.push(
      overrides(i, {
        frame_ordinal: 100 + i,
        lap_distance_m: (i / (count - 1)) * length,
        speed_mps: 50,
        throttle: 1,
        brake: 0,
        steering: 0.1,
        gear: 4,
        car_telemetry_available: true,
        motion_available: true,
        world_position_x_m: i * 3,
        world_position_z_m: 100 + Math.sin(i / 10) * 20,
      }),
    );
  }
  return { data: { attempt: { id: "rev" }, samples } };
}

test("v2 samples carry gear, channel coverage and the measured path", () => {
  const parsed = telemetry.traceFromEvidence(evidenceBody(400));
  assert.equal(parsed.state, "available");
  assert.ok(parsed.channels.gear);
  assert.equal(parsed.channels.gear[0][0][1], 4);
  assert.ok(parsed.coverage.speed.metres > 995);
  assert.equal(parsed.coverage.speed.runs, 1);
  assert.ok(Array.isArray(parsed.path) && parsed.path.length > 100);
  assert.deepEqual(parsed.path[0], [0, 0, 100 + Math.sin(0) * 20]);
  for (let i = 1; i < parsed.path.length; i += 1) assert.ok(parsed.path[i][0] - parsed.path[i - 1][0] >= 4, "path thinned to about 4 m");
  // Gear is optional: the four core channels still make a complete trace.
  const noGear = telemetry.traceFromEvidence(evidenceBody(200, (_i, sample) => ({ ...sample, gear: null })));
  assert.equal(noGear.channels.gear, undefined);
  assert.equal(noGear.coverage.gear, undefined);
});

test("holes in a channel show up as gaps in its coverage and positions are optional", () => {
  const holes = telemetry.traceFromEvidence(
    evidenceBody(400, (i, sample) => (i >= 100 && i < 140 ? { ...sample, brake: null, motion_available: false } : sample)),
  );
  assert.equal(holes.coverage.brake.runs, 2);
  assert.ok(holes.coverage.brake.metres < holes.coverage.speed.metres - 90);
  assert.equal(holes.coverage.speed.runs, 1);
  const noMotion = telemetry.traceFromEvidence(
    evidenceBody(200, (_i, sample) => ({ ...sample, world_position_x_m: null, world_position_z_m: null })),
  );
  assert.equal(noMotion.path, null);
  const tuple = telemetry.traceFromEvidence(
    evidenceBody(200, (i, sample) => ({ ...sample, world_position_x_m: undefined, world_position_z_m: undefined, world_position_m: [i, 0, i * 2] })),
  );
  assert.equal(tuple.path[1][2] > 0, true, "the world_position_m tuple is accepted");
});

test("unavailable traces say whether they can be processed on request", () => {
  assert.equal(telemetry.unavailableTrace("not_processed").processable, true);
  assert.equal(telemetry.unavailableTrace("trace_not_selected").processable, true);
  assert.equal(telemetry.unavailableTrace("analysis_read_budget_exceeded").processable, false);
  assert.equal(telemetry.unavailableTrace(null).processable, false);
  assert.equal(telemetry.unavailableTrace("not_processed").code, "not_processed");
});

test("value lookup, clipping and tick helpers", () => {
  const runs = [
    [
      [0, 0],
      [10, 100],
    ],
    [
      [100, 50],
      [110, 60],
    ],
  ];
  assert.equal(telemetry.valueAt(runs, 5), 50);
  assert.equal(telemetry.valueAt(runs, 50), null, "between runs there is no value");
  assert.equal(telemetry.valueAt(runs, 105), 55);
  assert.equal(telemetry.valueAt([[[0, 3], [30, 4]]], 15, { step: true }), 3);
  assert.equal(telemetry.valueAt([[[0, 3], [50, 4]]], 25), null, "neighbours more than 40 m apart are a hole");
  const clipped = telemetry.clipRuns(runs, { from: 5, to: 105 });
  assert.deepEqual(clipped[0], [
    [5, 50],
    [10, 100],
  ]);
  assert.deepEqual(clipped[1], [
    [100, 50],
    [105, 55],
  ]);
  assert.deepEqual(telemetry.clipRuns(runs, null), runs);
  const ticks = telemetry.ticksBetween(2340, 2980);
  assert.ok(ticks.length >= 3 && ticks.length <= 9);
  assert.ok(ticks.every((tick) => tick >= 2340 && tick <= 2980));
});

test("key differences can be scoped to a distance range with the region's own time delta", () => {
  const lap = (id, n, ms) => ({ id, lap: n, ms, sectors: null, attemptKey: null, revisionId: id });
  const target = lap("t", 5, 90500);
  const reference = lap("r", 3, 90000);
  const slow = trace({ speed: [profile((d) => (d > 400 && d < 600 ? 70 : 200))], throttle: [flat(100)] });
  const fast = trace({ speed: [flat(200)], throttle: [flat(100)] });
  const full = telemetry.lapDifferences(target, reference, slow, fast);
  assert.equal(full[0].text, "+0.500s (Lap 5 slower)");
  const scoped = telemetry.lapDifferences(target, reference, slow, fast, { range: { from: 400, to: 600 }, timeDeltaS: 0.3 });
  assert.equal(scoped[0].text, "+0.300s (Lap 5 slower)");
  assert.ok(scoped.some((item) => /Lower minimum speed \(-130 km\/h\)/.test(item.text)), JSON.stringify(scoped));
  // Outside the slow stretch nothing differs, so only the time line remains.
  const outside = telemetry.lapDifferences(target, reference, slow, fast, { range: { from: 0, to: 300 }, timeDeltaS: 0.01 });
  assert.deepEqual(outside.map((item) => item.text), ["+0.010s (Lap 5 slower)"]);
  // A range without a delta drops the time line instead of quoting the whole lap.
  assert.equal(telemetry.lapDifferences(target, reference, slow, fast, { range: { from: 400, to: 600 }, timeDeltaS: null })[0].emphasis, undefined);
});

/* ------------------------------------------------------------- tests: regions */

const region = (id, label, startM, endM, complexId = null, apexM = null) => ({
  id,
  label,
  startM,
  endM,
  apexM,
  direction: null,
  complexId,
});
const model = (overrides) => ({
  modelId: "monaco-2024",
  revision: 1,
  trackName: "Monaco",
  validation: "draft",
  origin: null,
  regionCount: 1,
  trackLengthM: 3337,
  regions: [region("t1", "T1", 100, 200)],
  ...overrides,
});

test("the region model for a session prefers validated over draft, then the newest revision", () => {
  const draft = model({ revision: 5, validation: "draft" });
  const validated = model({ revision: 2, validation: "validated" });
  const newerValidated = model({ revision: 3, validation: "validated" });
  assert.equal(reviewRegions.pickRegionModel([draft, validated]).revision, 2);
  assert.equal(reviewRegions.pickRegionModel([draft, validated, newerValidated]).revision, 3);
  assert.equal(reviewRegions.pickRegionModel([draft, model({ revision: 4 })]).revision, 5);
  assert.equal(reviewRegions.pickRegionModel([model({ regions: [] })]), null, "a model without regions is not shown");
  assert.equal(reviewRegions.pickRegionModel([]), null);
});

test("regions parse with distances as reported and unknown validation counts as draft", () => {
  const parsed = reviewRegions.parseRegionsResponse({
    data: {
      model_id: "monaco-2024",
      revision: 3,
      track_name: "Monaco",
      validation_status: "something_new",
      region_count: 2,
      track_length_m: 3337,
      regions: [
        { id: "t1", label: "T1", start_distance_m: 120, end_distance_m: 310, nominal_apex_m: 200, direction: "right", complex_id: null },
        { id: "bad", label: "x", start_distance_m: "no", end_distance_m: 5 },
      ],
    },
  });
  assert.equal(parsed.validation, "draft");
  assert.equal(parsed.regions.length, 1);
  assert.deepEqual([parsed.regions[0].startM, parsed.regions[0].endM, parsed.regions[0].apexM], [120, 310, 200]);
});

test("region choices list complexes first and build quick jumps from complex ids", () => {
  const m = model({
    regions: [
      region("t1", "T1", 100, 200),
      region("t6", "T6", 2340, 2500, "c-t6-t8"),
      region("t7", "T7", 2510, 2700, "c-t6-t8"),
      region("t8", "T8", 2710, 2980, "c-t6-t8"),
      region("t9", "T9", 3000, 3100, "solo"),
    ],
  });
  const choices = regions.regionChoices(m);
  assert.equal(choices[0].id, "cx:c-t6-t8");
  assert.equal(choices[0].label, "T6–T8");
  assert.deepEqual(choices[0].range, { from: 2340, to: 2980 });
  assert.equal(choices.filter((choice) => choice.complex).length, 1, "a complex of one region is just a region");
  assert.equal(choices.length, 1 + 5);
  assert.equal(regions.choiceOptionText(choices[0]), "T6–T8 (complex) · 2,340–2,980 m");
  assert.deepEqual(regions.quickJumps(choices), [{ id: "cx:c-t6-t8", label: "T6-8" }]);
  assert.equal(regions.findChoice(choices, "r:t7").range.from, 2510);
  assert.equal(regions.findChoice(choices, "r:nope"), null);
  assert.deepEqual(regions.regionChoices(null), []);
  const window = regions.paddedWindow({ from: 2340, to: 2980 }, 3337);
  assert.ok(window.from < 2340 && window.to <= 3337 && window.to > 2980);
});

test("track models for imported sessions match by circuit and length", () => {
  const summary = (overrides) => ({
    modelId: "m",
    revision: 1,
    trackName: "Jeddah",
    trackId: 29,
    layoutId: null,
    trackLengthM: 6175,
    validation: "draft",
    origin: null,
    regionCount: 20,
    ...overrides,
  });
  const list = [
    summary({ modelId: "a", revision: 4 }),
    summary({ modelId: "b", revision: 2, validation: "validated" }),
    summary({ modelId: "c", revision: 9, validation: "validated", trackLengthM: 3000 }),
    summary({ modelId: "d", revision: 8, validation: "validated", regionCount: 0 }),
  ];
  assert.equal(regions.matchTrackModel(list, "jeddah", 6180).modelId, "b");
  assert.equal(regions.matchTrackModel(list, "Sakhir", 5412), null);
  assert.equal(regions.matchTrackModel(list, null, null), null);
});

test("region heat follows the time difference thresholds", () => {
  assert.equal(regions.heatOf(-0.05), "gain");
  assert.equal(regions.heatOf(-0.2), "gain");
  assert.equal(regions.heatOf(0.05), "loss");
  assert.equal(regions.heatOf(0.049), "even");
  assert.equal(regions.heatOf(null), "none");
  assert.equal(regions.heatOf(Number.NaN), "none");
  const slow = trace({ speed: [flat(180)] });
  const fast = trace({ speed: [flat(200)] });
  const series = delta.deltaSeries(slow, fast, 20000, 19000, 1000);
  assert.ok(Math.abs(regions.regionDelta({ from: 0, to: 500 }, series) - 0.5) < 0.01);
  assert.equal(regions.regionDelta({ from: 0, to: 500 }, { unavailable: "x" }), null);
});

/* ---------------------------------------------------- tests: geometry, URL, export */

test("the measured line is fitted into the map box and cut by lap distance", () => {
  const path = Array.from({ length: 50 }, (_, i) => [i * 10, i * 4, 1000 + i]);
  const projected = geometry.projectPath(path);
  const xs = projected.map((p) => p[1]);
  const ys = projected.map((p) => p[2]);
  assert.ok(Math.min(...xs) >= geometry.MAP_PADDING - 1e-9 && Math.max(...xs) <= geometry.MAP_WIDTH - geometry.MAP_PADDING + 1e-9);
  assert.ok(Math.min(...ys) >= geometry.MAP_PADDING - 1e-9 && Math.max(...ys) <= geometry.MAP_HEIGHT - geometry.MAP_PADDING + 1e-9);
  assert.equal(geometry.pathSegment(projected, 100, 200).length, 11);
  assert.deepEqual(geometry.projectPath([]), []);
  const dash = geometry.outlineDash(500, 1000, 4000);
  assert.equal(dash.dashoffset, -0.125);
  assert.equal(dash.dasharray, "0.12500 2");
  assert.equal(geometry.outlineDash(500, 500, 4000), null);
  assert.equal(geometry.outlineDash(0, 100, 0), null);
});

test("URL state accepts only known views, region ids and sensible ranges", () => {
  assert.deepEqual(urlState.parseInitialState({}), { view: "overview", region: null, brush: null });
  assert.deepEqual(urlState.parseInitialState({ view: "telemetry", region: "cx:c-t6", from: "2340", to: "2980" }), {
    view: "telemetry",
    region: "cx:c-t6",
    brush: { from: 2340, to: 2980 },
  });
  const rejected = urlState.parseInitialState({ view: "nope", region: "<script>", from: "5", to: "3" });
  assert.deepEqual(rejected, { view: "overview", region: null, brush: null });
  assert.equal(urlState.parseInitialState({ from: "10", to: "x" }).brush, null);
  assert.equal(urlState.parseInitialState({ view: ["quality", "map"] }).view, "quality");
  assert.deepEqual(urlState.stateParams({ view: "overview", region: null, brush: null }), { view: null, region: null, from: null, to: null });
  assert.deepEqual(urlState.stateParams({ view: "map", region: "r:t1", brush: { from: 10.4, to: 99.6 } }), {
    view: "map",
    region: "r:t1",
    from: "10",
    to: "100",
  });
});

test("the telemetry export carries both laps and the derived delta on a 5 m grid", () => {
  const target = trace({ speed: [flat(180)], throttle: [flat(100)], gear: [flat(5)] }, { length: 100 });
  const reference = trace({ speed: [flat(200)], throttle: [flat(90)] }, { length: 100 });
  const series = delta.deltaSeries(
    trace({ speed: [flat(180, 100)] }, { length: 100 }),
    trace({ speed: [flat(200, 100)] }, { length: 100 }),
    1000,
    900,
    100,
  );
  const csv = exporter.telemetryCsv(target, reference, series);
  const lines = csv.split("\n");
  assert.equal(
    lines[0],
    "distance_m,target_speed_kph,target_throttle_pct,target_gear,reference_speed_kph,reference_throttle_pct,delta_s",
  );
  assert.equal(lines.length, 1 + 21);
  assert.equal(lines[1].split(",").length, 7);
  assert.equal(lines[1], "0,180.0,100.0,5,200.0,90.0,0.000");
  assert.equal(lines.at(-1).split(",")[6], "0.100");
  assert.equal(exporter.telemetryCsv(telemetry.unavailableTrace(null), reference, series), null);
  const targetOnly = exporter.telemetryCsv(target, null, null).split("\n");
  assert.equal(targetOnly[0], "distance_m,target_speed_kph,target_throttle_pct,target_gear");
});

test("lap time export adds validity, pit and tyre columns and quotes awkward fields", () => {
  const csv = exporter.lapTimesCsv([
    { lap: 1, ms: 73420, sectors: [18420, 35128, 19872], valid: true, pit: false, tyre: { label: "Medium", ageLaps: 3 } },
    { lap: 2, ms: 99000, sectors: null, valid: false, pit: true, tyre: { label: 'Soft, "fresh"', ageLaps: null } },
  ]);
  const lines = csv.split("\n");
  assert.equal(lines[0], "lap,lap_time,lap_time_ms,s1,s2,s3,valid,pit,tyre,tyre_age");
  assert.equal(lines[1], "1,1:13.420,73420,18.420,35.128,19.872,true,false,Medium,3");
  assert.equal(lines[2], '2,1:39.000,99000,,,,false,true,"Soft, ""fresh""",');
  assert.equal(exporter.slug("Monaco Grand Prix"), "monaco-grand-prix");
});

test("sector and tyre enrichment merges into v2 laps by revision id", () => {
  const laps = [
    { id: "a", lap: 1, ms: 90000, sectors: null, attemptKey: null, revisionId: "rev-a" },
    { id: "b", lap: 2, ms: 91000, sectors: null, attemptKey: null, revisionId: "rev-b" },
    { id: "c", lap: 3, ms: 92000, sectors: [1, 2, 3], attemptKey: "k", revisionId: null },
  ];
  const merged = enrich.mergeEnrichment(laps, {
    sessionId: "s",
    truncated: false,
    truncation: null,
    stints: [],
    laps: [
      {
        revisionId: "rev-a",
        sectorsMs: [30000, 30000, 30000],
        sectorsSource: "session_history",
        sectorsReason: null,
        tyre: { visualCompound: 16, actualCompound: 18, visualLabel: "Soft", actualLabel: "C3", ageLaps: 4, source: "car_status" },
        tyreReason: null,
      },
      { revisionId: "rev-b", sectorsMs: null, sectorsSource: null, sectorsReason: "session_history_not_in_journal", tyre: null, tyreReason: "car_status_not_in_journal" },
    ],
  });
  assert.deepEqual(merged[0].sectors, [30000, 30000, 30000]);
  assert.equal(merged[0].sectorsSource, "session_history");
  assert.deepEqual(merged[0].tyre, { label: "Soft", actual: "C3", ageLaps: 4 });
  assert.equal(merged[1].sectors, null);
  assert.equal(merged[1].sectorsReason, "Sector times were not in the recorded packets.");
  assert.equal(merged[1].tyreReason, "Tyre data was not in the recorded packets.");
  assert.equal(merged[2], laps[2], "laps without a revision id are untouched");
  assert.deepEqual(enrich.sectorReasons(merged), ["Sector times were not in the recorded packets."]);
});

test("the chart cursor maps to a point on the measured line", () => {
  const points = [
    [0, 10, 10],
    [100, 20, 10],
    [200, 20, 30],
  ];
  assert.deepEqual(geometry.pointAt(points, 50), [15, 10]);
  assert.deepEqual(geometry.pointAt(points, 150), [20, 20]);
  assert.deepEqual(geometry.pointAt(points, 200), [20, 30]);
  assert.equal(geometry.pointAt(points, 250), null);
  assert.equal(geometry.pointAt([], 10), null);
});
