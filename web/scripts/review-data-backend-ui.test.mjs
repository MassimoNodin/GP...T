import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

// Parsers and bounded readers added for the new UI areas. Payload fixtures
// below copy the shapes produced by the backend modules named in each parser
// (lap_enrichment.py, session_summary.py, track_region_routes.py,
// engineer_activity/store.py, engineer_preferences.py).

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

function toDataUrl(code) {
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}

/** Transpiles a source file and rewrites its module specifiers to data URLs. */
async function loadUrl(path, replacements = {}) {
  let code = transpile(await readFile(new URL(path, webRoot), "utf8"), path);
  for (const [specifier, url] of Object.entries(replacements)) {
    code = code.replaceAll(`from "${specifier}"`, `from ${JSON.stringify(url)}`);
  }
  return toDataUrl(code);
}

const dir = "features/review-data/";
const utilsUrl = await loadUrl(`${dir}parse-utils.ts`);
const withUtils = { "./parse-utils": utilsUrl };
const enrichment = await import(await loadUrl(`${dir}enrichment.ts`, withUtils));
const summary = await import(await loadUrl(`${dir}summary.ts`, withUtils));
const regions = await import(await loadUrl(`${dir}regions.ts`, withUtils));
const engineerEvents = await import(await loadUrl(`${dir}engineer-events.ts`, withUtils));
const preferences = await import(await loadUrl(`${dir}preferences.ts`, withUtils));
const reasons = await import(await loadUrl(`${dir}reasons.ts`));

/* ------------------------------------------------------------ fixtures */

function lapRow(overrides = {}) {
  return {
    revision_id: "rev-lap-1",
    attempt_number: 1,
    lap_number: 1,
    lap_time_ms: 92_104,
    disposition: "completed",
    readiness: "available",
    superseded: false,
    start_observed: true,
    valid: true,
    game_valid: true,
    pit: false,
    sectors_ms: [28_100, 36_004, 28_000],
    sectors_source: "session_history",
    sectors_reason: null,
    tyre: {
      visual_compound: 16,
      actual_compound: 18,
      visual_label: "soft",
      actual_label: "C3",
      age_laps: 2,
      source: "car_status",
    },
    tyre_reason: null,
    ...overrides,
  };
}

const lapsBody = {
  data: {
    session_id: "session-a",
    laps: [
      lapRow(),
      lapRow({
        revision_id: "rev-lap-2",
        attempt_number: 1,
        lap_number: 2,
        lap_time_ms: 95_500,
        game_valid: false,
        valid: false,
        sectors_ms: null,
        sectors_source: null,
        sectors_reason: "session_history_not_in_journal",
        tyre: null,
        tyre_reason: "car_status_not_in_journal",
      }),
      lapRow({
        revision_id: "rev-lap-3",
        lap_number: 3,
        lap_time_ms: null,
        disposition: "abandoned",
        game_valid: null,
        valid: true,
        pit: true,
        start_observed: null,
        sectors_ms: null,
        sectors_source: null,
        sectors_reason: "lap_not_completed",
        tyre: {
          visual_compound: 17,
          actual_compound: 19,
          visual_label: null,
          actual_label: null,
          age_laps: null,
          source: "session_history_stints",
        },
        superseded: true,
      }),
      { lap_number: 9 }, // no revision id: dropped
    ],
    stints: [
      {
        stint_index: 0,
        visual_compound: 16,
        actual_compound: 18,
        visual_label: "soft",
        actual_label: "C3",
        start_lap: 1,
        end_lap: 12,
        lap_count: 12,
        start_age_laps: 1,
        end_age_laps: 12,
      },
      { stint_index: 1, start_lap: 13, end_lap: 4 }, // end before start: dropped
    ],
    truncated: true,
    source: { version: "lap-enrichment-v1", truncation: "time_budget_exceeded" },
  },
};

