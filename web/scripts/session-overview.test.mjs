import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const typescript = require("typescript");
const { createElement } = require("react");
const reactServer = await import(pathToFileURL(require.resolve("react-dom/server")).href);
const jsxRuntimeUrl = pathToFileURL(require.resolve("react/jsx-runtime")).href;
const reactUrl = pathToFileURL(require.resolve("react")).href;
const sourceRoot = new URL("../src/", import.meta.url);

function transpile(source, fileName) {
  return typescript.transpileModule(source, {
    fileName,
    compilerOptions: {
      module: typescript.ModuleKind.ESNext,
      target: typescript.ScriptTarget.ES2022,
      jsx: typescript.JsxEmit.ReactJSX,
    },
  }).outputText;
}

// Modules are transpiled into a temp dir; imports are rewritten to the emitted
// files so the pure helpers and a few components can run under node:test.
const workDir = await mkdtemp(join(tmpdir(), "session-overview-"));
async function emit(name, code) {
  const path = join(workDir, name);
  await writeFile(path, code);
  return pathToFileURL(path).href;
}

const reactShim = await emit(
  "react.mjs",
  `import React from ${JSON.stringify(reactUrl)};
export default React;
export const { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, useSyncExternalStore } = React;
`,
);
const cssStub = await emit("css.mjs", "export default new Proxy({}, { get: (_, key) => String(key) });\n");
const linkStub = await emit(
  "link.mjs",
  `import { createElement } from ${JSON.stringify(reactUrl)};
export default function Link({ href, children, ...rest }) { return createElement("a", { href, ...rest }, children); }
`,
);

async function emitSource(path, replacements = {}) {
  const source = await readFile(new URL(path, sourceRoot), "utf8");
  let code = transpile(source, path.split("/").pop())
    .replaceAll('from "react/jsx-runtime"', `from ${JSON.stringify(jsxRuntimeUrl)}`)
    .replaceAll('from "react"', `from ${JSON.stringify(reactShim)}`)
    .replaceAll('from "next/link"', `from ${JSON.stringify(linkStub)}`);
  for (const [from, to] of Object.entries(replacements)) {
    code = code.replaceAll(`from "${from}"`, `from ${JSON.stringify(to)}`);
  }
  return emit(`${path.replaceAll("/", "_").replace(/\.tsx?$/, "")}.mjs`, code);
}

const parseUtilsUrl = await emitSource("features/review-data/parse-utils.ts");
const formatUrl = await emitSource("features/review-data/format.ts");
const reasonsUrl = await emitSource("features/review-data/reasons.ts");
const evidenceUrl = await emitSource("features/review-data/session-evidence.ts", { "./format": formatUrl });
const enrichmentUrl = await emitSource("features/review-data/enrichment.ts", { "./parse-utils": parseUtilsUrl });
const summaryUrl = await emitSource("features/review-data/summary.ts", { "./parse-utils": parseUtilsUrl });
const dataUrl = await emitSource("features/session-overview/overview-data.ts", {
  "@/features/review-data/format": formatUrl,
  "@/features/review-data/reasons": reasonsUrl,
  "@/features/review-data/session-evidence": evidenceUrl,
});
const deferredUrl = await emitSource("features/session-overview/deferred.ts", {
  "@/features/review-data/parse-utils": parseUtilsUrl,
  "@/features/review-data/reasons": reasonsUrl,
});

const data = await import(dataUrl);
const deferred = await import(deferredUrl);
const { parseLapEnrichment } = await import(enrichmentUrl);
const { parseSessionSummary } = await import(summaryUrl);

/* ------------------------------------------------------------------ fixtures */

function lap(id, number, ms, extra = {}) {
  return {
    id: String(id),
    lap: number,
    ms,
    valid: true,
    revisionId: `rev-${id}`,
    sectors: null,
    sectorsReason: null,
    tyre: null,
    tyreReason: null,
    pit: false,
    startObserved: true,
    traceAvailable: true,
    ...extra,
  };
}

const stint = (index, compound, startLap, endLap, extra = {}) => ({
  index,
  compound,
  label: compound[0].toUpperCase() + compound.slice(1),
  actual: null,
  startLap,
  endLap,
  lapCount: endLap - startLap + 1,
  startAge: 0,
  endAge: endLap - startLap + 1,
  ...extra,
});

/** Backend `/laps` payload with the keys verified in WP1 (stint_index, start_age_laps, ...). */
function lapsPayload(overrides = {}) {
  return {
    data: {
      session_id: "sess-1",
      laps: [
        {
          revision_id: "rev-1",
          lap_number: 1,
          attempt_number: 1,
          lap_time_ms: 90000,
          disposition: "completed",
          superseded: false,
          start_observed: true,
          valid: true,
          game_valid: true,
          pit: false,
          sectors_ms: [30000, 40000, 20000],
          sectors_source: "session_history",
          tyre: { visual_compound: 17, actual_compound: 18, visual_label: "Medium", actual_label: "C3", age_laps: 1, source: "car_status" },
        },
        {
          revision_id: "rev-2",
          lap_number: 2,
          attempt_number: 1,
          lap_time_ms: 91000,
          disposition: "completed",
          superseded: false,
          start_observed: true,
          valid: true,
          game_valid: true,
          pit: true,
          sectors_ms: null,
          sectors_reason: "session_history_not_in_journal",
          tyre: null,
          tyre_reason: "car_status_not_in_journal",
        },
      ],
      stints: [
        { stint_index: 2, visual_label: "Hard", actual_label: "C2", visual_compound: 18, actual_compound: 17, start_lap: 3, end_lap: 5, lap_count: 3, start_age_laps: 0, end_age_laps: 3 },
        { stint_index: 1, visual_label: "Medium", actual_label: "C3", visual_compound: 17, actual_compound: 18, start_lap: 1, end_lap: 2, lap_count: 2, start_age_laps: 0, end_age_laps: 2 },
      ],
      truncated: false,
      source: {},
      ...overrides,
    },
  };
}

function summaryPayload(events = [], overrides = {}) {
  return {
    data: {
      started_at_utc: "2024-06-04T15:00:00Z",
      ended_at_utc: null,
      last_observed_at_utc: "2024-06-04T16:42:18Z",
      time_reason: "session_active",
      session: { lifecycle: "ended", total_laps: 78 },
      session_time: { duration_s: 6138 },
      scheduled_duration_s: 7200,
      player: { pit_stop_count: 2, last_lap_number: 78 },
      weather: [
        { session_time_s: 100, weather: "clear", track_temperature_c: 38, air_temperature_c: 24 },
        { session_time_s: 200, weather: "clear", track_temperature_c: 38, air_temperature_c: 24 },
        { session_time_s: 2570, weather: "light_cloud", track_temperature_c: 35, air_temperature_c: 23 },
      ],
      events,
      totals: { listed: events.length, observed: events.length + 3, omitted_minor: 3, omitted_budget: 0, truncated: false, by_kind: {} },
      scan: { complete: true, stop_reason: "end_of_committed_journal" },
      ...overrides,
    },
  };
}

const summaryEvent = (kind, label, lap, extra = {}) => ({
  kind,
  label,
  session_time_s: null,
  lap,
  car_index: 0,
  is_player: false,
  code: null,
  detail: {},
  ...extra,
});

/* ------------------------------------------------------------------ stints */

