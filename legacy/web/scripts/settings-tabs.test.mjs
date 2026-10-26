import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

// Voice & Controls, Connections, Data & Storage and Advanced Tools: the pure
// parts (meter, donut shares, capture and track model rows, tile sources,
// connection-test copy, browser voice record) plus structural checks on the
// panels that cannot run without a browser.

const require = createRequire(import.meta.url);
const typescript = require("typescript");
const featuresRoot = new URL("../src/features/", import.meta.url);
const libRoot = new URL("../src/lib/", import.meta.url);
const settingsRoot = new URL("settings/", featuresRoot);

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

const storageUsageUrl = new URL("storage-usage.mjs", libRoot).href;
const parseUtilsUrl = await loadUrl(featuresRoot, "review-data/parse-utils.ts");
const reasonsUrl = await loadUrl(featuresRoot, "review-data/reasons.ts");
const speechDraftUrl = await loadUrl(libRoot, "local-speech-draft.ts");

const storageView = await import(await loadUrl(settingsRoot, "storage-view.ts", { "@/lib/storage-usage.mjs": storageUsageUrl }));
const trackModels = await import(await loadUrl(settingsRoot, "track-models.ts"));
const captures = await import(await loadUrl(settingsRoot, "captures.ts", { "@/lib/storage-usage.mjs": storageUsageUrl }));
const connections = await import(
  await loadUrl(settingsRoot, "connections.ts", {
    "@/lib/local-speech-draft": speechDraftUrl,
    "@/features/review-data/reasons": reasonsUrl,
  })
);
const audio = await import(await loadUrl(settingsRoot, "audio.ts"));
const devices = await import(await loadUrl(settingsRoot, "devices.ts"));
const browserVoice = await import(await loadUrl(settingsRoot, "browser-voice.ts"));
const regions = await import(await loadUrl(featuresRoot, "review-data/regions.ts", { "./parse-utils": parseUtilsUrl }));
const { parseStorageUsageResponse } = await import(storageUsageUrl);

const MIB = 1024 * 1024;
const GIB = 1024 * MIB;

/* ---------------------------------------------------------------- storage */

function storagePayload(overrides = {}) {
  const available = (bytes, files) => ({
    status: "available",
    logical_bytes: bytes,
    regular_file_count: files,
    excluded_entry_count: 0,
    reason: null,
  });
  return {
    api_version: "v1",
    status: "ok",
    data: {
      measurement_started_at_utc: "2026-10-12T18:41:00Z",
      measurement_completed_at_utc: "2026-10-12T18:42:00Z",
      measurement_note: "Sizes are logical bytes of regular files.",
      scopes: {
        database: available(1 * GIB, 1),
        finalized_captures: available(3 * GIB, 6),
        recorder_staging: available(0, 0),
        imported_traces: available(1 * GIB, 24),
        ...overrides.scopes,
      },
      volumes: {
        database_location: { status: "available", total_bytes: 512 * GIB, free_bytes: 128 * GIB, reason: null },
        recordings_location: { status: "available", total_bytes: 512 * GIB, free_bytes: 128 * GIB, reason: null },
        ...overrides.volumes,
      },
    },
  };
}

const buildView = (overrides) => storageView.buildStorageView(parseStorageUsageResponse(storagePayload(overrides)));

