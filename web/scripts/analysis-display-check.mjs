import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(
  new URL("../src/lib/analysis-display.ts", import.meta.url),
  "utf8",
);
const module = { exports: {} };
new Function(
  "module",
  "exports",
  ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText,
)(module, module.exports);
const {
  parseAnalysisInput,
  colouredTrackEdges,
  pointAtDistance,
  traceRuns,
  boundedTraceRuns,
  classifyRegion,
  nearestSample,
  regionsFromAttempt,
  tracesFromComparison,
} = module.exports;
const fixture = JSON.parse(
  readFileSync(
    new URL("../public/analysis-input-example.json", import.meta.url),
    "utf8",
  ),
);
assert.equal(parseAnalysisInput(fixture).track.regions.length, 4);
for (const mutate of [
  (x) => (x.comparison.distanceM[1] = 0),
  (x) => x.comparison.target.values.speed.pop(),
  (x) => (x.comparison.target.masks.speed[0] = 1),
  (x) => (x.track.segments[0][0].x = 1e308),
  (x) => (x.track.regions[0].deltaS = -0.2),
  (x) => (x.track.regions[1].id = x.track.regions[0].id),
  (x) => (x.comparison.target.values.throttle[0] = 2),
  (x) => (x.comparison.deltaS[0] = 1e308),
]) {
  const input = structuredClone(fixture);
  mutate(input);
  assert.throws(() => parseAnalysisInput(input));
}
const shape = {
  ...fixture.track,
  lengthM: 30,
  segments: [
    [
      { distanceM: 0, x: 0, z: 0 },
      { distanceM: 10, x: 10, z: 0 },
    ],
    [
      { distanceM: 20, x: 20, z: 0 },
      { distanceM: 30, x: 30, z: 0 },
    ],
  ],
  regions: [
    { id: "a", label: "Named turn", startM: 3, endM: 7, status: "faster" },
  ],
};
const edges = colouredTrackEdges(shape);
assert.deepEqual(
  edges.map((e) => [e.a.distanceM, e.b.distanceM, e.status]),
  [
    [0, 3, "unknown"],
    [3, 7, "faster"],
    [7, 10, "unknown"],
    [20, 30, "unknown"],
  ],
);
assert.equal(pointAtDistance(shape, 15), null, "geometry gaps stay open");
assert.equal(pointAtDistance(shape, 5).x, 5);
assert.deepEqual(
  traceRuns(
    [0, 1, 2, 3, 4],
    [5, 6, 7, 8, 9],
    [true, false, true, true, false],
  ).map((r) => r.map((p) => p.distance)),
  [[0], [2, 3]],
);
assert.deepEqual(
  traceRuns([0, 1, 2, 3], [5, 6, null, 8], undefined, [1, 3]).map((r) =>
    r.map((p) => p.distance),
  ),
  [[1]],
  "windows are half open",
);
const delta = {
  status: "diagnostic_region_delta_change",
  interval_connected_supported_time: true,
  delta_change_s: -0.1,
};
assert.equal(classifyRegion(delta), "faster");
assert.equal(
  classifyRegion({
    ...delta,
    status: "supported_region_delta_change",
    delta_change_s: 0.1,
  }),
  "slower",
);
assert.equal(classifyRegion({ ...delta, delta_change_s: 0.004 }), "similar");
assert.equal(
  classifyRegion({ ...delta, interval_connected_supported_time: false }),
  "unknown",
);
assert.equal(classifyRegion({ ...delta, delta_change_s: null }), "unknown");
const regions = regionsFromAttempt({
  regions: [
    {
      identifier: "t1",
      label: "Turn name",
      analysis_window_m: [101, 202],
      observations: {
        minimum_speed: { speed_kph: 123 },
        throttle_pickup: { 0.5: { status: "detected", distance_m: 155 } },
      },
    },
  ],
});
assert.equal(regions[0].throttlePickupM, 155);
assert.equal(regions[0].startM, 101, "origin is not applied twice");
const comparison = {
  distance_m: [0, 10],
  target_trace: {
    values: { speed_mps: [10, null], gear: [-1, 2] },
    masks: { speed_mps: [true, false], gear: [true, true] },
  },
  reference_trace: {
    values: { speed_mps: [20, 30] },
    masks: { speed_mps: [true, true] },
  },
  delta_s: [0, 0.1],
  delta_mask: [true, true],
};
const trace = tracesFromComparison(comparison, "Target", "Reference");
assert.deepEqual(trace.target.values.speed, [36, null]);
assert.deepEqual(trace.target.values.gear, [-1, 2]);
assert.deepEqual(trace.target.masks.speed, [true, false]);
assert.equal(nearestSample([0, 10, 30], 17), 1);
assert.equal(nearestSample([0, 10, 30], 25), 2);
const sparse = Array.from({ length: 50000 }, (_, i) => [
  { distance: i * 2, value: 10, index: i * 2 },
]);
const bounded = boundedTraceRuns(sparse);
assert.ok(bounded.length <= 128);
assert.ok(bounded.flat().length <= 2000);
const long = boundedTraceRuns([
  Array.from({ length: 100000 }, (_, i) => ({
    distance: i,
    value: i,
    index: i,
  })),
]);
assert.equal(long[0][0].distance, 0);
assert.equal(long[0].at(-1).distance, 99999);
assert.ok(long.flat().length <= 2000);
console.log(
  "Analysis input, region adapters, clipping, gap preservation, masks, units, cursor and rendering limits passed.",
);
