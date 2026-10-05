import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);

function loadExports(relativePath) {
  const source = readFileSync(resolve(webRoot, relativePath), "utf8");
  const javascript = ts.transpileModule(source, {
    compilerOptions: {
      jsx: ts.JsxEmit.ReactJSX,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const commonJsModule = { exports: {} };
  new Function("module", "exports", "require", javascript)(
    commonJsModule,
    commonJsModule.exports,
    require,
  );
  return commonJsModule.exports;
}

const { safeObservedConditionSummary } = loadExports(
  "src/app/ComparisonConditionsPanel.tsx",
);
const { safeParticipantContext } = loadExports(
  "src/app/PlayerParticipantContextPanel.tsx",
);
const { safeCarSetupContext } = loadExports(
  "src/app/PlayerCarSetupContextPanel.tsx",
);

const summary = {
  status: "observed",
  sample_count: 1,
  matched_sample_count: 1,
  missing_join_sample_count: 0,
  unavailable_reason_counts: {},
  fields: {
    speed: { valid_count: 1, missing_count: 0, invalid_count: 0 },
  },
  first_last_observed: {
    speed: {
      first: {
        value: 1,
        frame_identifier: 0xffff_ffff,
        session_time_s: 0,
        lap_distance_m: 0,
      },
      last: null,
    },
  },
  discrete_changes: Array.from({ length: 20 }, () => ({})),
  discrete_changes_truncated: false,
  distinct_compounds: { actual: [], visual: [] },
  fuel_quantity_unit_note: "Recorded game value",
  environment_context: {
    status: "observed",
    segment_count: 1,
    known_segment_count: 1,
    unknown_segment_count: 0,
    omitted_segment_count: 0,
    missing_value_counts: {},
    retained_segments: [
      {
        from_frame_identifier: 0xffff_ffff,
        weather_id: null,
        weather_name: null,
        track_temperature_c: null,
        air_temperature_c: null,
        formula_id: null,
      },
    ],
    distinct_values: {},
  },
};

assert.ok(safeObservedConditionSummary(summary));
assert.equal(
  safeObservedConditionSummary({
    ...summary,
    discrete_changes: [...summary.discrete_changes, {}],
  }),
  null,
);
assert.equal(
  safeObservedConditionSummary({
    ...summary,
    first_last_observed: {
      speed: {
        first: {
          ...summary.first_last_observed.speed.first,
          frame_identifier: 0x1_0000_0000,
        },
        last: null,
      },
    },
  }),
  null,
);

const unknownContext = {
  schema_version: 1,
  status: "unknown",
  continuity_claim: false,
  scope: {},
  at_start: { status: "unknown", reason: "No player report was observed." },
  observations: [],
  observed_change_count: 0,
  unknown_event_count: 0,
  observations_omitted_count: 0,
};

assert.ok(safeParticipantContext(unknownContext));
assert.ok(safeCarSetupContext(unknownContext));
assert.equal(
  safeParticipantContext({ ...unknownContext, status: { toString: null } }),
  false,
);
assert.equal(
  safeCarSetupContext({ ...unknownContext, status: { toString: null } }),
  false,
);

assert.equal(
  safeParticipantContext({
    ...unknownContext,
    status: "observed",
    at_start: {
      status: "reported",
      participant: {
        ai_controlled: false,
        driver_id: 1,
        network_id: 2,
        team_id: 3,
        my_team: true,
        race_number: 4,
        nationality_id: 5,
        name: "",
        your_telemetry: 1,
        tech_level: 1,
        platform_id: 1,
      },
    },
  }),
  true,
);

console.log("Comparison context validation checks passed.");
