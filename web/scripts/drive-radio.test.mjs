import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

// Drive Diagnostics: radio event mapping, outcomes (question/response
// pairing), counts, unread and follow mode, trigger copy, read-aloud rules,
// the polled feed, and the System Status and Current Activity mappings.

const require = createRequire(import.meta.url);
const typescript = require("typescript");
const webRoot = new URL("../src/", import.meta.url);

function transpile(source, fileName) {
  return typescript.transpileModule(source, {
    fileName,
    compilerOptions: {
      module: typescript.ModuleKind.ESNext,
      target: typescript.ScriptTarget.ES2022,
    },
  }).outputText;
}

/** Transpiles a source file and rewrites module specifiers to data URLs. */
async function loadUrl(path, replacements = {}) {
  let code = transpile(await readFile(new URL(path, webRoot), "utf8"), path);
  for (const [specifier, url] of Object.entries(replacements)) {
    code = code.replaceAll(`from "${specifier}"`, `from ${JSON.stringify(url)}`);
  }
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}

const reviewData = "features/review-data/";
const utilsUrl = await loadUrl(`${reviewData}parse-utils.ts`);
const reasonsUrl = await loadUrl(`${reviewData}reasons.ts`);
const eventsUrl = await loadUrl(`${reviewData}engineer-events.ts`, { "./parse-utils": utilsUrl });
const mapUrl = await loadUrl("features/live-status/map.ts");

const radio = await import(
  await loadUrl("features/drive-diagnostics/radio.ts", { "@/features/review-data/reasons": reasonsUrl })
);
const feed = await import(
  await loadUrl("features/drive-diagnostics/radio-feed.ts", { "@/features/review-data/engineer-events": eventsUrl })
);
const rows = await import(
  await loadUrl("features/drive-diagnostics/system-rows.ts", {
    "@/features/live-status/map": mapUrl,
    "@/features/review-data/reasons": reasonsUrl,
  })
);
const session = await import(await loadUrl("features/drive-diagnostics/session.ts"));
const events = await import(eventsUrl);

/* --------------------------------------------------------------- fixtures */

const BASE = Date.UTC(2026, 9, 12, 18, 40, 0);

function iso(offsetSeconds) {
  return new Date(BASE + offsetSeconds * 1000).toISOString();
}

function event(id, kind, offsetSeconds, extra = {}) {
  return {
    id,
    createdAtUtc: iso(offsetSeconds),
    kind,
    topic: null,
    text: `${kind} ${id}`,
    sessionId: null,
    lap: null,
    source: "system",
    relatedId: null,
    priority: "normal",
    detail: null,
    ...extra,
  };
}

function lapEvent(id, offsetSeconds, rule, detail, extra = {}) {
  return event(id, "proactive", offsetSeconds, {
    topic: "lap_time",
    source: "live_rules",
    sessionId: "s1",
    lap: 5,
    text: `Lap 5 message ${id}`,
    detail: { rule, lap_time_ms: 92_104, ...detail },
    ...extra,
  });
}

const NOW = BASE + 120_000;
const context = { now: NOW, timeZone: "UTC" };
const byKey = (list, id) => list.find((row) => row.id === `event-${id}`);

/* ------------------------------------------------------------------ format */

test("clock columns use the given time zone and 24 hour time", () => {
  assert.equal(radio.formatClock("2026-10-12T18:42:16.123Z", { timeZone: "UTC" }), "18:42");
  assert.equal(radio.formatClock("2026-10-12T18:42:16.123Z", { seconds: true, timeZone: "UTC" }), "18:42:16");
  assert.equal(radio.formatClock("2026-10-12T00:05:00Z", { timeZone: "UTC" }), "00:05");
  assert.equal(radio.formatClock("2026-10-12T18:42:00Z", { timeZone: "Asia/Tokyo" }), "03:42");
  assert.equal(radio.formatClock("not a date", { timeZone: "UTC" }), "--:--");
  assert.equal(radio.formatClock("2026-10-12T18:42:00Z", { timeZone: "Not/AZone" }), "18:42", "bad zones fall back to UTC");
});

test("lap times and deltas format like the engine messages", () => {
  assert.equal(radio.formatLapTime(92_104), "1:32.104");
  assert.equal(radio.formatLapTime(59_999), "0:59.999");
  assert.equal(radio.formatDelta(321), "+0.321");
  assert.equal(radio.formatDelta(-1500), "-1.500");
  assert.equal(radio.humanize("lap_time"), "Lap time");
});

/* ----------------------------------------------------------------- mapping */

