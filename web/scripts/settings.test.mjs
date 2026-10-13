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

const featuresRoot = new URL("../src/features/", import.meta.url);

/** Transpiles a source file and points the listed import specifiers at already-built modules. */
async function loadUrl(root, name, replacements = {}) {
  let code = typescript.transpileModule(await readFile(new URL(name, root), "utf8"), {
    fileName: name,
    compilerOptions: { module: typescript.ModuleKind.ESNext, target: typescript.ScriptTarget.ES2022 },
  }).outputText;
  for (const [specifier, url] of Object.entries(replacements)) {
    code = code.replaceAll(`from "${specifier}"`, `from ${JSON.stringify(url)}`);
  }
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}

const display = await loadModule("display.ts");
const preferences = await loadModule("preferences.ts");
const parseUtilsUrl = await loadUrl(featuresRoot, "review-data/parse-utils.ts");
const docUrl = await loadUrl(featuresRoot, "review-data/preferences.ts", { "./parse-utils": parseUtilsUrl });
const doc = await import(docUrl);
const legacyUrl = await loadUrl(sourceRoot, "preferences.ts");
const engine = await import(
  await loadUrl(sourceRoot, "engine-preferences.ts", {
    "@/features/review-data/preferences": docUrl,
    "./preferences": legacyUrl,
  })
);
const sections = await loadModule("sections.ts");
const fixtures = await import(await loadUrl(featuresRoot, "review-data/fixtures.ts"));

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
  assert.equal("activation" in defaults, false);
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
    JSON.stringify({ retention: "Keep everything", verbosity: "Telegraphic", voice: "Calm", activation: "Push to talk", topics: { Nope: true, "Tyre management": "yes" } }),
  );
  assert.equal(restored.verbosity, "Balanced");
  for (const retired of ["voice", "activation", "speechRate", "retention"]) {
    assert.equal(retired in restored, false, retired);
  }
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
  const screen = await readFile(new URL("AdvancedPanel.tsx", sourceRoot), "utf8");
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

/* ------------------------------------------------- engine-backed Assistance */

const fixtureSource = {
  topics: fixtures.settingsFixture.topics,
  focusAreas: fixtures.settingsFixture.focusAreas,
  verbosity: fixtures.settingsFixture.verbosity,
  interruption: fixtures.settingsFixture.interruption,
  depth: fixtures.settingsFixture.depth,
};

const stored = (overrides = {}, revision = 4) => ({
  doc: { ...doc.DEFAULT_PREFERENCES, ...overrides },
  revision,
  schemaVersion: 1,
});
const readOk = (overrides, revision) => ({ state: "ok", stored: stored(overrides, revision) });

test("every reference label maps to its engine key and back", () => {
  const labels = (record) => Object.values(record);
  assert.deepEqual(fixtureSource.topics.map(([label]) => label), labels(doc.TOPIC_LABELS));
  assert.deepEqual(fixtureSource.focusAreas.map(([label]) => label), labels(doc.FOCUS_LABELS));
  assert.deepEqual(fixtureSource.verbosity, labels(doc.VERBOSITY_LABELS));
  assert.deepEqual(fixtureSource.interruption, labels(doc.INTERRUPTION_LABELS));
  assert.deepEqual(fixtureSource.depth, labels(doc.DEPTH_LABELS));
  assert.deepEqual(Object.keys(doc.TOPIC_LABELS), [
    "tyre_management", "track_conditions", "race_strategy", "driving_technique",
    "gap_information", "rule_reminders", "setup_suggestions", "general_information",
  ]);
  assert.deepEqual(Object.keys(doc.FOCUS_LABELS), [
    "driving_consistency", "comparative_analysis", "corner_analysis", "setup_impact",
    "tyre_performance", "data_quality_notes",
  ]);
  // The reference defaults are the engine defaults.
  assert.ok(preferences.preferencesEqual(preferences.defaultPreferences(fixtureSource), doc.docToForm(doc.DEFAULT_PREFERENCES)));
  const changed = { ...doc.DEFAULT_PREFERENCES, depth: "in_depth", interruption: "never", verbosity: "detailed" };
  assert.deepEqual(doc.formToDoc(doc.docToForm(changed)), changed);
  // Only the six document keys are ever sent.
  assert.deepEqual(Object.keys(JSON.parse(doc.preferencesRequestBody(changed))), [
    "topics", "verbosity", "interruption", "depth", "interpretations", "focus_areas",
  ]);
});

test("dirty detection follows the saved form, not key order", () => {
  const state = engine.initPrefsState(readOk());
  assert.equal(engine.isDirty(state), false);
  const edited = engine.prefsReducer(state, {
    type: "edit",
    form: { ...state.form, topics: { ...state.form.topics, "Setup suggestions": true } },
  });
  assert.equal(engine.isDirty(edited), true);
  const reverted = engine.prefsReducer(edited, { type: "edit", form: state.saved });
  assert.equal(engine.isDirty(reverted), false);
  assert.equal(engine.isDirty(engine.prefsReducer(edited, { type: "discard" })), false);
});