test("donut shares add up and follow the measured bytes", () => {
  const view = buildView();
  assert.equal(view.used, "5 GiB used");
  assert.equal(view.available, "128 GiB available");
  assert.equal(view.capacity, "512 GiB volume");
  assert.equal(view.volumeShare, 0.75);
  assert.deepEqual(view.scopes.map((scope) => [scope.key, scope.share]), [
    ["database", 0.2],
    ["finalized_captures", 0.6],
    ["recorder_staging", 0],
    ["imported_traces", 0.2],
  ]);
  const segments = storageView.donutSegments(view.scopes);
  // The empty staging scope has no arc; the others cover the whole ring.
  assert.deepEqual(segments.map((segment) => segment.key), ["database", "finalized_captures", "imported_traces"]);
  const total = segments.reduce((sum, segment) => sum + segment.share, 0);
  assert.ok(Math.abs(total - 1) < 1e-9);
  assert.equal(segments[0].start, 0);
  assert.ok(Math.abs(segments[1].start - 0.2 * storageView.DONUT_CIRCUMFERENCE) < 1e-9);
  for (const segment of segments) {
    assert.ok(segment.length > 0 && segment.length < segment.share * storageView.DONUT_CIRCUMFERENCE, "arcs leave a small gap");
  }
  assert.deepEqual(segments.map((segment) => segment.color), ["var(--blue)", "var(--purple)", "var(--green)"]);
  assert.match(storageView.donutLabel(view), /^Storage by location\. 5 GiB used\. Session database 1 GiB, Recorded captures 3 GiB/);
});

test("an unmeasured scope is listed, hatched and never drawn", () => {
  const view = buildView({
    scopes: {
      imported_traces: {
        status: "unavailable",
        logical_bytes: null,
        regular_file_count: null,
        excluded_entry_count: null,
        reason: "trace_namespace_missing",
      },
    },
  });
  assert.equal(view.complete, false);
  assert.equal(view.used, "4 GiB+ used");
  const traces = view.scopes.find((scope) => scope.key === "imported_traces");
  assert.equal(traces.unavailable, true);
  assert.equal(traces.share, null);
  assert.equal(traces.value, "Trace folder unavailable");
  assert.equal(storageView.donutSegments(view.scopes).some((segment) => segment.key === "imported_traces"), false);
  assert.match(storageView.donutLabel(view), /Imported traces not measured/);
});

test("a single measured scope fills the ring without a gap and an empty store draws nothing", () => {
  const single = buildView({
    scopes: {
      database: { status: "available", logical_bytes: 0, regular_file_count: 0, excluded_entry_count: 0, reason: null },
      finalized_captures: { status: "available", logical_bytes: 2 * GIB, regular_file_count: 2, excluded_entry_count: 0, reason: null },
      imported_traces: { status: "available", logical_bytes: 0, regular_file_count: 0, excluded_entry_count: 0, reason: null },
    },
  });
  const [only, ...rest] = storageView.donutSegments(single.scopes);
  assert.equal(rest.length, 0);
  assert.ok(Math.abs(only.length - storageView.DONUT_CIRCUMFERENCE) < 1e-9);
  const empty = buildView({
    scopes: {
      database: { status: "available", logical_bytes: 0, regular_file_count: 0, excluded_entry_count: 0, reason: null },
      finalized_captures: { status: "available", logical_bytes: 0, regular_file_count: 0, excluded_entry_count: 0, reason: null },
      imported_traces: { status: "available", logical_bytes: 0, regular_file_count: 0, excluded_entry_count: 0, reason: null },
    },
  });
  assert.deepEqual(storageView.donutSegments(empty.scopes), []);
  assert.equal(empty.used, "0 B used");
});

test("an unknown volume leaves capacity and share empty", () => {
  const view = buildView({
    volumes: { recordings_location: { status: "unavailable", total_bytes: null, free_bytes: null, reason: "volume_unavailable" } },
  });
  assert.equal(view.volumeShare, null);
  assert.equal(view.capacity, null);
  assert.equal(view.available, "Free space unknown");
});

/* ----------------------------------------------------------- track models */

const modelRecord = (overrides) => ({
  model_id: "monaco-2024",
  revision: 1,
  track_id: 5,
  track_name: "Monaco",
  layout_id: "full",
  track_length_m: 3337,
  validation_status: "validated",
  origin: "packaged",
  region_count: 19,
  ...overrides,
});

const modelList = (records) => regions.parseTrackModelList({ api_version: "v1", status: "ok", data: records, reason: null });

