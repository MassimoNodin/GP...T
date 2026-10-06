import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(
  resolve(webRoot, "src/lib/engineer-ask.ts"),
  "utf8",
);
const javascript = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const dependencies = {
  "@/lib/engineer-query-match": {
    engineerQueryReportMatchesRequest: () => false,
  },
  "@/lib/comparison-report-match": {
    comparisonReportMatchesAttempts: () => false,
  },
  "@/lib/lap-debrief-match": {
    lapDebriefMatchesComparison: () => false,
  },
};
const commonJsModule = { exports: {} };
new Function("require", "module", "exports", javascript)(
  (name) => {
    if (!(name in dependencies)) throw new Error(`Unexpected import: ${name}`);
    return dependencies[name];
  },
  commonJsModule,
  commonJsModule.exports,
);
const { parseEngineerAskResult, parseEngineerRuntimeResponse } =
  commonJsModule.exports;

const runtimeStatus = {
  status: "not_configured",
  reason: "model_pin_missing",
  runtime: "Ollama",
  runtime_version: null,
  endpoint: "127.0.0.1:11435",
  model_name: "qwen3:4b",
  model_digest: null,
  model_quantization: null,
  model_parameter_size: null,
  cloud_routing: "unverified",
  requested_placement: "CPU",
  last_inference_placement: null,
  last_inference_at_utc: null,
};
const runtimeEnvelope = (data, status = "ok") => ({
  api_version: "v1",
  status,
  data,
});
assert.ok(parseEngineerRuntimeResponse(runtimeEnvelope(runtimeStatus)));
assert.equal(
  parseEngineerRuntimeResponse(
    runtimeEnvelope({ ...runtimeStatus, status: ["not_configured"] }),
  ),
  null,
);
assert.equal(
  parseEngineerRuntimeResponse(runtimeEnvelope(runtimeStatus, ["ok"])),
  null,
);

const selection = {
  intent: "attempt_summary",
  target_attempt_key: "run:1:0:2",
};
const unsupported = {
  schema_version: 1,
  analysis_version: "engineer-ask-v1",
  status: "unsupported",
  route: "unsupported",
  focus: "overview",
  message: "That question is outside the selected evidence.",
  selection,
  report: null,
  debrief: null,
  debrief_evidence: null,
  model: null,
  diagnostic_only: true,
  coaching_eligible: false,
  ranking_eligible: false,
};
const validation = {
  report: null,
  target: null,
  reference: null,
  model: null,
};
assert.ok(parseEngineerAskResult(unsupported, selection, validation));
assert.equal(
  parseEngineerAskResult(
    { ...unsupported, status: ["unsupported"] },
    selection,
    validation,
  ),
  null,
);

console.log("Engineer ask response guards passed.");
