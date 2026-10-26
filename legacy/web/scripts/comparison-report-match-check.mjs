import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(
  resolve(webRoot, "src/lib/comparison-report-match.ts"),
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
const { comparisonReportMatchesAttempts } = commonJsModule.exports;

const runId = "a".repeat(64);
const target = {
  attempt_key: `${runId}:14237356543050158953:0:2`,
  run_id: runId,
  session_uid: "14237356543050158953",
  car_index: 0,
  trace_sha256: "b".repeat(64),
  trace_schema_version: 4,
};
const reference = {
  ...target,
  attempt_key: `${runId}:14237356543050158953:0:1`,
  trace_sha256: "c".repeat(64),
};

function report() {
  return {
    target: { ...target },
    reference: { ...reference },
  };
}

assert.equal(
  comparisonReportMatchesAttempts(report(), target, reference),
  true,
);
for (const change of [
  (value) => {
    value.target.attempt_key = reference.attempt_key;
  },
  (value) => {
    value.target.run_id = "d".repeat(64);
  },
  (value) => {
    value.target.session_uid = "other-session";
  },
  (value) => {
    value.target.car_index = 1;
  },
  (value) => {
    value.target.trace_sha256 = "e".repeat(64);
  },
  (value) => {
    value.reference.trace_schema_version += 1;
  },
]) {
  const candidate = report();
  change(candidate);
  assert.equal(
    comparisonReportMatchesAttempts(candidate, target, reference),
    false,
  );
}
assert.equal(comparisonReportMatchesAttempts(null, target, reference), false);
assert.equal(comparisonReportMatchesAttempts({}, target, reference), false);

console.log("Comparison source identity checks passed.");