test("track model rows map status, origin and length", () => {
  const rows = trackModels.trackModelRows(
    modelList([
      modelRecord({ model_id: "spa-2024", track_name: "Spa-Francorchamps", validation_status: "draft", origin: "local_draft", track_length_m: 7004.4, region_count: 6 }),
      modelRecord({}),
      modelRecord({ revision: 2, origin: "reviewed" }),
      modelRecord({ model_id: "mystery", track_name: "Mystery", validation_status: "something_else", origin: null, track_length_m: null }),
    ]),
  );
  assert.deepEqual(rows.map((row) => row.key), ["monaco-2024@2", "monaco-2024@1", "mystery@1", "spa-2024@1"]);
  const [latest, packaged, mystery, spa] = rows;
  assert.deepEqual([latest.statusLabel, latest.originLabel, latest.reviewed], ["Reviewed", "Reviewed bundle", true]);
  assert.deepEqual([packaged.statusLabel, packaged.originLabel, packaged.lengthLabel, packaged.regionCount], ["Reviewed", "Packaged", "3,337 m", 19]);
  // Anything not positively validated is a draft; a missing origin or length is stated, not guessed.
  assert.deepEqual([mystery.statusLabel, mystery.originLabel, mystery.lengthLabel], ["Draft", "Unknown", null]);
  assert.deepEqual([spa.statusLabel, spa.originLabel, spa.lengthLabel], ["Draft", "Local draft", "7,004 m"]);
  assert.match(trackModels.DRAFT_FOOTNOTE, /not reviewed corner definitions/);
});

test("track model views and the status tile cover every read outcome", () => {
  const ok = trackModels.describeTrackModels({
    state: "ok",
    value: modelList([modelRecord({}), modelRecord({ model_id: "spa-2024", validation_status: "draft", origin: "local_draft" })]),
  });
  assert.equal(ok.state, "ok");
  assert.deepEqual([ok.reviewed, ok.draft], [1, 1]);
  assert.deepEqual(trackModels.trackModelsTile(ok), { name: "2 models", badge: "1 reviewed · 1 draft" });
  const one = trackModels.describeTrackModels({ state: "ok", value: modelList([modelRecord({})]) });
  assert.deepEqual(trackModels.trackModelsTile(one), { name: "1 model", badge: "1 reviewed · 0 draft" });
  const none = trackModels.describeTrackModels({ state: "ok", value: [] });
  assert.deepEqual(trackModels.trackModelsTile(none), { name: "No models", badge: "None installed" });
  for (const read of [{ state: "unreachable" }, { state: "timeout" }, { state: "unavailable", reason: "x", status: 500 }, { state: "busy", reason: "busy" }]) {
    const view = trackModels.describeTrackModels(read);
    assert.notEqual(view.state, "ok", read.state);
    assert.deepEqual(trackModels.trackModelsTile(view), { name: "Unavailable", badge: "Not measured" });
  }
});

test("region rows show label, range, apex and direction", () => {
  const model = regions.parseRegionsResponse({
    api_version: "v1",
    status: "ok",
    data: {
      model_id: "spa-2024",
      revision: 3,
      track_name: "Spa-Francorchamps",
      validation_status: "draft",
      origin: "local_draft",
      region_count: 2,
      regions: [
        { id: "r1", label: "La Source", start_distance_m: 120.4, end_distance_m: 260, nominal_apex_m: 190, direction: "right" },
        { id: "r2", label: "Eau Rouge", start_distance_m: 1200, end_distance_m: 1500 },
      ],
    },
  });
  assert.equal(trackModels.regionsHeading(model), "Spa-Francorchamps · revision 3 · Draft");
  assert.deepEqual(trackModels.regionRows(model.regions), [
    { id: "r1", label: "La Source", range: "120 m to 260 m", apex: "190 m", direction: "Right" },
    { id: "r2", label: "Eau Rouge", range: "1,200 m to 1,500 m", apex: null, direction: null },
  ]);
});

