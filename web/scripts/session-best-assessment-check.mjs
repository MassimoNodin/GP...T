import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(
  resolve(webRoot, "src/lib/session-best-assessment.ts"),
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
const { resolveLapOrderAnchor, sessionBestOverviewMatchesAnchor } =
  commonJsModule.exports;

const runId = "a".repeat(64);
const sessionUid = "14237356543050158953";
const anchorKey = `${runId}:${sessionUid}:0:2`;
const selectedKey = `${runId}:${sessionUid}:0:1`;
const anchor = {
  attempt_key: anchorKey,
  run_id: runId,
  session_uid: sessionUid,
  car_index: 0,
  attempt_number: 2,
  context: {
    session_type: "time_trial",
    game_mode: "time_trial",
    rule_set: "time_trial",
  },
};

function selectedAttemptPage(overrides = {}) {
  return {
    run_id: runId,
    session_uid: sessionUid,
    items: [],
    selected_attempts: [
      {
        requested_attempt_key: anchorKey,
        attempt: { ...anchor },
      },
    ],
    ...overrides,
  };
}

assert.deepEqual(
  resolveLapOrderAnchor(selectedAttemptPage(), anchorKey, runId, sessionUid),
  anchor,
  "an exact selected attempt resolves even when it is off the current page",
);
assert.equal(
  resolveLapOrderAnchor(
    selectedAttemptPage({ run_id: "b".repeat(64) }),
    anchorKey,
    runId,
    sessionUid,
  ),
  null,
);
assert.equal(
  resolveLapOrderAnchor(
    selectedAttemptPage({ session_uid: "other-session" }),
    anchorKey,
    runId,
    sessionUid,
  ),
  null,
);
assert.equal(
  resolveLapOrderAnchor(
    selectedAttemptPage({
      selected_attempts: [
        ...selectedAttemptPage().selected_attempts,
        ...selectedAttemptPage().selected_attempts,
      ],
    }),
    anchorKey,
    runId,
    sessionUid,
  ),
  null,
);
assert.equal(
  resolveLapOrderAnchor(
    selectedAttemptPage({
      selected_attempts: [
        { requested_attempt_key: anchorKey, attempt: { ...anchor, attempt_key: selectedKey } },
      ],
    }),
    anchorKey,
    runId,
    sessionUid,
  ),
  null,
);

const candidate = {
  attempt_key: selectedKey,
  attempt_number: 1,
  disposition: "completed",
  lap_time_ms: 80_000,
  recorded_time_rank: 1,
  time_trial_eligibility: "eligible",
  trace_sha256: "c".repeat(64),
  exclusion_reasons: [],
};

function report(overrides = {}) {
  return {
    policy_version: "run-session-player-best-v1",
    status: "assessed",
    anchor_attempt_key: anchorKey,
    scope: {
      type: "same_run_session_player",
      run_id: runId,
      session_uid: sessionUid,
      car_index: 0,
      context_anchor_attempt_key: anchorKey,
    },
    recorded_time_ordering: {
      status: "available",
      diagnostic_only: true,
      sort: "reported_lap_time_ms_ascending_then_attempt_number",
      candidate_count: 1,
      timed_candidate_count: 1,
      candidates_omitted_count: 0,
    },
    eligible_time_trial_best: {
      status: "selected",
      attempt: {
        attempt_key: selectedKey,
        attempt_number: 1,
        lap_time_ms: 80_000,
        trace_sha256: "c".repeat(64),
        trace_schema_version: 4,
      },
    },
    candidates: [{ ...candidate }],
    reasons: [],
    limits: {
      scope_attempts: 256,
      candidate_rows: 32,
      candidate_trace_bytes: 10_000_000,
      candidate_trace_rows: 1_000_000,
      candidate_context_bytes: 2_000_000,
      candidate_context_segments: 20_000,
    },
    ...overrides,
  };
}

assert.equal(sessionBestOverviewMatchesAnchor(report(), anchor), true);
assert.equal(
  sessionBestOverviewMatchesAnchor(
    report({
      candidates: [{ ...candidate, disposition: "partial" }],
    }),
    anchor,
  ),
  false,
  "an eligible candidate must have a completed disposition",
);
assert.equal(
  sessionBestOverviewMatchesAnchor(
    report({
      candidates: [{ ...candidate, exclusion_reasons: ["invalid_lap"] }],
    }),
    anchor,
  ),
  false,
  "an eligible candidate cannot retain exclusion reasons",
);
assert.equal(
  sessionBestOverviewMatchesAnchor(
    report({
      eligible_time_trial_best: { status: "no_eligible_lap", attempt: null },
    }),
    anchor,
  ),
  false,
  "an assessed report with a visible eligible candidate must select a best",
);
assert.equal(
  sessionBestOverviewMatchesAnchor(
    report({
      status: "abstained",
      eligible_time_trial_best: {
        status: "assessment_limit_exceeded",
        attempt: null,
      },
      reasons: ["candidate_trace_bytes_limit_exceeded"],
    }),
    anchor,
  ),
  true,
  "an abstained report may retain a candidate verified before a later limit",
);
assert.equal(
  sessionBestOverviewMatchesAnchor(
    report({ anchor_attempt_key: selectedKey }),
    anchor,
  ),
  false,
);

const inventoryLimited = report({
  status: "abstained",
  scope: null,
  recorded_time_ordering: {
    status: "unavailable",
    diagnostic_only: true,
    sort: "reported_lap_time_ms_ascending_then_attempt_number",
    candidate_count: null,
    timed_candidate_count: null,
    candidates_omitted_count: null,
  },
  eligible_time_trial_best: {
    status: "assessment_limit_exceeded",
    attempt: null,
  },
  candidates: [],
  reasons: ["candidate_scope_attempts_limit_exceeded"],
});
assert.equal(
  sessionBestOverviewMatchesAnchor(inventoryLimited, anchor),
  true,
  "inventory-limit abstention preserves its reason despite having no scope",
);
assert.equal(
  sessionBestOverviewMatchesAnchor(
    { ...inventoryLimited, reasons: [] },
    anchor,
  ),
  false,
  "an unscoped abstention without a concrete inventory-limit reason is rejected",
);
assert.equal(
  sessionBestOverviewMatchesAnchor(
    report({
      candidates: [
        { ...candidate, attempt_key: `${runId}:${sessionUid}:1:1` },
      ],
    }),
    anchor,
  ),
  false,
  "candidate identity must remain inside the anchor player scope",
);
assert.equal(
  sessionBestOverviewMatchesAnchor(
    report({
      eligible_time_trial_best: {
        status: "selected",
        attempt: {
          ...report().eligible_time_trial_best.attempt,
          attempt_key: `${runId}:${sessionUid}:1:1`,
        },
      },
    }),
    anchor,
  ),
  false,
  "selected-best identity must remain inside the anchor player scope",
);
for (const invalidBest of [
  { ...report().eligible_time_trial_best.attempt, trace_sha256: null },
  { ...report().eligible_time_trial_best.attempt, trace_schema_version: null },
]) {
  assert.equal(
    sessionBestOverviewMatchesAnchor(
      report({
        eligible_time_trial_best: { status: "selected", attempt: invalidBest },
      }),
      anchor,
    ),
    false,
    "a selected best requires complete trace provenance",
  );
}

const fasterEligible = {
  ...candidate,
  attempt_key: `${runId}:${sessionUid}:0:3`,
  attempt_number: 3,
  lap_time_ms: 70_000,
  recorded_time_rank: 1,
  time_trial_eligibility: "eligible",
};
const slowerSelected = {
  ...candidate,
  recorded_time_rank: 2,
};
assert.equal(
  sessionBestOverviewMatchesAnchor(
    report({
      recorded_time_ordering: {
        ...report().recorded_time_ordering,
        candidate_count: 2,
        timed_candidate_count: 2,
        candidates_omitted_count: 0,
      },
      candidates: [fasterEligible, slowerSelected],
    }),
    anchor,
  ),
  false,
  "a selected best cannot be slower than a visible eligible candidate",
);
assert.equal(
  sessionBestOverviewMatchesAnchor(
    report({
    eligible_time_trial_best: {
      status: "selected",
      attempt: {
        attempt_key: `${runId}:${sessionUid}:0:3`,
        attempt_number: 3,
        lap_time_ms: 70_000,
        trace_sha256: "c".repeat(64),
        trace_schema_version: 4,
      },
    },
      candidates: [
        { ...candidate, lap_time_ms: 90_000 },
        { ...candidate, attempt_key: `${runId}:${sessionUid}:0:3`, attempt_number: 3, lap_time_ms: 70_000, recorded_time_rank: 2 },
      ],
      recorded_time_ordering: {
        ...report().recorded_time_ordering,
        candidate_count: 2,
        timed_candidate_count: 2,
        candidates_omitted_count: 0,
      },
    }),
    anchor,
  ),
  false,
  "candidate rows must follow the producer's recorded-time sort",
);

const omittedPrefix = Array.from({ length: 32 }, (_, index) => ({
  ...candidate,
  attempt_key: `${runId}:${sessionUid}:0:${index + 1}`,
  attempt_number: index + 1,
  lap_time_ms: 50_000 + index * 1_000,
  recorded_time_rank: index + 1,
  time_trial_eligibility: "excluded",
}));
assert.equal(
  sessionBestOverviewMatchesAnchor(
    report({
      recorded_time_ordering: {
        ...report().recorded_time_ordering,
        candidate_count: 33,
        timed_candidate_count: 33,
        candidates_omitted_count: 1,
      },
      eligible_time_trial_best: {
        status: "selected",
        attempt: {
          attempt_key: `${runId}:${sessionUid}:0:33`,
          attempt_number: 33,
          lap_time_ms: 90_000,
          trace_sha256: "d".repeat(64),
          trace_schema_version: 4,
        },
      },
      candidates: omittedPrefix,
    }),
    anchor,
  ),
  true,
  "a selected best omitted after the retained diagnostic prefix remains valid",
);
assert.equal(
  sessionBestOverviewMatchesAnchor(
    report({ scope: { ...report().scope, run_id: "b".repeat(64) } }),
    anchor,
  ),
  false,
);
assert.equal(
  sessionBestOverviewMatchesAnchor(
    report({ candidates: Array.from({ length: 33 }, () => ({ ...candidate })) }),
    anchor,
  ),
  false,
);
assert.equal(
  sessionBestOverviewMatchesAnchor(
    report({
      candidates: [{ ...candidate, attempt_key: anchorKey, lap_time_ms: 79_999 }],
    }),
    anchor,
  ),
  false,
);
assert.equal(
  sessionBestOverviewMatchesAnchor(
    report({ status: { toString: null } }),
    anchor,
  ),
  false,
);

const practiceAnchor = {
  ...anchor,
  context: {
    session_type: "practice",
    game_mode: "practice",
    rule_set: "practice",
  },
};
assert.equal(
  sessionBestOverviewMatchesAnchor(
    report({
      eligible_time_trial_best: {
        status: "unsupported_mode",
        attempt: null,
      },
      candidates: [
        { ...candidate, time_trial_eligibility: "not_assessed" },
      ],
    }),
    practiceAnchor,
  ),
  true,
);
assert.equal(
  sessionBestOverviewMatchesAnchor(report(), practiceAnchor),
  false,
  "non-Time-Trial context cannot display an automatic eligible best",
);

console.log("Session lap-order identity and consistency checks passed.");