const summaryBody = {
  data: {
    version: "session-summary-v1",
    session: {
      id: "session-a",
      lifecycle: "ended",
      track_name: "Monaco",
      session_type: "race",
      total_laps: 78,
    },
    started_at_utc: "2026-10-04T15:00:00.120Z",
    ended_at_utc: "2026-10-04T16:42:18.000Z",
    last_observed_at_utc: "2026-10-04T16:42:18.000Z",
    time_source: "journal_captured_at_ns",
    time_reason: null,
    session_time: { start_s: 0, end_s: 6138.4, duration_s: 6138.4 },
    scheduled_duration_s: 7200,
    player: { car_index: 3, last_lap_number: 78, pit_stop_count: 2 },
    weather: [
      { session_time_s: 0, weather_id: 0, weather: "clear", track_temperature_c: 38, air_temperature_c: 24 },
      { session_time_s: 2470, weather_id: 1, weather: "light_cloud", track_temperature_c: 35, air_temperature_c: 23 },
    ],
    events: [
      { kind: "pit_entry", label: "Pit entry", session_time_s: 1200.5, lap: 12, car_index: 3, is_player: true, detail: { pit_stop_count: 1 }, code: null },
      { kind: "overtake", label: "{car} overtook {other}", session_time_s: 1500, lap: 15, car_index: 7, is_player: false, detail: { overtaken_car_index: 4 }, code: "OVTK", driver_name: null },
      { kind: "safety_car", label: "Safety car deployed", session_time_s: null, lap: null, car_index: null, is_player: false, detail: "not-an-object", code: "SCAR" },
      { label: "no kind" },
    ],
    totals: {
      by_kind: { overtake: 1, pit_entry: 1, safety_car: 1 },
      observed: 9,
      listed: 3,
      omitted_minor: 4,
      omitted_budget: 2,
      weather_points_omitted: 0,
      truncated: true,
      ignored_codes: {},
    },
    scan: { complete: false, stop_reason: "row_budget", committed_sequence: 10, rows_scanned: 5 },
  },
};

const regionsPayload = (overrides = {}) => ({
  model_id: "monaco-2024",
  revision: 1,
  packet_format: 2024,
  track_id: 5,
  track_name: "Monaco",
  layout_id: "grand_prix",
  track_length_m: 3337,
  validation_status: "validated",
  provenance: "unit test",
  region_count: 2,
  origin: "packaged",
  diagnostic_only: true,
  coaching_eligible: false,
  ranking_eligible: false,
  regions: [
    { id: "t1", label: "T1", start_distance_m: 200, end_distance_m: 320, nominal_apex_m: 260, direction: "right", complex_id: null },
    { id: "t6", label: "T6", start_distance_m: 2340, end_distance_m: 2980, nominal_apex_m: null, direction: "left", complex_id: "t6-t8" },
    { id: "bad", label: "no distances" },
  ],
  ...overrides,
});

const eventRow = (id, overrides = {}) => ({
  id,
  created_at_utc: new Date(Date.UTC(2026, 9, 4, 15, 0, id)).toISOString(),
  kind: "proactive",
  topic: "tyres",
  text: `Call ${id}`,
  session_id: "session-a",
  lap: 4,
  source: "live_rules",
  related_id: null,
  priority: "normal",
  detail: { reason: "wear" },
  ...overrides,
});

const storedBody = (revision = 7, overrides = {}) => ({
  api_version: "v1",
  status: "ok",
  data: {
    schema_version: 1,
    ...preferences.DEFAULT_PREFERENCES,
    revision,
    ...overrides,
  },
  reason: null,
});

/* ------------------------------------------------------------ enrichment */

test("lap enrichment keeps sectors, tyres, pit and revision identity", () => {
  const parsed = enrichment.parseLapEnrichment(lapsBody);
  assert.equal(parsed.laps.length, 3);
  assert.deepEqual(parsed.laps[0].sectorsMs, [28_100, 36_004, 28_000]);
  assert.equal(parsed.laps[0].sectorsSource, "session_history");
  assert.equal(parsed.laps[0].revisionId, "rev-lap-1");
  assert.equal(parsed.laps[0].tyre.actualLabel, "C3");
  assert.equal(parsed.laps[0].tyre.ageLaps, 2);
  assert.equal(parsed.laps[0].tyre.source, "car_status");

  const missing = parsed.laps[1];
  assert.equal(missing.sectorsMs, null);
  assert.equal(missing.sectorsReason, "session_history_not_in_journal");
  assert.equal(missing.tyre, null);
  assert.equal(missing.tyreReason, "car_status_not_in_journal");
  assert.equal(missing.valid, false);
  assert.equal(missing.gameValid, false);

  const abandoned = parsed.laps[2];
  assert.equal(abandoned.lapTimeMs, null);
  assert.equal(abandoned.pit, true);
  assert.equal(abandoned.superseded, true);
  assert.equal(abandoned.startObserved, null);
  assert.equal(abandoned.valid, true);
  assert.equal(abandoned.tyre.source, "session_history_stints");
  assert.equal(abandoned.tyre.actualLabel, null);
});