/* --------------------------------------------------------------- captures */

const source = (overrides) => ({
  capture_id: "a".repeat(32),
  display_name: "monaco-0604.f1ecap",
  byte_size: 482 * MIB,
  modified_at_utc: "2026-10-11T10:00:00Z",
  latest_job_id: null,
  latest_job_status: null,
  latest_job_run_id: null,
  available: true,
  download_version: "b".repeat(64),
  ...overrides,
});

test("capture rows carry size, import badge and only the actions that apply", () => {
  const imported = captures.captureRow(source({ latest_job_status: "complete" }));
  assert.deepEqual([imported.size, imported.badge, imported.canImport], ["482 MiB", { label: "Complete", tone: "green" }, false]);
  assert.equal(imported.downloadHref, `/api/recording-sources/${"a".repeat(32)}/download?version=${"b".repeat(64)}`);

  const fresh = captures.captureRow(source({}));
  assert.deepEqual([fresh.badge.label, fresh.canImport], ["Not imported", true]);

  const missing = captures.captureRow(source({ available: false }));
  assert.deepEqual([missing.badge, missing.canImport, missing.downloadHref], [{ label: "Missing", tone: "red" }, false, null]);

  const labels = Object.fromEntries(
    ["queued", "running", "failed", "interrupted", "cancelled", "mystery"].map((status) => [
      status,
      captures.importBadge({ latest_job_status: status, available: true }).label,
    ]),
  );
  assert.deepEqual(labels, {
    queued: "Queued",
    running: "Importing",
    failed: "Failed",
    interrupted: "Interrupted",
    cancelled: "Cancelled",
    mystery: "Unknown",
  });
});

test("captures page into a growing list without duplicates", () => {
  const page = (items, total, offset) => ({ items, selected_capture: null, total_count: total, limit: 10, offset, has_more: offset + items.length < total, filters: {} });
  const first = captures.capturesModel(page([source({ capture_id: "1".repeat(32) }), source({ capture_id: "2".repeat(32) })], 3, 0));
  assert.equal(captures.capturesSummary(first), "2 of 3 captures");
  assert.equal(first.hasMore, true);
  const second = captures.capturesModel(page([source({ capture_id: "2".repeat(32) }), source({ capture_id: "3".repeat(32) })], 3, 2), first.rows);
  assert.deepEqual(second.rows.map((row) => row.captureId[0]), ["1", "2", "3"]);
  assert.equal(captures.capturesSummary(second), "3 captures");
  assert.equal(second.hasMore, false);
  assert.equal(captures.capturesSummary(captures.capturesModel(page([], 0, 0))), "No captures");
});

test("no data panel offers to delete, rename or clean up anything", async () => {
  const data = await readFile(new URL("DataPanel.tsx", settingsRoot), "utf8");
  assert.match(data, /Keep everything\. Nothing is deleted automatically\./);
  assert.match(data, /Recordings stay in the recordings folder until you\s+remove them\./);
  assert.doesNotMatch(data, /method:\s*"DELETE"|>\s*Delete\b|>\s*Clean up|>\s*Rename|>\s*Remove/i);
  for (const file of ["AdvancedPanel.tsx", "VoicePanel.tsx", "ConnectionsPanel.tsx"]) {
    const source = await readFile(new URL(file, settingsRoot), "utf8");
    assert.doesNotMatch(source, /recording-groups|\/api\/replays|method:\s*"DELETE"/, file);
  }
});

/* ------------------------------------------------------ connections */

const baseTest = { baseline: 100, latest: 1304, state: "receiving", host: "192.168.1.115", port: 20777 };

