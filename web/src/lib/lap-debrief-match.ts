import type { Comparison, LapDebrief, LapRecord } from "@/lib/api";

const debriefVersion = "lap-debrief-v1";
const comparisonBriefVersion = "comparison-brief-v1";
const rankingVersion = "corner-loss-candidates-v1";
const rankingPolicyVersion = "tt-session-best-connected-regions-v1";
const referencePolicyVersion = "tt-session-best-v2-lifecycle";
const debriefRegionLimit = 3;
const debriefLimitationLimit = 8;
const debriefTextLimit = 2_400;

const supportFlags = [
  "target_resampled_time_connected",
  "reference_resampled_time_connected",
  "shared_delta_time_connected",
  "target_source_session_time_connected",
  "reference_source_session_time_connected",
  "interval_connected_supported_time",
] as const;
const supportValues = [
  ...supportFlags,
  "target_time_coverage",
  "reference_time_coverage",
  "shared_time_coverage",
] as const;

type AttemptIdentity = Pick<
  LapRecord,
  | "attempt_key"
  | "run_id"
  | "session_uid"
  | "car_index"
  | "trace_sha256"
  | "trace_schema_version"
>;

export function lapDebriefMatchesComparison(
  value: unknown,
  comparisonValue: unknown,
  target: LapRecord,
  reference: LapRecord,
): value is LapDebrief {
  const report = record(value);
  const comparison = record(comparisonValue);
  const limits = record(report?.limits);
  const regions = array(report?.ranked_regions);
  const limitations = array(report?.limitations);
  const official = report?.official_lap_time;
  if (
    !report ||
    !comparison ||
    !limits ||
    !regions ||
    !limitations ||
    report.schema_version !== 1 ||
    report.analysis_version !== debriefVersion ||
    typeof report.status !== "string" ||
    !["available", "partial", "abstained"].includes(report.status) ||
    report.comparison_policy !== comparison.comparison_policy ||
    (report.comparison_policy !== "time_trial" &&
      report.comparison_policy !== "practice_qualifying") ||
    report.diagnostic_only !== true ||
    report.coaching_eligible !== false ||
    !shortText(report.text, debriefTextLimit) ||
    limits.region_limit !== debriefRegionLimit ||
    limits.limitation_limit !== debriefLimitationLimit ||
    limits.text_characters !== debriefTextLimit ||
    regions.length > debriefRegionLimit ||
    limitations.length > debriefLimitationLimit ||
    !count(report.omitted_region_count) ||
    !count(report.omitted_limitation_count) ||
    !validLimitations(limitations) ||
    Object.keys(limits).length !== 3 ||
    !parentAttemptsMatch(comparison, target, reference)
  ) {
    return false;
  }

  const officialFactAvailable = comparisonBriefSupportsOfficialTime(
    comparison,
    target,
    reference,
  );
  if ((official !== null) !== officialFactAvailable) return false;
  if (
    official !== null &&
    !validOfficialFact(official, comparison, target, reference)
  ) {
    return false;
  }

  const ranking = record(comparison.corner_loss_candidates);
  const cornerBrief = record(comparison.corner_comparison_brief);
  const rankedRegionsValid =
    regions.length === 0
      ? report.omitted_region_count === 0
      : validRankedRegions(
          regions,
          report.omitted_region_count,
          comparison,
          target,
          reference,
          ranking,
          cornerBrief,
        );
  if (!rankedRegionsValid) return false;

  const projectedQualifications = projectDebriefQualifications(
    comparison,
    target,
    reference,
    officialFactAvailable,
    regions.length,
  );
  if (
    !projectedQualifications ||
    report.omitted_limitation_count !== projectedQualifications.omitted ||
    !sameLimitations(limitations, projectedQualifications.visible)
  ) {
    return false;
  }

  const expectedStatus = expectedDebriefStatus(
    official !== null,
    regions.length,
    limitations.length,
    report.omitted_region_count as number,
  );
  if (report.status !== expectedStatus) return false;

  return (
    report.text ===
    canonicalDebriefText(
      official,
      regions,
      limitations,
      report.omitted_limitation_count as number,
    )
  );
}

function parentAttemptsMatch(
  comparison: Record<string, unknown>,
  target: LapRecord,
  reference: LapRecord,
): boolean {
  const targetSource = record(comparison.target);
  const referenceSource = record(comparison.reference);
  return Boolean(
    targetSource &&
    referenceSource &&
    fullIdentityMatches(targetSource, target) &&
    fullIdentityMatches(referenceSource, reference) &&
    targetSource.lap_time_ms === target.lap_time_ms &&
    referenceSource.lap_time_ms === reference.lap_time_ms,
  );
}

function fullIdentityMatches(
  actual: Record<string, unknown>,
  expected: AttemptIdentity,
): boolean {
  return (
    actual.attempt_key === expected.attempt_key &&
    actual.run_id === expected.run_id &&
    actual.session_uid === expected.session_uid &&
    actual.car_index === expected.car_index &&
    actual.trace_sha256 === expected.trace_sha256 &&
    actual.trace_schema_version === expected.trace_schema_version
  );
}