test("lap enrichment stints and truncation come from the real field names", () => {
  const parsed = enrichment.parseLapEnrichment(lapsBody);
  assert.equal(parsed.stints.length, 1);
  assert.deepEqual(parsed.stints[0], {
    index: 0,
    visualLabel: "soft",
    actualLabel: "C3",
    visualCompound: 16,
    actualCompound: 18,
    startLap: 1,
    endLap: 12,
    lapCount: 12,
    startAge: 1,
    endAge: 12,
  });
  // The reason sits in source.truncation, not at the top level.
  assert.equal(parsed.truncated, true);
  assert.equal(parsed.truncation, "time_budget_exceeded");
  assert.equal(parsed.sessionId, "session-a");
});

test("lap enrichment rejects malformed bodies and malformed sectors", () => {
  assert.equal(enrichment.parseLapEnrichment(null), null);
  assert.equal(enrichment.parseLapEnrichment({ data: { laps: "nope" } }), null);
  const odd = enrichment.parseLapEnrichment({
    data: { laps: [lapRow({ sectors_ms: [1, 2], tyre: { visual_compound: 1 } })] },
  });
  assert.equal(odd.laps[0].sectorsMs, null);
  assert.equal(odd.laps[0].sectorsSource, null);
  assert.equal(odd.laps[0].tyre, null);
  assert.equal(odd.truncated, false);
  assert.equal(odd.truncation, null);
  const byRevision = enrichment.enrichmentByRevision(enrichment.parseLapEnrichment(lapsBody));
  assert.equal(byRevision.get("rev-lap-2").lapNumber, 2);
});

/* --------------------------------------------------------------- summary */

test("summary maps nested backend fields to the flat model", () => {
  const parsed = summary.parseSessionSummary(summaryBody);
  assert.equal(parsed.startedAtUtc, "2026-10-04T15:00:00.120Z");
  assert.equal(parsed.endedAtUtc, "2026-10-04T16:42:18.000Z");
  assert.equal(parsed.timeReason, null);
  assert.equal(parsed.durationS, 6138.4);
  assert.equal(parsed.scheduledDurationS, 7200);
  assert.equal(parsed.totalLaps, 78);
  assert.equal(parsed.pitStopCount, 2);
  assert.equal(parsed.lastLapNumber, 78);
  assert.equal(parsed.lifecycle, "ended");
  assert.deepEqual(parsed.weather[1], {
    sessionTimeS: 2470,
    weather: "light_cloud",
    trackTemperatureC: 35,
    airTemperatureC: 23,
  });
  assert.deepEqual(parsed.totals, {
    listed: 3,
    observed: 9,
    omittedMinor: 4,
    omittedBudget: 2,
    truncated: true,
    byKind: { overtake: 1, pit_entry: 1, safety_car: 1 },
  });
  assert.deepEqual(parsed.scan, { complete: false, stopReason: "row_budget" });
});

test("summary events finalise placeholders and tolerate odd detail", () => {
  const parsed = summary.parseSessionSummary(summaryBody);
  assert.equal(parsed.events.length, 3);
  assert.equal(parsed.events[0].kind, "pit_entry");
  assert.equal(parsed.events[0].isPlayer, true);
  assert.equal(parsed.events[0].detail.pit_stop_count, 1);
  assert.equal(parsed.events[1].label, "A driver overtook another driver");
  assert.equal(parsed.events[1].code, "OVTK");
  assert.equal(parsed.events[2].sessionTimeS, null);
  assert.equal(parsed.events[2].lap, null);
  assert.deepEqual(parsed.events[2].detail, {});
  assert.equal(summary.cleanEventLabel("Lights out"), "Lights out");
});

test("summary keeps absent capture times null with the reason", () => {
  const body = structuredClone(summaryBody);
  body.data.started_at_utc = null;
  body.data.ended_at_utc = null;
  body.data.time_reason = "session_active";
  body.data.scan.complete = true;
  body.data.scan.stop_reason = "end_of_committed_journal";
  body.data.started_at_utc = "not a date";
  const parsed = summary.parseSessionSummary(body);
  assert.equal(parsed.startedAtUtc, null);
  assert.equal(parsed.endedAtUtc, null);
  assert.equal(parsed.timeReason, "session_active");
  assert.equal(parsed.scan.complete, true);
  assert.equal(summary.parseSessionSummary({ data: { events: [] } }), null);
  assert.equal(summary.parseSessionSummary(null), null);
});

/* --------------------------------------------------------------- regions */

test("regions parse exact distances, apexes and complexes", () => {
  const model = regions.parseRegionsResponse({ data: regionsPayload() });
  assert.equal(model.modelId, "monaco-2024");
  assert.equal(model.validation, "validated");
  assert.equal(model.origin, "packaged");
  assert.equal(model.regions.length, 2);
  assert.deepEqual(model.regions[0], {
    id: "t1",
    label: "T1",
    startM: 200,
    endM: 320,
    apexM: 260,
    direction: "right",
    complexId: null,
  });
  assert.equal(model.regions[1].apexM, null);
  assert.equal(model.regions[1].complexId, "t6-t8");
  assert.equal(regions.parseRegionsResponse({ data: { model_id: "x" } }), null);
});

