import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(
  resolve(webRoot, "src/lib/engineer-query-match.ts"),
  "utf8",
);
const javascript = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const commonJsModule = { exports: {} };
new Function("module", "exports", javascript)(commonJsModule, commonJsModule.exports);
const {
  engineerQueryReportMatchesRequest,
  pairedRegionReportMatchesSelection,
  parseTrackModelKey,
} = commonJsModule.exports;

const runId = "a".repeat(64);
const sessionUid = "14237356543050158953";
const targetKey = `${runId}:${sessionUid}:0:2`;
const referenceKey = `${runId}:${sessionUid}:0:1`;
const targetHash = "b".repeat(64);
const referenceHash = "c".repeat(64);
const request = {
  intent: "region_comparison",
  targetAttemptKey: targetKey,
  referenceAttemptKey: referenceKey,
  comparisonPolicy: "time_trial",
  trackModelId: "melbourne-r3",
  trackModelRevision: 3,
  regionIdentifier: "turn_1",
};

function regionEngineerReport() {
  return {
    schema_version: 1,
    analysis_version: "engineer-query-v1",
    artifact_kind: "engineer_query",
    intent: "region_comparison",
    status: "partial",
    reason_codes: [],
    selected: {
      target_attempt_key: targetKey,
      reference_attempt_key: referenceKey,
      comparison_policy: "time_trial",
      track_model_id: "melbourne-r3",
      track_model_revision: 3,
      region_identifier: "turn_1",
    },
    facts: [
      {
        kind: "measured_difference",
        text: "The selected window has supported evidence.",
        source_fields: ["region.supported_difference"],
      },
    ],
    omitted_fact_count: 0,
    warnings: [],
    omitted_warning_count: 0,
    provenance: {
      verification_scope: "checksummed_trace_analysis",
      run_id: runId,
      attempts: {
        target: {
          attempt_key: targetKey,
          trace_sha256: targetHash,
          trace_schema_version: 3,
        },
        reference: {
          attempt_key: referenceKey,
          trace_sha256: referenceHash,
          trace_schema_version: 3,
        },
      },
      model: {
        model_id: "melbourne-r3",
        revision: 3,
        model_content_sha256: "f".repeat(64),
        origin: "reviewed",
      },
    },
    diagnostic_only: true,
    coaching_eligible: false,
    ranking_eligible: false,
  };
}

assert.equal(
  engineerQueryReportMatchesRequest(regionEngineerReport(), request),
  true,
);

for (const change of [
  (report) => { report.analysis_version = {}; },
  (report) => { report.analysis_version = "unknown-version"; },
  (report) => { report.selected.target_attempt_key = referenceKey; },
  (report) => { report.selected.reference_attempt_key = targetKey; },
  (report) => { report.selected.comparison_policy = "practice_qualifying"; },
  (report) => { report.selected.track_model_revision += 1; },
  (report) => { report.selected.region_identifier = "another-region"; },
  (report) => { report.provenance.attempts.reference.attempt_key = targetKey; },
  (report) => { report.provenance.attempts.target.trace_sha256 = "bad-hash"; },
  (report) => { report.provenance.model.model_id = "another-model"; },
  (report) => { report.facts = [null]; },
  (report) => { report.facts = Array.from({ length: 7 }, () => ({ kind: "x", text: "y", source_fields: [] })); },
  (report) => { report.warnings = [null]; },
  (report) => { report.warnings = Array.from({ length: 9 }, () => ({ code: "x", text: "y", source_fields: [] })); },
]) {
  const report = regionEngineerReport();
  change(report);
  assert.equal(engineerQueryReportMatchesRequest(report, request), false);
}

const unavailable = regionEngineerReport();
unavailable.status = "unavailable";
unavailable.provenance = { verification_scope: "not_verified" };
unavailable.facts = [];
assert.equal(engineerQueryReportMatchesRequest(unavailable, request), true);