function comparisonBriefSupportsOfficialTime(
  comparison: Record<string, unknown>,
  target: LapRecord,
  reference: LapRecord,
): boolean {
  const brief = record(comparison.comparison_brief);
  const facts = array(brief?.facts);
  const targetSource = record(comparison.target);
  const referenceSource = record(comparison.reference);
  if (
    !brief ||
    !facts ||
    !targetSource ||
    !referenceSource ||
    brief.schema_version !== 1 ||
    brief.analysis_version !== comparisonBriefVersion ||
    brief.status !== "available"
  ) {
    return false;
  }
  const matchingFacts = facts.filter(
    (entry) => record(entry)?.kind === "official_lap_time_difference",
  );
  if (matchingFacts.length !== 1) return false;
  const fact = record(matchingFacts[0]);
  const fields = record(fact?.source_fields);
  const provenance = record(fact?.provenance);
  const targetProvenance = record(provenance?.target);
  const referenceProvenance = record(provenance?.reference);
  const targetTime = positiveSafeInteger(targetSource.lap_time_ms);
  const referenceTime = positiveSafeInteger(referenceSource.lap_time_ms);
  const reported = finiteNumber(comparison.official_lap_time_difference_s);
  if (
    !fact ||
    !fields ||
    !targetProvenance ||
    !referenceProvenance ||
    targetTime === null ||
    referenceTime === null ||
    reported === null
  ) {
    return false;
  }
  const expected = (targetTime - referenceTime) / 1_000;
  return (
    reported === expected &&
    finiteNumber(fact.value) === expected &&
    fact.unit === "s" &&
    fact.direction === "target_minus_reference" &&
    fields.target === "target.lap_time_ms" &&
    fields.reference === "reference.lap_time_ms" &&
    fields.derived === "official_lap_time_difference_s" &&
    partialIdentityMatches(targetProvenance, target) &&
    partialIdentityMatches(referenceProvenance, reference)
  );
}

function validOfficialFact(
  value: unknown,
  comparison: Record<string, unknown>,
  target: LapRecord,
  reference: LapRecord,
): boolean {
  const official = record(value);
  const sourceFields = record(official?.source_fields);
  const provenance = record(official?.provenance);
  const targetProvenance = record(provenance?.target);
  const referenceProvenance = record(provenance?.reference);
  const targetSource = record(comparison.target);
  const referenceSource = record(comparison.reference);
  const targetTime = positiveSafeInteger(targetSource?.lap_time_ms);
  const referenceTime = positiveSafeInteger(referenceSource?.lap_time_ms);
  const parentDifference = finiteNumber(
    comparison.official_lap_time_difference_s,
  );
  if (
    !official ||
    !sourceFields ||
    !targetProvenance ||
    !referenceProvenance ||
    targetTime === null ||
    referenceTime === null ||
    parentDifference === null
  ) {
    return false;
  }
  const expected = (targetTime - referenceTime) / 1_000;
  return (
    finiteNumber(official.value_s) === expected &&
    parentDifference === expected &&
    official.direction === "target_minus_reference" &&
    official.text === officialFactText(expected) &&
    sourceFields.target === "target.lap_time_ms" &&
    sourceFields.reference === "reference.lap_time_ms" &&
    sourceFields.derived === "official_lap_time_difference_s" &&
    partialIdentityMatches(targetProvenance, target) &&
    partialIdentityMatches(referenceProvenance, reference)
  );
}

function validLimitations(values: unknown[]): boolean {
  const seen = new Set<string>();
  return values.every((value) => {
    const limitation = record(value);
    if (
      !limitation ||
      typeof limitation.code !== "string" ||
      !/^[\p{L}\p{N}_]{1,80}$/u.test(limitation.code) ||
      !shortText(limitation.text, 280) ||
      normalizeWhitespace(limitation.text as string) !== limitation.text ||
      seen.has(limitation.code)
    ) {
      return false;
    }
    seen.add(limitation.code);
    return true;
  });
}

type Qualification = { code: string; text: string };