test("Apply row copy for the clean, dirty, saving and saved states", () => {
  let state = engine.initPrefsState(readOk());
  let view = engine.assistanceView(state);
  assert.deepEqual(
    [view.applyLabel, view.applyDisabled, view.showDiscard, view.note, view.alert],
    ["Apply Changes", true, false, "Most settings apply immediately. Some connection settings require a restart.", null],
  );

  state = engine.prefsReducer(state, { type: "edit", form: { ...state.form, verbosity: "Detailed" } });
  view = engine.assistanceView(state);
  assert.deepEqual([view.applyLabel, view.applyDisabled, view.showDiscard, view.note], ["Apply Changes", false, true, "You have unsaved changes."]);

  state = engine.prefsReducer(state, { type: "saving" });
  view = engine.assistanceView(state);
  assert.deepEqual([view.applyLabel, view.applyDisabled, view.busy, view.note], ["Applying…", true, true, "Saving to the engine…"]);

  state = engine.prefsReducer(state, { type: "result", result: { ok: true, stored: stored({ verbosity: "detailed" }, 5) } });
  view = engine.assistanceView(state);
  assert.deepEqual(
    [view.applyLabel, view.applyDisabled, view.note, state.revision],
    ["Changes Applied", true, "Saved to the engine. The next engineer call uses these settings.", 5],
  );
  state = engine.prefsReducer(state, { type: "settle" });
  view = engine.assistanceView(state);
  assert.deepEqual([view.applyLabel, view.note], ["Apply Changes", "Saved to the engine. The next engineer call uses these settings."]);
  // The next edit returns to the unsaved note.
  state = engine.prefsReducer(state, { type: "edit", form: { ...state.form, depth: "Quick" } });
  assert.equal(engine.assistanceView(state).note, "You have unsaved changes.");
});

test("a 409 conflict offers Load latest and Overwrite and keeps the edits", () => {
  let state = engine.initPrefsState(readOk({}, 3));
  state = engine.prefsReducer(state, { type: "edit", form: { ...state.form, depth: "Quick" } });
  state = engine.prefsReducer(state, { type: "saving" });
  const result = doc.parsePreferencesSaveResult(409, { api_version: "v1", status: "unavailable", data: { revision: 7 }, reason: "preferences_revision_conflict" }, '"7"');
  state = engine.prefsReducer(state, { type: "result", result });
  const view = engine.assistanceView(state);
  assert.equal(view.alert.tone, "warning");
  assert.equal(view.alert.title, "Settings changed elsewhere");
  assert.equal(view.alert.text, "Another window saved revision 7 while you were editing.");
  assert.deepEqual(view.alert.actions, ["load-latest", "overwrite"]);
  assert.equal(view.applyDisabled, false);
  assert.equal(state.form.depth, "Quick");
  assert.equal(engine.saveRevision(state, state.failure.currentRevision), 7);
  assert.equal(engine.saveRevision(state, null), 3);
  // Editing does not clear a conflict; adopting the latest document does.
  state = engine.prefsReducer(state, { type: "edit", form: { ...state.form, depth: "Standard" } });
  assert.equal(state.failure.kind, "conflict");
  state = engine.prefsReducer(state, { type: "adopt", stored: stored({ depth: "in_depth" }, 7) });
  assert.equal(state.failure, null);
  assert.equal(state.revision, 7);
  assert.equal(state.form.depth, "In-depth");
  assert.equal(engine.assistanceView(state).alert, null);
});

test("422, 403, 503 and unreachable saves have distinct, honest states", () => {
  const base = engine.prefsReducer(engine.initPrefsState(readOk()), {
    type: "edit",
    form: { ...doc.docToForm(doc.DEFAULT_PREFERENCES), depth: "Quick" },
  });
  const fail = (status, body) =>
    engine.assistanceView(engine.prefsReducer(base, { type: "result", result: doc.parsePreferencesSaveResult(status, body) }));

  const invalid = fail(422, { reason: "preferences_invalid_value", data: { field: "depth" } });
  assert.equal(invalid.note, "The engine rejected these settings (depth). Reload the page and try again.");
  assert.equal(invalid.noteTone, "red");
  assert.equal(invalid.applyDisabled, false);

  const forbidden = fail(403, { reason: "preferences_control_not_authorized" });
  assert.equal(forbidden.note, "The web app is not allowed to change engine settings. Check the engine's control token.");
  assert.equal(forbidden.applyDisabled, true);
  assert.equal(forbidden.noteTone, "red");

  const unavailable = fail(503, { reason: "preferences_store_unavailable" });
  assert.match(unavailable.note, /The engine could not save these settings \(preferences store unavailable\)\. Try again\./);
  assert.equal(unavailable.applyDisabled, false);

  const unreachable = fail(503, { reason: "local_engine_unavailable" });
  assert.equal(unreachable.note, "The local engine is not reachable. Your changes are kept; try again.");
  assert.equal(unreachable.applyDisabled, false);

  // Editing clears a rejection, but a forbidden save stays forbidden.
  const rejected = engine.prefsReducer(base, {
    type: "result",
    result: doc.parsePreferencesSaveResult(422, { reason: "x", data: { field: "depth" } }),
  });
  assert.equal(engine.prefsReducer(rejected, { type: "edit", form: base.saved }).failure, null);
  const denied = engine.prefsReducer(base, { type: "result", result: doc.parsePreferencesSaveResult(403, { reason: "x" }) });
  assert.equal(engine.prefsReducer(denied, { type: "edit", form: base.saved }).failure.kind, "forbidden");
});