test("events map to rows newest first with the reference kind columns", () => {
  const list = [
    event(1, "question", 0, { source: "v2_ask", sessionId: "s1", lap: 5 }),
    event(2, "response", 3, { source: "v2_ask", relatedId: 1, detail: { status: "answered", route: "measured" } }),
    lapEvent(3, 60, "lap_completed", { delta_ms: 321, best_ms: 91_783 }),
    event(4, "issue", 70, { source: "v2_ask", detail: { reason: "engineer_ask_busy", http_status: 409 } }),
  ];
  const result = radio.buildRadioRows(list, context);
  assert.deepEqual(result.map((row) => row.seq), [4, 3, 2, 1]);
  assert.deepEqual(result.map((row) => row.kind), ["issue", "proactive", "response", "question"]);
  assert.deepEqual(
    result.map((row) => radio.KIND_LABEL[row.kind]),
    ["Issue", "Proactive", "Engineer", "You"],
  );
  assert.deepEqual(
    result.map((row) => row.title),
    ["Issue", "Proactive Message", "Engineer Response", "Driver Question"],
  );
  assert.equal(result[3].time, "18:40");
  assert.equal(result[3].timestamp, "18:40:00");
});

test("question outcomes pair with responses and failures by related id", () => {
  const list = [
    event(1, "question", 0, { source: "v2_ask" }),
    event(2, "response", 4, { source: "v2_ask", relatedId: 1, detail: { status: "answered" } }),
    event(3, "question", 10, { source: "v2_ask" }),
    event(4, "issue", 12, { source: "v2_ask", relatedId: 3, detail: { reason: "engineer_ask_unavailable" } }),
    event(5, "question", 20, { source: "v2_ask" }),
    event(6, "question", NOW / 1000 - BASE / 1000 - 5, { source: "v2_ask" }),
  ];
  const result = radio.buildRadioRows(list, context);
  assert.deepEqual([byKey(result, 1).outcome, byKey(result, 1).outcomeTone], ["Responded", "green"]);
  assert.deepEqual([byKey(result, 3).outcome, byKey(result, 3).outcomeTone], ["No answer", "red"]);
  assert.deepEqual([byKey(result, 5).outcome, byKey(result, 5).outcomeTone], ["No answer", "grey"], "older than 30 s");
  assert.deepEqual([byKey(result, 6).outcome, byKey(result, 6).outcomeTone], ["Waiting", "amber"], "within 30 s");
});

test("pending question is the newest unanswered one inside the wait window", () => {
  const list = [
    event(1, "question", 0),
    event(2, "response", 2, { relatedId: 1, detail: { status: "answered" } }),
    event(3, "question", 100),
    event(4, "question", 110),
  ];
  assert.equal(radio.pendingQuestion(list, BASE + 115_000).id, 4);
  assert.equal(radio.pendingQuestion(list, BASE + 200_000), null, "expired");
  assert.equal(radio.pendingQuestion(list.slice(0, 2), BASE + 3_000), null, "answered");
});

test("response outcomes follow detail.status", () => {
  const make = (status) => event(2, "response", 3, { relatedId: 1, detail: status ? { status } : null });
  const outcome = (status) => radio.buildRadioRows([make(status)], context)[0];
  assert.deepEqual([outcome("answered").outcome, outcome("answered").outcomeTone], ["Answered", "green"]);
  assert.deepEqual([outcome("partial").outcome, outcome("partial").outcomeTone], ["Partial", "amber"]);
  assert.deepEqual([outcome("unavailable").outcome, outcome("unavailable").outcomeTone], ["Unavailable", "grey"]);
  assert.equal(outcome(null).outcome, "Answered");
});

test("proactive outcome is Logged until read-aloud reports a result", () => {
  const list = [lapEvent(1, 0, "lap_completed", { delta_ms: 100 }), lapEvent(2, 5, "pit_lap", {}), lapEvent(3, 9, "personal_best", {})];
  const logged = radio.buildRadioRows(list, context);
  assert.deepEqual(logged.map((row) => row.outcome), ["Logged", "Logged", "Logged"]);
  const played = radio.buildRadioRows(list, { ...context, playback: { 1: "played", 2: "not_played" } });
  assert.equal(byKey(played, 1).outcome, "Played");
  assert.equal(byKey(played, 1).outcomeTone, "green");
  assert.equal(byKey(played, 1).output, "Spoken on this computer");
  assert.equal(byKey(played, 2).outcome, "Not played");
  assert.equal(byKey(played, 2).output, "Shown in Radio History");
  assert.equal(byKey(played, 3).outcome, "Logged");
});

test("issues are Logged for review", () => {
  const row = radio.buildRadioRows([event(1, "issue", 0, { detail: { reason: "engineer_ask_busy" } })], context)[0];
  assert.equal(row.outcome, "Logged");
  assert.equal(row.output, "Logged for review");
});

/* ------------------------------------------------------------ details copy */