test("session track models: validated beats draft, then the highest revision", () => {
  const body = {
    data: {
      session_id: "session-a",
      models: [
        regionsPayload({ model_id: "monaco-2024", revision: 3, validation_status: "draft" }),
        regionsPayload({ model_id: "monaco-2024", revision: 1, validation_status: "validated" }),
        regionsPayload({ model_id: "monaco-2024", revision: 2, validation_status: "validated" }),
        regionsPayload({ model_id: "empty", revision: 9, validation_status: "validated", regions: [] }),
      ],
      reason: null,
    },
  };
  const parsed = regions.parseSessionTrackModels(body);
  assert.equal(parsed.models.length, 4);
  const picked = regions.pickRegionModel(parsed.models);
  assert.equal(picked.revision, 2);
  assert.equal(picked.validation, "validated");
  const draftOnly = regions.pickRegionModel(parsed.models.filter((m) => m.validation === "draft"));
  assert.equal(draftOnly.revision, 3);
  assert.equal(regions.pickRegionModel([]), null);
});

test("an empty compatible model list is normal and keeps its reason", () => {
  const parsed = regions.parseSessionTrackModels({
    data: { session_id: "s", models: [], reason: "compatible_track_model_unavailable" },
  });
  assert.deepEqual(parsed.models, []);
  assert.equal(parsed.reason, "compatible_track_model_unavailable");
  assert.equal(regions.pickRegionModel(parsed.models), null);
  assert.equal(regions.parseSessionTrackModels({ data: { models: "x" } }), null);
});

test("track model list parses records and treats unknown status as draft", () => {
  const list = regions.parseTrackModelList({
    data: [
      { model_id: "monaco-2024", revision: 1, track_id: 5, track_name: "Monaco", layout_id: "gp", track_length_m: 3337, validation_status: "validated", origin: "packaged", region_count: 19 },
      { model_id: "jeddah", revision: 1, track_name: "Jeddah", validation_status: "something", region_count: 0 },
      { revision: 1 },
    ],
  });
  assert.equal(list.length, 2);
  assert.equal(list[0].validation, "validated");
  assert.equal(list[1].validation, "draft");
  assert.equal(regions.parseTrackModelList({ data: {} }), null);
});

/* ------------------------------------------------------ engineer events */

test("event pages sort, dedupe and report next_after", () => {
  const page = engineerEvents.parseEngineerEventPage({
    data: {
      events: [
        eventRow(7, { kind: "response", related_id: 6, priority: "high" }),
        eventRow(5),
        eventRow(5, { text: "duplicate id" }),
        eventRow(6, { kind: "question", source: "v2_ask", topic: null, session_id: null, lap: null, detail: null }),
        eventRow(8, { kind: "unknown-kind" }),
        eventRow(9, { priority: "urgent" }),
      ],
      next_after: 9,
    },
  });
  assert.deepEqual(page.events.map((event) => event.id), [5, 6, 7, 9]);
  assert.equal(page.events[0].text, "Call 5");
  assert.equal(page.events[1].detail, null);
  assert.equal(page.events[2].relatedId, 6);
  assert.equal(page.events[2].priority, "high");
  assert.equal(page.events[3].priority, "normal");
  assert.equal(page.nextAfter, 9);

  const empty = engineerEvents.parseEngineerEventPage({ data: { events: [], next_after: 42 } });
  assert.deepEqual(empty, { events: [], nextAfter: 42 });
  assert.equal(engineerEvents.parseEngineerEventPage({ data: {} }), null);
});

test("event reads distinguish unavailable log from unreachable engine", () => {
  const ok = engineerEvents.parseEngineerEventsRead(200, { data: { events: [eventRow(1)], next_after: 1 } });
  assert.equal(ok.state, "ok");
  assert.deepEqual(
    engineerEvents.parseEngineerEventsRead(503, { status: "unavailable", reason: "engineer_activity_unavailable" }),
    { state: "unavailable", reason: "engineer_activity_unavailable" },
  );
  assert.deepEqual(engineerEvents.parseEngineerEventsRead(503, { reason: "local_engine_unavailable" }), {
    state: "unreachable",
  });
  assert.equal(engineerEvents.parseEngineerEventsRead(422, { reason: "x" }).state, "unavailable");
});

