import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(
  resolve(webRoot, "src/lib/lap-debrief-match.ts"),
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
const { lapDebriefMatchesComparison } = commonJsModule.exports;

const complete = comparison();
const available = debrief(complete);
assert.equal(
  lapDebriefMatchesComparison(
    available,
    complete,
    complete.target,
    complete.reference,
  ),
  true,
);

const partialWithoutOfficialFact = comparison({ official: false });
partialWithoutOfficialFact.target.lap_time_ms = null;
partialWithoutOfficialFact.reference.lap_time_ms = null;
const partialDebrief = debrief(partialWithoutOfficialFact);
assert.equal(partialDebrief.status, "partial");
assert.equal(partialDebrief.official_lap_time, null);
assert.equal(
  lapDebriefMatchesComparison(
    partialDebrief,
    partialWithoutOfficialFact,
    partialWithoutOfficialFact.target,
    partialWithoutOfficialFact.reference,
  ),
  true,
);

const partialWithOmittedRegions = comparison({ omitted: 2 });
const omittedDebrief = debrief(partialWithOmittedRegions);
assert.equal(omittedDebrief.status, "partial");
assert.equal(omittedDebrief.omitted_region_count, 2);
assert.equal(
  lapDebriefMatchesComparison(
    omittedDebrief,
    partialWithOmittedRegions,
    partialWithOmittedRegions.target,
    partialWithOmittedRegions.reference,
  ),
  true,
);

const boundedIdentityComparison = comparison();
const longIdentifier = `sector-${"x".repeat(130)}`;
const longLabel = `  Complex   corner ${"label ".repeat(22)}finish  `;
boundedIdentityComparison.corner_loss_candidates.ranked_candidates[0].region_id =
  longIdentifier;
boundedIdentityComparison.corner_loss_candidates.ranked_candidates[0].region_label =
  longLabel;
boundedIdentityComparison.corner_comparison_brief.regions[0].region_id =
  longIdentifier;
boundedIdentityComparison.corner_comparison_brief.regions[0].region_label =
  longLabel;
const boundedIdentityDebrief = debrief(boundedIdentityComparison);
assert.equal(
  boundedIdentityDebrief.ranked_regions[0].region_id,
  boundedText(longIdentifier, 120),
);
assert.equal(
  boundedIdentityDebrief.ranked_regions[0].region_label,
  boundedText(longLabel, 120),
);
assert.equal(
  lapDebriefMatchesComparison(
    boundedIdentityDebrief,
    boundedIdentityComparison,
    boundedIdentityComparison.target,
    boundedIdentityComparison.reference,
  ),
  true,
);

const astralIdentifierComparison = comparison();
const astralIdentifier = "🚦".repeat(61);
astralIdentifierComparison.corner_loss_candidates.ranked_candidates[0].region_id =
  astralIdentifier;
astralIdentifierComparison.corner_comparison_brief.regions[0].region_id =
  astralIdentifier;
const astralIdentifierDebrief = debrief(astralIdentifierComparison);
assert.equal(astralIdentifier.length, 122);
assert.equal(Array.from(astralIdentifier).length, 61);
assert.equal(
  astralIdentifierDebrief.ranked_regions[0].region_id,
  astralIdentifier,
);
assert.equal(
  lapDebriefMatchesComparison(
    astralIdentifierDebrief,
    astralIdentifierComparison,
    astralIdentifierComparison.target,
    astralIdentifierComparison.reference,
  ),
  true,
);

const pythonRoundingComparison = comparison();
const firstCandidate =
  pythonRoundingComparison.corner_loss_candidates.ranked_candidates[0];
const firstSummary =
  pythonRoundingComparison.corner_comparison_brief.regions[0];
firstCandidate.recorded_time_difference_s = 0.0625;
firstCandidate.connected_support.exit_delta_s = 0.0625;
firstSummary.connected_support.exit_delta_s = 0.0625;
firstSummary.facts[0].value = 0.0625;
firstSummary.facts[0].connected_support.exit_delta_s = 0.0625;
firstSummary.facts[0].boundary_delta_evidence.exit_target_minus_reference_s = 0.0625;
const pythonRoundingDebrief = debrief(pythonRoundingComparison);
pythonRoundingDebrief.ranked_regions[0].text =
  "Turn 1 recorded 0.062 s more interval time across [100, 170) m.";
pythonRoundingDebrief.text = canonicalDebriefText(pythonRoundingDebrief);
assert.equal(
  lapDebriefMatchesComparison(
    pythonRoundingDebrief,
    pythonRoundingComparison,
    pythonRoundingComparison.target,
    pythonRoundingComparison.reference,
  ),
  true,
);

const abstained = comparison({
  policy: "practice_qualifying",
  official: false,
});
const abstainedDebrief = debrief(abstained);
assert.equal(abstainedDebrief.status, "abstained");
assert.equal(abstainedDebrief.ranked_regions.length, 0);
assert.equal(
  lapDebriefMatchesComparison(
    abstainedDebrief,
    abstained,
    abstained.target,
    abstained.reference,
  ),
  true,
);

const incompleteCapture = comparison({
  briefLimitations: [
    {
      code: "capture_incomplete",
      text: "Capture ended before session finish.",
    },
  ],
});
const qualifiedDebrief = debrief(incompleteCapture);
assert.equal(qualifiedDebrief.limitations[0].code, "capture_incomplete");
assert.equal(
  lapDebriefMatchesComparison(
    qualifiedDebrief,
    incompleteCapture,
    incompleteCapture.target,
    incompleteCapture.reference,
  ),
  true,
);
const deletedQualification = structuredClone(qualifiedDebrief);
deletedQualification.limitations = [];
deletedQualification.status = "available";
deletedQualification.text = canonicalDebriefText(deletedQualification);
assert.equal(
  lapDebriefMatchesComparison(
    deletedQualification,
    incompleteCapture,
    incompleteCapture.target,
    incompleteCapture.reference,
  ),
  false,
);

const duplicateQualifications = comparison({
  briefLimitations: [
    { code: "capture_incomplete", text: "First capture qualification." },
    { code: "capture_incomplete", text: "Duplicate qualification." },
  ],
});
const deduplicatedDebrief = debrief(duplicateQualifications);
assert.equal(deduplicatedDebrief.limitations.length, 1);
assert.equal(
  deduplicatedDebrief.limitations[0].text,
  "First capture qualification.",
);
const changedDuplicateWinner = structuredClone(deduplicatedDebrief);
changedDuplicateWinner.limitations[0].text = "Duplicate qualification.";
changedDuplicateWinner.text = canonicalDebriefText(changedDuplicateWinner);
assert.equal(
  lapDebriefMatchesComparison(
    changedDuplicateWinner,
    duplicateQualifications,
    duplicateQualifications.target,
    duplicateQualifications.reference,
  ),
  false,
);

const upstreamOmissions = comparison({ omittedBriefLimitations: 4 });
const upstreamOmissionsDebrief = debrief(upstreamOmissions);
assert.equal(upstreamOmissionsDebrief.omitted_limitation_count, 4);
assert.equal(
  lapDebriefMatchesComparison(
    upstreamOmissionsDebrief,
    upstreamOmissions,
    upstreamOmissions.target,
    upstreamOmissions.reference,
  ),
  true,
);
const wrongOmissionCount = structuredClone(upstreamOmissionsDebrief);
wrongOmissionCount.omitted_limitation_count = 3;
wrongOmissionCount.text = canonicalDebriefText(wrongOmissionCount);
assert.equal(
  lapDebriefMatchesComparison(
    wrongOmissionCount,
    upstreamOmissions,
    upstreamOmissions.target,
    upstreamOmissions.reference,
  ),
  false,
);

const missingComparisonBrief = comparison();
missingComparisonBrief.comparison_brief = null;
const missingBriefDebrief = debrief(missingComparisonBrief);
assert.equal(missingBriefDebrief.official_lap_time, null);
assert.deepEqual(
  missingBriefDebrief.limitations.map((item) => item.code),
  ["official_lap_time_unavailable", "comparison_brief_unavailable"],
);
assert.equal(
  lapDebriefMatchesComparison(
    missingBriefDebrief,
    missingComparisonBrief,
    missingComparisonBrief.target,
    missingComparisonBrief.reference,
  ),
  true,
);

const eightQualifications = comparison({
  briefLimitations: Array.from({ length: 8 }, (_, index) => ({
    code: `warning_${index + 1}`,
    text: `Source qualification ${index + 1}.`,
  })),
});
assert.equal(debrief(eightQualifications).omitted_limitation_count, 0);
const nineQualifications = comparison({
  briefLimitations: Array.from({ length: 9 }, (_, index) => ({
    code: `warning_${index + 1}`,
    text: `Source qualification ${index + 1}.`,
  })),
});
const nineQualificationDebrief = debrief(nineQualifications);
assert.equal(nineQualificationDebrief.limitations.length, 8);
assert.equal(nineQualificationDebrief.omitted_limitation_count, 1);
assert.equal(
  lapDebriefMatchesComparison(
    nineQualificationDebrief,
    nineQualifications,
    nineQualifications.target,
    nineQualifications.reference,
  ),
  true,
);

const priorityQualified = comparison({
  policy: "practice_qualifying",
  briefLimitations: Array.from({ length: 8 }, (_, index) => ({
    code: `warning_${index + 1}`,
    text: `Source qualification ${index + 1}.`,
  })),
});
const priorityDebrief = debrief(priorityQualified);
assert.equal(priorityDebrief.limitations[0].code, "ranked_regions_unavailable");
assert.equal(priorityDebrief.omitted_limitation_count, 1);
assert.equal(
  lapDebriefMatchesComparison(
    priorityDebrief,
    priorityQualified,
    priorityQualified.target,
    priorityQualified.reference,
  ),
  true,
);

const rankingVersionMismatch = comparison();
const rankingVersionDebrief = debrief(rankingVersionMismatch);
rankingVersionMismatch.corner_loss_candidates.analysis_version = "old-version";
assertRejectedRegionReport(
  rankingVersionDebrief,
  rankingVersionMismatch,
  "D0032 ranked-region evidence failed its version, authority, or attempt-provenance check.",
);

const d0033AuthorityMismatch = comparison();
const d0033AuthorityDebrief = debrief(d0033AuthorityMismatch);
d0033AuthorityMismatch.corner_analysis.diagnostic_only = true;
assertRejectedRegionReport(
  d0033AuthorityDebrief,
  d0033AuthorityMismatch,
  "The existing D0033 authority and provenance checks did not authorize this region ranking.",
);

const unavailableCornerBrief = comparison();
const unavailableCornerDebrief = debrief(unavailableCornerBrief);
unavailableCornerBrief.corner_comparison_brief.status = "abstained";
assertRejectedRegionReport(
  unavailableCornerDebrief,
  unavailableCornerBrief,
  "D0033 did not provide a matching available summary for the ranked regions.",
);

const rankingCountMismatch = comparison();
const rankingCountDebrief = debrief(rankingCountMismatch);
rankingCountMismatch.corner_loss_candidates.ranked_candidates = [];
assertRejectedRegionReport(
  rankingCountDebrief,
  rankingCountMismatch,
  "D0032 and D0033 ranked-region counts are missing or exceed the debrief limit.",
);

const malformedRankedEntry = comparison();
const malformedEntryDebrief = debrief(malformedRankedEntry);
malformedRankedEntry.corner_loss_candidates.ranked_candidates[0] = null;
assertRejectedRegionReport(
  malformedEntryDebrief,
  malformedRankedEntry,
  "A ranked-region entry is malformed; no partial ranked list was substituted.",
);

const invalidD0033Fact = comparison();
const invalidFactDebrief = debrief(invalidD0033Fact);
invalidD0033Fact.corner_comparison_brief.regions[0].facts[0].value = 99;
assertRejectedRegionReport(
  invalidFactDebrief,
  invalidD0033Fact,
  "A D0033 region summary did not match its D0032 rank, measurements, or provenance.",
);

const rankingOrderMismatch = comparison();
const rankingOrderDebrief = debrief(rankingOrderMismatch);
rankingOrderMismatch.corner_loss_candidates.ranked_candidates = [
  rankingOrderMismatch.corner_loss_candidates.ranked_candidates[1],
  rankingOrderMismatch.corner_loss_candidates.ranked_candidates[0],
  rankingOrderMismatch.corner_loss_candidates.ranked_candidates[2],
];
rankingOrderMismatch.corner_loss_candidates.ranked_candidates.forEach(
  (candidate, index) => (candidate.rank = index + 1),
);
rankingOrderMismatch.corner_comparison_brief.regions = [
  rankingOrderMismatch.corner_comparison_brief.regions[1],
  rankingOrderMismatch.corner_comparison_brief.regions[0],
  rankingOrderMismatch.corner_comparison_brief.regions[2],
];
rankingOrderMismatch.corner_comparison_brief.regions.forEach(
  (region, index) => (region.rank = index + 1),
);
assertRejectedRegionReport(
  rankingOrderDebrief,
  rankingOrderMismatch,
  "D0032 rank order or unique region identity did not match its descending measurement ranking.",
);

const noPositiveRegions = comparison();
const noPositiveDebrief = debrief(noPositiveRegions);
noPositiveRegions.corner_loss_candidates.status =
  "no_positive_supported_differences";
noPositiveRegions.corner_loss_candidates.ranked_candidates = [];
noPositiveRegions.corner_comparison_brief.status = "no_ranked_candidates";
noPositiveRegions.corner_comparison_brief.regions = [];
noPositiveRegions.corner_comparison_brief.gate_reasons = [
  "no_ranked_corner_candidates",
];
assertRejectedRegionReport(
  noPositiveDebrief,
  noPositiveRegions,
  "No positive supported recorded-region differences qualified for ranking.",
  "ranked_regions_abstained",
);

const gatedRegions = comparison();
const gatedRegionsDebrief = debrief(gatedRegions);
gatedRegions.corner_loss_candidates.status = "abstained";
gatedRegions.corner_loss_candidates.ranked_candidates = [];
gatedRegions.corner_loss_candidates.gate_reasons = ["trace_pair_mismatch"];
gatedRegions.corner_comparison_brief.status = "abstained";
gatedRegions.corner_comparison_brief.regions = [];
gatedRegions.corner_comparison_brief.gate_reasons = ["trace_pair_mismatch"];
assertRejectedRegionReport(
  gatedRegionsDebrief,
  gatedRegions,
  "Ranked region measurements abstained at the existing evidence gates: trace pair mismatch.",
  "ranked_regions_abstained",
);

const malformedStatus = structuredClone(available);
malformedStatus.status = { toString: null };
assert.equal(
  lapDebriefMatchesComparison(
    malformedStatus,
    complete,
    complete.target,
    complete.reference,
  ),
  false,
);

assert.equal(
  lapDebriefMatchesComparison(
    undefined,
    complete,
    complete.target,
    complete.reference,
  ),
  false,
);

for (const change of [
  (candidate) => {
    candidate.ranked_regions[0].provenance.target.trace_sha256 = "e".repeat(64);
  },
  (candidate) => {
    candidate.ranked_regions[0].provenance.reference_selection.policy_version =
      "other-policy";
  },
  (candidate) => {
    candidate.official_lap_time.provenance.reference.run_id = "wrong-run";
  },
  (candidate) => {
    candidate.limits.region_limit = 4;
  },
  (candidate) => {
    candidate.coaching_eligible = true;
  },
  (candidate) => {
    candidate.ranked_regions[0].connected_support.shared_time_coverage = 0.5;
  },
]) {
  const candidate = structuredClone(available);
  change(candidate);
  assert.equal(
    lapDebriefMatchesComparison(
      candidate,
      complete,
      complete.target,
      complete.reference,
    ),
    false,
  );
}

const changedTarget = { ...complete.target, attempt_key: "different-target" };
assert.equal(
  lapDebriefMatchesComparison(
    available,
    complete,
    changedTarget,
    complete.reference,
  ),
  false,
);

console.log(
  "Lap debrief status, exact attempt provenance, ranking authority, bounds, and nested support checks passed.",
);

function comparison({
  policy = "time_trial",
  official = true,
  omitted = 0,
  briefLimitations = [],
  omittedBriefLimitations = 0,
} = {}) {
  const runId = "a".repeat(64);
  const target = attempt(runId, 3, "c".repeat(64), 90_200);
  const reference = attempt(runId, 1, "b".repeat(64), 90_000);
  const targetIdentity = identity(target);
  const referenceIdentity = identity(reference);
  const model = {
    model_id: "reviewed-model",
    revision: 1,
    validation_status: "validated",
    registered: true,
    approved_for_candidate_ranking: true,
    model_content_sha256: "d".repeat(64),
    approval_provenance: { review_record: "synthetic-approval" },
  };
  const selection = {
    reference_kind: "session_best",
    status: "selected",
    policy_version: "tt-session-best-v2-lifecycle",
    scope: { run_id: runId, session_uid: "42", car_index: 0 },
    selected_reference: {
      attempt_key: reference.attempt_key,
      trace_sha256: reference.trace_sha256,
    },
    reasons: [],
  };
  const provenance = {
    target: targetIdentity,
    reference: referenceIdentity,
    model,
    reference_selection: selection,
  };
  const regions = [];
  for (let rank = 1; rank <= 3; rank += 1) {
    const regionId = `turn-${rank}`;
    const label = `Turn ${rank}`;
    const start = rank * 100;
    const end = start + 70;
    const difference = (4 - rank) / 100;
    const connectedSupport = {
      target_resampled_time_connected: true,
      reference_resampled_time_connected: true,
      shared_delta_time_connected: true,
      target_source_session_time_connected: true,
      reference_source_session_time_connected: true,
      interval_connected_supported_time: true,
      target_time_coverage: 1,
      reference_time_coverage: 1,
      shared_time_coverage: 1,
      entry_delta_s: 0,
      exit_delta_s: difference,
    };
    const candidate = {
      rank,
      region_id: regionId,
      region_label: label,
      analysis_window_m: [start, end],
      recorded_time_difference_s: difference,
      measurement_direction: "target_minus_reference_interval_time_difference",
      connected_support: connectedSupport,
    };
    const fact = {
      kind: "recorded_interval_time_difference",
      value: difference,
      unit: "s",
      direction: "target_minus_reference",
      analysis_window_m: [start, end],
      connected_support: structuredClone(connectedSupport),
      boundary_delta_evidence: {
        entry_target_minus_reference_s: 0,
        exit_target_minus_reference_s: difference,
      },
      source_fields: {
        entry_delta: "corner_analysis.regions[].delta_change.entry_delta_s",
        exit_delta: "corner_analysis.regions[].delta_change.exit_delta_s",
        derived_difference:
          "corner_analysis.regions[].delta_change.delta_change_s",
      },
      provenance: structuredClone(provenance),
      text: "Generated interval measurement from D0033.",
    };
    regions.push({
      candidate,
      summary: {
        rank,
        region_id: regionId,
        region_label: label,
        analysis_window_m: [start, end],
        facts: [fact],
        connected_support: structuredClone(connectedSupport),
        provenance: structuredClone(provenance),
      },
    });
  }

  const briefFacts = official
    ? [
        {
          kind: "official_lap_time_difference",
          value: 0.2,
          unit: "s",
          direction: "target_minus_reference",
          source_fields: {
            target: "target.lap_time_ms",
            reference: "reference.lap_time_ms",
            derived: "official_lap_time_difference_s",
          },
          provenance: { target: targetIdentity, reference: referenceIdentity },
        },
      ]
    : [];
  const brief = {
    schema_version: 1,
    analysis_version: "comparison-brief-v1",
    status: official ? "available" : "unavailable",
    diagnostic_only: false,
    coaching_eligible: false,
    facts: briefFacts,
    limitations: briefLimitations,
    limits: { omitted_limitation_count: omittedBriefLimitations },
  };
  const ranking =
    policy === "time_trial"
      ? {
          schema_version: 1,
          analysis_version: "corner-loss-candidates-v1",
          policy_version: "tt-session-best-connected-regions-v1",
          status: "ranked",
          coaching_eligible: false,
          source: {
            ...provenance,
            capture_evidence: "passed_session_best_policy",
          },
          ranked_candidates: regions.map((item) => item.candidate),
          omitted_candidate_count: omitted,
          gate_reasons: [],
          gate_reasons_omitted_count: 0,
        }
      : null;
  const cornerBrief = ranking
    ? {
        schema_version: 1,
        analysis_version: "corner-comparison-brief-v1",
        status: "available",
        coaching_eligible: false,
        regions: regions.map((item) => item.summary),
        gate_reasons: [],
        gate_reasons_omitted_count: 0,
        omitted_region_count: omitted,
      }
    : {
        schema_version: 1,
        analysis_version: "corner-comparison-brief-v1",
        status: "abstained",
        coaching_eligible: false,
        regions: [],
        gate_reasons: ["unsupported_comparison_policy"],
        gate_reasons_omitted_count: 0,
        omitted_region_count: 0,
      };

  return {
    comparison_policy: policy,
    official_lap_time_difference_s: official ? 0.2 : null,
    target,
    reference,
    track: { track_length_m: 6_000 },
    comparison_brief: brief,
    corner_loss_candidates: ranking,
    corner_comparison_brief: cornerBrief,
    corner_analysis: ranking
      ? {
          diagnostic_only: false,
          model: {
            model_id: model.model_id,
            revision: model.revision,
            validation_status: "validated",
          },
          source: { target: targetIdentity, reference: referenceIdentity },
        }
      : null,
  };
}

function debrief(comparison) {
  const ranking = comparison.corner_loss_candidates;
  const regions = (ranking?.ranked_candidates ?? []).map((candidate, index) => {
    const summary = comparison.corner_comparison_brief.regions[index];
    const fact = summary.facts[0];
    const [start, end] = candidate.analysis_window_m;
    return {
      rank: candidate.rank,
      region_id: boundedText(candidate.region_id, 120),
      region_label: boundedText(candidate.region_label, 120),
      analysis_window_m: [start, end],
      recorded_time_difference_s: candidate.recorded_time_difference_s,
      text: `${boundedText(candidate.region_label, 120)} recorded ${pythonFixed3(candidate.recorded_time_difference_s)} s more interval time across [${start}, ${end}) m.`,
      source_fields: {
        difference:
          "corner_loss_candidates.ranked_candidates[].recorded_time_difference_s",
        window: "corner_loss_candidates.ranked_candidates[].analysis_window_m",
      },
      connected_support: structuredClone(candidate.connected_support),
      provenance: {
        target: identity(comparison.target),
        reference: identity(comparison.reference),
        model: {
          model_id: fact.provenance.model.model_id,
          revision: fact.provenance.model.revision,
          model_content_sha256: fact.provenance.model.model_content_sha256,
        },
        reference_selection: {
          reference_kind: fact.provenance.reference_selection.reference_kind,
          status: fact.provenance.reference_selection.status,
          policy_version: fact.provenance.reference_selection.policy_version,
        },
      },
    };
  });
  const brief = comparison.comparison_brief;
  const official = brief?.facts?.find(
    (fact) => fact.kind === "official_lap_time_difference",
  );
  const officialFact = official
    ? {
        value_s: 0.2,
        direction: "target_minus_reference",
        text: "The target lap was 0.200 s slower by official lap time.",
        source_fields: {
          target: "target.lap_time_ms",
          reference: "reference.lap_time_ms",
          derived: "official_lap_time_difference_s",
        },
        provenance: {
          target: identity(comparison.target),
          reference: identity(comparison.reference),
        },
      }
    : null;
  const limitations = [];
  if (!officialFact) {
    limitations.push({
      code: "official_lap_time_unavailable",
      text: "A consistent official lap-time difference could not be verified from both completed attempts.",
    });
  }
  if (!ranking) {
    limitations.push({
      code: "ranked_regions_unavailable",
      text: "Ranked region measurements are available only under the existing Time Trial ranking policy.",
    });
  }
  if (!brief) {
    limitations.push({
      code: "comparison_brief_unavailable",
      text: "The source comparison brief is unavailable; no lap-time fact was added.",
    });
  }
  const seenCodes = new Set(limitations.map((item) => item.code));
  for (const item of (brief?.limitations ?? []).slice(0, 32)) {
    if (
      item &&
      typeof item.code === "string" &&
      typeof item.text === "string" &&
      item.text.trim() &&
      !seenCodes.has(safeCode(item.code))
    ) {
      const code = safeCode(item.code);
      seenCodes.add(code);
      limitations.push({ code, text: boundedText(item.text, 280) });
    }
  }
  const priority = {
    attempt_integrity: 0,
    capture_incomplete: 1,
    capture_completeness_unknown: 2,
    recording_losses: 3,
    recording_loss_counts_unknown: 3,
    replay_losses: 4,
    replay_loss_counts_unknown: 4,
    delta_coverage_partial: 5,
    delta_coverage_unknown: 5,
    ranked_regions_rejected: 6,
    ranked_regions_abstained: 7,
    ranked_regions_unavailable: 7,
    official_lap_time_unavailable: 8,
    practice_qualifying_conditions_uncontrolled: 9,
    diagnostic_comparison: 10,
    comparison_brief_unavailable: 11,
  };
  limitations.sort(
    (a, b) =>
      (priority[a.code] ?? 50) - (priority[b.code] ?? 50) ||
      a.code.localeCompare(b.code),
  );
  const omittedLimitations =
    Math.max(0, limitations.length - 8) +
    (brief?.limits?.omitted_limitation_count ?? 0);
  const visibleLimitations = limitations.slice(0, 8);
  const omittedRegions =
    comparison.corner_comparison_brief.omitted_region_count ?? 0;
  const status =
    !officialFact && regions.length === 0
      ? "abstained"
      : visibleLimitations.length > 0 ||
          !officialFact ||
          regions.length === 0 ||
          omittedRegions > 0
        ? "partial"
        : "available";
  const parts = [];
  if (officialFact) parts.push(officialFact.text);
  if (regions.length > 0) {
    parts.push(
      `Largest authorized recorded-region differences: ${regions.map((region) => region.text).join(" ")}`,
    );
  } else if (
    !limitations.some((item) => item.code.startsWith("ranked_regions_"))
  ) {
    parts.push("No ranked region measurements qualified for this comparison.");
  }
  parts.push(
    "These are recorded measurements; they do not establish causes or recommend driving changes.",
  );
  if (visibleLimitations.length > 0) {
    parts.push(
      `Limitations: ${visibleLimitations.map((item) => item.text.replace(/\.+$/, "")).join("; ")}.`,
    );
  }
  if (omittedLimitations > 0) {
    parts.push(
      "Additional limitation details were omitted by the configured bounds.",
    );
  }
  return {
    schema_version: 1,
    analysis_version: "lap-debrief-v1",
    status,
    comparison_policy: comparison.comparison_policy,
    diagnostic_only: true,
    coaching_eligible: false,
    text: parts.join(" "),
    official_lap_time: officialFact,
    ranked_regions: regions,
    omitted_region_count: omittedRegions,
    limitations: visibleLimitations,
    omitted_limitation_count: omittedLimitations,
    limits: { region_limit: 3, limitation_limit: 8, text_characters: 2_400 },
  };
}

function assertRejectedRegionReport(
  report,
  comparison,
  text,
  code = "ranked_regions_rejected",
) {
  report.ranked_regions = [];
  report.omitted_region_count = 0;
  report.limitations = [{ code, text }];
  report.status = report.official_lap_time ? "partial" : "abstained";
  report.text = canonicalDebriefText(report);
  assert.equal(
    lapDebriefMatchesComparison(
      report,
      comparison,
      comparison.target,
      comparison.reference,
    ),
    true,
    `expected regional limitation to match: ${text}`,
  );
}

function attempt(runId, number, traceSha256, lapTimeMs) {
  return {
    attempt_key: `${runId}:42:0:${number}`,
    run_id: runId,
    session_uid: "42",
    car_index: 0,
    attempt_number: number,
    disposition: "completed",
    lap_time_ms: lapTimeMs,
    trace_sha256: traceSha256,
    trace_schema_version: 3,
  };
}

function identity(attempt) {
  return {
    attempt_key: attempt.attempt_key,
    run_id: attempt.run_id,
    trace_sha256: attempt.trace_sha256,
  };
}

function boundedText(value, maximum) {
  const normalized = value.trim().replace(/\s+/g, " ");
  const characters = Array.from(normalized);
  if (characters.length <= maximum) return normalized;
  return `${characters
    .slice(0, maximum - 1)
    .join("")
    .trimEnd()}…`;
}

function safeCode(value) {
  const safe = Array.from(value)
    .map((character) => (/^[\p{L}\p{N}_]$/u.test(character) ? character : "_"))
    .join("");
  return Array.from(safe).slice(0, 80).join("") || "unspecified_limitation";
}

function pythonFixed3(value) {
  return value === 0.0625 ? "0.062" : value.toFixed(3);
}

function canonicalDebriefText(report) {
  const parts = [];
  if (report.official_lap_time) parts.push(report.official_lap_time.text);
  if (report.ranked_regions.length > 0) {
    parts.push(
      `Largest authorized recorded-region differences: ${report.ranked_regions.map((region) => region.text).join(" ")}`,
    );
  } else if (
    !report.limitations.some((item) => item.code.startsWith("ranked_regions_"))
  ) {
    parts.push("No ranked region measurements qualified for this comparison.");
  }
  parts.push(
    "These are recorded measurements; they do not establish causes or recommend driving changes.",
  );
  if (report.limitations.length > 0) {
    parts.push(
      `Limitations: ${report.limitations.map((item) => item.text.replace(/\.+$/, "")).join("; ")}.`,
    );
  }
  if (report.omitted_limitation_count > 0) {
    parts.push(
      "Additional limitation details were omitted by the configured bounds.",
    );
  }
  return boundedText(parts.join(" "), 2_400);
}
