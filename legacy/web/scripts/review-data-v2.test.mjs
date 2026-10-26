import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const typescript = require("typescript");
const sourceRoot = new URL("../src/features/review-data/", import.meta.url);

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

// session-evidence.ts only imports ./format at runtime (other imports are
// type-only and erased), so inline format.ts as a data URL.
const formatUrl = toDataUrl(transpile(await readFile(new URL("format.ts", sourceRoot), "utf8"), "format.ts"));
const evidenceSource = transpile(
  await readFile(new URL("session-evidence.ts", sourceRoot), "utf8"),
  "session-evidence.ts",
).replace('from "./format"', `from ${JSON.stringify(formatUrl)}`);
const evidence = await import(toDataUrl(evidenceSource));

const CONTEXT = {
  air_temperature_c: 26,
  packet_format: 2025,
  rule_set: "practice_qualifying",
  session_type: "practice_1",
  track_length_m: 6175,
  track_name: "Jeddah",
  track_temperature_c: 36,
  weather_name: "clear",
  total_laps: 1,
};

function sessionBody(overrides = {}) {
  return {
    data: [
      {
        id: "4a09407f07885540b29221e7636ec37f",
        generation: "58c75388",
        uid: "1748447935434161293",
        lifecycle: "ended",
        acquisition: "receiving",
        context: JSON.stringify(CONTEXT),
        occurrence: 1,
        ...overrides,
      },
    ],
  };
}

function attemptRecord(id, role, payload, extra = {}) {
  return {
    id,
    role,
    published_sequence: extra.sequence ?? 10,
    detail: { state: extra.detail ?? "available" },
    payload: {
      attempt_number: payload.lap_number,
      disposition: "completed",
      game_valid: true,
      pit_encountered: false,
      start_observed: true,
      superseded: null,
      context_segments: [{ context: null, from_frame_identifier: 1 }, { context: CONTEXT, from_frame_identifier: 2 }],
      ...payload,
    },
  };
}

const attemptsBody = {
  data: [
    attemptRecord("opp-1", "opponent", { lap_number: 1, lap_time_ms: 80000 }),
    attemptRecord("rev-3", "player", { lap_number: 3, lap_time_ms: 93996 }, { sequence: 30 }),
    attemptRecord("rev-1", "player", { lap_number: 1, lap_time_ms: 95367, start_observed: false }, { sequence: 11 }),
    attemptRecord("rev-2", "player", { lap_number: 2, lap_time_ms: 93834 }, { sequence: 20 }),
    attemptRecord("rev-4", "player", { lap_number: 4, lap_time_ms: 92000, game_valid: false }),
    attemptRecord("rev-5", "player", { lap_number: 5, lap_time_ms: 107565, pit_encountered: true }),
    attemptRecord("rev-6", "player", {
      lap_number: 6,
      lap_time_ms: null,
      disposition: "abandoned",
      game_valid: null,
    }),
    attemptRecord("rev-7", "player", { lap_number: 7, lap_time_ms: 90000, superseded: "rev-8" }),
    // The game re-timed lap 2: the higher attempt number wins.
    attemptRecord("rev-2b", "player", { lap_number: 2, attempt_number: 9, lap_time_ms: 93700 }),
    { id: "broken" },
  ],
};

test("context parsing reads the JSON string and tolerates bad input", () => {
  const context = evidence.parseEvidenceContext(JSON.stringify(CONTEXT));
  assert.deepEqual(context, {
    trackName: "Jeddah",
    sessionType: "practice_1",
    weatherName: "clear",
    airTemperatureC: 26,
    trackTemperatureC: 36,
    packetFormat: 2025,
    ruleSet: "practice_qualifying",
    trackLengthM: 6175,
    totalLaps: 1,
  });
  assert.deepEqual(evidence.parseEvidenceContext(CONTEXT), context);
  assert.equal(evidence.parseEvidenceContext("{not json"), null);
  assert.equal(evidence.parseEvidenceContext(null), null);
  assert.equal(evidence.parseEvidenceContext("[1,2]"), null);
  assert.equal(evidence.parseEvidenceContext('"text"'), null);
  const sparse = evidence.parseEvidenceContext('{"track_name":"","air_temperature_c":"hot","packet_format":null}');
  assert.equal(sparse.trackName, null);
  assert.equal(sparse.airTemperatureC, null);
  assert.equal(sparse.packetFormat, null);
});

