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

const codexStatus = {
  ...runtimeStatus,
  runtime: "Codex",
  reason: "codex_subscription_login_required",
  endpoint: "codex:stdio",
  model_name: "gpt-6-luna",
  billing: "codex_subscription",
  fallback_enabled: false,
  cloud_routing: "subscription",
  requested_placement: "cloud",
};
assert.ok(parseEngineerRuntimeResponse(runtimeEnvelope(codexStatus)));
for (const changes of [
  { model_name: "gpt-6-sol" },
  { billing: "api_key" },
  { fallback_enabled: true },
  { requested_placement: "CPU" },
  { status: ["ready"] },
]) {
  assert.equal(
    parseEngineerRuntimeResponse(
      runtimeEnvelope({ ...codexStatus, ...changes }),
    ),
    null,
  );
}
const codexModel = {
  runtime: "Codex",
  model_name: "gpt-6-luna",
  billing: "codex_subscription",
  fallback_enabled: false,
  model_digest: "not_applicable",
  runtime_version: null,
  inference_placement: "cloud",
  latency_ms: 150,
};
assert.ok(
  parseEngineerAskResult(
    { ...unsupported, model: codexModel },
    selection,
    validation,
  ),
);
for (const changes of [
  { model_name: "gpt-6-sol" },
  { billing: "api_key" },
  { fallback_enabled: true },
  { latency_ms: -1 },
]) {
  assert.equal(
    parseEngineerAskResult(
      { ...unsupported, model: { ...codexModel, ...changes } },
      selection,
      validation,
    ),
    null,
  );
}

console.log("Engineer ask response guards passed.");