test("enrichment merges sectors, tyres and pit flags by revision and maps stints", () => {
  const enrichment = parseLapEnrichment(lapsPayload());
  const applied = data.applyEnrichment([lap(1, 1, 90000), lap(2, 2, 91000), lap(3, 3, 92000)], enrichment);
  assert.deepEqual(applied.laps[0].sectors, [30000, 40000, 20000]);
  assert.equal(applied.laps[0].tyre.compound, "medium");
  assert.equal(applied.laps[0].tyre.actual, "C3");
  assert.equal(applied.laps[0].tyre.ageLaps, 1);
  assert.equal(applied.laps[1].sectors, null);
  assert.equal(applied.laps[1].sectorsReason, "Sector times were not in the recorded packets.");
  assert.equal(applied.laps[1].tyreReason, "Tyre data was not in the recorded packets.");
  assert.equal(applied.laps[1].pit, true);
  // A lap `/laps` does not list keeps the default reasons.
  assert.equal(applied.laps[2].sectorsReason, "Sector times were not reported for this lap.");
  assert.deepEqual(
    applied.stints.map((item) => [item.index, item.compound, item.startAge, item.endAge, item.actual]),
    [
      [1, "medium", 0, 2, "C3"],
      [2, "hard", 0, 3, "C2"],
    ],
  );
  assert.equal(applied.partialReason, null);
});

test("truncated enrichment reports why and marks unlisted laps with that reason", () => {
  const enrichment = parseLapEnrichment(lapsPayload({ truncated: true, source: { truncation: "time_budget_exceeded" } }));
  const applied = data.applyEnrichment([lap(3, 3, 92000)], enrichment);
  assert.equal(applied.partialReason, "Reading the recording took too long, so the results are partial.");
  assert.equal(applied.laps[0].tyreReason, applied.partialReason);
});

test("compound families come from the visual label, then the visual id", () => {
  assert.equal(data.compoundFromTyre("Soft"), "soft");
  assert.equal(data.compoundFromTyre("Intermediate"), "inter");
  assert.equal(data.compoundFromTyre("Super Soft"), "super_soft");
  assert.equal(data.compoundFromTyre(null, 18), "hard");
  assert.equal(data.compoundFromTyre(null, 99), "unknown");
  assert.equal(data.compoundColor("inter"), "var(--stint-inter)");
});

test("stint statistics use clean laps only and degradation is the least-squares slope in s/lap", () => {
  // 20 ms per lap slower, one invalid lap, one pit lap and one lap that did not start on the line.
  const laps = [];
  for (let i = 1; i <= 8; i += 1) laps.push(lap(i, i, 80000 + i * 20));
  laps[2] = { ...laps[2], valid: false, ms: 99999 };
  laps[3] = { ...laps[3], pit: true, ms: 120000 };
  laps[4] = { ...laps[4], startObserved: false, ms: 70000 };
  const stats = data.stintStats(stint(1, "medium", 1, 8), laps);
  assert.equal(stats.cleanLaps, 5);
  assert.equal(stats.best, 80020);
  assert.ok(Math.abs(stats.degradation - 0.02) < 1e-9);
  assert.equal(Math.round(stats.average), Math.round([1, 2, 6, 7, 8].reduce((sum, i) => sum + 80000 + i * 20, 0) / 5));
});

test("degradation needs four clean laps; tones follow the thresholds", () => {
  const laps = [lap(1, 1, 80000), lap(2, 2, 80100), lap(3, 3, 80200)];
  assert.equal(data.stintStats(stint(1, "soft", 1, 3), laps).degradation, null);
  assert.equal(data.stintStats(stint(1, "soft", 9, 12), laps).best, null);
  assert.equal(data.degradationTone(0.06), "red");
  assert.equal(data.degradationTone(0.05), "neutral");
  assert.equal(data.degradationTone(-0.01), "green");
  assert.equal(data.formatDegradation(0.021), "+0.021 s/lap");
  assert.equal(data.formatDegradation(-0.004), "-0.004 s/lap");
  assert.equal(data.linearSlope([[1, 5]]), null);
});

test("lap ranges, uncovered laps and stint bar gaps", () => {
  assert.equal(data.formatLapRanges([14, 12, 13, 20, 12]), "12–14, 20");
  const stints = [stint(1, "soft", 1, 10), stint(2, "hard", 15, 20)];
  const laps = Array.from({ length: 20 }, (_, i) => lap(i + 1, i + 1, 80000));
  assert.deepEqual(data.lapsWithoutStint(laps, stints), [11, 12, 13, 14]);
  const segments = data.stintSegments(stints, 22);
  assert.deepEqual(
    segments.map((segment) => [segment.kind, segment.laps]),
    [["stint", 10], ["gap", 4], ["stint", 6], ["gap", 2]],
  );
  assert.equal(data.totalLapsOf(laps, stints), 20);
  assert.deepEqual(data.stintTicks(78).slice(0, 3), [1, 10, 20]);
  assert.equal(data.stintTicks(78).at(-1), 78);
  assert.deepEqual(data.stintTicks(1), [1]);
});

test("pit markers sit on stint boundaries that have a pit lap", () => {
  const stints = [stint(1, "soft", 1, 12), stint(2, "medium", 13, 54), stint(3, "hard", 55, 78)];
  const laps = [lap(12, 12, 80000, { pit: true }), lap(55, 55, 80000, { pit: true }), lap(30, 30, 80000, { pit: true })];
  assert.deepEqual(data.pitMarkers(stints, laps), [{ lap: 12 }, { lap: 55 }]);
  assert.equal(data.pitMarkers(stints, []).length, 0);
  assert.match(data.stintBarLabel(stints, [{ lap: 12 }]), /Soft 12 laps, Medium 42 laps, Hard 24 laps\. Pit stops on lap 12/);
});

/* -------------------------------------------------------------------- laps */

test("lap filters, counts and in/out lap hiding", () => {
  const laps = [
    lap(1, 1, 90000, { startObserved: false }),
    lap(2, 2, 80000),
    lap(3, 3, 81000, { valid: false }),
    lap(4, 4, 82000, { pit: true }),
  ];
  assert.deepEqual(data.lapCounts(laps), { all: 4, valid: 3, invalid: 1, pit: 1 });
  assert.deepEqual(data.filterLaps(laps, "all", false).map((l) => l.lap), [1, 2, 3, 4]);
  assert.deepEqual(data.filterLaps(laps, "valid", false).map((l) => l.lap), [1, 2, 4]);
  assert.deepEqual(data.filterLaps(laps, "invalid", false).map((l) => l.lap), [3]);
  assert.deepEqual(data.filterLaps(laps, "pit", false).map((l) => l.lap), [4]);
  assert.deepEqual(data.filterLaps(laps, "all", true).map((l) => l.lap), [2, 3]);
  assert.deepEqual(data.filterLaps(laps, "pit", true).map((l) => l.lap), []);
});

test("lap sorting by number, time, sector and delta; missing sectors sort last", () => {
  const laps = [
    lap(1, 1, 82000, { sectors: [30000, 30000, 22000] }),
    lap(2, 2, 80000, { sectors: [29000, 31000, 20000] }),
    lap(3, 3, 81000),
    lap(4, 4, 79000, { valid: false }),
  ];
  const order = (compare) => [...laps].sort(compare).map((l) => l.lap);
  assert.deepEqual(order(data.compareLapNumber), [1, 2, 3, 4]);
  assert.deepEqual(order(data.compareLapTime), [4, 2, 3, 1]);
  assert.deepEqual(order(data.compareSector(0)), [2, 1, 3, 4]);
  assert.deepEqual(order(data.compareSector(2)), [2, 1, 3, 4]);
  // Invalid laps are not compared, so they trail the delta ranking.
  assert.deepEqual(order(data.compareDeltaToBest(80000)), [2, 3, 1, 4]);
});