test("event query clamps limits and drops invalid session filters", () => {
  assert.equal(engineerEvents.engineerEventsQuery({}), "limit=50");
  assert.equal(engineerEvents.engineerEventsQuery({ after: 12, limit: 500 }), "after=12&limit=200");
  assert.equal(engineerEvents.engineerEventsQuery({ limit: 0 }), "limit=1");
  assert.equal(
    engineerEvents.engineerEventsQuery({ after: -1, sessionId: "a b" }),
    "limit=50",
  );
  assert.equal(
    engineerEvents.engineerEventsQuery({ limit: 50, sessionId: "abc-123" }),
    "limit=50&session_id=abc-123",
  );
});

test("merging polled pages dedupes by id and keeps the newest 200", () => {
  const first = Array.from({ length: 150 }, (_, index) => engineerEvents.parseEngineerEventPage({
    data: { events: [eventRow(index + 1)], next_after: index + 1 },
  }).events[0]);
  const second = Array.from({ length: 100 }, (_, index) => engineerEvents.parseEngineerEventPage({
    data: { events: [eventRow(index + 141)], next_after: index + 141 },
  }).events[0]);
  const merged = engineerEvents.mergeEngineerEvents(first, second);
  assert.equal(merged.length, 200);
  assert.equal(merged[0].id, 41, "ids 1..240 overlap on 141..150; the newest 200 are kept");
  assert.equal(merged[199].id, 240);
  assert.equal(new Set(merged.map((event) => event.id)).size, 200);
});

/* ----------------------------------------------------------- preferences */

test("preferences parse strictly and round trip through the form", () => {
  const stored = preferences.parseStoredPreferences(storedBody(7));
  assert.equal(stored.revision, 7);
  assert.equal(stored.schemaVersion, 1);
  assert.deepEqual(stored.doc, preferences.DEFAULT_PREFERENCES);

  const form = preferences.docToForm(stored.doc);
  assert.equal(form.verbosity, "Balanced");
  assert.equal(form.interruption, "Allow interruptions");
  assert.equal(form.depth, "Standard");
  assert.equal(form.topics["Tyre management"], true);
  assert.equal(form.topics["Setup suggestions"], false);
  assert.equal(form.focusAreas["Data quality notes"], false);

  form.topics["Setup suggestions"] = true;
  form.interruption = "Never interrupt";
  form.depth = "In-depth";
  const changed = preferences.formToDoc(form);
  assert.equal(changed.topics.setup_suggestions, true);
  assert.equal(changed.interruption, "never");
  assert.equal(changed.depth, "in_depth");
  assert.equal(preferences.preferencesDiffer(stored.doc, changed), true);
  assert.equal(preferences.preferencesDiffer(stored.doc, preferences.formToDoc(preferences.docToForm(stored.doc))), false);

  assert.equal(preferences.parseStoredPreferences(storedBody(1, { verbosity: "loud" })), null);
  assert.equal(preferences.parseStoredPreferences(storedBody(1, { topics: { tyre_management: true } })), null);
  assert.equal(preferences.parseStoredPreferences({ data: null }), null);
});

test("the request body carries exactly the six document keys", () => {
  const body = JSON.parse(preferences.preferencesRequestBody(preferences.DEFAULT_PREFERENCES));
  assert.deepEqual(Object.keys(body).sort(), [
    "depth",
    "focus_areas",
    "interpretations",
    "interruption",
    "topics",
    "verbosity",
  ]);
  assert.equal(Object.keys(body.topics).length, 8);
  assert.equal(Object.keys(body.focus_areas).length, 6);
  assert.equal("revision" in body, false);
  assert.equal("schema_version" in body, false);
});

test("If-Match helpers quote the revision and read ETags back", () => {
  assert.equal(preferences.ifMatchHeader(7), '"7"');
  assert.equal(preferences.ifMatchHeader(-2), '"0"');
  assert.equal(preferences.revisionFromEtag('"7"'), 7);
  assert.equal(preferences.revisionFromEtag('W/"12"'), 12);
  assert.equal(preferences.revisionFromEtag("3"), 3);
  assert.equal(preferences.revisionFromEtag("*"), null);
  assert.equal(preferences.revisionFromEtag(null), null);
});