const unavailableWithoutProvenance = structuredClone(unavailable);
unavailableWithoutProvenance.provenance = null;
assert.equal(
  engineerQueryReportMatchesRequest(unavailableWithoutProvenance, request),
  false,
);

const unavailableWithFacts = structuredClone(unavailable);
unavailableWithFacts.facts = [
  { kind: "unsupported", text: "Must not be narrated.", source_fields: [] },
];
assert.equal(engineerQueryReportMatchesRequest(unavailableWithFacts, request), false);

const target = {
  attempt_key: targetKey,
  run_id: runId,
  session_uid: sessionUid,
  car_index: 0,
  trace_sha256: targetHash,
  trace_schema_version: 3,
};
const reference = {
  ...target,
  attempt_key: referenceKey,
  trace_sha256: referenceHash,
};
const model = {
  model_id: "melbourne-r3",
  revision: 3,
  packet_format: 2025,
  track_id: 10,
  track_name: "Melbourne",
  layout_id: "grand-prix",
  track_length_m: 5278,
  content_sha256: "d".repeat(64),
  model_content_sha256: "f".repeat(64),
  bundle_content_sha256: "d".repeat(64),
  source_kind: "review_bundle",
  origin: "reviewed",
};

function pairedReport() {
  return {
    schema_version: 1,
    analysis_version: "paired-distance-region-observations-v1",
    region_analysis_version: "distance-regions-v2",
    artifact_kind: "paired_distance_region_observations",
    status: "available",
    comparison_policy: "time_trial",
    diagnostic_only: true,
    coaching_eligible: false,
    ranking_eligible: false,
    attempts: {
      target: { ...target },
      reference: { ...reference },
    },
    model: { ...model },
    track: {
      packet_format: 2025,
      track_id: 10,
      track_name: "Melbourne",
      track_length_m: 5278,
    },
    regions: [
      {
        identifier: "turn_1",
        label: "Opening window",
        analysis_window_m: [100, 400],
        diagnostic_only: true,
        coaching_eligible: false,
        ranking_eligible: false,
      },
    ],
  };
}

const selection = {
  target,
  reference,
  comparisonPolicy: "time_trial",
  model,
};
assert.equal(pairedRegionReportMatchesSelection(pairedReport(), selection), true);
for (const change of [
  (report) => { report.attempts.target.trace_sha256 = "e".repeat(64); },
  (report) => { report.attempts.reference.attempt_key = targetKey; },
  (report) => { report.model.revision += 1; },
  (report) => { report.model.content_sha256 = "e".repeat(64); },
  (report) => { report.model.model_content_sha256 = "e".repeat(64); },
  (report) => { report.model.bundle_content_sha256 = "e".repeat(64); },
  (report) => { report.model.source_kind = "package_artifact"; },
  (report) => { report.analysis_version = "unexpected-version"; },
  (report) => { report.track.track_id += 1; },
  (report) => { report.regions[0].identifier = ""; },
  (report) => { report.regions[0].coaching_eligible = true; },
]) {
  const report = pairedReport();
  change(report);
  assert.equal(pairedRegionReportMatchesSelection(report, selection), false);
}

assert.deepEqual(parseTrackModelKey("model@with-at@7"), {
  modelId: "model@with-at",
  revision: 7,
});
assert.equal(parseTrackModelKey("model@0"), null);
assert.deepEqual(parseTrackModelKey("model@1000000000000000"), {
  modelId: "model",
  revision: 1000000000000000,
});
assert.deepEqual(parseTrackModelKey(`${"🟠".repeat(128)}@9007199254740991`), {
  modelId: "🟠".repeat(128),
  revision: 9007199254740991,
});
assert.equal(parseTrackModelKey(`${"🟠".repeat(129)}@1`), null);
assert.equal(parseTrackModelKey("model@9007199254740992"), null);

console.log("Engineer query identity and paired-region provenance checks passed.");