test("connection test reports packets seen, silence and unreadable receivers", () => {
  assert.deepEqual(connections.connectionTestOutcome(baseTest), {
    tone: "success",
    text: "Receiving data: 1,204 packets in 10 s.",
    packets: 1204,
  });
  assert.equal(connections.connectionTestOutcome({ ...baseTest, latest: 101 }).text, "Receiving data: 1 packet in 10 s.");

  const silent = connections.connectionTestOutcome({ ...baseTest, latest: 100, state: "listening" });
  assert.equal(silent.tone, "warning");
  assert.equal(
    silent.text,
    "No packets in 10 s. In F1, set UDP Telemetry On, IP 192.168.1.115, port 20777, send rate 20 Hz or higher.",
  );
  // A wildcard bind address cannot be typed into the game, so the hint says what to enter.
  assert.match(
    connections.connectionTestOutcome({ ...baseTest, latest: 100, host: "0.0.0.0" }).text,
    /IP this computer's IP address, port 20777/,
  );
  assert.match(connections.connectionTestOutcome({ ...baseTest, latest: 100, port: null }).text, /port 20777/);

  // The counter restarts with the receiver: the new value is what arrived since.
  assert.equal(connections.connectionTestOutcome({ ...baseTest, baseline: 5000, latest: 30 }).packets, 30);

  const unreadable = connections.connectionTestOutcome({ ...baseTest, latest: null, state: null });
  assert.equal(unreadable.tone, "error");
  assert.match(unreadable.text, /could not read the receiver/);
  const disabled = connections.connectionTestOutcome({ ...baseTest, state: "disabled" });
  assert.equal(disabled.tone, "warning");
  assert.match(disabled.text, /disabled/);
  assert.equal(connections.CONNECTION_TEST_SECONDS, 10);
});

const sha = (character) => character.repeat(64);
const speechBody = (data) => ({ api_version: "v1", status: "ok", data, reason: null });

test("speech recognition card covers ready, not set up, unreachable and unrecognised", () => {
  const ready = connections.describeSpeech(
    speechBody({
      status: "ready",
      reason: null,
      runtime: "whisper.cpp",
      runtime_version: "1.9.3",
      model_name: "tiny.en",
      model_sha256: sha("a"),
      runtime_id: sha("b"),
      language: "en",
      placement: "CPU",
      maximum_clip_seconds: 12,
      audio_persisted: false,
    }),
  );
  assert.equal(ready.state, "ready");
  assert.equal(ready.runtimeId, sha("b"));
  const row = (view, label) => view.rows.find((item) => item.label === label)?.value;
  assert.equal(row(ready, "Runtime"), "whisper.cpp 1.9.3");
  assert.equal(row(ready, "Model"), "tiny.en");
  assert.equal(row(ready, "Placement"), "Local · CPU");
  assert.equal(row(ready, "Audio"), "Audio is not stored");

  const notSetUp = connections.describeSpeech(speechBody({ status: "unavailable", reason: "runtime_unavailable" }));
  assert.equal(notSetUp.state, "unavailable");
  assert.equal(notSetUp.runtimeId, null);
  assert.equal(row(notSetUp, "Reason"), "The speech runtime is not installed.");
  assert.equal(notSetUp.statusLabel, "Not set up");

  for (const body of [null, undefined, { api_version: "v1", status: "unavailable", data: null, reason: "engineer_speech_status_unavailable" }]) {
    const view = connections.describeSpeech(body);
    assert.equal(view.state, "unreachable");
    assert.equal(view.tone, "red");
  }
  assert.equal(connections.describeSpeech({ hello: "world" }).state, "invalid");
  // An engine that claims audio is kept is not trusted.
  assert.equal(connections.describeSpeech(speechBody({ status: "ready", runtime: "whisper.cpp", audio_persisted: true })).state, "invalid");
  assert.equal(connections.speechReasonText(null), "Speech recognition is not set up on the engine.");
});

/* ----------------------------------------------------------- voice and mic */