const limitationPriorities: Record<string, number> = {
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

function projectDebriefQualifications(
  comparison: Record<string, unknown>,
  target: LapRecord,
  reference: LapRecord,
  officialFactAvailable: boolean,
  regionCount: number,
): { visible: Qualification[]; omitted: number } | null {
  const projected: Qualification[] = [];
  const seenCodes = new Set<string>();
  const add = (code: string, text: string) => {
    if (seenCodes.has(code)) return;
    seenCodes.add(code);
    projected.push({ code, text: boundedText(text, 280) });
  };

  if (!officialFactAvailable) {
    add(
      "official_lap_time_unavailable",
      "A consistent official lap-time difference could not be verified from both completed attempts.",
    );
  }

  if (regionCount === 0) {
    const regionLimitation = rankedRegionLimitation(
      comparison,
      target,
      reference,
    );
    if (!regionLimitation) return null;
    add(regionLimitation.code, regionLimitation.text);
  }

  const brief = record(comparison.comparison_brief);
  if (!brief) {
    add(
      "comparison_brief_unavailable",
      "The source comparison brief is unavailable; no lap-time fact was added.",
    );
  } else {
    const rawLimitations = array(brief.limitations);
    if (rawLimitations) {
      for (const item of rawLimitations.slice(0, 32)) {
        const limitation = record(item);
        if (
          limitation &&
          typeof limitation.code === "string" &&
          typeof limitation.text === "string" &&
          limitation.text.trim().length > 0
        ) {
          add(safeCode(limitation.code), limitation.text);
        }
      }
    }
  }

  projected.sort((left, right) => {
    const priorityDifference =
      (limitationPriorities[left.code] ?? 50) -
      (limitationPriorities[right.code] ?? 50);
    if (priorityDifference !== 0) return priorityDifference;
    return left.code < right.code ? -1 : left.code > right.code ? 1 : 0;
  });

  let omitted = Math.max(0, projected.length - debriefLimitationLimit);
  if (brief) {
    const sourceLimits = record(brief.limits);
    omitted += nonnegativeInteger(sourceLimits?.omitted_limitation_count) ?? 0;
  }
  return {
    visible: projected.slice(0, debriefLimitationLimit),
    omitted,
  };
}

function rankedRegionLimitation(
  comparison: Record<string, unknown>,
  target: LapRecord,
  reference: LapRecord,
): Qualification | null {
  if (comparison.comparison_policy !== "time_trial") {
    return {
      code: "ranked_regions_unavailable",
      text: "Ranked region measurements are available only under the existing Time Trial ranking policy.",
    };
  }
  const ranking = record(comparison.corner_loss_candidates);
  if (!ranking) {
    return {
      code: "ranked_regions_unavailable",
      text: "The comparison has no D0032 ranked-region evidence.",
    };
  }
  const cornerBrief = record(comparison.corner_comparison_brief);
  if (ranking.status !== "ranked") {
    let reasons = rankingReasonCodes(ranking.gate_reasons);
    if (reasons.length === 0 && cornerBrief) {
      reasons = rankingReasonCodes(cornerBrief.gate_reasons);
    }
    if (ranking.status === "no_positive_supported_differences") {
      return {
        code: "ranked_regions_abstained",
        text: "No positive supported recorded-region differences qualified for ranking.",
      };
    }
    if (reasons.length > 0) {
      return {
        code: "ranked_regions_abstained",
        text: `Ranked region measurements abstained at the existing evidence gates: ${reasons
          .slice(0, 3)
          .map(humanizeReason)
          .join(", ")}.`,
      };
    }
    return {
      code: "ranked_regions_abstained",
      text: "Ranked region measurements abstained at the existing evidence gates.",
    };
  }

  const source = record(ranking.source);
  if (
    ranking.analysis_version !== rankingVersion ||
    ranking.policy_version !== rankingPolicyVersion ||
    ranking.coaching_eligible !== false ||
    !rankingSourceMatchesAttempts(source, target, reference)
  ) {
    return {
      code: "ranked_regions_rejected",
      text: "D0032 ranked-region evidence failed its version, authority, or attempt-provenance check.",
    };
  }
  const model = record(source?.model);
  const selection = record(source?.reference_selection);
  if (
    !source ||
    !model ||
    !selection ||
    !rankingAuthorityMatches(
      comparison,
      target,
      reference,
      source,
      model,
      selection,
    )
  ) {
    return {
      code: "ranked_regions_rejected",
      text: "The existing D0033 authority and provenance checks did not authorize this region ranking.",
    };
  }
  if (
    !cornerBrief ||
    cornerBrief.analysis_version !== "corner-comparison-brief-v1" ||
    cornerBrief.status !== "available" ||
    cornerBrief.coaching_eligible !== false ||
    rankingReasonCodes(cornerBrief.gate_reasons).length > 0
  ) {
    return {
      code: "ranked_regions_rejected",
      text: "D0033 did not provide a matching available summary for the ranked regions.",
    };
  }

  const candidates = array(ranking.ranked_candidates);
  const summaries = array(cornerBrief.regions);
  if (
    !candidates ||
    !summaries ||
    candidates.length > debriefRegionLimit ||
    summaries.length !== candidates.length ||
    candidates.length === 0
  ) {
    return {
      code: "ranked_regions_rejected",
      text: "D0032 and D0033 ranked-region counts are missing or exceed the debrief limit.",
    };
  }

  const rankingSource = record(ranking.source);
  const targetSource = record(comparison.target);
  const referenceSource = record(comparison.reference);
  if (!rankingSource || !targetSource || !referenceSource) {
    return {
      code: "ranked_regions_rejected",
      text: "Ranked-region attempt provenance is unavailable.",
    };
  }

  const seenIds = new Set<string>();
  let previousDifference = Number.POSITIVE_INFINITY;
  let previousStart = Number.NEGATIVE_INFINITY;
  let previousId = "";
  for (let index = 0; index < candidates.length; index += 1) {
    const candidate = record(candidates[index]);
    const summary = record(summaries[index]);
    if (!candidate || !summary) {
      return {
        code: "ranked_regions_rejected",
        text: "A ranked-region entry is malformed; no partial ranked list was substituted.",
      };
    }
    const facts = array(summary.facts);
    const fact = record(facts?.[0]);
    const derivedRegion = projectedRegionFromCandidate(
      candidate,
      rankingSource,
      target,
      reference,
    );
    if (
      !facts ||
      !fact ||
      !derivedRegion ||
      !validRankedRegion(
        derivedRegion,
        candidate,
        summary,
        fact,
        index + 1,
        comparison,
        target,
        reference,
        model,
        selection,
      )
    ) {
      return {
        code: "ranked_regions_rejected",
        text: "A D0033 region summary did not match its D0032 rank, measurements, or provenance.",
      };
    }
    const regionId = candidate.region_id as string;
    const difference = candidate.recorded_time_difference_s as number;
    const start = (candidate.analysis_window_m as number[])[0];
    if (
      seenIds.has(regionId) ||
      difference > previousDifference ||
      (difference === previousDifference &&
        (start < previousStart ||
          (start === previousStart && regionId < previousId)))
    ) {
      return {
        code: "ranked_regions_rejected",
        text: "D0032 rank order or unique region identity did not match its descending measurement ranking.",
      };
    }
    seenIds.add(regionId);
    previousDifference = difference;
    previousStart = start;
    previousId = regionId;
  }
  return null;
}

function rankingSourceMatchesAttempts(
  source: Record<string, unknown> | null,
  target: LapRecord,
  reference: LapRecord,
): boolean {
  return Boolean(
    source &&
    partialIdentityMatches(record(source.target), target) &&
    partialIdentityMatches(record(source.reference), reference),
  );
}

function rankingAuthorityMatches(
  comparison: Record<string, unknown>,
  target: LapRecord,
  reference: LapRecord,
  source: Record<string, unknown>,
  model: Record<string, unknown>,
  selection: Record<string, unknown>,
): boolean {
  const cornerAnalysis = record(comparison.corner_analysis);
  const cornerAnalysisSource = record(cornerAnalysis?.source);
  const cornerAnalysisModel = record(cornerAnalysis?.model);
  return Boolean(
    cornerAnalysis &&
    cornerAnalysisSource &&
    cornerAnalysisModel &&
    partialIdentityMatches(record(source.target), target) &&
    partialIdentityMatches(record(source.reference), reference) &&
    cornerAnalysis.diagnostic_only === false &&
    partialIdentityMatches(record(cornerAnalysisSource.target), target) &&
    partialIdentityMatches(record(cornerAnalysisSource.reference), reference) &&
    cornerAnalysisModel.model_id === model.model_id &&
    cornerAnalysisModel.revision === model.revision &&
    cornerAnalysisModel.validation_status === "validated" &&
    validApprovedModel(model) &&
    validSessionBestSelection(selection, reference),
  );
}

function projectedRegionFromCandidate(
  candidate: Record<string, unknown>,
  rankingSource: Record<string, unknown>,
  target: LapRecord,
  reference: LapRecord,
): Record<string, unknown> | null {
  const regionId = boundedSourceText(candidate.region_id, 256);
  const regionLabel = boundedSourceText(candidate.region_label, 256);
  const window = bounds(candidate.analysis_window_m);
  const difference = finiteNumber(candidate.recorded_time_difference_s);
  const support = record(candidate.connected_support);
  const model = record(rankingSource.model);
  const selection = record(rankingSource.reference_selection);
  if (
    !regionId ||
    !regionLabel ||
    !window ||
    difference === null ||
    !support ||
    !model ||
    !selection
  ) {
    return null;
  }
  const label = boundedText(regionLabel, 120);
  return {
    rank: candidate.rank,
    region_id: boundedText(regionId, 120),
    region_label: label,
    analysis_window_m: window,
    recorded_time_difference_s: difference,
    text: `${label} recorded ${difference.toFixed(3)} s more interval time across [${window[0]}, ${window[1]}) m.`,
    source_fields: {
      difference:
        "corner_loss_candidates.ranked_candidates[].recorded_time_difference_s",
      window: "corner_loss_candidates.ranked_candidates[].analysis_window_m",
    },
    connected_support: support,
    provenance: {
      target: attemptIdentityProjection(target),
      reference: attemptIdentityProjection(reference),
      model: {
        model_id: model.model_id,
        revision: model.revision,
        model_content_sha256: model.model_content_sha256,
      },
      reference_selection: {
        reference_kind: selection.reference_kind,
        status: selection.status,
        policy_version: selection.policy_version,
      },
    },
  };
}

function attemptIdentityProjection(value: AttemptIdentity) {
  return {
    attempt_key: value.attempt_key,
    run_id: value.run_id,
    trace_sha256: value.trace_sha256,
  };
}

function rankingReasonCodes(value: unknown): string[] {
  const reasons = array(value);
  if (!reasons) return [];
  return reasons
    .slice(0, 8)
    .filter((reason): reason is string => typeof reason === "string")
    .map(safeCode);
}

function humanizeReason(value: string): string {
  return boundedText(value.replaceAll("_", " ").replaceAll("-", " "), 100);
}

function safeCode(value: string): string {
  const safe = Array.from(value)
    .map((character) => (/^[\p{L}\p{N}_]$/u.test(character) ? character : "_"))
    .join("");
  return Array.from(safe).slice(0, 80).join("") || "unspecified_limitation";
}

function nonnegativeInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : null;
}

