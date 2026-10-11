import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const typescript = require("typescript");
const sourceRoot = new URL("../src/features/settings/", import.meta.url);

async function loadModule(name) {
  const source = await readFile(new URL(name, sourceRoot), "utf8");
  const output = typescript.transpileModule(source, {
    fileName: name,
    compilerOptions: {
      module: typescript.ModuleKind.ESNext,
      target: typescript.ScriptTarget.ES2022,
    },
  }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(output).toString("base64")}`);
}

const display = await loadModule("display.ts");
const preferences = await loadModule("preferences.ts");

// Shapes observed from the live backend (read-only GETs).
const runtimeEnvelope = {
  api_version: "v1",
  status: "ok",
  data: {
    runtime: "Codex",
    model_name: "gpt-6-luna",
    billing: "codex_subscription",
    fallback_enabled: false,
    cloud_routing: "subscription",
    requested_placement: "cloud",
    endpoint: "codex:stdio",
    runtime_version: null,
    model_digest: null,
    model_quantization: null,
    model_parameter_size: null,
    last_inference_placement: null,
    last_inference_at_utc: null,
    status: "ready",
    reason: null,
  },
  reason: null,
};

const acquisition = {
  state: "stale",
  error: null,
  host: "192.168.1.115",
  port: 20777,
  processed: 156,
  queue_limit: 1024,
  journaled: 156,
  pending_publication: 0,
  queue_depth: 0,
  last_publication_delay_s: 0.022529982,
  received: 156,
  receiver_mode: "isolated_process",
  dropped: 0,
  kernel_dropped: 0,
  socket_errors: 0,
  detail_processing: { profile: "demand_v1" },
};

const row = (view, label) => view.rows.find((item) => item.label === label);

test("engineer runtime maps to model, placement, status and last inference", () => {
  const info = display.parseRuntimeInfo(runtimeEnvelope);
  assert.equal(info.modelName, "gpt-6-luna");
  const view = display.describeRuntime(info);
  assert.equal(view.available, true);
  assert.equal(view.tone, "green");
  assert.equal(view.statusLabel, "Ready");
  assert.equal(view.summary, "Codex · gpt-6-luna");
  assert.equal(row(view, "Runtime").value, "Codex");
  assert.equal(row(view, "Model").value, "gpt-6-luna");
  assert.equal(row(view, "Placement").value, "Cloud (Codex subscription)");
  assert.deepEqual(row(view, "Status"), { label: "Status", value: "Ready", tone: "green" });
  assert.equal(row(view, "Last inference").value, "None recorded");
  assert.equal(row(view, "Reason"), undefined);
});

test("engineer runtime shows a timestamp, placement and reason when reported", () => {
  const info = display.parseRuntimeInfo({
    ...runtimeEnvelope,
    status: "unavailable",
    data: {
      ...runtimeEnvelope.data,
      status: "not_configured",
      reason: "codex_login_required",
      last_inference_placement: "cloud",
      last_inference_at_utc: "2026-10-11T14:53:35.838783Z",
    },
  });
  const view = display.describeRuntime(info);
  assert.equal(view.tone, "amber");
  assert.equal(view.statusLabel, "Not configured");
  assert.equal(row(view, "Reason").value, "Codex login required");
  assert.equal(row(view, "Last inference").value, "2026-10-11 14:53 UTC (cloud)");
});

test("engineer runtime describes a local runtime without claiming verified routing", () => {
  const info = display.parseRuntimeInfo({
    api_version: "v1",
    status: "ok",
    data: {
      runtime: "Ollama",
      model_name: "qwen3:4b",
      cloud_routing: "unverified",
      requested_placement: "CPU",
      endpoint: "127.0.0.1:11435",
      status: "model_unavailable",
      reason: null,
      last_inference_placement: null,
      last_inference_at_utc: null,
    },
  });
  const view = display.describeRuntime(info);
  assert.equal(row(view, "Placement").value, "CPU (cloud routing not verified)");
  assert.equal(view.statusLabel, "Model unavailable");
  assert.equal(view.tone, "amber");
});

test("an unreachable or malformed runtime is an explicit unavailable state", () => {
  for (const value of [null, undefined, "x", {}, { api_version: "v2", data: {} }, { api_version: "v1", data: null }]) {
    assert.equal(display.parseRuntimeInfo(value), null);
  }
  const view = display.describeRuntime(null);
  assert.equal(view.available, false);
  assert.equal(view.tone, "red");
  assert.equal(view.statusLabel, "Unavailable");
  assert.equal(row(view, "Model").value, "Unavailable");
  assert.doesNotMatch(JSON.stringify(view), /Ready|gpt-6/);
});

test("telemetry receiver maps state, address, mode, counters and delay", () => {
  const info = display.parseReceiverInfo(acquisition);
  assert.equal(info.host, "192.168.1.115");
  assert.equal(info.port, 20777);
  const view = display.describeReceiver(info);
  assert.equal(view.available, true);
  assert.equal(view.stateLabel, "Stale");
  assert.equal(view.tone, "amber");
  assert.equal(row(view, "Receiver").value, "Stale");
  assert.equal(row(view, "Address").value, "192.168.1.115:20777");
  assert.equal(row(view, "Receiver mode").value, "Isolated process");
  assert.equal(row(view, "Packets received").value, "156");
  assert.equal(row(view, "Packets dropped").value, "0 (kernel 0, socket errors 0)");
  assert.equal(row(view, "Publication delay").value, "23 ms");
});

test("receiving state is green and the wildcard bind address is explained", () => {
  const info = display.parseReceiverInfo({ ...acquisition, state: "receiving", host: "0.0.0.0", received: 1234567 });
  const view = display.describeReceiver(info);
  assert.equal(view.tone, "green");
  assert.equal(row(view, "Address").value, "0.0.0.0:20777 (all interfaces)");
  assert.equal(row(view, "Packets received").value, "1,234,567");
});

test("a disabled receiver reports no counters instead of zeros", () => {
  const info = display.parseReceiverInfo({ state: "disabled", host: null, port: null });
  const view = display.describeReceiver(info);
  assert.equal(view.tone, "grey");
  assert.equal(row(view, "Packets received").value, "Not reported");
  assert.equal(row(view, "Packets dropped").value, "Not reported");
  assert.equal(row(view, "Publication delay").value, "Not reported");
  assert.equal(row(view, "Address").value, "Not reported");
});

test("an unreachable receiver is an explicit unavailable state", () => {
  assert.equal(display.parseReceiverInfo(null), null);
  assert.equal(display.parseReceiverInfo({ error: "x" }), null);
  const view = display.describeReceiver(null);
  assert.equal(view.available, false);
  assert.equal(view.tone, "red");
  assert.equal(row(view, "Receiver").value, "Local engine unavailable");
  assert.equal(row(view, "Packets received").value, "Unavailable");
});

test("counts and delays are validated and formatted", () => {
  const info = display.parseReceiverInfo({ ...acquisition, received: -1, port: 99999, last_publication_delay_s: -1, kernel_dropped: null });
  assert.equal(info.received, null);
  assert.equal(info.port, null);
  assert.equal(info.publicationDelayS, null);
  assert.equal(display.formatDelay(0.0002), "< 1 ms");
  assert.equal(display.formatDelay(0), "0 ms");
  assert.equal(display.formatDelay(null), "Not reported");
  assert.equal(display.formatUtc("not a date"), null);
  assert.equal(display.formatUtc(null), null);
  const view = display.describeReceiver(info);
  assert.match(row(view, "Packets dropped").value, /kernel n\/a/);
});

test("game connection tile only claims Connected while packets are arriving", () => {
  const reference = { name: "F1 24 (PC)", state: "Connected (120 ms)" };
  const receiving = display.describeGameTile(
    display.parseReceiverInfo({ ...acquisition, state: "receiving" }),
    reference,
  );
  assert.deepEqual(receiving, { name: "F1 24 (PC)", state: "Connected (23 ms)", tone: "green-text" });
  const stale = display.describeGameTile(display.parseReceiverInfo(acquisition), reference);
  assert.equal(stale.state, "Not connected");
  assert.equal(stale.tone, "amber-text");
  const disabled = display.describeGameTile(display.parseReceiverInfo({ state: "disabled" }), reference);
  assert.equal(disabled.state, "Disabled");
  const offline = display.describeGameTile(null, reference);
  assert.equal(offline.state, "Engine unavailable");
  assert.equal(offline.tone, "grey-text");
});

const source = {
  topics: [
    ["Tyre management", true],
    ["Setup suggestions", false],
  ],
  focusAreas: [["Corner analysis", true]],
  verbosity: ["Concise", "Balanced", "Detailed"],
  interruption: ["Allow interruptions", "Queue until finished", "Never interrupt"],
  depth: ["Quick", "Standard", "In-depth"],
};

test("preferences default to the reference values and restore stored changes", () => {
  const defaults = preferences.defaultPreferences(source);
  assert.equal(defaults.verbosity, "Balanced");
  assert.equal(defaults.topics["Setup suggestions"], false);
  assert.equal("retention" in defaults, false);
  assert.ok(preferences.preferencesEqual(defaults, preferences.restorePreferences(source, null)));

  const stored = { ...defaults, verbosity: "Detailed", topics: { ...defaults.topics, "Setup suggestions": true } };
  const restored = preferences.restorePreferences(source, JSON.stringify(stored));
  assert.equal(restored.verbosity, "Detailed");
  assert.equal(restored.topics["Setup suggestions"], true);
  assert.ok(preferences.preferencesEqual(restored, stored));
  assert.equal(preferences.preferencesEqual(restored, defaults), false);
});

test("preferences ignore retired keys, unknown options and unreadable storage", () => {
  const defaults = preferences.defaultPreferences(source);
  const restored = preferences.restorePreferences(
    source,
    JSON.stringify({ retention: "Keep everything", verbosity: "Telegraphic", voice: "Calm", topics: { Nope: true, "Tyre management": "yes" } }),
  );
  assert.equal(restored.verbosity, "Balanced");
  assert.equal(restored.voice, "Calm");
  assert.equal("retention" in restored, false);
  assert.equal("Nope" in restored.topics, false);
  assert.equal(restored.topics["Tyre management"], true);
  for (const raw of ["{", "[]", "null", "42", ""]) {
    assert.ok(preferences.preferencesEqual(preferences.restorePreferences(source, raw), defaults), raw);
  }
});

test("preference comparison ignores key order", () => {
  const defaults = preferences.defaultPreferences(source);
  const reordered = JSON.parse(JSON.stringify(Object.fromEntries(Object.entries(defaults).reverse())));
  assert.ok(preferences.preferencesEqual(defaults, reordered));
});

test("advanced tool pointers match the real command line", async () => {
  const cli = await readFile(new URL("../../f1_engineer/cli.py", import.meta.url), "utf8");
  const screen = await readFile(new URL("Settings.tsx", sourceRoot), "utf8");
  for (const subcommand of ["inspect", "replay-udp", "api"]) {
    assert.match(cli, new RegExp(String.raw`commands\.add_parser\(\s*"${subcommand}"`), subcommand);
  }
  for (const flag of ["--host", "--dry-run", "--confirm-idle", "--manual-acquisition", "--udp-host", "--udp-port"]) {
    assert.ok(cli.includes(`"${flag}"`), flag);
    assert.ok(screen.includes(flag), `${flag} is shown on the Advanced Tools tab`);
  }
  // replay-udp requires --host, so the example must include it.
  assert.match(screen, /replay-udp[^<]*&lt;capture\.f1ecap&gt; --host/);
});