test("level meter maps RMS to 20 segments on a decibel scale", () => {
  assert.equal(audio.METER_SEGMENTS, 20);
  assert.equal(audio.litSegments(0), 0);
  assert.equal(audio.litSegments(1), 20);
  assert.equal(audio.litSegments(0.001), 0, "-60 dB is the floor");
  assert.equal(audio.litSegments(0.1), 13, "-20 dB is two thirds up the scale");
  assert.equal(audio.litSegments(Number.NaN), 0);
  assert.equal(audio.litSegments(5), 20);
  assert.ok(audio.litSegments(0.05) < audio.litSegments(0.2));
  assert.deepEqual([audio.segmentTone(0), audio.segmentTone(14), audio.segmentTone(18)], ["green", "amber", "red"]);
  assert.ok(Math.abs(audio.rmsOf(new Float32Array([0.5, -0.5, 0.5, -0.5])) - 0.5) < 1e-9);
  assert.equal(audio.rmsOf([]), 0);
  assert.equal(audio.peakOf([0.1, -0.7, 0.3]), 0.7);
});

test("recordings are resampled to 16 kHz, trimmed and silence is detected", () => {
  const second = new Float32Array(48_000).fill(0.25);
  const down = audio.resampleMono(second, 48_000);
  assert.equal(down.length, 16_000);
  assert.ok(down.every((value) => Math.abs(value - 0.25) < 1e-6));
  assert.equal(audio.resampleMono(new Float32Array(16_000), 16_000).length, 16_000);
  const up = audio.resampleMono(new Float32Array([0, 1]), 8_000);
  assert.equal(up.length, 4);
  assert.equal(up[0], 0);
  assert.equal(audio.resampleMono(new Float32Array(0), 48_000).length, 0);

  const joined = audio.joinBlocks([new Float32Array([1, 2, 3]), new Float32Array([4, 5, 6])], 4);
  assert.deepEqual([...joined], [1, 2, 3, 4]);
  assert.equal(audio.joinBlocks([], 10).length, 0);

  assert.equal(audio.isSilent(new Float32Array(1000)), true);
  assert.equal(audio.isSilent(new Float32Array([0, 0.2, 0])), false);
  assert.equal(audio.TEST_SECONDS, 5);
});

test("test microphone copy: what we heard, timing and failures", () => {
  assert.equal(audio.heardText("  box box this lap "), "We heard: “box box this lap”");
  assert.equal(audio.formatSeconds(800), "0.8 s");
  assert.equal(audio.formatSeconds(-5), "0.0 s");
  assert.equal(audio.transcribeFailureText(503, "engineer_speech_unavailable"), "Speech recognition is not set up on the engine.");
  assert.equal(audio.transcribeFailureText(null, null), "The local engine is not reachable.");
  assert.match(audio.transcribeFailureText(403, null), /control token/);
  assert.match(audio.transcribeFailureText(504, null), /took too long/);
  assert.match(audio.transcribeFailureText(413, null), /too large/);
  assert.match(audio.transcribeFailureText(409, null), /busy/);
  assert.match(audio.transcribeFailureText(500, null), /could not process/);
});

test("voice rate, volume and voice choice", () => {
  assert.deepEqual([audio.RATE_MIN, audio.RATE_MAX, audio.RATE_STEP], [0.8, 1.4, 0.1]);
  assert.equal(audio.snapRate(1.04), 1);
  assert.equal(audio.snapRate(1.26), 1.3);
  assert.equal(audio.snapRate(0.5), 0.8);
  assert.equal(audio.snapRate(2), 1.4);
  assert.equal(audio.snapRate(Number.NaN), 1);
  assert.equal(audio.rateLabel(1), "1.0×");
  assert.equal(audio.volumeLabel(0.9), "90%");
  assert.equal(audio.volumeLabel(2), "100%");
  assert.match(audio.SAMPLE_PHRASE, /^Lap 28, one thirteen four two zero, plus zero point five seven eight to your best\.$/);

  const voices = [
    { voiceURI: "fr", name: "Amelie", lang: "fr-FR", localService: true },
    { voiceURI: "cloud", name: "Cloud", lang: "en-US", localService: false },
    { voiceURI: "b", name: "Zira", lang: "en-US", localService: true },
    { voiceURI: "a", name: "Aria", lang: "en-GB", localService: true },
  ];
  const options = audio.localVoiceOptions(voices);
  assert.deepEqual(options.map((option) => option.voiceURI), ["a", "b", "fr"], "local only, English first, then by name");
  assert.equal(audio.pickVoice(options, "b").name, "Zira");
  assert.equal(audio.pickVoice(options, "gone").voiceURI, "a", "a removed voice falls back to the first");
  assert.equal(audio.pickVoice([], null), null);
});