test("lap rule triggers, evidence and limitations", () => {
  const triggers = {
    lap_invalidated: "Lap invalidated by the game",
    pit_lap: "Lap included a pit stop",
    first_timed_lap: "First timed lap of the session",
    personal_best: "New personal best",
    lap_completed: "Lap completed",
  };
  for (const [rule, copy] of Object.entries(triggers)) {
    const row = radio.buildRadioRows([lapEvent(1, 0, rule, {})], context)[0];
    assert.equal(row.trigger, copy, rule);
    assert.equal(row.limitations, radio.LAP_RULE_LIMITATION, rule);
  }

  const completed = radio.buildRadioRows(
    [lapEvent(1, 0, "lap_completed", { delta_ms: 321, best_ms: 91_783 }, { priority: "normal" })],
    context,
  )[0];
  assert.deepEqual(completed.evidence, [
    "Lap 5",
    "Lap time 1:32.104",
    "Delta to best +0.321",
    "Topic: Lap time",
    "Priority: Normal",
  ]);
  assert.equal(completed.timings, "Logged at 18:40:00");
  assert.equal(completed.output, "Shown in Radio History");

  const best = radio.buildRadioRows(
    [lapEvent(1, 0, "personal_best", { previous_best_ms: 92_425, delta_ms: -321 }, { priority: "high" })],
    context,
  )[0];
  assert.deepEqual(best.evidence, [
    "Lap 5",
    "Lap time 1:32.104",
    "Previous best 1:32.425",
    "Delta to previous best -0.321",
    "Topic: Lap time",
    "Priority: High",
  ]);
});

test("telemetry rules use their own trigger copy and no lap limitation", () => {
  const telemetry = (rule, kind = "proactive") =>
    event(1, kind, 0, {
      topic: "telemetry",
      source: "live_rules",
      detail: { rule, state: "receiving", previous_state: "stale" },
    });
  const copy = (rule) => radio.buildRadioRows([telemetry(rule)], context)[0];
  assert.equal(copy("connected").trigger, "Telemetry connected");
  assert.equal(copy("recovery").trigger, "Telemetry recovered");
  assert.equal(copy("failed").trigger, "Telemetry listener stopped");
  assert.equal(copy("something_else").trigger, "Telemetry state changed");
  assert.equal(copy("recovery").limitations, null);
  assert.ok(copy("recovery").evidence.includes("Receiver state: Stale to Receiving"));
});

test("question and response details", () => {
  const list = [
    event(1, "question", 0, { source: "v2_ask", sessionId: "session-abc", lap: 7, text: "How was my braking?" }),
    event(2, "response", 4.26, {
      source: "v2_ask",
      relatedId: 1,
      text: "Brakes were consistent.",
      detail: { status: "answered", route: "measured", focus: "braking" },
    }),
  ];
  const result = radio.buildRadioRows(list, context);
  const question = byKey(result, 1);
  assert.equal(question.trigger, "Asked from Lap Analysis");
  assert.deepEqual(question.evidence, ["Session #session-abc", "Lap 7"]);
  assert.equal(question.output, "Sent to the engineer");
  assert.equal(question.limitations, null);

  const response = byKey(result, 2);
  assert.equal(response.trigger, "Answer to: How was my braking?");
  assert.deepEqual(response.evidence, ["Route: Measured", "Focus: Braking", "Status: Answered"]);
  assert.equal(response.timings, "Answered 4.3 s after the question");
  assert.equal(response.output, "Shown in Radio History");
  assert.equal(response.limitations, null);
});

test("a response without its question in the loaded list falls back honestly", () => {
  const row = radio.buildRadioRows([event(9, "response", 4, { relatedId: 1, detail: { status: "answered" } })], context)[0];
  assert.equal(row.trigger, "Answer to a driver question");
  assert.equal(row.timings, "Logged at 18:40:04");
});

test("partial responses carry the reason as a limitation", () => {
  const partial = radio.buildRadioRows(
    [event(2, "response", 4, { relatedId: 1, detail: { status: "partial", reason: "analysis_read_budget_exceeded" } })],
    context,
  )[0];
  assert.equal(partial.limitations, "The answer was partial: the telemetry for this lap is too large to read.");
  const bare = radio.buildRadioRows([event(2, "response", 4, { detail: { status: "partial" } })], context)[0];
  assert.equal(bare.limitations, "The answer was partial.");
});

test("issue trigger and evidence come from the failure detail", () => {
  const row = radio.buildRadioRows(
    [event(1, "issue", 0, { source: "v2_ask", detail: { reason: "engineer_ask_busy", http_status: 409 } })],
    context,
  )[0];
  assert.equal(row.trigger, "The engineer is busy. Try again in a moment");
  assert.deepEqual(row.evidence, ["HTTP 409"]);
  const bare = radio.buildRadioRows([event(2, "issue", 0)], context)[0];
  assert.equal(bare.trigger, "Issue logged by the engine");
  assert.deepEqual(bare.evidence, []);
});

test("unknown reason codes are humanised", () => {
  const row = radio.buildRadioRows([event(1, "issue", 0, { detail: { reason: "handler_RuntimeError" } })], context)[0];
  assert.equal(row.trigger, "Handler runtimeerror");
});

/* ------------------------------------------------- counts, unread, follow */