function sameLimitations(
  actual: unknown[],
  expected: Qualification[],
): boolean {
  return (
    actual.length === expected.length &&
    actual.every((value, index) => {
      const limitation = record(value);
      return (
        limitation?.code === expected[index].code &&
        limitation.text === expected[index].text
      );
    })
  );
}

function validRankedRegions(
  values: unknown[],
  omittedRegionCount: unknown,
  comparison: Record<string, unknown>,
  target: LapRecord,
  reference: LapRecord,
  ranking: Record<string, unknown> | null,
  cornerBrief: Record<string, unknown> | null,
): boolean {
  const source = record(ranking?.source);
  const model = record(source?.model);
  const selection = record(source?.reference_selection);
  const candidates = array(ranking?.ranked_candidates);
  const briefRegions = array(cornerBrief?.regions);
  if (
    comparison.comparison_policy !== "time_trial" ||
    !ranking ||
    !source ||
    !model ||
    !selection ||
    !candidates ||
    !cornerBrief ||
    !briefRegions ||
    ranking.schema_version !== 1 ||
    ranking.analysis_version !== rankingVersion ||
    ranking.policy_version !== rankingPolicyVersion ||
    ranking.status !== "ranked" ||
    ranking.coaching_eligible !== false ||
    ranking.omitted_candidate_count !== omittedRegionCount ||
    ranking.omitted_candidate_count !== cornerBrief.omitted_region_count ||
    ranking.gate_reasons_omitted_count !== 0 ||
    !Array.isArray(ranking.gate_reasons) ||
    ranking.gate_reasons.length !== 0 ||
    !partialIdentityMatches(record(source.target), target) ||
    !partialIdentityMatches(record(source.reference), reference) ||
    source.capture_evidence !== "passed_session_best_policy" ||
    !rankingAuthorityMatches(
      comparison,
      target,
      reference,
      source,
      model,
      selection,
    ) ||
    cornerBrief.schema_version !== 1 ||
    cornerBrief.analysis_version !== "corner-comparison-brief-v1" ||
    cornerBrief.status !== "available" ||
    cornerBrief.coaching_eligible !== false ||
    cornerBrief.gate_reasons_omitted_count !== 0 ||
    !Array.isArray(cornerBrief.gate_reasons) ||
    cornerBrief.gate_reasons.length !== 0 ||
    candidates.length !== values.length ||
    briefRegions.length !== values.length ||
    candidates.length === 0 ||
    candidates.length > debriefRegionLimit
  ) {
    return false;
  }

  const seenRegionIds = new Set<string>();
  const seenDisplayRegionIds = new Set<string>();
  let previousDifference = Number.POSITIVE_INFINITY;
  let previousStart = Number.NEGATIVE_INFINITY;
  let previousId = "";
  for (let index = 0; index < values.length; index += 1) {
    const region = record(values[index]);
    const candidate = record(candidates[index]);
    const summary = record(briefRegions[index]);
    const facts = array(summary?.facts);
    const fact = record(facts?.[0]);
    if (
      !region ||
      !candidate ||
      !summary ||
      !facts ||
      !fact ||
      !validRankedRegion(
        region,
        candidate,
        summary,
        fact,
        index + 1,
        comparison,
        target,
        reference,
        model,
        selection,
      )
    ) {
      return false;
    }
    const regionId = candidate.region_id as string;
    const displayRegionId = region.region_id as string;
    const difference = candidate.recorded_time_difference_s as number;
    const start = (candidate.analysis_window_m as number[])[0];
    if (
      seenRegionIds.has(regionId) ||
      seenDisplayRegionIds.has(displayRegionId) ||
      difference > previousDifference ||
      (difference === previousDifference &&
        (start < previousStart ||
          (start === previousStart && regionId < previousId)))
    ) {
      return false;
    }
    seenRegionIds.add(regionId);
    seenDisplayRegionIds.add(displayRegionId);
    previousDifference = difference;
    previousStart = start;
    previousId = regionId;
  }
  return true;
}