test("browser-only voice record: defaults, round trip and the Drive read-aloud flag", () => {
  assert.deepEqual(browserVoice.DEFAULT_BROWSER_VOICE, { voiceURI: null, readAloud: false });
  for (const raw of [null, "", "{", "[]", "7", "x".repeat(2000)]) {
    assert.deepEqual(browserVoice.parseBrowserVoice(raw), { voiceURI: null, readAloud: false }, String(raw).slice(0, 10));
  }
  const raw = browserVoice.serializeBrowserVoice({ voiceURI: "Microsoft Aria", readAloud: true });
  assert.deepEqual(JSON.parse(raw), { schema_version: 1, voiceURI: "Microsoft Aria" });
  assert.deepEqual(browserVoice.parseBrowserVoice(raw, "1"), { voiceURI: "Microsoft Aria", readAloud: true });
  assert.deepEqual(browserVoice.parseBrowserVoice(raw, "0"), { voiceURI: "Microsoft Aria", readAloud: false });
  assert.deepEqual(browserVoice.parseBrowserVoice(null, "1"), { voiceURI: null, readAloud: true });
  assert.equal(browserVoice.READ_ALOUD_KEY, "f1-engineer:drive-read-aloud");
  assert.equal(browserVoice.parseBrowserVoice(JSON.stringify({ voiceURI: 7 })).voiceURI, null);
});

/* -------------------------------------------------------------- tiles */

const AUDIO = (overrides) => ({ ...devices.INITIAL_BROWSER_AUDIO, checked: true, ...overrides });
const mics = [
  { deviceId: "default", label: "Default - Shure MV7" },
  { deviceId: "usb", label: "" },
];

test("microphone and audio tiles come from the browser, never the reference devices", () => {
  assert.deepEqual(devices.microphoneTile(devices.INITIAL_BROWSER_AUDIO), { name: "Checking…", state: "Checking", tone: "grey-text" });
  assert.deepEqual(devices.microphoneTile(AUDIO({ permission: "granted", devices: mics })), {
    name: "Default - Shure MV7",
    state: "Ready",
    tone: "green-text",
  });
  assert.equal(devices.microphoneTile(AUDIO({ permission: "granted", devices: mics }), "usb").name, "Microphone 2");
  assert.equal(devices.microphoneTile(AUDIO({ permission: "granted", devices: mics }), "gone").name, "Default - Shure MV7");
  assert.deepEqual(devices.microphoneTile(AUDIO({ permission: "prompt" })), { name: "No microphone selected", state: "Not set up", tone: "amber-text" });
  assert.deepEqual(devices.microphoneTile(AUDIO({ permission: "unknown" })).state, "Not set up");
  assert.deepEqual(devices.microphoneTile(AUDIO({ permission: "denied" })), { name: "Access blocked", state: "Blocked", tone: "red-text" });
  assert.deepEqual(devices.microphoneTile(AUDIO({ permission: "granted", devices: [] })), { name: "No microphone found", state: "Not set up", tone: "amber-text" });
  assert.equal(devices.microphoneTile(AUDIO({ micSupported: false })).state, "Unavailable");

  const voice = { voiceURI: "a", name: "Microsoft Aria", lang: "en-GB" };
  assert.deepEqual(devices.audioOutputTile(AUDIO({}), voice), { name: "Microsoft Aria", state: "Ready", tone: "green-text" });
  assert.deepEqual(devices.audioOutputTile(AUDIO({}), null), { name: "No local voice", state: "Not set up", tone: "amber-text" });
  assert.equal(devices.audioOutputTile(AUDIO({ speechSupported: false }), voice).state, "Unavailable");
  assert.equal(devices.audioOutputTile(devices.INITIAL_BROWSER_AUDIO, voice).state, "Checking");
  for (const tile of [devices.microphoneTile(AUDIO({ permission: "denied" })), devices.audioOutputTile(AUDIO({}), null)]) {
    assert.doesNotMatch(JSON.stringify(tile), /Shure|MacBook/);
  }
});