test("filter counts cover the loaded events", () => {
  const list = [
    event(1, "question", 0),
    event(2, "response", 1),
    event(3, "proactive", 2),
    event(4, "proactive", 3),
    event(5, "issue", 4),
  ];
  assert.deepEqual(radio.radioCounts(radio.buildRadioRows(list, context)), {
    all: 5,
    question: 1,
    response: 1,
    proactive: 2,
    issue: 1,
  });
  assert.deepEqual(
    radio.FILTER_ITEMS.map((item) => item.label),
    ["All", "Questions", "Responses", "Proactive", "Issues"],
  );
  assert.equal(radio.FILTER_EMPTY.proactive, "No proactive messages yet.");
});

test("unread covers events newer than the last seen id until read", () => {
  const list = [{ id: 3 }, { id: 4 }, { id: 5 }, { id: 6 }];
  assert.deepEqual([...radio.unreadIds(list, 4, new Set())], [5, 6]);
  assert.deepEqual([...radio.unreadIds(list, 4, new Set([5]))], [6]);
  assert.deepEqual([...radio.unreadIds(list, 6, new Set())], []);
  assert.equal(radio.newestId(list), 6);
  assert.equal(radio.newestId([]), 0);
});

test("follow mode moves selection only from the newest row", () => {
  assert.equal(radio.followSelection(5, 5, 7), 7, "newest selected: follow");
  assert.equal(radio.followSelection(3, 5, 7), 3, "older row selected: stay");
  assert.equal(radio.followSelection(null, null, 7), 7, "nothing selected: take the newest");
  assert.equal(radio.followSelection(5, 5, null), 5);
  assert.equal(radio.followSelection(5, 5, 5), 5);
});

/* -------------------------------------------------------------- read aloud */

test("speech decisions follow the interruption preference", () => {
  const decide = (interruption, priority, speaking) => radio.speechDecision({ interruption, priority, speaking });
  assert.equal(decide("allow", "high", false), "speak");
  assert.equal(decide("allow", "high", true), "cancel_and_speak");
  assert.equal(decide("allow", "normal", true), "queue");
  assert.equal(decide("allow", "low", true), "queue");
  assert.equal(decide("queue", "high", true), "queue");
  assert.equal(decide("never", "high", true), "drop");
  assert.equal(decide("never", "normal", false), "speak");
});

test("only proactive calls and responses are speakable", () => {
  assert.equal(radio.isSpeakable({ kind: "proactive", text: "Lap 5" }), true);
  assert.equal(radio.isSpeakable({ kind: "response", text: "Answer" }), true);
  assert.equal(radio.isSpeakable({ kind: "question", text: "Why?" }), false);
  assert.equal(radio.isSpeakable({ kind: "issue", text: "Oops" }), false);
  assert.equal(radio.isSpeakable({ kind: "response", text: "  " }), false);
});

test("speech chunks split at sentences and respect the limit", () => {
  assert.deepEqual(radio.speechChunks("Lap 5, 1:32.104. New personal best."), ["Lap 5, 1:32.104. New personal best."]);
  const long = `${"Word ".repeat(30).trim()}. ${"Other ".repeat(30).trim()}.`;
  const chunks = radio.speechChunks(long, 100);
  assert.ok(chunks.length >= 2);
  assert.ok(chunks.every((chunk) => chunk.length <= 100));
  assert.equal(chunks.join(" ").replace(/\s+/g, " "), long.replace(/\s+/g, " "));
  assert.deepEqual(radio.speechChunks("   "), []);
});

test("the speaking preview keeps the first 40 characters", () => {
  assert.equal(radio.speakingPreview("Short call"), "Short call");
  const preview = radio.speakingPreview("Lap 5, 1:32.104, included a pit stop and more words here");
  assert.equal(preview, "Lap 5, 1:32.104, included a pit stop and…");
  assert.ok(preview.length <= 41);
});

/* ------------------------------------------------------------------ fixture */

test("reference rows keep the reference copy and are marked as fixture rows", () => {
  const fixture = [
    {
      id: "radio-24",
      time: "18:42",
      timestamp: "18:42:16",
      kind: "proactive",
      text: "Tyre temps increasing.",
      outcome: "Played",
      outcomeTone: "green",
      trigger: "Tyre temperature trend",
      evidence: ["Front left tyre: 104C"],
      output: "TTS playback (1.8 s)",
      timings: "Generated in 320 ms",
      limitations: "Only tyre surface temps available.",
    },
    {
      id: "radio-23",
      time: "18:40",
      timestamp: "18:40:41",
      kind: "question",
      text: "Gap to Norris?",
      outcome: "Responded",
      outcomeTone: "blue",
      trigger: "Push-to-talk question",
      evidence: [],
      output: "Routed",
      timings: "Transcribed in 410 ms",
    },
  ];
  const result = radio.fixtureRadioRows(fixture);
  assert.equal(result[0].seq, null);
  assert.equal(result[0].title, "Proactive Message");
  assert.equal(result[0].limitations, "Only tyre surface temps available.");
  assert.equal(result[1].outcomeTone, "grey", "unknown tones degrade to grey");
  assert.equal(result[1].limitations, null);
});