test("save results cover success, conflict, invalid, forbidden and unavailable", () => {
  const saved = preferences.parsePreferencesSaveResult(200, storedBody(8), '"8"');
  assert.equal(saved.ok, true);
  assert.equal(saved.stored.revision, 8);

  assert.deepEqual(
    preferences.parsePreferencesSaveResult(
      409,
      { status: "unavailable", data: { revision: 9 }, reason: "preferences_revision_conflict" },
      '"9"',
    ),
    { ok: false, kind: "conflict", currentRevision: 9 },
  );
  // The ETag header is the fallback when the body carries no revision.
  assert.deepEqual(
    preferences.parsePreferencesSaveResult(409, { reason: "preferences_revision_conflict" }, '"4"'),
    { ok: false, kind: "conflict", currentRevision: 4 },
  );
  assert.deepEqual(
    preferences.parsePreferencesSaveResult(
      422,
      { data: { field: "topics.tyre_management" }, reason: "preferences_invalid_type" },
    ),
    { ok: false, kind: "invalid", field: "topics.tyre_management", reason: "preferences_invalid_type" },
  );
  assert.equal(
    preferences.parsePreferencesSaveResult(403, { reason: "preferences_control_not_authorized" }).kind,
    "forbidden",
  );
  assert.equal(
    preferences.parsePreferencesSaveResult(503, { reason: "preferences_store_unavailable" }).kind,
    "unavailable",
  );
  assert.equal(
    preferences.parsePreferencesSaveResult(503, { reason: "local_engine_unavailable" }).kind,
    "unreachable",
  );
});

test("preference reads separate unreadable files from an unreachable engine", () => {
  assert.equal(preferences.parsePreferencesRead(200, storedBody(0)).state, "ok");
  assert.deepEqual(
    preferences.parsePreferencesRead(503, { reason: "preferences_file_invalid" }),
    { state: "unreadable", reason: "preferences_file_invalid" },
  );
  assert.deepEqual(preferences.parsePreferencesRead(200, { data: {} }), {
    state: "unreadable",
    reason: "preferences_file_invalid",
  });
  assert.deepEqual(preferences.parsePreferencesRead(404, {}), { state: "unreachable" });
});

/* --------------------------------------------------------------- reasons */

test("reason codes are humanised through one table", () => {
  assert.equal(reasons.humanReason("session_history_not_in_journal"), "Sector times were not in the recorded packets.");
  assert.equal(reasons.humanReason("session_active"), "The session is still active.");
  assert.equal(reasons.humanReason("recording_upload_content_type_invalid"), "Only .f1ecap capture files can be imported.");
  assert.equal(reasons.humanReason("not_processed"), "Detailed telemetry has not been processed for this lap.");
  assert.equal(reasons.humanReason("some_new_backend_code"), "Some new backend code");
  assert.equal(reasons.humanReason(null), "");
  assert.equal(reasons.humanReason("constructor"), "Constructor");
  assert.equal(reasons.isKnownReason("busy"), true);
  assert.equal(reasons.isKnownReason("toString"), false);
});

/* ---------------------------------------------- bounded readers (backend.ts) */

const apiTransportUrl = await loadUrl("lib/local-api-transport.ts");
const apiAuthorizationUrl = await loadUrl("lib/api-authorization.ts");
const localProxyUrl = await loadUrl("lib/local-proxy.ts", {
  "./api-authorization": apiAuthorizationUrl,
  "./local-api-transport": apiTransportUrl,
});
const proxyJsonUrl = await loadUrl("lib/proxy-json.ts", { "@/lib/local-proxy": localProxyUrl });
const recordingCatalogUrl = await loadUrl("lib/recording-catalog.ts");
const formatUrl = await loadUrl(`${dir}format.ts`);
const sessionEvidenceUrl = await loadUrl(`${dir}session-evidence.ts`, { "./format": formatUrl });
const stubUrl = toDataUrl(
  "export const requestApi = async () => null;\n" +
    "export const parseAcquisitionStatus = () => null;\n" +
    "export const parseStorageUsageResponse = () => null;\n",
);
const backend = await import(
  await loadUrl(`${dir}backend.ts`, {
    "@/lib/acquisition-status": stubUrl,
    "@/lib/api": stubUrl,
    "@/lib/local-proxy": localProxyUrl,
    "@/lib/proxy-json": proxyJsonUrl,
    "@/lib/recording-catalog": recordingCatalogUrl,
    "@/lib/storage-usage.mjs": stubUrl,
    "./session-evidence": sessionEvidenceUrl,
    "./enrichment": await loadUrl(`${dir}enrichment.ts`, withUtils),
    "./engineer-events": await loadUrl(`${dir}engineer-events.ts`, withUtils),
    "./parse-utils": utilsUrl,
    "./preferences": await loadUrl(`${dir}preferences.ts`, withUtils),
    "./regions": await loadUrl(`${dir}regions.ts`, withUtils),
    "./summary": await loadUrl(`${dir}summary.ts`, withUtils),
  })
);

const originalFetch = globalThis.fetch;
let calls = [];
let handler = async () => Response.json({}, { status: 500 });