test("session and attempt parsers skip malformed records", () => {
  const sessions = evidence.parseEvidenceSessions({ data: [...sessionBody().data, { lifecycle: "ended" }, null, 4] });
  assert.equal(sessions.length, 1);
  assert.equal(sessions[0].id, "4a09407f07885540b29221e7636ec37f");
  assert.equal(sessions[0].context.trackName, "Jeddah");
  assert.deepEqual(evidence.parseEvidenceSessions(undefined), []);
  assert.deepEqual(evidence.parseEvidenceSessions({ data: "nope" }), []);

  const attempts = evidence.parseEvidenceAttempts(attemptsBody);
  assert.equal(attempts.length, 9); // "broken" has no payload
  assert.equal(attempts[0].role, "opponent");
  assert.equal(attempts[0].context.trackName, "Jeddah"); // first non-null segment
  assert.equal(attempts[1].traceAvailable, true);
  assert.equal(attempts.find((a) => a.id === "rev-7").superseded, true);
});

test("session maps to a library row with Game source and lifecycle status", () => {
  const [session] = evidence.parseEvidenceSessions(sessionBody());
  const row = evidence.evidenceLibraryFields(session, session.context, 3);
  assert.equal(row.key, session.id);
  assert.equal(row.title, "Jeddah");
  assert.deepEqual(row.type, { label: "Practice 1", tone: "amber" });
  assert.deepEqual(row.source, { label: "Game", tone: "blue" });
  assert.deepEqual(row.meta, ["F1 25", "Clear 26°C"]);
  assert.equal(row.laps, 3);
  assert.equal(row.notes, "");
  assert.deepEqual(row.status, { label: "Complete", tone: "green" });
  assert.equal(row.href, "/review/4a09407f07885540b29221e7636ec37f");
  assert.match(row.date, /^Date not recorded/);
  assert.ok(row.date.includes("4a09407f"));

  const unknown = evidence.evidenceLibraryFields({ ...session, context: null }, null, 0);
  assert.equal(unknown.title, "Unknown circuit");
  assert.equal(unknown.type.label, "Session");
  assert.deepEqual(unknown.meta, ["F1"]);
});

test("session type tones follow the library convention", () => {
  assert.equal(evidence.typeTone("race"), "green");
  assert.equal(evidence.typeTone("sprint_race"), "green");
  assert.equal(evidence.typeTone("practice_2"), "amber");
  assert.equal(evidence.typeTone("time_trial"), "purple");
  assert.equal(evidence.typeTone("qualifying_1"), "grey");
  assert.equal(evidence.typeTone(null), "grey");
});

test("lifecycle maps to status badges", () => {
  assert.deepEqual(evidence.lifecycleStatus("ended"), { label: "Complete", tone: "green" });
  assert.deepEqual(evidence.lifecycleStatus("interrupted"), { label: "Partial", tone: "amber" });
  assert.deepEqual(evidence.lifecycleStatus("active"), { label: "Live", tone: "blue" });
  assert.deepEqual(evidence.lifecycleStatus("live"), { label: "Live", tone: "blue" });
  assert.deepEqual(evidence.lifecycleStatus("archived"), { label: "Archived", tone: "grey" });
  assert.deepEqual(evidence.lifecycleStatus(""), { label: "Unknown", tone: "grey" });
});