/* ----------------------------------------------------------------- the feed */

const page = (list, nextAfter) => ({ state: "ok", page: { events: list, nextAfter } });

test("first read replaces the list and reports no arrivals", () => {
  const state = feed.initialFeedState({ status: "unreachable" });
  const next = feed.reduceFeed(state, page([event(1, "question", 0), event(2, "response", 1)], 2), null);
  assert.deepEqual(next.events.map((item) => item.id), [1, 2]);
  assert.equal(next.nextAfter, 2);
  assert.equal(next.status, "ok");
  assert.equal(next.arrivalSeq, 0);
  assert.deepEqual(next.arrived, []);
});

test("later pages append, dedupe by id and report only new events", () => {
  let state = feed.initialFeedState({ status: "ok", events: [event(1, "question", 0), event(2, "response", 1)], nextAfter: 2 });
  state = feed.reduceFeed(state, page([event(2, "response", 1), event(3, "proactive", 5)], 3), 2);
  assert.deepEqual(state.events.map((item) => item.id), [1, 2, 3]);
  assert.deepEqual(state.arrived.map((item) => item.id), [3]);
  assert.equal(state.arrivalSeq, 1);
  assert.equal(state.nextAfter, 3);
  const quiet = feed.reduceFeed(state, page([], 3), 3);
  assert.equal(quiet.arrivalSeq, 1, "an empty page is not an arrival");
  assert.equal(quiet.events, state.events);
});

test("the list is bounded to the newest 200 events", () => {
  const many = Array.from({ length: 230 }, (_, index) => event(index + 1, "proactive", index));
  let state = feed.initialFeedState({ status: "ok", events: many.slice(0, 100), nextAfter: 100 });
  state = feed.reduceFeed(state, page(many.slice(100, 150), 150), 100);
  state = feed.reduceFeed(state, page(many.slice(150), 230), 150);
  assert.equal(state.events.length, feed.MAX_FEED_EVENTS);
  assert.equal(state.events[0].id, 31);
  assert.equal(state.events.at(-1).id, 230);
});

test("one dropped poll is tolerated; repeated failures change the status", () => {
  let state = feed.initialFeedState({ status: "ok", events: [event(1, "question", 0)], nextAfter: 1 });
  state = feed.reduceFeed(state, { state: "unreachable" }, 1);
  assert.equal(state.status, "ok");
  state = feed.reduceFeed(state, { state: "unreachable" }, 1);
  assert.equal(state.status, "unreachable");
  assert.equal(state.events.length, 1, "events are kept while disconnected");
  state = feed.reduceFeed(state, { state: "unavailable", reason: "engineer_activity_unavailable" }, 1);
  assert.equal(state.status, "unavailable");
  assert.equal(state.reason, "engineer_activity_unavailable");
  state = feed.reduceFeed(state, page([event(2, "response", 4)], 2), 1);
  assert.equal(state.status, "ok");
  assert.equal(state.failures, 0);
  assert.deepEqual(state.arrived.map((item) => item.id), [2]);
});

test("pages are requested through the proxy with the cursor and page size", async () => {
  const calls = [];
  const fetcher = async (url) => {
    calls.push(url);
    return {
      status: 200,
      json: async () => ({
        api_version: "v2",
        status: "ok",
        data: { events: [{ id: 5, created_at_utc: iso(0), kind: "question", topic: null, text: "Hi", session_id: null, lap: null, source: "v2_ask", related_id: null, priority: "normal", detail: null }], next_after: 5 },
      }),
    };
  };
  const first = await feed.fetchEventsPage(null, new AbortController().signal, fetcher);
  const next = await feed.fetchEventsPage(5, new AbortController().signal, fetcher);
  assert.deepEqual(calls, ["/api/engineer/events?limit=50", "/api/engineer/events?after=5&limit=50"]);
  assert.equal(first.state, "ok");
  assert.equal(next.page.nextAfter, 5);
});