test("an unreadable saved file shows defaults, allows replacing it and saves unconditionally", () => {
  const read = doc.parsePreferencesRead(503, { api_version: "v1", status: "unavailable", data: null, reason: "preferences_file_invalid" });
  let state = engine.initPrefsState(read);
  assert.equal(state.access, "unreadable");
  assert.equal(state.revision, null);
  const view = engine.assistanceView(state);
  assert.equal(view.alert.tone, "error");
  assert.equal(
    view.alert.text,
    "Saved settings could not be read (preferences file invalid). Defaults are shown; applying replaces the saved file.",
  );
  assert.equal(view.applyDisabled, false, "defaults can be applied over a damaged file");
  assert.equal(view.controlsDisabled, false);
  assert.equal(engine.saveRevision(state, null), null);
  assert.ok(preferences.preferencesEqual(state.form, doc.docToForm(doc.DEFAULT_PREFERENCES)));
  // A successful save repairs the state.
  state = engine.prefsReducer(state, { type: "result", result: { ok: true, stored: stored({}, 1) } });
  assert.equal(state.access, "ready");
  assert.equal(engine.assistanceView(state).alert, null);
});

test("an unreachable engine disables every control and says why", () => {
  const state = engine.initPrefsState({ state: "unreachable" });
  const view = engine.assistanceView(state);
  assert.equal(view.controlsDisabled, true);
  assert.equal(view.applyDisabled, true);
  assert.deepEqual(view.alert, {
    tone: "info",
    text: "Settings cannot be loaded because the local engine is not reachable.",
    actions: [],
  });
  assert.equal(view.showDiscard, false);
});

test("the browser-local copy is offered once, only when it differs from the engine copy", () => {
  const saved = doc.docToForm(doc.DEFAULT_PREFERENCES);
  const legacy = preferences.parseLegacyPreferences(fixtureSource, JSON.stringify({ verbosity: "Detailed", voice: "Calm" }));
  assert.equal(legacy.verbosity, "Detailed");
  const plan = engine.planMigration(legacy, saved, "ready");
  assert.equal(plan.state, "offer");
  assert.equal(plan.form.verbosity, "Detailed");
  const sameAsEngine = preferences.parseLegacyPreferences(fixtureSource, JSON.stringify({ voice: "Calm" }));
  assert.equal(engine.planMigration(sameAsEngine, saved, "ready").state, "identical");
  assert.equal(engine.planMigration(null, saved, "ready").state, "none");
  assert.equal(engine.planMigration(legacy, saved, "unreachable").state, "none");
  assert.equal(preferences.parseLegacyPreferences(fixtureSource, "{"), null);
  assert.equal(preferences.parseLegacyPreferences(fixtureSource, null), null);
});

test("?section= selects a tab and falls back to Assistance", () => {
  assert.deepEqual(sections.SECTIONS.map((item) => item.key), ["assistance", "voice", "connections", "data", "advanced"]);
  for (const key of ["assistance", "voice", "connections", "data", "advanced"]) assert.equal(sections.parseSection(key), key);
  for (const bad of [undefined, null, "", "Voice", "other", ["voice", "data"]]) assert.equal(sections.parseSection(bad), "assistance");
  assert.equal(sections.sectionHref("assistance"), "/settings");
  assert.equal(sections.sectionHref("connections"), "/settings?section=connections");
  assert.equal(sections.searchWithSection("?a=1&section=voice", "data"), "?a=1&section=data");
  assert.equal(sections.searchWithSection("?section=voice", "assistance"), "");
});

test("the Assistance tab keeps the reference labels and the Apply copy", async () => {
  const screen = await readFile(new URL("AssistancePanel.tsx", sourceRoot), "utf8");
  for (const label of ["Live Assistance", "Review Assistance", "Assistance Topics", "Verbosity", "Interruption Behaviour", "Analysis Depth", "Include Interpretations", "Focus Areas"]) {
    assert.ok(screen.includes(label), label);
  }
  const copy = await readFile(new URL("engine-preferences.ts", sourceRoot), "utf8");
  assert.ok(copy.includes("Most settings apply immediately. Some connection settings require a restart."));
  // Voice, activation and read-aloud are browser-only and are not part of the engine document.
  assert.equal(JSON.stringify(Object.keys(doc.DEFAULT_PREFERENCES)).includes("voice"), false);
  assert.equal(screen.includes("Question Activation"), false);
});