test("the Microphone card stage and permission errors", () => {
  const stages = [
    [devices.INITIAL_BROWSER_AUDIO, "checking"],
    [AUDIO({ micSupported: false }), "unsupported"],
    [AUDIO({ permission: "denied" }), "blocked"],
    [AUDIO({ permission: "prompt" }), "needs-access"],
    [AUDIO({ permission: "unknown" }), "needs-access"],
    [AUDIO({ permission: "granted", devices: [] }), "no-device"],
    [AUDIO({ permission: "granted", devices: mics }), "ready"],
  ];
  for (const [state, stage] of stages) assert.equal(devices.microphoneStage(state), stage);
  assert.deepEqual(devices.permissionAfterError("NotAllowedError"), { permission: "denied", noDevice: false });
  assert.deepEqual(devices.permissionAfterError("NotFoundError"), { permission: "granted", noDevice: true });
  assert.deepEqual(devices.permissionAfterError("AbortError"), { permission: "prompt", noDevice: false });
});

/* ------------------------------------------------------- panel structure */

test("the status card reports radio devices without track models and the panels have ids", async () => {
  const tiles = await readFile(new URL("StatusTiles.tsx", settingsRoot), "utf8");
  for (const label of ["Radio microphone", "Engineer voice", "Game Connection", "Storage"]) {
    assert.ok(tiles.includes(`>${label}</div>`), label);
  }
  assert.doesNotMatch(tiles, /model\.status\.(microphone|audio|trackModels)/, "no reference device values");
  assert.doesNotMatch(tiles, /Track Models|Audio Output/);
  const root = await readFile(new URL("Settings.tsx", settingsRoot), "utf8");
  assert.doesNotMatch(root, /useBrowserAudio/);
  const voice = await readFile(new URL("VoicePanel.tsx", settingsRoot), "utf8");
  assert.doesNotMatch(voice, /getUserMedia|openMic|LevelMeter/);
  assert.match(root, /role="tabpanel"/);
  assert.match(root, /aria-labelledby=\{`\$\{TAB_ID_PREFIX\}-\$\{key\}`\}/);
  assert.match(root, /controls=\{panelId\}/);
  assert.match(root, /window\.history\.replaceState/);
  assert.match(root, /beforeunload/);
  const page = await readFile(new URL("../../app/settings/page.tsx", settingsRoot), "utf8");
  assert.match(page, /parseSection\(query\.section\)/);
});

test("new settings CSS uses tokens, not raw hex colours", async () => {
  const css = await readFile(new URL("SettingsTabs.module.css", settingsRoot), "utf8");
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/);
});

test("icons on the new panels are SVG components", async () => {
  for (const file of ["VoicePanel.tsx", "DataPanel.tsx", "ConnectionsPanel.tsx", "AdvancedPanel.tsx", "StatusTiles.tsx"]) {
    const source = await readFile(new URL(file, settingsRoot), "utf8");
    assert.doesNotMatch(source, /<img\b|[▶■⬇↻●✓]/u, file);
  }
});