function validRankedRegion(
  region: Record<string, unknown>,
  candidate: Record<string, unknown>,
  summary: Record<string, unknown>,
  fact: Record<string, unknown>,
  rank: number,
  comparison: Record<string, unknown>,
  target: LapRecord,
  reference: LapRecord,
  model: Record<string, unknown>,
  selection: Record<string, unknown>,
): boolean {
  const regionWindow = bounds(region.analysis_window_m);
  const candidateWindow = bounds(candidate.analysis_window_m);
  const summaryWindow = bounds(summary.analysis_window_m);
  const difference = finiteNumber(region.recorded_time_difference_s);
  const candidateDifference = finiteNumber(
    candidate.recorded_time_difference_s,
  );
  const support = record(region.connected_support);
  const candidateSupport = record(candidate.connected_support);
  const summarySupport = record(summary.connected_support);
  const factSupport = record(fact.connected_support);
  const factFields = record(fact.source_fields);
  const boundary = record(fact.boundary_delta_evidence);
  const provenance = record(region.provenance);
  const summaryProvenance = record(summary.provenance);
  const factProvenance = record(fact.provenance);
  const sourceFields = record(region.source_fields);
  const track = record(comparison.track);
  const trackLength = finiteNumber(track?.track_length_m);
  if (
    !regionWindow ||
    !candidateWindow ||
    !summaryWindow ||
    difference === null ||
    difference <= 0 ||
    candidateDifference !== difference ||
    !support ||
    !candidateSupport ||
    !summarySupport ||
    !factSupport ||
    !completeSupport(support) ||
    !supportsMatch(support, candidateSupport) ||
    !supportsMatch(support, summarySupport) ||
    !supportsMatch(support, factSupport) ||
    !factFields ||
    !sourceFields ||
    !boundary ||
    !provenance ||
    !summaryProvenance ||
    !factProvenance
  ) {
    return false;
  }
  const start = regionWindow[0];
  const end = regionWindow[1];
  const candidateId = boundedSourceText(candidate.region_id, 256);
  const candidateLabel = boundedSourceText(candidate.region_label, 256);
  const expectedRegionId = candidateId ? boundedText(candidateId, 120) : null;
  const label = candidateLabel ? boundedText(candidateLabel, 120) : "";
  const entryDelta = finiteNumber(support.entry_delta_s);
  const exitDelta = finiteNumber(support.exit_delta_s);
  const factEntry = finiteNumber(boundary.entry_target_minus_reference_s);
  const factExit = finiteNumber(boundary.exit_target_minus_reference_s);
  const debriefProvenanceMatches = regionProvenanceMatches(
    provenance,
    target,
    reference,
    model,
    selection,
    true,
  );
  const summaryProvenanceMatches = regionProvenanceMatches(
    summaryProvenance,
    target,
    reference,
    model,
    selection,
    false,
  );
  const factProvenanceMatches = regionProvenanceMatches(
    factProvenance,
    target,
    reference,
    model,
    selection,
    false,
  );
  return (
    region.rank === rank &&
    candidate.rank === rank &&
    summary.rank === rank &&
    typeof region.region_id === "string" &&
    expectedRegionId !== null &&
    candidateId !== null &&
    candidateLabel !== null &&
    region.region_id.length > 0 &&
    Array.from(region.region_id).length <= 120 &&
    region.region_id === expectedRegionId &&
    region.region_label === label &&
    shortText(region.text, 512) &&
    regionTextMatches(region.text, label, difference, start, end) &&
    sameBounds(regionWindow, candidateWindow) &&
    sameBounds(regionWindow, summaryWindow) &&
    trackLength !== null &&
    trackLength > 0 &&
    start >= 0 &&
    end <= trackLength &&
    candidate.measurement_direction ===
      "target_minus_reference_interval_time_difference" &&
    summary.region_id === candidateId &&
    summary.region_label === candidateLabel &&
    fact.kind === "recorded_interval_time_difference" &&
    fact.unit === "s" &&
    fact.direction === "target_minus_reference" &&
    close(fact.value, difference) &&
    sameBounds(bounds(fact.analysis_window_m), regionWindow) &&
    factFields.entry_delta ===
      "corner_analysis.regions[].delta_change.entry_delta_s" &&
    factFields.exit_delta ===
      "corner_analysis.regions[].delta_change.exit_delta_s" &&
    factFields.derived_difference ===
      "corner_analysis.regions[].delta_change.delta_change_s" &&
    sourceFields.difference ===
      "corner_loss_candidates.ranked_candidates[].recorded_time_difference_s" &&
    sourceFields.window ===
      "corner_loss_candidates.ranked_candidates[].analysis_window_m" &&
    entryDelta !== null &&
    exitDelta !== null &&
    factEntry !== null &&
    factExit !== null &&
    close(exitDelta - entryDelta, difference) &&
    close(factEntry, entryDelta) &&
    close(factExit, exitDelta) &&
    debriefProvenanceMatches &&
    summaryProvenanceMatches &&
    factProvenanceMatches
  );
}