test("laps are player-only, completed, de-duplicated and ordered", () => {
  const attempts = evidence.parseEvidenceAttempts(attemptsBody);
  const laps = evidence.evidenceLaps(attempts);
  assert.deepEqual(
    laps.map((lap) => [lap.id, lap.lap, lap.ms, lap.valid, lap.pit, lap.revisionId]),
    [
      ["1", 1, 95367, true, false, "rev-1"],
      ["2", 2, 93700, true, false, "rev-2b"],
      ["3", 3, 93996, true, false, "rev-3"],
      ["4", 4, 92000, false, false, "rev-4"],
      ["5", 5, 107565, true, true, "rev-5"],
    ],
  );
  assert.equal(laps[0].startObserved, false);
  assert.equal(laps[0].sectors, null);
  assert.equal(laps[0].traceAvailable, true);
});

test("session best ignores laps the game invalidated", () => {
  const laps = evidence.evidenceLaps(evidence.parseEvidenceAttempts(attemptsBody));
  const best = evidence.bestEvidenceLap(laps);
  assert.equal(best.lap, 2); // lap 4 is faster but invalid
  assert.equal(evidence.bestEvidenceLap([]), null);
  assert.equal(evidence.bestEvidenceLap(laps.filter((lap) => !lap.valid)), null);
});

test("deltas, time driven and events are derived from the laps", () => {
  const attempts = evidence.parseEvidenceAttempts(attemptsBody);
  const laps = evidence.evidenceLaps(attempts);
  const best = evidence.bestEvidenceLap(laps);
  assert.equal(evidence.lapDeltaMs(laps[2], best), 296);
  assert.equal(evidence.lapDeltaMs(best, best), 0);
  assert.equal(evidence.lapDeltaMs(best, null), null);
  assert.equal(evidence.timeDrivenMs(laps), 95367 + 93700 + 93996 + 92000 + 107565);
  assert.equal(evidence.timeDrivenMs([]), null);

  const events = evidence.evidenceEvents(attempts, laps);
  assert.deepEqual(
    events.map((event) => [event.lap, event.text]),
    [
      [2, "Session best (1:33.700)"],
      [4, "Lap invalidated by the game"],
      [5, "Pit lane in or out lap"],
      [6, "Lap not completed"],
    ],
  );
  assert.equal(events[0].highlight, true);
});

test("progression keeps valid racing laps and marks best and selected", () => {
  const laps = evidence.evidenceLaps(evidence.parseEvidenceAttempts(attemptsBody));
  const best = evidence.bestEvidenceLap(laps);
  const progression = evidence.progressionFrom(laps, "3", best.id);
  assert.deepEqual(
    progression.points.map((point) => [point.lap, point.tone]),
    [
      [1, "green"],
      [2, "purple"],
      [3, "blue"],
    ],
  ); // lap 4 invalid, lap 5 (pit) is slower than median + 6 s
  assert.equal(progression.illustrative, false);
  assert.ok(progression.yMin <= 93700 && progression.yMax >= 95367);
  assert.equal(evidence.progressionFrom([], "", null).points.length, 0);
  assert.equal(evidence.progressionFrom([{ id: "1", lap: 1, ms: 90000, valid: false }], "1", null).points.length, 0);
});

test("conditions, session recency and context fallback", () => {
  const [session] = evidence.parseEvidenceSessions(sessionBody({ context: null }));
  const attempts = evidence.parseEvidenceAttempts(attemptsBody);
  assert.equal(session.context, null);
  assert.equal(evidence.evidenceSessionContext(session, attempts).trackName, "Jeddah");
  assert.equal(evidence.evidenceSessionContext(session, []), null);
  assert.equal(evidence.conditionsLabel({ weatherName: "light_rain", airTemperatureC: 18.4 }), "Light Rain 18°C");
  assert.equal(evidence.conditionsLabel({ weatherName: null, airTemperatureC: 20 }), "20°C");
  assert.equal(evidence.conditionsLabel({ weatherName: null, airTemperatureC: null }), null);
  assert.equal(evidence.conditionsLabel(null), null);
  assert.equal(evidence.sessionRecency(attempts), 30);
  assert.equal(evidence.sessionRecency([]), -1);
  assert.equal(evidence.formatTrackLength(6175), "6,175 m");
  assert.equal(evidence.formatTrackLength(null), "—");
});