test("session best sectors ignore invalid laps; footnotes list reasons with lap ranges", () => {
  const laps = [
    lap(1, 1, 82000, { sectors: [30000, 30000, 22000] }),
    lap(2, 2, 80000, { sectors: [29000, 31000, 20000] }),
    lap(3, 3, 70000, { sectors: [1, 1, 1], valid: false }),
    lap(4, 4, 81000, { sectorsReason: "Sector times were not in the recorded packets." }),
    lap(5, 5, 81000, { sectorsReason: "Sector times were not in the recorded packets." }),
    lap(6, 6, 81000, { sectorsReason: "Sector times were not in the recorded packets." }),
  ];
  assert.deepEqual(data.sessionBestSectors(laps), [29000, 30000, 20000]);
  assert.equal(
    data.reasonFootnote(laps, (l) => l.sectorsReason, (l) => l.sectors === null),
    "— Sector times were not in the recorded packets (laps 4–6).",
  );
  assert.equal(data.reasonFootnote([laps[0]], (l) => l.sectorsReason, (l) => l.sectors === null), undefined);
  assert.equal(data.tyreLabel({ compound: "medium", label: "Medium", actual: "C3", ageLaps: 15 }), "Medium · 15 laps");
  assert.equal(data.tyreLabel({ compound: "hard", label: "Hard", actual: null, ageLaps: null }), "Hard");
});

test("chart series: delta and sector metrics, outliers left off, metric options", () => {
  const laps = [
    lap(1, 1, 80000, { sectors: [20000, 40000, 20000] }),
    lap(2, 2, 80500, { sectors: [20300, 40100, 20100] }),
    lap(3, 3, 81000, { sectors: [20600, 40200, 20200] }),
    lap(4, 4, 120000, { pit: true, sectors: [60000, 40200, 20200] }),
  ];
  const delta = data.buildSeries(laps, "delta", "2", "1");
  assert.deepEqual(delta.points.map((p) => p.lap), [1, 2, 3]);
  assert.equal(delta.values[3], 1000);
  assert.equal(delta.points.find((p) => p.lap === 2).tone, "blue");
  assert.equal(delta.points.find((p) => p.lap === 1).tone, "purple");
  const sector = data.buildSeries(laps, "s1", "1", "1");
  assert.ok(!sector.points.some((p) => p.lap === 4), "the pit lap sector is an outlier");
  assert.ok(sector.yMax >= 20600 && sector.yMin <= 20000);
  assert.equal(data.buildSeries([], "s2", "", null).points.length, 0);
  assert.deepEqual(data.metricOptions(laps, false), ["lap", "delta", "s1", "s2", "s3"]);
  assert.deepEqual(data.metricOptions([lap(1, 1, 80000)], false), ["lap", "delta"]);
  assert.deepEqual(data.metricOptions(laps, true), ["lap"]);
  assert.equal(data.formatAxisValue("delta", 500), "+0.5");
  assert.equal(data.formatAxisValue("lap", 68000), "1:08");
  assert.equal(data.formatMetricValue("s1", 18420), "18.420");
  const scale = data.niceScale(0, 1700);
  assert.deepEqual([scale.yMin, scale.yStep], [0, 500]);
});

/* ------------------------------------------------------------------ events */

test("event categories follow the plan", () => {
  assert.equal(data.categoryOf("lights_out"), "race_control");
  assert.equal(data.categoryOf("safety_car"), "race_control");
  assert.equal(data.categoryOf("penalty"), "penalties");
  assert.equal(data.categoryOf("stop_go_served"), "penalties");
  assert.equal(data.categoryOf("pit_entry"), "pit");
  assert.equal(data.categoryOf("pit_lap"), "pit");
  assert.equal(data.categoryOf("collision"), "incidents");
  assert.equal(data.categoryOf("flashback"), "incidents");
  assert.equal(data.categoryOf("fastest_lap", true), "my_laps");
  assert.equal(data.categoryOf("fastest_lap", false), "other");
  assert.equal(data.categoryOf("invalid_lap"), "my_laps");
  assert.equal(data.categoryOf("overtake", true), "other");
  assert.equal(data.eventIconFor("penalty", "penalties"), "alert-red");
  assert.equal(data.eventIconFor("invalid_lap", "my_laps"), "alert-amber");
  assert.equal(data.eventIconFor("collision", "incidents"), "car");
});

test("events merge: duplicates keep the summary wording, other drivers' overtakes are dropped, sorted by lap", () => {
  const base = [
    ...data.lapEvents([lap(57, 57, 72842), lap(12, 12, 80000, { pit: true }), lap(3, 3, 85000, { valid: false })], "57"),
    ...data.notCompletedEvents([9]),
  ];
  const summary = parseSessionSummary(
    summaryPayload([
      summaryEvent("lights_out", "Lights out", 1, { session_time_s: 100 }),
      summaryEvent("fastest_lap", "Fastest lap 1:12.842", 57, { is_player: true, session_time_s: 5000 }),
      summaryEvent("fastest_lap", "Another driver's fastest lap", 57, { is_player: false, session_time_s: 5001 }),
      summaryEvent("overtake", "Someone passed someone", 20, { is_player: false }),
      summaryEvent("overtake", "You passed {other}", 21, { is_player: true }),
      summaryEvent("penalty", "Player: +5s time penalty", 12, { is_player: true, session_time_s: 1800 }),
    ]),
  );
  const merged = data.mergeEvents(base, summary.data ?? summary.events);
  const labels = merged.map((event) => `${event.lap}:${event.label}`);
  assert.deepEqual(labels, [
    "1:Lights out",
    "3:Lap invalidated by the game",
    "9:Lap not completed",
    "12:Player: +5s time penalty",
    "12:Pit lane in or out lap",
    "21:You passed another driver",
    "57:Fastest lap 1:12.842",
    "57:Another driver's fastest lap",
  ]);
  assert.equal(merged.find((event) => event.label === "Fastest lap 1:12.842").highlight, true);
  assert.ok(!labels.some((label) => label.includes("Session best")), "the lap-derived duplicate is replaced");
});

test("event filters, counts, all-cars switch and relative time", () => {
  const events = data.mergeEvents([], parseSessionSummary(
    summaryPayload([
      summaryEvent("lights_out", "Lights out", 1, { session_time_s: 100 }),
      summaryEvent("penalty", "Player: +5s", 12, { is_player: true, session_time_s: 1800 }),
      summaryEvent("collision", "Two cars collided", 14, { session_time_s: 2000 }),
      summaryEvent("pit_entry", "Player pits", 15, { is_player: true, session_time_s: 2500 }),
      summaryEvent("fastest_lap", "Fastest lap", 16, { is_player: true, session_time_s: 8000 }),
    ]),
  ).events);
  assert.deepEqual(data.eventCounts(events), { all: 5, race_control: 1, penalties: 1, pit: 1, my_laps: 1, incidents: 1 });
  assert.deepEqual(data.filterEvents(events, "penalties", false).map((e) => e.lap), [12]);
  assert.deepEqual(data.filterEvents(events, "all", false).map((e) => e.lap), [1, 12, 15, 16]);
  assert.deepEqual(data.filterEvents(events, "all", true).map((e) => e.lap), [1, 12, 14, 15, 16]);
  assert.equal(data.isKeyEvent(events.find((e) => e.kind === "collision")), false);
  const origin = data.eventOrigin(events);
  assert.equal(origin, 100);
  assert.equal(data.relativeEventTime(events[1], origin), "28:20");
  assert.equal(data.relativeEventTime({ ...events[0], timeS: null }, origin), null);
  assert.equal(data.formatClock(5000), "1:23:20");
  assert.equal(data.formatClock(0), "00:00");
});