function validApprovedModel(model: Record<string, unknown>): boolean {
  return (
    model.validation_status === "validated" &&
    model.registered === true &&
    model.approved_for_candidate_ranking === true &&
    typeof model.model_id === "string" &&
    model.model_id.length > 0 &&
    model.model_id.length <= 256 &&
    positiveSafeInteger(model.revision) !== null &&
    isSha256(model.model_content_sha256) &&
    record(model.approval_provenance) !== null
  );
}

function validSessionBestSelection(
  selection: Record<string, unknown>,
  reference: LapRecord,
): boolean {
  const selected = record(selection.selected_reference);
  return Boolean(
    selection.reference_kind === "session_best" &&
    selection.status === "selected" &&
    selection.policy_version === referencePolicyVersion &&
    Array.isArray(selection.reasons) &&
    selection.reasons.length === 0 &&
    selected &&
    selected.attempt_key === reference.attempt_key &&
    selected.trace_sha256 === reference.trace_sha256,
  );
}

function regionProvenanceMatches(
  value: Record<string, unknown>,
  target: LapRecord,
  reference: LapRecord,
  model: Record<string, unknown>,
  selection: Record<string, unknown>,
  selectionIsProjection: boolean,
): boolean {
  const targetValue = record(value.target);
  const referenceValue = record(value.reference);
  const modelValue = record(value.model);
  return Boolean(
    partialIdentityMatches(targetValue, target) &&
    partialIdentityMatches(referenceValue, reference) &&
    modelValue &&
    modelValue.model_id === model.model_id &&
    modelValue.revision === model.revision &&
    modelValue.model_content_sha256 === model.model_content_sha256 &&
    referenceSelectionMatches(
      value.reference_selection,
      selection,
      selectionIsProjection,
    ),
  );
}

