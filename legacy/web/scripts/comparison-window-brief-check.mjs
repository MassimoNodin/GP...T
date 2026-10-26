import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(
  resolve(webRoot, "src/lib/comparison-window-brief.ts"),
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
const { distanceBracket, distanceWindowBriefMatches } = commonJsModule.exports;

const target = {
  attempt_key: "target-attempt",
  run_id: "a".repeat(64),
  trace_sha256: "b".repeat(64),
};
const reference = {
  attempt_key: "reference-attempt",
  run_id: "c".repeat(64),
  trace_sha256: "d".repeat(64),
};
const window = [1250, 1500];
const comparison = {
  comparison_policy: "time_trial",
  target,
  reference,
};

function provenance() {
  return {
    comparison_policy: "time_trial",
    window_m: { start_m: window[0], end_m: window[1] },
    target: { ...target },
    reference: { ...reference },
  };
}

function brief() {
  return {
    schema_version: 1,
    artifact_kind: "distance_window_brief",
    analysis_version: "distance-window-brief-v1",
    status: "available",
    diagnostic_only: true,
    coaching_eligible: false,
    text: "Observed interval evidence.",
    window_m: { start_m: window[0], end_m: window[1] },
    provenance: provenance(),
    facts: [
      {
        kind: "interval_time_change",
        text: "Observed interval change was 0.2 s.",
        source_fields: { target: "trace.session_time_s" },
        provenance: provenance(),
      },
    ],
    limitations: [],
    warnings: [],
  };
}

const validBrief = brief();
assert.equal(
  distanceWindowBriefMatches(validBrief, comparison, target, reference, window),
  true,
  "an exact brief and nested fact are accepted",
);

const staleFact = structuredClone(validBrief);
staleFact.facts[0].provenance.target.trace_sha256 = "e".repeat(64);
assert.equal(
  distanceWindowBriefMatches(staleFact, comparison, target, reference, window),
  false,
  "a stale fact is rejected even when the outer brief provenance matches",
);

const staleOuterTrace = structuredClone(validBrief);
staleOuterTrace.provenance.reference.trace_sha256 = "e".repeat(64);
assert.equal(
  distanceWindowBriefMatches(staleOuterTrace, comparison, target, reference, window),
  false,
  "stale outer trace provenance is rejected",
);

const staleWindow = structuredClone(validBrief);
staleWindow.facts[0].provenance.window_m.end_m += 1;
assert.equal(
  distanceWindowBriefMatches(staleWindow, comparison, target, reference, window),
  false,
  "a fact from another interval is rejected",
);

const nonStringSourceField = structuredClone(validBrief);
nonStringSourceField.facts[0].source_fields.target = 17;
assert.equal(
  distanceWindowBriefMatches(nonStringSourceField, comparison, target, reference, window),
  false,
  "malformed source field labels are rejected",
);

const excessiveFacts = structuredClone(validBrief);
excessiveFacts.facts = Array.from({ length: 6 }, (_, index) => ({
  ...validBrief.facts[0],
  kind: `fact-${index}`,
}));
assert.equal(
  distanceWindowBriefMatches(excessiveFacts, comparison, target, reference, window),
  false,
  "fact counts above the producer limit are rejected",
);

const excessiveLimitations = structuredClone(validBrief);
excessiveLimitations.limitations = Array.from({ length: 9 }, (_, index) => ({
  code: `limitation-${index}`,
  text: "Bounded limitation.",
}));
assert.equal(
  distanceWindowBriefMatches(excessiveLimitations, comparison, target, reference, window),
  false,
  "limitation counts above the producer limit are rejected",
);

const excessiveWarnings = structuredClone(validBrief);
excessiveWarnings.warnings = Array.from({ length: 9 }, (_, index) => ({
  code: `warning-${index}`,
  text: "Bounded warning.",
}));
assert.equal(
  distanceWindowBriefMatches(excessiveWarnings, comparison, target, reference, window),
  false,
  "warning counts above the producer limit are rejected",
);

assert.equal(
  distanceBracket(500.02, [500.01, 500.04]),
  "500.0–500.1 m bracket",
  "uncertainty endpoints round outward instead of narrowing the bracket",
);
assert.equal(
  distanceBracket(500.02, [500.2, 500.1]),
  "500.0 m; unbracketed",
  "a reversed bracket is never displayed as a valid range",
);

console.log(
  "Comparison window checks passed: exact and stale brief/fact provenance, safe source fields, producer fact cap, and outward bracket formatting.",
);