test("an engine 503 differs from a proxy 503", async () => {
  const respond = (status, reason) => async () => ({ status, json: async () => ({ status: "unavailable", data: null, reason }) });
  const signal = new AbortController().signal;
  const engine = await feed.fetchEventsPage(null, signal, respond(503, "engineer_activity_unavailable"));
  assert.deepEqual(engine, { state: "unavailable", reason: "engineer_activity_unavailable" });
  const proxy = await feed.fetchEventsPage(null, signal, respond(503, "local_engine_unavailable"));
  assert.deepEqual(proxy, { state: "unreachable" });
});

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test("the poller is idle while hidden, polls with the live cursor and catches up on full pages", async () => {
  const cursors = [];
  const reads = [];
  let cursor = 10;
  const responses = [
    page([event(11, "proactive", 0), event(12, "proactive", 1)], 12), // full page (pageSize 2)
    page([event(13, "proactive", 2)], 13),
  ];
  const poller = feed.createFeedPoller({
    fetchRead: async (at) => {
      cursors.push(at);
      return responses.shift() ?? page([], cursor);
    },
    getCursor: () => cursor,
    onRead: (read) => {
      reads.push(read);
      if (read.state === "ok") cursor = read.page.nextAfter;
    },
    intervalMs: 30,
    slowIntervalMs: 30,
    pageSize: 2,
  });
  await wait(20);
  assert.deepEqual(cursors, [], "no request while hidden");
  poller.setHidden(false);
  await wait(15);
  assert.deepEqual(cursors.slice(0, 2), [10, 12], "a full page is followed by an immediate catch-up");
  await wait(60);
  poller.setHidden(true);
  const afterHide = cursors.length;
  await wait(60);
  assert.equal(cursors.length, afterHide, "no requests while hidden again");
  poller.stop();
  assert.ok(cursors.includes(13));
});

test("poll failures are reported as unreachable and polling continues slowly", async () => {
  const reads = [];
  let calls = 0;
  const poller = feed.createFeedPoller({
    fetchRead: async () => {
      calls += 1;
      throw new Error("network");
    },
    getCursor: () => null,
    onRead: (read) => reads.push(read),
    intervalMs: 10,
    slowIntervalMs: 20,
  });
  poller.setHidden(false);
  await wait(70);
  poller.stop();
  assert.ok(calls >= 2);
  assert.ok(reads.every((read) => read.state === "unreachable"));
});

test("stale responses after hide/show are dropped", async () => {
  const reads = [];
  let release;
  const gate = new Promise((resolve) => (release = resolve));
  let first = true;
  const poller = feed.createFeedPoller({
    fetchRead: async () => {
      if (first) {
        first = false;
        await gate;
        return page([event(1, "proactive", 0)], 1);
      }
      return page([], 0);
    },
    getCursor: () => null,
    onRead: (read) => reads.push(read),
    intervalMs: 500,
    slowIntervalMs: 500,
  });
  poller.setHidden(false);
  await wait(5);
  poller.setHidden(true);
  poller.setHidden(false);
  release();
  await wait(30);
  poller.stop();
  assert.equal(reads.filter((read) => read.state === "ok" && read.page.events.length > 0).length, 0);
});

test("events parsing and merging come from the shared parser", () => {
  const merged = events.mergeEngineerEvents([event(1, "question", 0)], [event(2, "response", 1), event(1, "question", 0)]);
  assert.deepEqual(merged.map((item) => item.id), [1, 2]);
});

/* ---------------------------------------------------------- system status */

function acquisition(state) {
  return {
    state,
    received: 10,
    journaled: 10,
    processed: 10,
    dropped: 0,
    kernel_dropped: 0,
    socket_errors: 0,
    queue_depth: 0,
    queue_limit: 1024,
    pending_publication: 0,
    last_publication_delay_s: 0.12,
  };
}

const runtimeReady = { status: "ready", reason: null, runtime: "Codex", model_name: "gpt-6-luna", last_inference_at_utc: null };

test("speech status is read from the transcribe envelope", () => {
  const ready = rows.parseSpeechStatus({ api_version: "v1", status: "ok", data: { status: "ready", reason: null, placement: "CPU" } });
  assert.deepEqual(ready, { kind: "ready", placement: "CPU" });
  const unavailable = rows.parseSpeechStatus({ data: { status: "unavailable", reason: "runtime_dependency_unavailable" } });
  assert.deepEqual(unavailable, { kind: "unavailable", reason: "runtime_dependency_unavailable" });
  assert.equal(rows.parseSpeechStatus({ api_version: "v1", status: "unavailable", data: null, reason: "engineer_speech_status_unavailable" }), null);
  assert.equal(rows.parseSpeechStatus(null), null);
});

test("speech recognition row states", () => {
  assert.deepEqual(rows.speechRow({ kind: "ready", placement: "CPU" }), {
    label: "Speech Recognition",
    status: "Ready",
    tone: "green",
    detail: "Local · CPU",
  });
  const unavailable = rows.speechRow({ kind: "unavailable", reason: "runtime_dependency_unavailable" });
  assert.equal(unavailable.status, "Unavailable");
  assert.equal(unavailable.tone, "amber");
  assert.equal(unavailable.detail, "Runtime dependency unavailable");
  assert.equal(rows.speechRow({ kind: "unreachable" }).tone, "grey");
  assert.equal(rows.speechRow({ kind: "checking" }).status, "Checking…");
});