function referenceSelectionMatches(
  actualValue: unknown,
  expectedValue: Record<string, unknown>,
  projectionOnly: boolean,
): boolean {
  const actual = record(actualValue);
  if (!actual) return false;
  if (!projectionOnly) return sameBoundedJson(actual, expectedValue);
  return (
    actual.reference_kind === expectedValue.reference_kind &&
    actual.status === expectedValue.status &&
    actual.policy_version === expectedValue.policy_version &&
    Object.keys(actual).length === 3
  );
}

function sameBoundedJson(left: unknown, right: unknown): boolean {
  let remainingNodes = 2_048;
  function visit(a: unknown, b: unknown, depth: number): boolean {
    remainingNodes -= 1;
    if (remainingNodes < 0 || depth > 10) return false;
    if (a === b) return true;
    if (typeof a !== typeof b || a === null || b === null) return false;
    if (typeof a === "number") {
      return Number.isFinite(a) && Number.isFinite(b) && a === b;
    }
    if (typeof a === "string") {
      return (
        typeof b === "string" &&
        a.length <= 4_096 &&
        b.length <= 4_096 &&
        a === b
      );
    }
    if (typeof a === "boolean") return a === b;
    if (Array.isArray(a) || Array.isArray(b)) {
      if (
        !Array.isArray(a) ||
        !Array.isArray(b) ||
        a.length !== b.length ||
        a.length > 128
      ) {
        return false;
      }
      return a.every((entry, index) => visit(entry, b[index], depth + 1));
    }
    const objectA = record(a);
    const objectB = record(b);
    if (!objectA || !objectB) return false;
    const keysA = Object.keys(objectA).sort();
    const keysB = Object.keys(objectB).sort();
    return (
      keysA.length === keysB.length &&
      keysA.length <= 128 &&
      keysA.every(
        (key, index) =>
          key.length <= 128 &&
          key === keysB[index] &&
          visit(objectA[key], objectB[key], depth + 1),
      )
    );
  }
  return visit(left, right, 0);
}

function partialIdentityMatches(
  actual: Record<string, unknown> | null,
  expected: AttemptIdentity,
): boolean {
  return Boolean(
    actual &&
    actual.attempt_key === expected.attempt_key &&
    actual.run_id === expected.run_id &&
    actual.trace_sha256 === expected.trace_sha256,
  );
}

function completeSupport(value: Record<string, unknown>): boolean {
  return (
    supportFlags.every((key) => value[key] === true) &&
    [
      value.target_time_coverage,
      value.reference_time_coverage,
      value.shared_time_coverage,
    ].every((coverage) => {
      const number = finiteNumber(coverage);
      return number !== null && number >= 0 && number <= 1;
    }) &&
    finiteNumber(value.entry_delta_s) !== null &&
    finiteNumber(value.exit_delta_s) !== null
  );
}

function supportsMatch(
  left: Record<string, unknown>,
  right: Record<string, unknown>,
): boolean {
  return supportValues.every((key) => left[key] === right[key]);
}

function expectedDebriefStatus(
  hasOfficialFact: boolean,
  regionCount: number,
  limitationCount: number,
  omittedRegionCount: number,
): string {
  if (!hasOfficialFact && regionCount === 0) return "abstained";
  if (
    limitationCount > 0 ||
    !hasOfficialFact ||
    regionCount === 0 ||
    omittedRegionCount > 0
  ) {
    return "partial";
  }
  return "available";
}

