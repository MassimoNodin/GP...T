import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

// Engineer Questions: answer mapping (v2 and v1), qualification copy, failure
// states per HTTP status, request building, thread and feedback storage,
// process-telemetry results and the telemetry CSV columns.

const require = createRequire(import.meta.url);
const typescript = require("typescript");
const featuresRoot = new URL("../src/features/", import.meta.url);

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

async function load(...spec) {
  return import(`data:text/javascript;base64,${Buffer.from(await moduleCode(spec.length === 1 ? spec[0] : spec)).toString("base64")}`);
}

const REASONS = "review-data/reasons.ts";
const ANSWER = ["lap-analysis/answer.ts", { "@/features/review-data/reasons": REASONS }];
const answer = await load(...ANSWER);
const client = await load("lap-analysis/ask-client.ts", { "./answer": ANSWER });
const thread = await load("lap-analysis/thread.ts");
const feedback = await load("lap-analysis/feedback.ts");
const telemetryModule = "lap-analysis/lap-telemetry.ts";
const processing = await load("lap-analysis/process-telemetry.ts", {
  "@/features/review-data/reasons": REASONS,
  "./lap-telemetry": telemetryModule,
});
const telemetry = await load(telemetryModule);
const delta = await load("lap-analysis/delta.ts", { "./lap-telemetry": telemetryModule });
const exporter = await load("lap-analysis/export.ts", {
  "@/features/review-data/format": "review-data/format.ts",
  "./delta": ["lap-analysis/delta.ts", { "./lap-telemetry": telemetryModule }],
  "./lap-telemetry": telemetryModule,
});

/* ------------------------------------------------------------------ fixtures */

const LAPS = [
  { keys: ["rev-28", "28"], lap: 28, ms: 73420 },
  { keys: ["rev-57", "57"], lap: 57, ms: 72842 },
];

const v2Body = (data) => ({ data });
const available = (overrides = {}) =>
  v2Body({
    schema_version: 1,
    status: "available",
    reason: null,
    selection: { target: "rev-28", reference: "rev-57", origin: "revision" },
    message: "The target lap is 578 ms slower than the reference lap.",
    report: {
      distance_m: [0, 5, 3330, 3337],
      channels: { speed_mps: {}, throttle: {}, brake: {}, steering: {}, time_s: {} },
      delta_time: { values: [] },
      braking_zones: [{ start_m: 100 }],
      qualifications: ["fuel_and_tyre_conditions_not_established", "numeric_distance_regions_not_named_corners"],
    },
    model: { model_name: "llama3.1:8b", latency_ms: 2412 },
    ...overrides,
  });

/* --------------------------------------------------------------- v2 mapping */

test("a v2 answer maps scope, evidence, related laps, limitations and model", () => {
  const view = answer.answerView(available(), LAPS, "v2");
  assert.equal(view.status, "answered");
  assert.equal(view.message, "The target lap is 578 ms slower than the reference lap.");
  assert.deepEqual(view.scope, ["Full lap (0 m – 3,337 m)"]);
  assert.deepEqual(view.evidence, ["Speed trace, throttle input, brake input, steering input, time delta, braking zones"]);
  assert.deepEqual(view.relatedLaps, [
    { role: "Target", lap: 28, time: "1:13.420" },
    { role: "Reference", lap: 57, time: "1:12.842" },
  ]);
  assert.deepEqual(view.limitations, ["Fuel load and tyre condition were not established.", "Regions are distance ranges, not named corners."]);
  assert.deepEqual(view.model, { name: "llama3.1:8b", latencyMs: 2412 });
  assert.equal(view.hint, null);
});

test("limitations are capped at four with the rest behind a disclosure, and unknown codes are humanised", () => {
  const codes = [
    "fuel_and_tyre_conditions_not_established",
    "later_braking_is_not_automatically_better",
    "pit_encountered",
    "acquisition_gap",
    "fuel_load_uncontrolled", // same copy as tyre and traffic: deduplicated
    "tyre_condition_uncontrolled",
    "brand_new_code",
  ];
  const view = answer.answerView(available({ report: { distance_m: [0, 10], channels: {}, qualifications: codes } }), LAPS);
  assert.equal(view.limitations.length, 4);
  assert.deepEqual(view.moreLimitations, ["Fuel, tyres and traffic were not controlled.", "Brand new code."]);
  assert.equal(answer.qualificationCopy("measurement_not_reference_ranking"), "This is a measurement, not a ranking of laps.");
  assert.equal(answer.qualificationCopy("invalid_or_unknown_game_validity"), "One of the laps was invalid or its validity is unknown.");
  assert.equal(answer.qualificationCopy("different_lifecycle_epochs"), "The laps come from different parts of the session.");
  assert.equal(answer.qualificationCopy("track_geometry_not_fully_established"), "Track geometry was not fully established.");
});