test("microphone state and row", () => {
  assert.deepEqual(rows.microphoneState("granted", ["", "Shure MV7"]), { kind: "allowed", label: "Shure MV7" });
  assert.deepEqual(rows.microphoneState("granted", ["", " "]), { kind: "allowed", label: null });
  assert.deepEqual(rows.microphoneState("denied", ["Shure MV7"]), { kind: "blocked" });
  assert.deepEqual(rows.microphoneState("prompt", []), { kind: "not_set_up" });
  assert.deepEqual(rows.microphoneState(null, []), { kind: "not_set_up" });

  assert.deepEqual(rows.microphoneRow({ kind: "allowed", label: "Shure MV7" }), {
    label: "Microphone Input",
    status: "Allowed",
    tone: "green",
    detail: "Shure MV7",
  });
  assert.equal(rows.microphoneRow({ kind: "allowed", label: null }).detail, "Default microphone");
  assert.deepEqual(
    [rows.microphoneRow({ kind: "not_set_up" }).status, rows.microphoneRow({ kind: "not_set_up" }).detail],
    ["Not set up", "Allow in Settings"],
  );
  const blocked = rows.microphoneRow({ kind: "blocked" });
  assert.deepEqual([blocked.status, blocked.tone, blocked.detail], ["Blocked", "red", "Blocked by the browser"]);
  assert.equal(rows.microphoneRow({ kind: "unsupported" }).detail, "This browser has no microphone access");
  assert.equal(rows.microphoneRow({ kind: "checking" }).status, "Checking…");
});

test("audio output row states", () => {
  const available = rows.audioRow({ kind: "available", voice: "Microsoft Zira", speaking: false });
  assert.deepEqual([available.status, available.tone, available.detail], ["Available", "green", "Microsoft Zira"]);
  const speaking = rows.audioRow({ kind: "available", voice: "Microsoft Zira", speaking: true });
  assert.deepEqual([speaking.status, speaking.tone, speaking.detail], ["Speaking", "green", "Microsoft Zira"]);
  const none = rows.audioRow({ kind: "no_voice" });
  assert.deepEqual([none.status, none.tone, none.detail], ["No local voice", "grey", "Install a system voice"]);
  assert.equal(rows.audioRow({ kind: "unsupported" }).status, "Unsupported");
  assert.equal(rows.audioRow({ kind: "checking" }).status, "Checking…");
});

test("proactive assistance row comes from the saved topics", () => {
  const topics = { a: true, b: true, c: false, d: true, e: false, f: false, g: false, h: false };
  assert.deepEqual(rows.proactiveState(topics), { kind: "enabled", enabled: 3, total: 8 });
  assert.deepEqual(rows.proactiveState(Object.fromEntries(Object.keys(topics).map((key) => [key, false]))), { kind: "off" });
  assert.deepEqual(rows.proactiveState(null), { kind: "unknown" });

  const enabled = rows.proactiveRow({ kind: "enabled", enabled: 6, total: 8 });
  assert.deepEqual([enabled.status, enabled.tone, enabled.detail], ["Enabled", "green", "6 of 8 topics"]);
  const off = rows.proactiveRow({ kind: "off" });
  assert.deepEqual([off.status, off.tone, off.detail], ["Off", "grey", "All topics are off"]);
  const unknown = rows.proactiveRow({ kind: "unknown" });
  assert.deepEqual([unknown.status, unknown.tone, unknown.detail], ["Unknown", "grey", "Preferences unreadable"]);
});

const sources = {
  microphone: { kind: "allowed", label: "Shure MV7" },
  speech: { kind: "ready", placement: "CPU" },
  audio: { kind: "available", voice: "Zira", speaking: false },
  proactive: { kind: "enabled", enabled: 6, total: 8 },
};

test("seven rows in reference order, backed by their own sources", () => {
  const result = rows.buildDriveRows({ acquisition: acquisition("receiving"), runtime: runtimeReady, ...sources });
  assert.deepEqual(
    result.map((row) => row.label),
    [
      "Engineer Runtime",
      "Telemetry (Game Data)",
      "Microphone Input",
      "Speech Recognition",
      "Question Processing",
      "Audio Output",
      "Proactive Assistance",
    ],
  );
  assert.equal(result[0].status, "Running");
  assert.equal(result[1].status, "Receiving");
  assert.equal(result[2].detail, "Shure MV7");
  assert.equal(result[3].detail, "Local · CPU");
  assert.equal(result[4].status, "Ready");
  assert.equal(result[5].detail, "Zira");
  assert.equal(result[6].detail, "6 of 8 topics");
});

test("browser rows are unaffected by the engine; engine rows read unavailable when it is unreachable", () => {
  const result = rows.buildDriveRows({ acquisition: null, runtime: null, ...sources });
  const byLabel = Object.fromEntries(result.map((row) => [row.label, row]));
  for (const label of ["Engineer Runtime", "Telemetry (Game Data)", "Speech Recognition", "Question Processing", "Proactive Assistance"]) {
    assert.deepEqual([byLabel[label].status, byLabel[label].tone, byLabel[label].detail], ["Unavailable", "grey", "Service unreachable"], label);
  }
  assert.equal(byLabel["Microphone Input"].status, "Allowed");
  assert.equal(byLabel["Audio Output"].status, "Available");
});