globalThis.fetch = async (url, init) => {
  calls.push({ url: String(url), init });
  return handler(String(url), init);
};
test.after(() => {
  globalThis.fetch = originalFetch;
});

function respond(handlerFn) {
  calls = [];
  handler = handlerFn;
}

const json = (body, status = 200, headers = {}) =>
  Response.json(body, { status, headers });

test("fetchLapEnrichment parses, forwards without a token and reuses a good read", async () => {
  respond(async () => json(lapsBody));
  const first = await backend.fetchLapEnrichment("session-reuse-1");
  assert.equal(first.state, "ok");
  assert.equal(first.value.laps.length, 3);
  assert.equal(
    calls[0].url,
    "http://127.0.0.1:8765/api/v2/session-evidence/sessions/session-reuse-1/laps",
  );
  assert.equal(new Headers(calls[0].init.headers).has("authorization"), false);
  await backend.fetchLapEnrichment("session-reuse-1");
  assert.equal(calls.length, 1, "second read within 10 s must come from the cache");
});

test("fetchLapEnrichment reports busy without caching it, and rejects bad ids", async () => {
  respond(async () => json({ api_version: "v1", status: "unavailable", data: null, reason: "lap_enrichment_busy" }, 409));
  const busy = await backend.fetchLapEnrichment("session-busy");
  assert.deepEqual(busy, { state: "busy", reason: "lap_enrichment_busy" });
  respond(async () => json(lapsBody));
  assert.equal((await backend.fetchLapEnrichment("session-busy")).state, "ok");
  assert.equal(calls.length, 1, "a busy answer must not be cached");

  calls = [];
  const invalid = await backend.fetchLapEnrichment("../etc/passwd");
  assert.equal(invalid.state, "unavailable");
  assert.equal(invalid.status, 404);
  assert.equal(calls.length, 0);
});

test("readers return timeout when the budget runs out and unreachable on network failure", async () => {
  // AbortSignal.timeout timers are unref'd; keep the loop alive while the stub hangs.
  const keepAlive = setTimeout(() => {}, 5000);
  respond(
    (_url, init) =>
      new Promise((_resolve, reject) => {
        init.signal.addEventListener("abort", () => reject(new Error("aborted")));
      }),
  );
  const slow = await backend.fetchLapEnrichment("session-slow", { timeoutMs: 20 });
  assert.deepEqual(slow, { state: "timeout" });
  const slowSummary = await backend.fetchSummary("session-slow", { timeoutMs: 20 });
  assert.deepEqual(slowSummary, { state: "timeout" });
  clearTimeout(keepAlive);

  respond(async () => {
    throw new TypeError("fetch failed");
  });
  assert.deepEqual(await backend.fetchLapEnrichment("session-down"), { state: "unreachable" });
});

test("oversized or non-JSON engine bodies are treated as unreachable", async () => {
  respond(async () => new Response("x".repeat(3 * 1024 * 1024), { headers: { "content-type": "application/json" } }));
  assert.deepEqual(await backend.fetchLapEnrichment("session-big"), { state: "unreachable" });
  respond(async () => new Response("<html>nope</html>", { status: 200 }));
  assert.deepEqual(await backend.fetchSessionTrackModels("session-html"), { state: "unreachable" });
});

test("fetchSummary parses and remembers the capture start time (bounded to 256)", async () => {
  respond(async () => json(summaryBody));
  const result = await backend.fetchSummary("session-date-0");
  assert.equal(result.state, "ok");
  assert.equal(backend.cachedSummaryDate("session-date-0"), "2026-10-04T15:00:00.120Z");
  assert.equal(backend.cachedSummaryDate("never-read"), null);

  const unknownTime = structuredClone(summaryBody);
  unknownTime.data.started_at_utc = null;
  unknownTime.data.time_reason = "captured_at_not_wall_clock";
  respond(async () => json(unknownTime));
  const noDate = await backend.fetchSummary("session-no-date");
  assert.equal(noDate.state, "ok");
  assert.equal(noDate.value.timeReason, "captured_at_not_wall_clock");
  assert.equal(backend.cachedSummaryDate("session-no-date"), null);

  respond(async () => json(summaryBody));
  for (let index = 1; index <= 270; index += 1) {
    await backend.fetchSummary(`session-date-${index}`);
  }
  const dates = backend.cachedSummaryDates();
  assert.equal(dates.size, 256);
  assert.equal(dates.has("session-date-0"), false, "oldest entries are evicted first");
  assert.equal(dates.has("session-date-270"), true);
});