test("race-lap reports are scoped to the lap, and a missing reference leaves one related lap", () => {
  const view = answer.answerView(
    available({ selection: { target: "rev-28", reference: null, origin: "revision", kind: "race_lap" }, report: { lap: { lap_time_ms: 73420 } } }),
    LAPS,
  );
  assert.deepEqual(view.scope, ["Lap 28 (race lap diagnostic)"]);
  assert.deepEqual(view.relatedLaps, [{ role: "Target", lap: 28, time: "1:13.420" }]);
  assert.deepEqual(view.evidence, []);
});

test("unavailable and unsupported answers carry the reason copy and a hint", () => {
  const noReference = answer.answerView(available({ status: "unavailable", reason: "reference_unavailable", report: null, message: "x" }), LAPS);
  assert.equal(noReference.status, "unavailable");
  assert.equal(noReference.message, "No eligible reference lap is available.");
  assert.equal(noReference.hint, null);
  const unprocessed = answer.answerView(
    available({ status: "unavailable", reason: "historical_materialization_not_authorized", report: null, message: "x" }),
    LAPS,
  );
  assert.match(unprocessed.hint, /Process unprocessed telemetry/);
  const unsupported = answer.answerView(available({ status: "unsupported", reason: "question_unsupported", report: null, message: "Only pair comparison." }), LAPS);
  assert.equal(unsupported.status, "unsupported");
  assert.equal(unsupported.message, "Only pair comparison.");
  assert.equal(unsupported.hint, "Try one of the suggested questions.");
  const pending = answer.answerView(available({ status: "pending", reason: "detail_wait_timeout", report: null, message: "x" }), LAPS);
  assert.equal(pending.status, "unavailable");
});

test("bodies that are not answers map to null so the HTTP status decides", () => {
  assert.equal(answer.answerView(null, LAPS), null);
  assert.equal(answer.answerView({ status: "unavailable", data: null, reason: "x" }, LAPS), null);
  assert.equal(answer.answerView(v2Body({ status: "weird", message: "m" }), LAPS), null);
  assert.equal(answer.answerView(v2Body({ status: "available", message: "" }), LAPS), null);
  assert.equal(answer.answerView({ data: { status: "answered", message: "m" } }, LAPS, "v1"), null, "v1 needs the ok envelope");
});

/* --------------------------------------------------------------- v1 mapping */

test("a v1 answer maps facts to evidence and warnings to limitations", () => {
  const body = {
    api_version: "v1",
    status: "ok",
    data: {
      status: "partial",
      message: "Recorded facts for this lap.",
      selection: { intent: "attempt_summary", target_attempt_key: "28" },
      report: {
        facts: [{ kind: "lap_time", text: "Lap time 1:13.420.", source_fields: [] }, { kind: "game_validity", text: "The game accepted the lap." }],
        warnings: [{ code: "session_context_unknown", text: "Session context is unavailable." }],
      },
      model: { model_name: "qwen3:4b", latency_ms: 800 },
    },
  };
  const view = answer.answerView(body, LAPS, "v1");
  assert.equal(view.status, "partial");
  assert.deepEqual(view.evidence, ["Lap time 1:13.420.", "The game accepted the lap."]);
  assert.deepEqual(view.limitations, ["Session context is unavailable."]);
  assert.deepEqual(view.scope, ["Lap 28 (1:13.420)"]);
  assert.deepEqual(view.relatedLaps, [{ role: "Target", lap: 28, time: "1:13.420" }]);
  assert.deepEqual(view.model, { name: "qwen3:4b", latencyMs: 800 });
  const unavailable = answer.answerView(
    { status: "ok", data: { status: "unavailable", reason: "lap_debrief_selection_unavailable", message: "generic", selection: {}, report: null } },
    LAPS,
    "v1",
  );
  assert.equal(unavailable.status, "unavailable");
  assert.equal(unavailable.message, "Lap debrief selection unavailable");
});

/* ------------------------------------------------------------------ failures */