test("one failing engine endpoint only affects its own rows", () => {
  const result = rows.buildDriveRows({ acquisition: acquisition("listening"), runtime: null, ...sources });
  const byLabel = Object.fromEntries(result.map((row) => [row.label, row]));
  assert.equal(byLabel["Telemetry (Game Data)"].status, "Waiting");
  assert.equal(byLabel["Engineer Runtime"].status, "Unavailable");
  assert.equal(byLabel["Speech Recognition"].status, "Ready");
});

/* ------------------------------------------------------- current activity */

function activity(overrides = {}) {
  return rows.driveActivity({
    acquisition: acquisition("receiving"),
    runtime: runtimeReady,
    speaking: null,
    answering: false,
    ...overrides,
  });
}

test("current activity states and waveform modes", () => {
  assert.deepEqual(activity(), {
    text: "Following live telemetry",
    tone: "green",
    wave: { kind: "static", tone: "green", dim: true },
  });
  assert.deepEqual(activity({ speaking: "Lap 5, 1:32.104" }), {
    text: "Speaking: Lap 5, 1:32.104",
    tone: "green",
    wave: { kind: "animated", tone: "red" },
  });
  assert.deepEqual(activity({ answering: true }), {
    text: "Answering your question…",
    tone: "blue",
    wave: { kind: "typing", tone: "blue" },
  });
  assert.deepEqual(activity({ acquisition: acquisition("listening") }), {
    text: "Waiting for the game",
    tone: "grey",
    wave: { kind: "flat", tone: "grey" },
  });
  assert.equal(activity({ acquisition: acquisition("starting") }).text, "Waiting for the game");
  assert.equal(activity({ acquisition: acquisition("stale") }).text, "Waiting for the game");
  assert.deepEqual(activity({ acquisition: acquisition("stale"), recentlyLive: true }), {
    text: "Telemetry went quiet",
    tone: "amber",
    wave: { kind: "flat", tone: "amber" },
  });
  assert.deepEqual(activity({ acquisition: acquisition("disabled") }), {
    text: "Idle — no live session",
    tone: "grey",
    wave: { kind: "flat", tone: "grey" },
  });
  assert.deepEqual(activity({ acquisition: null, runtime: null }), {
    text: "Unavailable",
    tone: "grey",
    wave: { kind: "none", tone: "grey" },
  });
});

test("current activity priorities", () => {
  assert.equal(activity({ speaking: "x", answering: true }).text, "Speaking: x", "speaking wins over answering");
  assert.equal(activity({ acquisition: null, runtime: null, answering: true }).text, "Unavailable");
  assert.equal(activity({ runtime: { ...runtimeReady, status: "not_configured" } }).tone, "amber", "live telemetry with no ready engineer");
  assert.equal(activity({ acquisition: null }).text, "Telemetry unavailable");
});

/* ---------------------------------------------------------- session context */

test("session context subtitle says Live or Last session", () => {
  assert.deepEqual(session.contextSubtitle({ typeLabel: "Practice 1", current: true }), ["Practice 1", "Live"]);
  assert.deepEqual(session.contextSubtitle({ typeLabel: "Race", current: false }), ["Race", "Last session"]);
});

test("session context model carries the id for the Review link", () => {
  const model = session.sessionContextModel(
    {
      session: { id: "abc-123", lifecycle: "ended", acquisition: "stale", context: { track_name: "Jeddah", session_type: "practice_1" } },
      current: false,
    },
    (name) => name.toLowerCase(),
  );
  assert.equal(model.id, "abc-123");
  assert.equal(model.typeLabel, "Practice 1");
  assert.deepEqual(model.subtitle, ["Practice 1", "Ended"], "the sidebar footer keeps the lifecycle wording");
});

test("connect checklist ticks off from live receiver state", async () => {
  const steps = await import(await loadUrl("features/drive-diagnostics/connect-steps.ts"));
  const idle = steps.checklistSteps({ reachable: true, acquisitionState: "stale", receiver: { host: "192.168.1.115", port: 20777 } });
  assert.deepEqual(idle.map((step) => step.done), [true, true, false, false]);
  assert.match(idle[2].hint, /192\.168\.1\.115, port 20777/);
  const anyHost = steps.checklistSteps({ reachable: true, acquisitionState: "listening", receiver: { host: "0.0.0.0", port: null } });
  assert.match(anyHost[2].hint, /this computer's IP address, port 20777/);
  const live = steps.checklistSteps({ reachable: true, acquisitionState: "receiving", receiver: { host: null, port: 20777 } });
  assert.ok(live.every((step) => step.done));
  const off = steps.checklistSteps({ reachable: false, acquisitionState: null, receiver: { host: null, port: null } });
  assert.deepEqual(off.map((step) => step.done), [false, false, false, false]);
});