test("events footer counts what the engine left out", () => {
  const summary = parseSessionSummary(summaryPayload([summaryEvent("lights_out", "Lights out", 1)]));
  assert.equal(
    data.eventsFooter(20, summary),
    "Showing 20 of 23 events. 3 minor events (speed traps, other drivers' overtakes) are not listed.",
  );
  assert.equal(data.eventsFooter(4, null), "Showing 4 of 4 events.");
  assert.match(data.scanStoppedText("time_budget"), /^The event scan stopped early \(reading the recording took too long/);
});

/* ----------------------------------------------------------------- summary */

test("derived summary lists text then items in the planned order", () => {
  const laps = [
    lap(1, 1, 80000),
    lap(2, 2, 79000),
    lap(3, 3, 81000),
    lap(4, 4, 83000, { valid: false }),
    lap(5, 5, 82000),
  ];
  const summary = parseSessionSummary(
    summaryPayload([
      summaryEvent("safety_car", "Safety car deployed", 3, { detail: { event: "deployed" } }),
      summaryEvent("safety_car", "Safety car ending", 4, { detail: { event: "ending" } }),
      summaryEvent("virtual_safety_car", "VSC deployed", 5, { detail: { event: "deployed" } }),
    ]),
  );
  const result = data.deriveSummary({ laps, bestId: "2", stintCount: 2, summary, limitations: ["Tyre data is partial."] });
  assert.equal(
    result.text,
    "5 timed laps recorded across 2 stints. Best lap 1:19.000 on lap 2, 2.000s faster than the median lap.",
  );
  assert.deepEqual(
    result.items.map((item) => [item.tone, item.glyph, item.text]),
    [
      ["green", "check", "Best lap 1:19.000 (lap 2)"],
      ["amber", "alert", "Inconsistent: lap times within ±1.118 s over 4 clean laps"],
      ["amber", "alert", "1 lap invalidated by the game"],
      ["blue", "info", "2 pit stops"],
      ["amber", "alert", "2 safety car periods"],
      ["grey", "info", "Weather changed 1 time"],
      ["amber", "alert", "Tyre data is partial."],
    ],
  );
  assert.equal(data.deriveSummary({ laps: [], bestId: null, stintCount: null, summary: null }), null);
  const bare = data.deriveSummary({ laps: [lap(1, 1, 80000, { valid: false })], bestId: null, stintCount: null, summary: null });
  assert.equal(bare.text, "1 timed lap recorded.");
  assert.deepEqual(bare.items.map((item) => item.text), ["1 lap invalidated by the game"]);
});

/* -------------------------------------------------------------------- hero */

test("real hero field set is shared by v1 and v2 and has no Build, Assists or Damage", () => {
  const base = {
    lapCount: 12,
    bestMs: 72842,
    conditions: "Clear 24°C",
    trackTemperatureC: 38.4,
    packetFormat: 2024,
    trackLengthM: 3337,
    timeDrivenMs: 6138000,
    ruleSet: "practice_qualifying",
    partial: false,
  };
  for (const source of ["v1", "v2"]) {
    const fields = data.realHeroFields({ ...base, source });
    assert.deepEqual(fields.stats.map((f) => f.label), ["Laps", "Best Lap", "Conditions", "Track Temp"]);
    assert.deepEqual(fields.details.map((f) => f.label), ["Game", "Track Length", "Time Driven", "Rule Set", "Data Source"]);
  }
  const v2 = data.realHeroFields({ ...base, source: "v2" });
  assert.deepEqual(v2.stats.map((f) => f.value), ["12", "1:12.842", "Clear 24°C", "38°C"]);
  assert.deepEqual(v2.details.map((f) => f.value), ["F1 24", "3,337 m", "1:42:18", "Practice Qualifying", "Game (Live)"]);
  assert.equal(data.realHeroFields({ ...base, source: "v2", partial: true }).details[4].value, "Game (Live, partial)");
  assert.equal(data.realHeroFields({ ...base, source: "v1" }).details[4].value, "Import");
});

test("hero fields with no value carry the planned reasons", () => {
  const empty = data.realHeroFields({
    source: "v2",
    lapCount: 0,
    bestMs: null,
    conditions: null,
    trackTemperatureC: null,
    packetFormat: null,
    trackLengthM: null,
    timeDrivenMs: null,
    ruleSet: null,
    partial: false,
  });
  const byLabel = Object.fromEntries([...empty.stats, ...empty.details].map((f) => [f.label, f]));
  assert.equal(byLabel["Best Lap"].reason, "No valid timed lap");
  assert.equal(byLabel["Conditions"].reason, "Weather was not reported");
  assert.equal(byLabel["Track Temp"].reason, "Track temperature was not reported");
  assert.equal(byLabel["Track Length"].reason, "Track length was not reported");
  assert.equal(byLabel["Rule Set"].reason, "Rule set was not reported");
  assert.ok(Object.values(byLabel).filter((f) => f.value === null).every((f) => f.reason));
  assert.equal(byLabel["Laps"].value, "0");
});

test("the observed date line and summary-filled hero fields", () => {
  assert.deepEqual(data.heroDateLine("Race", null, "3f2a9c1b"), ["Race", "Date not recorded", "#3f2a9c1b"]);
  assert.deepEqual(data.heroDateLine("Race", "2024-06-04T15:00:00Z", "3f2a9c1b"), [
    "Race",
    { text: "4 Jun 2024, 15:00", title: "Time the engine first observed this session", iso: "2024-06-04T15:00:00Z" },
  ]);
  const fields = data.realHeroFields({
    source: "v2", lapCount: 3, bestMs: 1, conditions: null, trackTemperatureC: null, packetFormat: 2024,
    trackLengthM: null, timeDrivenMs: 1, ruleSet: null, partial: false,
  });
  const hero = { title: "T", track: "t", lines: [data.heroDateLine("Race", null, "3f2a9c1b"), ["F1 24", "Player"]], status: { label: "Complete", tone: "green" }, ...fields };
  const summary = parseSessionSummary(summaryPayload());
  const applied = data.applySummaryToHero(hero, summary, "v2", "3f2a9c1b");
  assert.deepEqual(applied.stats.map((f) => f.value).slice(2), ["Light Cloud 23°C", "35°C"]);
  assert.equal(applied.lines[0][1].text, "4 Jun 2024, 15:00");
  assert.equal(data.applySummaryToHero(hero, summary, "v1", null), hero);
  assert.equal(data.applySummaryToHero(hero, null, "v2", "3f2a9c1b"), hero);
});

/* ------------------------------------------------------------------- setup */

test("setup values are grouped with units; invalid fields stay unavailable", () => {
  const snapshot = {
    front_wing: 24, rear_wing: 18, on_throttle_differential: 55, off_throttle_differential: 50,
    front_camber: -3.5, rear_camber: -2, front_toe: 0.05, rear_toe: 0.4,
    front_suspension: 41, rear_suspension: 21, front_anti_roll_bar: 16, rear_anti_roll_bar: 9,
    front_suspension_height: 30, rear_suspension_height: 50,
    brake_pressure_percent: 95, brake_bias_percent: 56, engine_braking_percent: 60,
    front_left_tyre_pressure_psi: 23.5, front_right_tyre_pressure_psi: 23.5,
    rear_left_tyre_pressure_psi: 21.5, rear_right_tyre_pressure_psi: null,
    ballast: 6, fuel_load: 100.04, invalid_fields: ["rear_toe"],
  };
  const groups = Object.fromEntries(data.setupGroups(snapshot).map((g) => [g.title, g.rows]));
  assert.deepEqual(groups["Aerodynamics"].map((r) => r.value), ["24", "18"]);
  assert.deepEqual(groups["Transmission"].map((r) => r.value), ["55%", "50%"]);
  assert.deepEqual(groups["Suspension geometry"].map((r) => r.value), ["-3.50°", "-2.00°", "0.05°", null]);
  assert.deepEqual(groups["Brakes"].map((r) => r.value), ["95%", "56%", "60%"]);
  assert.deepEqual(groups["Tyres"].map((r) => r.value), ["23.5 psi", "23.5 psi", "21.5 psi", null]);
  assert.deepEqual(groups["Weight"].map((r) => r.value), ["6", "100.0 kg"]);
  const view = data.setupFromContexts([null, { at_start: { status: "unknown" }, observed_change_count: 0 }, { at_start: { status: "reported", setup: snapshot }, observed_change_count: 2 }]);
  assert.equal(view.changeCount, 2);
  assert.equal(data.setupFromContexts([null, undefined]), null);
});

test("conditions: weather icons, consecutive repeats collapse, temperatures", () => {
  assert.equal(data.weatherKind("clear"), "clear");
  assert.equal(data.weatherKind("light_cloud"), "light_cloud");
  assert.equal(data.weatherKind("overcast"), "overcast");
  assert.equal(data.weatherKind("heavy_rain"), "heavy_rain");
  assert.equal(data.weatherKind("storm"), "storm");
  const summary = parseSessionSummary(summaryPayload());
  const { rows, total } = data.weatherRows(summary.weather);
  assert.equal(total, 2);
  assert.deepEqual(rows.map((row) => [row.label, row.detail]), [
    ["Clear", "Air 24°C · Track 38°C"],
    ["Light Cloud", "Air 23°C · Track 35°C"],
  ]);
  assert.equal(data.temperaturesText(null, 30), "Track 30°C");
});

test("session facts use summary times, or the reason they are missing", () => {
  const context = {
    sessionType: "race", ruleSet: null, packetFormat: 2024, trackName: "Monaco", trackLengthM: 3337,
    totalLaps: null, weather: null, airTemperatureC: null, trackTemperatureC: null,
    startedAtUtc: null, endedAtUtc: null, durationS: null,
  };
  const rows = Object.fromEntries(data.sessionFacts(context, parseSessionSummary(summaryPayload())).map((r) => [r.label, r]));
  assert.equal(rows["Started"].value, "4 Jun 2024, 15:00");
  assert.equal(rows["Ended"].value, null);
  assert.equal(rows["Ended"].reason, "The session is still active.");
  assert.equal(rows["Duration"].value, "1:42:18 (2:00:00 scheduled)");
  assert.equal(rows["Laps"].value, "78 scheduled");
  assert.equal(rows["Track"].value, "Monaco · 3,337 m");
  assert.equal(rows["Rule set"].reason, "Rule set was not reported");
  const noSummary = Object.fromEntries(data.sessionFacts(context, null).map((r) => [r.label, r]));
  assert.equal(noSummary["Started"].reason, "Capture times were not recorded");
});

/* ----------------------------------------------------- compare and URL state */

const compareLaps = [
  lap(28, 28, 73420),
  lap(57, 57, 72842),
  lap(58, 58, 73000),
  lap(59, 59, 72500, { valid: false }),
];

test("reference defaults to the session best, else the next fastest valid lap", () => {
  assert.equal(data.defaultReferenceId(compareLaps, "57", "28"), "57");
  assert.equal(data.defaultReferenceId(compareLaps, "57", "57"), "58");
  assert.equal(data.defaultReferenceId([lap(1, 1, 80000)], "1", "1"), null);
});

test("compare resolves target, reference and delta; explicit reference must differ from the target", () => {
  const resolved = data.resolveCompare({ laps: compareLaps, bestId: "57", targetId: "28", referenceId: null });
  assert.equal(resolved.referenceId, "57");
  assert.equal(resolved.deltaMs, 578);
  assert.equal(data.compareSentence(resolved.target, resolved.reference), "Lap 28 is +0.578 slower than Lap 57.");
  assert.equal(data.compareSentence(resolved.reference, resolved.target), "Lap 57 is -0.578 faster than Lap 28.");
  const explicit = data.resolveCompare({ laps: compareLaps, bestId: "57", targetId: "28", referenceId: "58" });
  assert.equal(explicit.referenceId, "58");
  const same = data.resolveCompare({ laps: compareLaps, bestId: "57", targetId: "57", referenceId: "57" });
  assert.equal(same.referenceId, "58");
  const stale = data.resolveCompare({ laps: compareLaps, bestId: "57", targetId: "28", referenceId: "404" });
  assert.equal(stale.referenceId, "57");
});

test("compare warnings cover invalid and unprocessed laps", () => {
  const target = lap(28, 28, 73420, { traceAvailable: false });
  const reference = lap(59, 59, 72500, { valid: false });
  assert.deepEqual(data.compareWarnings(target, reference), [
    "Lap 28 has no processed telemetry; Lap Analysis can process it.",
    "Lap 59 was invalidated; the comparison may be misleading.",
  ]);
  assert.deepEqual(data.compareWarnings(compareLaps[0], compareLaps[1]), []);
});

test("the Lap Analysis link carries both lap ids", () => {
  assert.equal(data.analysisHref("sess 1", "28", "57"), "/review/sess%201/laps?target=28&reference=57");
  assert.equal(data.analysisHref("s", "28", null), "/review/s/laps?target=28");
  assert.equal(data.analysisHref("s", null, null), "/review/s/laps");
});

test("URL state: parse, ignore unknown ids, and write only non-default values", () => {
  const defaults = { selectedId: "28" };
  assert.deepEqual(data.parseOverviewParams(new URLSearchParams(""), compareLaps, defaults), {
    view: "overview", mode: "single", selectedId: "28", referenceId: null,
  });
  assert.deepEqual(
    data.parseOverviewParams({ view: "laps", lap: "58" }, compareLaps, defaults),
    { view: "laps", mode: "single", selectedId: "58", referenceId: null },
  );
  assert.deepEqual(
    data.parseOverviewParams({ mode: "compare", target: "28", reference: "58" }, compareLaps, defaults),
    { view: "overview", mode: "compare", selectedId: "28", referenceId: "58" },
  );
  assert.equal(data.parseOverviewParams({ view: "nope", lap: "404", mode: "compare", reference: "28" }, compareLaps, defaults).view, "overview");
  assert.equal(data.parseOverviewParams({ lap: "404" }, compareLaps, defaults).selectedId, "28");
  assert.equal(
    data.parseOverviewParams({ mode: "compare", target: "28", reference: "28" }, compareLaps, defaults).referenceId,
    null,
  );

  const state = { view: "overview", mode: "single", selectedId: "28", referenceId: null, resolvedReferenceId: "57" };
  assert.equal(data.overviewQuery("", state, defaults), "");
  assert.equal(data.overviewQuery("?x=1&lap=9&view=stints", { ...state, selectedId: "58", view: "laps" }, defaults), "?x=1&view=laps&lap=58");
  assert.equal(
    data.overviewQuery("?lap=9", { ...state, mode: "compare", selectedId: "28", referenceId: "57" }, defaults),
    "?mode=compare&target=28&reference=57",
  );
});

/* ------------------------------------------------------------ deferred data */

function model(overrides = {}) {
  return {
    laps: [lap(1, 1, 90000)],
    enrichment: { state: "deferred" },
    summaryData: { state: "deferred" },
    events: [],
    ...overrides,
  };
}

test("deferred enrichment resolves from the browser read", () => {
  const live = { status: "loaded", value: parseLapEnrichment(lapsPayload()) };
  assert.equal(data.resolveEnrichment(model(), { status: "loading" }).status, "loading");
  assert.equal(data.resolveEnrichment(model(), { status: "idle" }).status, "loading");
  const loaded = data.resolveEnrichment(model(), live);
  assert.equal(loaded.status, "loaded");
  assert.deepEqual(loaded.laps[0].sectors, [30000, 40000, 20000]);
  assert.equal(loaded.stints.length, 2);
  const failed = data.resolveEnrichment(model(), { status: "unavailable", reason: "Nope." });
  assert.deepEqual([failed.status, failed.reason], ["unavailable", "Nope."]);
  const fromServer = data.resolveEnrichment(
    model({ enrichment: { state: "loaded", value: { stints: [] }, partialReason: "Partial." } }),
    { status: "idle" },
  );
  assert.deepEqual([fromServer.status, fromServer.partialReason], ["loaded", "Partial."]);
  assert.equal(data.resolveEnrichment(model({ enrichment: { state: "unavailable", reason: "No." } }), live).status, "unavailable");
});

test("deferred summary resolves, and events merge only once it has loaded", () => {
  const live = { status: "loaded", value: parseSessionSummary(summaryPayload([summaryEvent("lights_out", "Lights out", 1)])) };
  assert.equal(data.resolveSummary(model(), { status: "loading" }).status, "loading");
  const summary = data.resolveSummary(model(), live);
  assert.equal(summary.status, "loaded");
  assert.deepEqual(data.eventsFor(model(), summary).map((e) => e.label), ["Lights out"]);
  const base = data.lapEvents([lap(1, 1, 90000, { pit: true })], null);
  assert.deepEqual(data.eventsFor(model({ events: base }), { status: "loading" }).map((e) => e.label), ["Pit lane in or out lap"]);
});

test("server reads become sections: ok loaded, busy/timeout deferred, otherwise unavailable", () => {
  assert.deepEqual(data.sectionFromRead({ state: "ok", value: 1 }), { state: "loaded", value: 1, partialReason: null });
  assert.deepEqual(data.sectionFromRead({ state: "busy", reason: "summary_busy" }), { state: "deferred" });
  assert.deepEqual(data.sectionFromRead({ state: "timeout" }), { state: "deferred" });
  assert.deepEqual(data.sectionFromRead({ state: "unavailable", reason: "session_not_found", status: 404 }), {
    state: "unavailable",
    reason: "That session could not be found.",
  });
  assert.equal(data.sectionFromRead({ state: "unreachable" }).state, "unavailable");
});

test("proxy responses: ok, busy retries, and unavailable reasons", () => {
  assert.deepEqual(deferred.BUSY_RETRY_DELAYS_MS, [1000, 3000]);
  const ok = deferred.interpretDeferredResponse(200, lapsPayload(), parseLapEnrichment);
  assert.equal(ok.kind, "ok");
  assert.equal(ok.value.stints.length, 2);
  assert.equal(deferred.interpretDeferredResponse(200, { data: 1 }, parseLapEnrichment).kind, "unavailable");
  const busy = deferred.interpretDeferredResponse(409, { status: "unavailable", reason: "lap_enrichment_busy" }, parseLapEnrichment);
  assert.equal(busy.kind, "busy");
  assert.equal(
    deferred.interpretDeferredResponse(409, { reason: "something_else" }, parseLapEnrichment).kind,
    "unavailable",
  );
  assert.equal(
    deferred.interpretDeferredResponse(404, { reason: "session_not_found" }, parseLapEnrichment).reason,
    "That session could not be found.",
  );
  assert.equal(deferred.interpretDeferredResponse(502, null, parseLapEnrichment).reason, "This section is not available.");
});

/* -------------------------------------------------- Lap Selection rendering */

const iconsUrl = await emitSource("features/ui-foundation/icons.tsx");
const unitsUrl = await emitSource("features/ui-foundation/units.tsx");
const primitivesUrl = await emitSource("features/ui-foundation/primitives.tsx", {
  "./icons": iconsUrl,
  "./units": unitsUrl,
  "./primitives.module.css": cssStub,
});
const segmentedUrl = await emitSource("features/ui-foundation/Segmented.tsx", {
  "./primitives": primitivesUrl,
  "./primitives.module.css": cssStub,
});
const stepperUrl = await emitSource("features/ui-foundation/LapStepper.tsx", {
  "./icons": iconsUrl,
  "./primitives": primitivesUrl,
  "./primitives-extra.module.css": cssStub,
});
const selectionUrl = await emitSource("features/session-overview/LapSelection.tsx", {
  "@/features/review-data/format": formatUrl,
  "@/features/ui-foundation/icons": iconsUrl,
  "@/features/ui-foundation/LapStepper": stepperUrl,
  "@/features/ui-foundation/primitives": primitivesUrl,
  "@/features/ui-foundation/Segmented": segmentedUrl,
  "@/features/ui-foundation/units": unitsUrl,
  "./overview-data": dataUrl,
  "./SessionOverview.module.css": cssStub,
  "./OverviewViews.module.css": cssStub,
});
const { LapSelection } = await import(selectionUrl);

function renderSelection(mode, overrides = {}) {
  const compare = data.resolveCompare({ laps: compareLaps, bestId: "57", targetId: "28", referenceId: null });
  return reactServer.renderToStaticMarkup(
    createElement(LapSelection, {
      laps: compareLaps,
      bestId: "57",
      mode,
      onModeChange() {},
      selectedId: "28",
      onSelect() {},
      compare,
      onReference() {},
      onSwap() {},
      compareHref: data.analysisHref("sess", compare.target.id, compare.referenceId),
      ...overrides,
    }),
  );
}

test("Lap Selection mode control is a radiogroup, not tabs, and keeps the reference text", () => {
  const markup = renderSelection("single");
  assert.match(markup, /role="radiogroup"[^>]*aria-label="Lap selection mode"/);
  assert.equal([...markup.matchAll(/role="radio"/g)].length, 2);
  assert.doesNotMatch(markup, /role="tab"/);
  assert.match(markup, /aria-checked="true"[^>]*>Single Lap</);
  assert.match(markup, /Session best: 1:12\.842 \(Lap 57\)/);
  assert.match(markup, /Comparisons are only available for laps with complete telemetry data\./);
  assert.match(markup, /aria-label="Previous lap"/);
  assert.match(markup, /\+0\.578/);
});

test("compare mode shows both steppers, swap, the sentence and a link carrying both ids", () => {
  const markup = renderSelection("compare");
  assert.match(markup, /aria-checked="true"[^>]*>Compare Laps</);
  assert.match(markup, /role="group" aria-label="Target"/);
  assert.match(markup, /role="group" aria-label="Reference"/);
  assert.match(markup, /aria-label="Swap target and reference"/);
  assert.match(markup, /Lap 28 is \+0\.578 slower than Lap 57\./);
  assert.match(markup, /Session best/);
  assert.match(markup, /href="\/review\/sess\/laps\?target=28&amp;reference=57"[^>]*>[\s\S]*Compare in Lap Analysis/);
  assert.doesNotMatch(markup, /aria-label="Previous lap"/);
});

test("compare mode warns about invalid laps and disables the swap without a pair", () => {
  const compare = data.resolveCompare({ laps: compareLaps, bestId: "57", targetId: "28", referenceId: "59" });
  const markup = renderSelection("compare", { compare });
  assert.match(markup, /Lap 59 was invalidated; the comparison may be misleading\./);
  assert.doesNotMatch(markup, /Comparisons are only available/);
  const single = renderSelection("compare", {
    laps: [compareLaps[0]],
    compare: data.resolveCompare({ laps: [compareLaps[0]], bestId: "28", targetId: "28", referenceId: null }),
  });
  assert.match(single, /Choose two timed laps to compare\./);
  assert.match(single, /<button[^>]*disabled=""[^>]*aria-label="Swap target and reference"/);
  assert.match(single, /role="radiogroup"[^>]*aria-disabled="true"/);
});

/* ------------------------------------------- whole-page server render smoke */

// The real component graph (views, primitives, DataTable, loader) is emitted
// recursively; only the network readers, CSS modules and next/link are stubbed.
const graph = new Map();
const backendStub = await emit(
  "backend-stub.mjs",
  `export const cachedSummaryDate = () => null;
export const completedLaps = () => [];
export const fetchAcquisition = async () => null;
export const fetchEvidenceAttempts = async () => null;
export const fetchEvidenceSession = async () => null;
export const fetchLapEnrichment = async () => ({ state: "timeout" });
export const fetchLaps = async () => null;
export const fetchSession = async () => null;
export const fetchSummary = async () => ({ state: "timeout" });
export const sessionBestLap = () => null;
export const telemetryFooter = (_status, footer) => footer;
export const telemetryStatus = () => ({ tone: "green", label: "Telemetry live" });
export const TELEMETRY_OFFLINE = { tone: "red", label: "Telemetry offline" };
`,
);

async function sourceExists(candidate) {
  try {
    await readFile(new URL(candidate, sourceRoot));
    return true;
  } catch {
    return false;
  }
}

async function loadGraph(relativePath) {
  const key = new URL(relativePath, sourceRoot).href;
  if (graph.has(key)) return graph.get(key);
  const pending = (async () => {
    const source = await readFile(new URL(relativePath, sourceRoot), "utf8");
    let code = transpile(source, relativePath.split("/").pop())
      .replaceAll('from "react/jsx-runtime"', `from ${JSON.stringify(jsxRuntimeUrl)}`)
      .replaceAll('from "react"', `from ${JSON.stringify(reactShim)}`)
      .replaceAll('from "next/link"', `from ${JSON.stringify(linkStub)}`);
    const specifiers = [...new Set([...code.matchAll(/from "([^"]+)"/g)].map((match) => match[1]))];
    for (const specifier of specifiers) {
      let target = null;
      if (specifier.endsWith(".css")) {
        target = cssStub;
      } else if (specifier === "@/features/review-data/backend") {
        target = backendStub;
      } else if (specifier.startsWith("@/") || specifier.startsWith(".")) {
        const base = specifier.startsWith("@/")
          ? specifier.slice(2)
          : new URL(specifier, new URL(relativePath, sourceRoot)).href.slice(sourceRoot.href.length);
        for (const candidate of [`${base}.ts`, `${base}.tsx`]) {
          if (await sourceExists(candidate)) {
            target = await loadGraph(candidate);
            break;
          }
        }
      }
      if (target) code = code.replaceAll(`from "${specifier}"`, `from ${JSON.stringify(target)}`);
    }
    return emit(`graph_${graph.size}_${relativePath.replaceAll("/", "_").replace(/\.tsx?$/, "")}.mjs`, code);
  })();
  graph.set(key, pending);
  return pending;
}

const loader = await import(await loadGraph("features/session-overview/load.ts"));
const { SessionOverview } = await import(await loadGraph("features/session-overview/SessionOverview.tsx"));

const FOOTER = { tone: "green", label: "Telemetry live", lines: ["F1 24", "Ready"] };

function renderOverview(model, initial) {
  return reactServer.renderToStaticMarkup(createElement(SessionOverview, { model, initial }));
}

const at = (view, extra = {}) => ({ view, mode: "single", selectedId: "28", referenceId: null, ...extra });

const EVIDENCE_SESSION = {
  id: "4a09407f07885540b29221e7636ec37f",
  generation: "g",
  uid: "1",
  lifecycle: "ended",
  acquisition: "receiving",
  occurrence: 1,
  context: {
    trackName: "Monaco",
    sessionType: "race",
    weatherName: "clear",
    airTemperatureC: 24,
    trackTemperatureC: null,
    packetFormat: 2024,
    ruleSet: null,
    trackLengthM: 3337,
    totalLaps: null,
  },
};
const attempt = (lapNumber, ms, extra = {}) => ({
  id: `rev-${lapNumber}`,
  role: "player",
  publishedSequence: lapNumber,
  attemptNumber: 1,
  lapNumber,
  lapTimeMs: ms,
  disposition: "completed",
  gameValid: true,
  pitEncountered: false,
  startObserved: true,
  superseded: false,
  traceAvailable: true,
  context: null,
  ...extra,
});

test("fixture session keeps its reference content on every tab", async () => {
  const model = await loader.loadOverview("monaco-2024-race");
  assert.equal(model.source, "fixture");
  const overview = renderOverview(model);
  assert.match(overview, /role="tabpanel" id="overview-panel-overview" aria-labelledby="overview-tab-overview"/);
  assert.match(overview, /aria-controls="overview-panel-laps"/);
  assert.match(overview, /Monaco Grand Prix/);
  assert.match(overview, /Game Build<\/dt><dd>1\.22/);
  assert.match(overview, /Track Grip/);
  assert.match(overview, /Soft \(12\)/);
  assert.match(overview, /Medium \(42\)/);
  assert.match(overview, /Hard \(24\)/);
  assert.match(overview, /A strong and consistent race/);
  assert.match(overview, /Notable Events \(8\)/);
  assert.match(overview, /Personal best \(1:12\.842\)/);
  assert.doesNotMatch(overview, /View all events/);
  assert.match(overview, /<option value="lap"[^>]*>Lap Time<\/option>/);
  assert.doesNotMatch(overview, /Delta to Best/);
  assert.match(overview, /Session best: 1:12\.842 \(Lap 57\)/);
  assert.match(overview, /href="\/review\/monaco-2024-race\/laps\?target=28&amp;reference=57"/);

  const laps = renderOverview(model, at("laps"));
  assert.match(laps, /Laps \(78\)/);
  assert.match(laps, /aria-label="Filter laps"/);
  assert.match(laps, /Valid<span[^>]*> \(78\)/);
  assert.match(laps, /18\.420/);
  assert.match(laps, /Analyse lap 28/);
  assert.doesNotMatch(laps, /Game Build/, "the hero is not shown on the Laps tab");

  const stints = renderOverview(model, at("stints"));
  assert.match(stints, /Stint Pace/);
  assert.match(stints, /Pit stop, lap 12/);
  assert.match(stints, /aria-label="Lap times by stint"/);

  const events = renderOverview(model, at("events"));
  assert.match(events, /Events \(8\)/);
  assert.match(events, /Showing 8 of 8 events\./);
  assert.match(events, /View lap/);

  const setup = renderOverview(model, at("setup"));
  assert.match(setup, /Car Setup \(at session start\)/);
  assert.match(setup, /Front wing/);
  assert.match(setup, /Assists<\/dt><dd>Off/);
  assert.match(setup, /Light Cloud/);
  assert.match(setup, /1:42:18 \(2:00:00 scheduled\)/);
});

test("compare mode from the URL renders both laps and a compare link", async () => {
  const model = await loader.loadOverview("monaco-2024-race");
  const markup = renderOverview(model, at("overview", { mode: "compare", referenceId: "55" }));
  assert.match(markup, /Lap 28 is \+0\.\d{3} slower than Lap 55\./);
  assert.match(markup, /href="\/review\/monaco-2024-race\/laps\?target=28&amp;reference=55"/);
  assert.match(markup, /Reference Lap/);
  assert.match(markup, /stroke="var\(--reference\)"/);
  assert.match(markup, /vs Lap 55/);
});

test("a v2 session with deferred sections renders skeletons, no reference content and the v2 hero fields", () => {
  const model = loader.evidenceModel(
    EVIDENCE_SESSION,
    [attempt(1, 90000), attempt(2, 80000), attempt(3, 81000, { gameValid: false })],
    false,
    FOOTER,
  );
  assert.equal(model.source, "v2");
  assert.equal(model.enrichment.state, "deferred");
  const markup = renderOverview(model, at("overview", { selectedId: "3" }));
  assert.match(markup, /Date not recorded/);
  assert.match(markup, /#4a09407f/);
  assert.match(markup, /Track Temp/);
  assert.match(markup, /Not available: Track temperature was not reported/);
  assert.doesNotMatch(markup, /Game Build|Assists|Damage|Track Grip/);
  assert.doesNotMatch(markup, /Soft \(12\)/);
  assert.doesNotMatch(markup, /A strong and consistent race/);
  assert.match(markup, /Session Summary/);
  assert.match(markup, /Notable Events \(0\)/);

  const setup = renderOverview(model, at("setup", { selectedId: "3" }));
  assert.match(setup, /Car setup is not recorded for live sessions yet/);
  assert.match(setup, /Assist and damage settings are not included in the game(&#x27;|')s telemetry\./);
  assert.doesNotMatch(setup, /Front wing/);
});

test("a v2 session whose sections loaded on the server shows derived summary, stints and events", () => {
  const enrichment = parseLapEnrichment(
    lapsPayload({
      laps: [
        {
          revision_id: "rev-1",
          lap_number: 1,
          lap_time_ms: 90000,
          valid: true,
          game_valid: true,
          pit: false,
          start_observed: true,
          sectors_ms: [30000, 40000, 20000],
          sectors_source: "session_history",
          tyre: { visual_compound: 17, actual_compound: 18, visual_label: "Medium", actual_label: "C3", age_laps: 1, source: "car_status" },
        },
      ],
      stints: [
        { stint_index: 1, visual_label: "Medium", actual_label: "C3", visual_compound: 17, actual_compound: 18, start_lap: 1, end_lap: 2, lap_count: 2, start_age_laps: 0, end_age_laps: 2 },
      ],
    }),
  );
  const summary = parseSessionSummary(
    summaryPayload([
      summaryEvent("lights_out", "Lights out", 1, { session_time_s: 100 }),
      summaryEvent("penalty", "Player: +5s time penalty (corner cutting)", 2, { session_time_s: 700, is_player: true }),
    ]),
  );
  const model = loader.evidenceModel(
    EVIDENCE_SESSION,
    [attempt(1, 90000), attempt(2, 80000), attempt(3, 81000, { gameValid: false, pitEncountered: true })],
    false,
    FOOTER,
    { enrichment: { state: "ok", value: enrichment }, summary: { state: "ok", value: summary } },
  );
  assert.equal(model.enrichment.state, "loaded");
  assert.equal(model.header[2], "4 Jun 2024");
  const overview = renderOverview(model, at("overview", { selectedId: "3" }));
  assert.match(overview, /3 timed laps recorded across 1 stint. Best lap 1:20\.000 on lap 2/);
  assert.match(overview, /Medium \(2\)/);
  assert.match(overview, /Tyre data is missing for laps 3/);
  assert.match(overview, /4 Jun 2024, 15:00/);
  assert.match(overview, /Lights out/);
  assert.match(overview, /<option value="s1"[^>]*>Sector 1<\/option>/);

  const laps = renderOverview(model, at("laps", { selectedId: "3" }));
  assert.match(laps, /Laps \(3\)/);
  assert.match(laps, /Medium · 1 lap/);
  assert.match(laps, /Sector times were not reported for this lap/);
  assert.match(laps, /Invalid laps are not compared/);

  const events = renderOverview(model, at("events", { selectedId: "3" }));
  assert.match(events, /aria-label="Filter events"/);
  assert.match(events, /Show all cars/);
  assert.match(events, /Player: \+5s time penalty \(corner cutting\)/);
  assert.match(events, /Showing \d+ of \d+ events\. 3 minor events/);
  assert.match(events, /View lap/);

  const stints = renderOverview(model, at("stints", { selectedId: "3" }));
  assert.match(stints, /Not available: Needs at least four clean laps/);
});

test("a v2 session whose enrichment is unavailable hides sector and tyre columns", () => {
  const model = loader.evidenceModel(EVIDENCE_SESSION, [attempt(1, 90000)], false, FOOTER, {
    enrichment: { state: "unavailable", reason: "lap_enrichment_busy", status: 409 },
    summary: { state: "unreachable" },
  });
  assert.equal(model.enrichment.state, "unavailable");
  const laps = renderOverview(model, at("laps", { selectedId: "1" }));
  assert.match(laps, /Sector and tyre details are not available for this session\./);
  assert.doesNotMatch(laps, />S1</);
  const stints = renderOverview(model, at("stints", { selectedId: "1" }));
  assert.match(stints, /Tyre data was not recorded for this session\./);
  const events = renderOverview(model, at("events", { selectedId: "1" }));
  assert.match(events, /The session timeline is not available/);
});

test("speed traps are detail, not notable events", () => {
  const events = data.mergeEvents([], parseSessionSummary(
    summaryPayload([
      summaryEvent("speed_trap", "Speed trap 318 km/h - You", 2, { is_player: true, session_time_s: 200 }),
      summaryEvent("safety_car", "Safety car: deployed", 3, { session_time_s: 300 }),
      summaryEvent("drs_enabled", "DRS enabled", 1, { session_time_s: 50 }),
    ]),
  ).events);
  assert.deepEqual(data.scopeEvents(events, false).map((e) => e.kind), ["safety_car"]);
  assert.equal(data.scopeEvents(events, true).length, 3);
});

test("lap chart markers place pit and safety car laps, and outliers can be excluded", () => {
  const laps = [
    lap(1, 1, 93500),
    lap(2, 2, 93300),
    lap(3, 3, 93400, { pit: true }),
    lap(4, 4, 101000),
    lap(5, 5, 93600),
  ];
  const events = parseSessionSummary(
    summaryPayload([
      summaryEvent("pit_entry", "Pit entry", 3, { is_player: true, session_time_s: 280 }),
      summaryEvent("collision", "You and Car 2 collided", 4, { is_player: true, session_time_s: 300 }),
      summaryEvent("safety_car", "Safety car: deployed", null, { session_time_s: 310, detail: { event: "deployed" } }),
      summaryEvent("safety_car", "Safety car: returned", null, { session_time_s: 400, detail: { event: "returned" } }),
      summaryEvent("overtake", "You overtook Car 3", 5, { is_player: true, session_time_s: 390 }),
    ]),
  ).events;
  assert.equal(data.lapAtTime(events, 310), 4);
  const markers = data.lapMarkers(laps, events);
  assert.deepEqual(markers, [
    { lap: 3, kind: "pit" },
    { lap: 4, kind: "sc" },
    { lap: 5, kind: "sc" },
  ]);
  const outliers = data.outlierLapIds(laps, markers);
  assert.deepEqual([...outliers].sort(), ["3", "4", "5"]);
  const tight = data.buildSeries(laps, "lap", "1", "2", outliers);
  assert.deepEqual(tight.points.map((point) => point.lap), [1, 2]);
  const all = data.buildSeries(laps, "lap", "1", "2", new Set());
  assert.equal(all.points.length, 5);
  assert.ok(all.yMax >= 101000);
  assert.equal(data.formatAxisValue("lap", 93500), "1:33.5");
  assert.equal(data.formatAxisValue("lap", 94000), "1:34");
});

test("theoretical best adds the best valid sectors", () => {
  const laps = [
    lap(1, 1, 93500, { sectors: [30000, 33000, 30500] }),
    lap(2, 2, 93300, { sectors: [30200, 32800, 30300] }),
    lap(3, 3, 92900, { valid: false, sectors: [29000, 32000, 30000] }),
  ];
  const theory = data.theoreticalBest(laps, 93300);
  assert.equal(theory.ms, 30000 + 32800 + 30300);
  assert.equal(theory.gainMs, 200);
  assert.equal(data.theoreticalBestText(theory), "1:33.100, 0.200 s quicker than your best lap");
  assert.equal(data.theoreticalBest([lap(1, 1, 93500, { sectors: null })], 93500), null);
  const summary = data.deriveSummary({ laps, bestId: "2", stintCount: null, summary: null });
  assert.ok(summary.items.some((item) => item.text.startsWith("Theoretical best 1:33.100")));
});

test("consistency is the spread of clean laps, ignoring mistakes", () => {
  const laps = [
    lap(1, 1, 93000),
    lap(2, 2, 93200),
    lap(3, 3, 93400),
    lap(4, 4, 99000),
    lap(5, 5, 93100, { pit: true }),
    lap(6, 6, 92000, { valid: false }),
  ];
  const consistency = data.consistencyOf(laps);
  assert.equal(consistency.laps, 3, "the 99 s mistake, the pit lap and the invalid lap are left out");
  assert.equal(Math.round(consistency.spreadMs), 163);
  assert.equal(consistency.rating, "Very consistent");
  assert.equal(data.formatSpread(consistency), "±0.163 s");
  assert.equal(data.consistencyOf(laps.slice(0, 2)), null);
  const stint = data.stintStats({ index: 1, compound: "Medium", startLap: 1, endLap: 4, laps: 4 }, laps);
  assert.equal(stint.consistency.laps, 3);
});
