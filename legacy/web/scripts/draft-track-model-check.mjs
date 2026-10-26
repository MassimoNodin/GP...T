import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(
  resolve(webRoot, "src/lib/draft-track-model.ts"),
  "utf8",
);
const javascript = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const commonJsModule = { exports: {} };
new Function("module", "exports", javascript)(
  commonJsModule,
  commonJsModule.exports,
);
const { draftTrackModelResultMatches } = commonJsModule.exports;

const attempt = {
  attempt_key: `${"a".repeat(64)}:42:0:3`,
  run_id: "a".repeat(64),
  session_uid: "14237356543050158953",
  car_index: 0,
  attempt_number: 3,
  trace_sha256: "b".repeat(64),
  trace_schema_version: 3,
  context: {
    session_type: "time_trial",
    packet_format: 2025,
    track_id: 10,
    track_name: "Shanghai",
    track_length_m: 5451.0,
  },
};
const request = {
  modelId: "draft-shanghai-v1",
  revision: 2,
  layoutId: "caller-layout",
  regions: [
    {
      identifier: "window_1",
      label: "Window 1",
      start_distance_m: 100,
      end_distance_m: 250,
      braking_search_window_m: [120, 180],
    },
  ],
};

function buildResult() {
  return {
    status: "draft_model_built",
    catalog_installation: "not_performed",
    model: {
      schema_version: 1,
      model_id: request.modelId,
      revision: request.revision,
      packet_format: 2025,
      track_id: 10,
      track_name: "Shanghai",
      layout_id: request.layoutId,
      track_length_m: 5451.0,
      distance_origin_m: 0,
      validation_status: "draft",
      corners: [
        {
          identifier: "window_1",
          label: "Window 1",
          start_distance_m: 100,
          end_distance_m: 250,
          braking_search_window_m: [120, 180],
        },
      ],
    },
    source: {
      attempt_key: attempt.attempt_key,
      run_id: attempt.run_id,
      session_uid: attempt.session_uid,
      car_index: attempt.car_index,
      attempt_number: attempt.attempt_number,
      source_trace_sha256: attempt.trace_sha256,
      source_trace_schema_version: attempt.trace_schema_version,
      context_mode: "time_trial",
      packet_format: 2025,
      track_id: 10,
      track_name: "Shanghai",
      track_length_m: 5451.0,
      scope_matches_attempt: true,
      attempt_metadata_matches_attempt: true,
      trace_metadata_matches_attempt: true,
      context_timeline_matches_attempt: true,
      context_matches_attempt: true,
    },
    warnings: [],
  };
}

assert.equal(
  draftTrackModelResultMatches(buildResult(), attempt, request),
  true,
);

for (const sessionType of ["short_practice", "one_shot_qualifying"]) {
  const supportedAttempt = {
    ...attempt,
    context: { ...attempt.context, session_type: sessionType },
  };
  const supportedResult = buildResult();
  supportedResult.source.context_mode = "practice_qualifying";
  assert.equal(
    draftTrackModelResultMatches(supportedResult, supportedAttempt, request),
    true,
  );
}

for (const change of [
  (result) => {
    result.source.attempt_key = "another-attempt";
  },
  (result) => {
    result.source.run_id = "c".repeat(64);
  },
  (result) => {
    result.source.session_uid = "other-session";
  },
  (result) => {
    result.source.source_trace_sha256 = "d".repeat(64);
  },
  (result) => {
    result.source.source_trace_schema_version += 1;
  },
  (result) => {
    result.source.context_matches_attempt = false;
  },
  (result) => {
    result.model.revision += 1;
  },
  (result) => {
    result.model.layout_id = "another-layout";
  },
  (result) => {
    result.model.track_id += 1;
  },
  (result) => {
    result.catalog_installation = "installed";
  },
  (result) => {
    result.model.corners[0].end_distance_m += 1;
  },
  (result) => {
    result.model.corners[0].braking_search_window_m[0] += 1;
  },
  (result) => {
    result.model.corners[0].unexpected = true;
  },
  (result) => {
    result.model.corners[0].label = null;
  },
  (result) => {
    result.warnings = [null];
  },
  (result) => {
    result.warnings = Array.from({ length: 17 }, () => ({
      code: "x",
      text: "y",
    }));
  },
  (result) => {
    result.warnings = [{ code: "x", text: "y".repeat(2049) }];
  },
]) {
  const result = buildResult();
  change(result);
  assert.equal(draftTrackModelResultMatches(result, attempt, request), false);
}

const raceAttempt = {
  ...attempt,
  context: { ...attempt.context, session_type: "race" },
};
assert.equal(
  draftTrackModelResultMatches(buildResult(), raceAttempt, request),
  false,
);

console.log("Draft track model source and request identity checks passed.");