test("each failure status maps to its own turn state and copy", () => {
  const busy = answer.askFailure(409, "comparison_read_budget_busy");
  assert.deepEqual([busy.kind, busy.retryable], ["busy", true]);
  assert.equal(busy.message, "The engineer is busy with another request. Try again in a moment.");
  const slow = answer.askFailure(504, "engineer_request_deadline_exceeded");
  assert.deepEqual([slow.kind, slow.message], ["timeout", "The engineer took too long to answer. Try again."]);
  assert.equal(answer.askFailure(null, null, true).kind, "timeout");
  const forbidden = answer.askFailure(403, "engineer_control_not_authorized");
  assert.deepEqual([forbidden.kind, forbidden.retryable], ["forbidden", false]);
  assert.equal(forbidden.message, "The web app is not allowed to ask questions. Check the engine's control token.");
  assert.equal(answer.askFailure(401, null).kind, "forbidden");
  for (const status of [null, 502, 503]) {
    const offline = answer.askFailure(status, null);
    assert.deepEqual([offline.kind, offline.message], ["offline", "The local engine is unavailable."]);
  }
  const invalid = answer.askFailure(422, "engineer_request_invalid");
  assert.deepEqual([invalid.kind, invalid.retryable], ["invalid", false]);
  assert.equal(answer.askFailure(413, "engineer_request_limit_exceeded").message, "The question is too long.");
  assert.equal(answer.askFailure(500, null).kind, "unavailable");
});

test("suggested questions depend on whether a reference lap is selected", () => {
  assert.deepEqual(answer.suggestedQuestions({ targetLap: 28, referenceLap: 57 }), [
    "Where did I lose time to Lap 57?",
    "How did my braking compare?",
    "Summarise Lap 28",
  ]);
  assert.deepEqual(answer.suggestedQuestions({ targetLap: 28, referenceLap: null }), [
    "Summarise Lap 28",
    "Was Lap 28 valid?",
    "What limits this lap's data?",
  ]);
  assert.deepEqual(answer.suggestedQuestions({ targetLap: null, referenceLap: 3 }), []);
  assert.equal(answer.pendingCopy(28, 57), "Measuring Lap 28 against Lap 57…");
  assert.equal(answer.pendingCopy(28, null), "Reading Lap 28…");
});

/* ------------------------------------------------------------------ requests */

const v2Lap = (n, ms) => ({ id: `rev-${n}`, lap: n, ms, attemptKey: null, revisionId: `rev-${n}` });
const v1Lap = (n, ms) => ({ id: String(n), lap: n, ms, attemptKey: String(n), revisionId: null });
const context = (overrides = {}) => ({
  source: "v2",
  sessionKey: "abc123",
  live: true,
  runtimeReady: false,
  sessionType: "practice_1",
  region: null,
  ...overrides,
});

test("v2 questions go through the session proxy with revision ids", () => {
  const plan = client.planAsk(context({ runtimeReady: true }), "Where did I lose time?", v2Lap(28, 73420), v2Lap(57, 72842), true);
  assert.equal(plan.kind, "request");
  assert.equal(plan.version, "v2");
  assert.equal(plan.url, "/api/review/sessions/abc123/ask");
  assert.deepEqual(plan.body, {
    question: "Where did I lose time?",
    target: "rev-28",
    reference: "rev-57",
    allow_materialization: true,
    use_model: true,
  });
  const noReference = client.planAsk(context(), "Summarise", v2Lap(28, 1), null, false);
  assert.equal(noReference.body.reference, null);
  assert.equal(noReference.body.use_model, false, "no model unless the runtime is ready");
  assert.equal(noReference.body.allow_materialization, false);
  assert.equal(client.planAsk(context(), "q", null, null, false).kind, "blocked");
  assert.equal(client.planAsk(context(), "q", { ...v2Lap(1, 1), revisionId: null }, null, false).kind, "blocked");
});

test("v1 questions use attempt keys, and a selected region switches to a region comparison", () => {
  const summary = client.planAsk(context({ source: "v1" }), "Summarise", v1Lap(5, 1), v1Lap(3, 1), false);
  assert.equal(summary.url, "/api/engineer/ask");
  assert.deepEqual(summary.body, { question: "Summarise", selection: { intent: "attempt_summary", target_attempt_key: "5" } });
  const region = client.planAsk(
    context({ source: "v1", sessionType: "time_trial", region: { modelId: "monaco-2024", revision: 3, regionId: "t6" } }),
    "How was T6?",
    v1Lap(5, 1),
    v1Lap(3, 1),
    false,
  );
  assert.deepEqual(region.body.selection, {
    intent: "region_comparison",
    target_attempt_key: "5",
    reference_attempt_key: "3",
    comparison_policy: "time_trial",
    track_model_id: "monaco-2024",
    track_model_revision: 3,
    region_identifier: "t6",
  });
  const noReference = client.planAsk(context({ source: "v1", region: { modelId: "m", revision: 1, regionId: "t1" } }), "q", v1Lap(5, 1), null, false);
  assert.equal(noReference.body.selection.intent, "attempt_summary", "a region comparison needs a reference attempt");
  assert.equal(client.planAsk(context({ source: "v1" }), "q", { ...v1Lap(5, 1), attemptKey: null }, null, false).kind, "blocked");
});