function canonicalDebriefText(
  officialValue: unknown,
  regions: unknown[],
  limitations: unknown[],
  omittedLimitationCount: number,
): string {
  const parts: string[] = [];
  const official = record(officialValue);
  if (official) parts.push(official.text as string);
  if (regions.length > 0) {
    const regionTexts = regions.map((value) => record(value)?.text as string);
    parts.push(
      `Largest authorized recorded-region differences: ${regionTexts.join(" ")}`,
    );
  } else if (
    !limitations.some((value) => {
      const code = record(value)?.code;
      return typeof code === "string" && code.startsWith("ranked_regions_");
    })
  ) {
    parts.push("No ranked region measurements qualified for this comparison.");
  }
  parts.push(
    "These are recorded measurements; they do not establish causes or recommend driving changes.",
  );
  if (limitations.length > 0) {
    const texts = limitations.map((value) => {
      const text = record(value)?.text as string;
      return text.replace(/\.+$/, "");
    });
    parts.push(`Limitations: ${texts.join("; ")}.`);
  }
  if (omittedLimitationCount > 0) {
    parts.push(
      "Additional limitation details were omitted by the configured bounds.",
    );
  }
  return boundedText(parts.join(" "), debriefTextLimit);
}

function officialFactText(value: number): string {
  if (value > 0) {
    return `The target lap was ${value.toFixed(3)} s slower by official lap time.`;
  }
  if (value < 0) {
    return `The target lap was ${Math.abs(value).toFixed(3)} s faster by official lap time.`;
  }
  return "The target and reference had equal official lap times to 0.001 s.";
}

function bounds(value: unknown): [number, number] | null {
  const values = array(value);
  if (!values || values.length !== 2) return null;
  const start = finiteNumber(values[0]);
  const end = finiteNumber(values[1]);
  return start !== null && end !== null && end > start ? [start, end] : null;
}

function sameBounds(
  left: [number, number] | null,
  right: [number, number] | null,
): boolean {
  return Boolean(left && right && left[0] === right[0] && left[1] === right[1]);
}

function boundedText(value: string, maximum: number): string {
  const normalized = normalizeWhitespace(value);
  const characters = Array.from(normalized);
  if (characters.length <= maximum) return normalized;
  return `${characters
    .slice(0, maximum - 1)
    .join("")
    .trimEnd()}…`;
}

function boundedSourceText(value: unknown, maximum: number): string | null {
  return typeof value === "string" &&
    value.length > 0 &&
    Array.from(value).length <= maximum
    ? value
    : null;
}

function regionTextMatches(
  value: string,
  label: string,
  difference: number,
  start: number,
  end: number,
): boolean {
  const prefix = `${label} recorded `;
  const numeric = "[+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?";
  const suffix = " s more interval time across ";
  if (!value.startsWith(prefix)) return false;
  const suffixIndex = value.indexOf(suffix, prefix.length);
  if (suffixIndex < 0) return false;
  const displayedDifference = value.slice(prefix.length, suffixIndex);
  if (!/^\d+\.\d{3}$/.test(displayedDifference)) return false;
  const windowMatch = value
    .slice(suffixIndex + suffix.length)
    .match(new RegExp(`^\\[(${numeric}), (${numeric})\\) m\\.$`));
  if (!windowMatch) return false;
  const shownDifference = Number(displayedDifference);
  const shownStart = Number(windowMatch[1]);
  const shownEnd = Number(windowMatch[2]);
  return (
    Math.abs(shownDifference - difference) <= 0.000500001 &&
    roundedGeneralValueMatches(shownStart, start) &&
    roundedGeneralValueMatches(shownEnd, end)
  );
}

function roundedGeneralValueMatches(shown: number, exact: number): boolean {
  if (!Number.isFinite(shown) || !Number.isFinite(exact)) return false;
  if (exact === 0) return shown === 0;
  const exponent = Math.floor(Math.log10(Math.abs(exact)));
  const halfUnit = 0.5 * 10 ** (exponent - 5);
  const floatingTolerance = Number.EPSILON * Math.max(1, Math.abs(exact)) * 2;
  return Math.abs(shown - exact) <= halfUnit + floatingTolerance;
}

function normalizeWhitespace(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

function shortText(value: unknown, maximum: number): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    Array.from(value).length <= maximum
  );
}

function count(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

function positiveSafeInteger(value: unknown): number | null {
  return Number.isSafeInteger(value) && (value as number) > 0
    ? (value as number)
    : null;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function close(left: unknown, right: unknown): boolean {
  const a = finiteNumber(left);
  const b = finiteNumber(right);
  return a !== null && b !== null && Math.abs(a - b) <= 1e-9;
}

function isSha256(value: unknown): boolean {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function array(value: unknown): unknown[] | null {
  return Array.isArray(value) ? value : null;
}
