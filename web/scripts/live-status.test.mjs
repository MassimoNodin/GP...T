import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const typescript = require("typescript");
const sourceRoot = new URL("../src/", import.meta.url);

// These modules only have type-only imports, so a plain transpile is enough.
async function load(relativePath) {
  const source = await readFile(new URL(relativePath, sourceRoot), "utf8");
  const output = typescript.transpileModule(source, {
    fileName: relativePath,
    compilerOptions: {
      module: typescript.ModuleKind.ESNext,
      target: typescript.ScriptTarget.ES2022,
    },
  }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(output).toString("base64")}`);
}

const map = await load("features/live-status/map.ts");
const poller = await load("features/live-status/poller.ts");
const session = await load("features/drive-diagnostics/session.ts");

function acquisition(state, extra = {}) {
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
    last_publication_delay_s: 0.0225,
    ...extra,
  };
}

function runtime(status, reason = null) {
  return {
    status,
    reason,
    runtime: "Codex",
    model_name: "gpt-6-luna",
    last_inference_at_utc: null,
  };
}

const reference = [
  { label: "Engineer Runtime", status: "Running", tone: "green", detail: "Since 14:27" },
  { label: "Telemetry (Game Data)", status: "Receiving", tone: "green", detail: "120 ms" },
  { label: "Microphone Input", status: "Available", tone: "green", detail: "Shure MV7" },
  { label: "Question Processing", status: "Ready", tone: "green", detail: "< 1 s" },
];

test("acquisition state maps to header pill and footer", () => {
  assert.deepEqual(map.telemetryStatus(acquisition("receiving")), { tone: "green", label: "Telemetry live" });
  assert.deepEqual(map.telemetryStatus(acquisition("listening")), { tone: "grey", label: "Waiting for game" });
  assert.deepEqual(map.telemetryStatus(acquisition("starting")), { tone: "grey", label: "Waiting for game" });
  assert.deepEqual(map.telemetryStatus(acquisition("stale")), { tone: "grey", label: "Waiting for game" });
  assert.deepEqual(map.telemetryStatus(acquisition("disabled")), { tone: "grey", label: "Telemetry disabled" });
  assert.deepEqual(map.telemetryStatus(acquisition("stopped")), { tone: "red", label: "Telemetry offline" });
  assert.deepEqual(map.telemetryStatus(acquisition("failed")), { tone: "red", label: "Telemetry offline" });
  assert.deepEqual(map.telemetryStatus(null), { tone: "red", label: "Backend unavailable" });

  assert.deepEqual(map.telemetryFooter(acquisition("receiving"), "F1 25"), {
    tone: "green",
    label: "Telemetry live",
    lines: ["F1 25", "Ready"],
  });
  assert.deepEqual(map.telemetryFooter(acquisition("listening"), "F1 25").lines, ["F1 25", "Listening on UDP 20777"]);
  assert.deepEqual(map.telemetryFooter(acquisition("stale"), "F1 25").lines, ["F1 25", "Listening on UDP 20777"]);
  assert.deepEqual(map.telemetryFooter(null, "F1 25").lines, ["F1 25", "Not reachable"]);
  assert.equal(map.telemetryShell(acquisition("stale"), "x").status.label, "Waiting for game");
});

test("engineer runtime maps to header pill and footer", () => {
  assert.deepEqual(map.engineerStatus(runtime("ready")), { tone: "green", label: "Engineer running" });
  assert.deepEqual(map.engineerStatus(runtime("not_configured")), { tone: "amber", label: "Engineer not configured" });
  assert.deepEqual(map.engineerStatus(runtime("unavailable", "codex_missing")), { tone: "red", label: "Engineer unavailable" });
  assert.deepEqual(map.engineerStatus(null), { tone: "red", label: "Engineer offline" });
  assert.deepEqual(map.engineerFooter(runtime("ready"), ["Jeddah", "Practice 1  •  Ended"]), {
    tone: "green",
    label: "Engineer running",
    lines: ["Jeddah", "Practice 1  •  Ended"],
  });
});

test("runtime envelope is read leniently", () => {
  const envelope = {
    api_version: "v1",
    status: "ok",
    reason: null,
    data: {
      runtime: "Codex",
      model_name: "gpt-6-luna",
      status: "ready",
      reason: null,
      // Populated after the first answer; must not make the runtime look unavailable.
      last_inference_at_utc: "2026-10-12T10:00:00Z",
    },
  };
  assert.deepEqual(map.parseRuntimeInfo(envelope), {
    status: "ready",
    reason: null,
    runtime: "Codex",
    model_name: "gpt-6-luna",
    last_inference_at_utc: "2026-10-12T10:00:00Z",
  });
  assert.equal(map.parseRuntimeInfo({ api_version: "v1", status: "unavailable", data: null, reason: "x" }), null);
  assert.equal(map.parseRuntimeInfo({ api_version: "v2", data: { status: "ready" } }), null);
  assert.equal(map.parseRuntimeInfo({ api_version: "v1", data: { status: 3 } }), null);
  assert.equal(map.parseRuntimeInfo("nope"), null);
});

test("system rows take live telemetry, runtime and question state", () => {
  const rows = map.buildSystemRows(reference, acquisition("receiving"), runtime("ready"));
  assert.deepEqual(rows[0], { label: "Engineer Runtime", status: "Running", tone: "green", detail: "gpt-6-luna" });
  assert.deepEqual(rows[1], { label: "Telemetry (Game Data)", status: "Receiving", tone: "green", detail: "23 ms" });
  assert.deepEqual(rows[2], reference[2], "rows without a backend source keep the reference value");
  assert.deepEqual(rows[3], { label: "Question Processing", status: "Ready", tone: "green", detail: "Codex" });

  const degraded = map.buildSystemRows(reference, acquisition("stale"), runtime("not_configured", "codex_login_required"));
  assert.equal(degraded[0].status, "Not configured");
  assert.equal(degraded[0].tone, "amber");
  assert.equal(degraded[0].detail, "Codex login required");
  assert.equal(degraded[1].status, "Waiting");
  assert.equal(map.telemetryRow(acquisition("stale"), true).status, "Stale");
  assert.equal(degraded[3].status, "Unavailable");
  assert.equal(degraded[3].tone, "red");

  const broken = map.buildSystemRows(reference, acquisition("failed"), runtime("unavailable"));
  assert.equal(broken[0].status, "Unavailable");
  assert.equal(broken[1].status, "Failed");
  assert.equal(broken[1].tone, "red");
});

test("one failed endpoint marks only its own rows unavailable", () => {
  const rows = map.buildSystemRows(reference, null, runtime("ready"));
  assert.equal(rows[0].status, "Running");
  assert.equal(rows[1].status, "Unavailable");
  assert.equal(rows[1].tone, "grey");
  assert.equal(rows[2].status, "Available");
});

test("unreachable backend makes every row unavailable", () => {
  const rows = map.buildSystemRows(reference, null, null);
  assert.equal(rows.length, reference.length);
  for (const row of rows) {
    assert.equal(row.status, "Unavailable");
    assert.equal(row.tone, "grey");
    assert.equal(row.detail, "Service unreachable");
  }
  assert.deepEqual(map.currentActivity("Listening...", null, null), { text: "Unavailable", tone: "grey" });
  assert.deepEqual(map.currentActivity("Listening...", acquisition("stale"), null), { text: "Listening...", tone: "green" });
});

test("live data pill is live only while packets are arriving", () => {
  assert.deepEqual(map.liveDataPill(acquisition("receiving")), { live: true, label: "Live game data" });
  for (const state of ["listening", "stale", "stopped", "failed", "disabled", "starting"]) {
    assert.deepEqual(map.liveDataPill(acquisition(state)), { live: false, label: "No live data" });
  }
  assert.deepEqual(map.liveDataPill(null), { live: false, label: "No live data" });
});

test("failure filter tolerates a single dropped poll", () => {
  const seen = [];
  const push = poller.createFailureFilter((value) => seen.push(value));
  push("a");
  push(null);
  assert.deepEqual(seen, ["a"]);
  push(null);
  assert.deepEqual(seen, ["a", null]);
  push("b");
  push(null);
  assert.deepEqual(seen, ["a", null, "b"]);
});

test("endpoint poller stays idle while hidden and polls when shown", async () => {
  const results = [];
  let calls = 0;
  const instance = poller.createEndpointPoller({
    url: "/api/engineer/runtime",
    parse: (body) => body,
    onResult: (value) => results.push(value),
    intervalMs: 5,
    fetchJson: async () => {
      calls += 1;
      return { ok: true, json: async () => ({ n: calls }) };
    },
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(calls, 0, "no request before the tab is visible");
  instance.setHidden(false);
  await new Promise((resolve) => setTimeout(resolve, 40));
  instance.setHidden(true);
  const afterHide = calls;
  assert.ok(afterHide >= 2, "polls repeatedly while visible");
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(calls, afterHide, "no requests while hidden");
  instance.stop();
  assert.ok(results.length >= 1 && results.every((value) => value && typeof value.n === "number"));
});

test("endpoint poller reports non-ok responses as unavailable", async () => {
  const results = [];
  const instance = poller.createEndpointPoller({
    url: "/api/engineer/runtime",
    parse: (body) => body,
    onResult: (value) => results.push(value),
    intervalMs: 1000,
    fetchJson: async () => ({ ok: false, json: async () => ({}) }),
  });
  instance.setHidden(false);
  await new Promise((resolve) => setTimeout(resolve, 20));
  instance.stop();
  assert.deepEqual(results, [null]);
});

const trackKey = (name) =>
  (name ?? "").toLowerCase().replace(/[^a-z0-9 ]/g, "").split(/\s+/)[0] || "generic";

function evidence(id, lifecycle, acquisitionState, context) {
  return { id, lifecycle, acquisition: acquisitionState, context: JSON.stringify(context) };
}

const jeddah = {
  air_temperature_c: 26,
  packet_format: 2025,
  session_type: "practice_1",
  total_laps: 1,
  track_length_m: 6175,
  track_name: "Jeddah",
  track_temperature_c: 36,
  weather_name: "clear",
  progress: { duration_s: 1800, total_laps: 1 },
};
const sakhir = {
  air_temperature_c: 31,
  packet_format: 2025,
  session_type: "race",
  total_laps: 14,
  track_name: "Sakhir (Bahrain)",
  weather_name: "light_cloud",
};

test("current session prefers active, then the receiver's session, then the last listed", () => {
  const ended = evidence("a", "ended", "receiving", jeddah);
  const interrupted = evidence("b", "interrupted", "stale", sakhir);
  const active = evidence("c", "active", "receiving", sakhir);

  assert.deepEqual(session.pickCurrentSession([ended, interrupted, active], "a"), { session: active, current: true });
  assert.deepEqual(session.pickCurrentSession([ended, interrupted], "b"), { session: interrupted, current: false });
  assert.deepEqual(session.pickCurrentSession([ended, interrupted], "zzz"), { session: interrupted, current: false });
  assert.deepEqual(session.pickCurrentSession([ended], null), { session: ended, current: false });
  assert.equal(session.pickCurrentSession([], null), null);
});

test("session context uses the real session values", () => {
  const model = session.sessionContextModel(
    { session: evidence("a", "ended", "receiving", jeddah), current: false },
    trackKey,
  );
  assert.equal(model.title, "Jeddah");
  assert.equal(model.track, "jeddah");
  assert.deepEqual(model.subtitle, ["Practice 1", "Ended"]);
  assert.deepEqual(model.headerItems, ["Jeddah", "Practice 1"]);
  assert.deepEqual(model.facts, [
    ["Game", "F1 25"],
    ["Conditions", "Clear, 26°C"],
    ["Track temp", "36°C"],
    ["Duration", "30 min"],
    ["Length", "6.175 km"],
  ]);

  const race = session.sessionContextModel(
    { session: evidence("b", "active", "receiving", sakhir), current: true },
    trackKey,
  );
  assert.equal(race.track, "sakhir");
  assert.deepEqual(race.subtitle, ["Race", "Live"]);
  assert.deepEqual(race.facts, [
    ["Game", "F1 25"],
    ["Conditions", "Light Cloud, 31°C"],
    ["Laps", "14"],
  ]);
});

test("session context is rejected without a track name", () => {
  assert.equal(
    session.sessionContextModel({ session: evidence("a", "ended", "receiving", { session_type: "race" }), current: false }, trackKey),
    null,
  );
  assert.equal(
    session.sessionContextModel({ session: { id: "a", lifecycle: "ended", acquisition: "", context: "not json" }, current: false }, trackKey),
    null,
  );
});

test("one combined header pill across screens", () => {
  const live = map.combinedStatus(acquisition("receiving"), runtime("ready"));
  assert.equal(live.label, "Telemetry live · Engineer ready");
  assert.equal(live.tone, "green");
  assert.equal(live.details.length, 2);
  // "stale" with no recent live data is just a closed game.
  const idle = map.combinedStatus(acquisition("stale"), runtime("ready"));
  assert.equal(idle.label, "Waiting for game · Engineer ready");
  assert.equal(idle.tone, "grey");
  const quiet = map.combinedStatus(acquisition("stale"), runtime("ready"), true);
  assert.equal(quiet.label, "Telemetry stale · Engineer ready");
  assert.equal(quiet.tone, "amber");
  assert.equal(map.telemetryDetail(acquisition("stale")), "Listening on UDP 20777");
  assert.equal(map.telemetryDetail(acquisition("stale"), true), "Packets stopped arriving");
  const broken = map.combinedStatus(acquisition("receiving"), null);
  assert.equal(broken.label, "Telemetry live · Engineer offline");
  assert.equal(broken.tone, "red");
  const offline = map.combinedStatus(null, null);
  assert.equal(offline.label, "Engine offline");
  assert.deepEqual(map.sessionFooter(null, null, ["x"]), map.ENGINE_OFFLINE_FOOTER);
  assert.deepEqual(map.sessionFooter(acquisition("receiving"), null, ["Jeddah"]), {
    tone: "green",
    label: "Telemetry live",
    lines: ["Jeddah"],
  });
});