function stubFetch(handler) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    return handler(url, init);
  };
  fn.calls = calls;
  return fn;
}
const json = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

test("a successful ask returns the mapped answer and posts JSON", async () => {
  const fetchImpl = stubFetch(() => json(200, available()));
  const plan = client.planAsk(context(), "Compare", v2Lap(28, 73420), v2Lap(57, 72842), false);
  const outcome = await client.runAsk(plan, [v2Lap(28, 73420), v2Lap(57, 72842)], { fetchImpl });
  assert.equal(outcome.kind, "answer");
  assert.equal(outcome.view.relatedLaps[1].lap, 57);
  assert.equal(fetchImpl.calls[0].init.method, "POST");
  assert.equal(fetchImpl.calls[0].init.headers["content-type"], "application/json");
  assert.equal(JSON.parse(fetchImpl.calls[0].init.body).target, "rev-28");
});

test("ask errors map from the HTTP status, network failure, timeout and cancellation", async () => {
  const plan = client.planAsk(context(), "q", v2Lap(28, 1), null, false);
  const run = (handler, options = {}) => client.runAsk(plan, [v2Lap(28, 1)], { fetchImpl: stubFetch(handler), ...options });
  assert.equal((await run(() => json(409, { reason: "comparison_read_budget_busy" }))).failure.kind, "busy");
  assert.equal((await run(() => json(504, { reason: "engineer_request_deadline_exceeded" }))).failure.kind, "timeout");
  assert.equal((await run(() => json(403, { reason: "engineer_control_not_authorized" }))).failure.kind, "forbidden");
  assert.equal((await run(() => json(503, null))).failure.kind, "offline");
  assert.equal((await run(() => { throw new TypeError("fetch failed"); })).failure.kind, "offline");
  const garbled = await run(() => json(200, { nope: true }));
  assert.equal(garbled.kind, "failure");

  const hang = (_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
  const timedOut = await run(hang, { timeoutMs: 10 });
  assert.equal(timedOut.failure.kind, "timeout");

  const controller = new AbortController();
  const pending = run(hang, { signal: controller.signal, timeoutMs: 5000 });
  controller.abort();
  assert.equal((await pending).kind, "cancelled");
});

/* ------------------------------------------------------- thread and feedback */

test("threads are keyed per session and laps, capped at 20 turns, and tolerate bad storage", () => {
  assert.equal(thread.threadKey("abc", "rev-28", "rev-57"), "race-engineer.thread.abc.rev-28.rev-57");
  assert.equal(thread.threadKey("abc", "rev-28", null), "race-engineer.thread.abc.rev-28.none");
  const view = answer.answerView(available(), LAPS);
  const turns = Array.from({ length: 25 }, (_, i) => ({
    id: String(i),
    question: `Q${i}`,
    askedAt: "18:45",
    targetLap: 28,
    referenceLap: 57,
    outcome: i % 3 === 0 ? { kind: "cancelled" } : i % 3 === 1 ? { kind: "answer", view } : { kind: "failure", failure: answer.askFailure(409, null) },
  }));
  const stored = thread.parseThread(thread.serializeThread(turns));
  assert.equal(stored.length, 20);
  assert.equal(stored[0].question, "Q5");
  assert.deepEqual(stored.at(-1).outcome, turns.at(-1).outcome);
  assert.deepEqual(stored.find((turn) => turn.outcome.kind === "answer").outcome.view, view);
  assert.deepEqual(thread.parseThread(null), []);
  assert.deepEqual(thread.parseThread("{not json"), []);
  assert.deepEqual(thread.parseThread('{"a":1}'), []);
  assert.deepEqual(thread.parseThread(JSON.stringify([{ id: "1", question: "x", askedAt: "1", targetLap: 1, outcome: { kind: "answer", view: { status: "bogus" } } }])), []);
});

test("feedback is stored on this computer, replaces an earlier rating and keeps 200 entries", () => {
  const entry = (n, rating = "up", reason = null) => ({
    question: `Q${n}`,
    session: "abc",
    targetLap: 28,
    referenceLap: 57,
    rating,
    reason,
    at: new Date(2026, 9, 12, 18, 45, n % 60).toISOString(),
  });
  let list = [];
  for (let i = 0; i < 205; i += 1) list = feedback.recordFeedback(list, entry(i));
  assert.equal(list.length, 200);
  assert.equal(list[0].question, "Q5");
  const replaced = feedback.recordFeedback(list, entry(100, "down", "Inaccurate"));
  assert.equal(replaced.length, 200);
  assert.equal(feedback.findFeedback(replaced, entry(100)).rating, "down");
  assert.equal(feedback.findFeedback(replaced, entry(100)).reason, "Inaccurate");
  assert.equal(replaced.at(-1).question, "Q100", "the newest rating moves to the end");
  assert.equal(feedback.findFeedback(replaced, { ...entry(100), session: "other" }), null);
  assert.deepEqual(feedback.parseFeedback(feedback.serializeFeedback(list)), list);
  assert.deepEqual(feedback.parseFeedback("nonsense"), []);
  assert.deepEqual(feedback.parseFeedback(JSON.stringify([{ question: 1 }])), []);

  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  assert.equal(feedback.saveFeedback(storage, entry(1)), true);
  assert.equal(feedback.saveFeedback(storage, entry(2, "down", "Other")), true);
  assert.equal(feedback.loadFeedback(storage).length, 2);
  assert.equal(store.has("race-engineer.answer-feedback.v1"), true);
  assert.equal(feedback.saveFeedback({ getItem: () => null, setItem: () => { throw new Error("quota"); } }, entry(3)), false);
  assert.equal(feedback.saveFeedback(null, entry(3)), false);
  assert.match(feedback.FEEDBACK_SAVED, /saved on this computer/);
});

/* ------------------------------------------------------ process telemetry + CSV */

test("processing a deferred lap returns a parsed trace or a state the UI can show", async () => {
  const samples = Array.from({ length: 200 }, (_, i) => ({
    frame_ordinal: i,
    lap_distance_m: i * 5,
    speed_mps: 50,
    throttle: 1,
    brake: 0,
    steering: 0,
    car_telemetry_available: true,
  }));
  const ok = await processing.processTelemetry("abc", "rev 31", {
    fetchImpl: stubFetch(() => json(200, { data: { attempt: {}, samples, persistence: "request_scoped_ephemeral" } })),
  });
  assert.equal(ok.state, "ok");
  assert.equal(ok.trace.state, "available");
  const calls = [];
  await processing.processTelemetry("abc", "rev 31", { fetchImpl: stubFetch((url, init) => { calls.push({ url, init }); return json(409, { reason: "materialization_busy" }); }) });
  assert.equal(calls[0].url, "/api/review/sessions/abc/attempts/rev%2031/materialize");
  assert.equal(calls[0].init.method, "POST");
  const run = (handler) => processing.processTelemetry("abc", "rev", { fetchImpl: stubFetch(handler) });
  assert.deepEqual(await run(() => json(409, null)), { state: "busy", message: "The telemetry reader is busy. Try again in a moment." });
  assert.match((await run(() => json(403, null))).message, /control token/);
  assert.equal((await run(() => json(422, { reason: "analysis_read_budget_exceeded" }))).retryable, false);
  assert.equal((await run(() => json(503, null))).retryable, true);
  assert.equal((await run(() => { throw new TypeError("down"); })).message, "The local engine is unavailable.");
  assert.equal((await run(() => json(200, { data: { samples: [] } }))).state, "failed");
});

test("the telemetry export has distance, per-lap channels and the derived delta as columns", () => {
  const run = (value, length = 100) => Array.from({ length: length / 5 + 1 }, (_, i) => [i * 5, value]);
  const channels = (speed) => ({ speed: [run(speed)], throttle: [run(100)], brake: [run(0)], steering: [run(0)] });
  const lap = (speed) => ({ state: "available", samples: 21, length: 100, channels: channels(speed), coverage: { speed: { metres: 100, runs: 1 } }, path: null });
  const series = delta.deltaSeries(lap(180), lap(200), 1000, 900, 100);
  const header = exporter.telemetryCsv(lap(180), lap(200), series).split("\n")[0].split(",");
  assert.deepEqual(header, [
    "distance_m",
    "target_speed_kph",
    "target_throttle_pct",
    "target_brake_pct",
    "target_steering_pct",
    "reference_speed_kph",
    "reference_throttle_pct",
    "reference_brake_pct",
    "reference_steering_pct",
    "delta_s",
  ]);
  assert.equal(exporter.telemetryCsv(telemetry.unavailableTrace("not_processed"), null, null), null);
});