test("fetchSummary maps busy and missing sessions", async () => {
  respond(async () => json({ status: "unavailable", data: null, reason: "summary_busy" }, 409));
  assert.deepEqual(await backend.fetchSummary("session-sum-busy"), { state: "busy", reason: "summary_busy" });
  respond(async () => json({ status: "unavailable", data: null, reason: "session_not_found" }, 404));
  assert.deepEqual(await backend.fetchSummary("session-sum-gone"), {
    state: "unavailable",
    reason: "session_not_found",
    status: 404,
  });
});

test("region readers use the exact revision and validate ids", async () => {
  respond(async () => json({ data: regionsPayload() }));
  const read = await backend.fetchRegions("monaco-2024", 1);
  assert.equal(read.state, "ok");
  assert.equal(
    calls[0].url,
    "http://127.0.0.1:8765/api/v1/track-models/monaco-2024/regions?revision=1",
  );
  calls = [];
  assert.equal((await backend.fetchRegions("../x", 1)).state, "unavailable");
  assert.equal((await backend.fetchRegions("monaco-2024", 0)).state, "unavailable");
  assert.equal(calls.length, 0);

  respond(async () => json({ data: { session_id: "s", models: [regionsPayload()], reason: null } }));
  const models = await backend.fetchSessionTrackModels("session-models");
  assert.equal(models.state, "ok");
  assert.equal(models.value.models[0].regions.length, 2);

  respond(async () => json({ data: [{ model_id: "monaco-2024", revision: 1, track_name: "Monaco", validation_status: "validated", region_count: 2 }] }));
  const list = await backend.fetchTrackModels();
  assert.equal(list.state, "ok");
  assert.equal(list.value[0].modelId, "monaco-2024");
});

test("fetchEngineerEvents builds a bounded query and reports each failure kind", async () => {
  respond(async () => json({ data: { events: [eventRow(3)], next_after: 3 } }));
  const ok = await backend.fetchEngineerEvents({ after: 2, limit: 999, sessionId: "session-a" });
  assert.equal(ok.state, "ok");
  assert.equal(ok.page.nextAfter, 3);
  assert.equal(
    calls[0].url,
    "http://127.0.0.1:8765/api/v2/engineer/events?after=2&limit=200&session_id=session-a",
  );
  respond(async () => json({ status: "unavailable", data: null, reason: "engineer_activity_unavailable" }, 503));
  assert.equal((await backend.fetchEngineerEvents()).state, "unavailable");
  respond(async () => {
    throw new TypeError("fetch failed");
  });
  assert.deepEqual(await backend.fetchEngineerEvents(), { state: "unreachable" });
});

test("fetchPreferences returns the stored document, unreadable files and unreachable engines", async () => {
  respond(async () => json(storedBody(5), 200, { etag: '"5"' }));
  const ok = await backend.fetchPreferences();
  assert.equal(ok.state, "ok");
  assert.equal(ok.stored.revision, 5);
  respond(async () => json({ status: "unavailable", data: null, reason: "preferences_file_unreadable" }, 503));
  assert.deepEqual(await backend.fetchPreferences(), { state: "unreadable", reason: "preferences_file_unreadable" });
  respond(async () => {
    throw new TypeError("fetch failed");
  });
  assert.deepEqual(await backend.fetchPreferences(), { state: "unreachable" });
});

test("fetchRecordingSources builds a bounded query and validates the page", async () => {
  const captureId = "a".repeat(32);
  const page = {
    items: [
      {
        capture_id: captureId,
        display_name: "monaco.f1ecap",
        byte_size: 1024,
        modified_at_utc: "2026-10-04T12:00:00Z",
        latest_job_id: null,
        latest_job_status: null,
        latest_job_run_id: null,
        available: true,
        download_version: "b".repeat(64),
      },
    ],
    selected_capture: null,
    total_count: 1,
    limit: 25,
    offset: 0,
    has_more: false,
    filters: { query: "", latest_job_status: "all", availability: "all", selected_capture_id: null },
  };
  respond(async () => json({ api_version: "v1", status: "ok", data: page }));
  const result = await backend.fetchRecordingSources({ limit: 500, offset: 0, q: "monaco" });
  assert.equal(result.items.length, 1);
  assert.equal(
    calls[0].url,
    "http://127.0.0.1:8765/api/v1/recording-sources/page?limit=50&offset=0&q=monaco",
  );
  respond(async () => json({ status: "unavailable", data: null, reason: "recording_catalog_unavailable" }));
  assert.equal(await backend.fetchRecordingSources(), null);
  respond(async () => json({ status: "unavailable", data: null, reason: "limit" }, 422));
  assert.equal(await backend.fetchRecordingSources({ limit: 1 }), null);
});
