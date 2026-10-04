import type { EngineerQueryReport, LapDebrief } from "@/lib/api";

export const RECORDED_SPEECH_PLAN_VERSION =
  "recorded-evidence-speech-v1" as const;
export const RECORDED_SPEECH_MAX_CHARACTERS = 5_000;
export const RECORDED_SPEECH_MAX_CHUNKS = 24;
export const RECORDED_SPEECH_MAX_CHUNK_CHARACTERS = 240;
const MAX_SOURCE_IDENTITY_CHARACTERS = 64_000;
const MAX_SOURCE_IDENTITY_NODES = 8_192;
const MAX_SOURCE_IDENTITY_DEPTH = 16;

export type RecordedSpeechSource =
  | { kind: "engineer_query"; report: EngineerQueryReport }
  | {
      kind: "lap_debrief";
      report: LapDebrief;
      targetAttemptKey: string;
      referenceAttemptKey: string;
    };

export interface RecordedSpeechPlan {
  version: typeof RECORDED_SPEECH_PLAN_VERSION;
  source_key: string;
  chunks: string[];
  character_count: number;
}

export type RecordedSpeechPlanResult =
  { ok: true; plan: RecordedSpeechPlan } | { ok: false; reason: string };

export function buildRecordedSpeechPlan(
  source: RecordedSpeechSource,
): RecordedSpeechPlanResult {
  try {
    return buildRecordedSpeechPlanInternal(source);
  } catch {
    return {
      ok: false,
      reason: "The displayed report contains malformed or unsupported data.",
    };
  }
}

function buildRecordedSpeechPlanInternal(
  source: RecordedSpeechSource,
): RecordedSpeechPlanResult {
  if (
    !source ||
    (source.kind !== "engineer_query" && source.kind !== "lap_debrief")
  ) {
    return { ok: false, reason: "The displayed report type is unsupported." };
  }
  const segments =
    source.kind === "engineer_query"
      ? engineerQuerySegments(source.report)
      : lapDebriefSegments(source);
  if (!segments.ok) return segments;

  const chunks: string[] = [];
  for (const segment of segments.value) {
    const split = splitSpeechSegment(segment);
    if (!split) {
      return {
        ok: false,
        reason: "A report field cannot fit within the speech limits.",
      };
    }
    chunks.push(...split);
    if (chunks.length > RECORDED_SPEECH_MAX_CHUNKS) {
      return { ok: false, reason: "This report exceeds the utterance limit." };
    }
  }

  const characterCount = chunks.join(" ").length;
  if (characterCount > RECORDED_SPEECH_MAX_CHARACTERS) {
    return {
      ok: false,
      reason: "This report exceeds the speech length limit.",
    };
  }
  if (chunks.length === 0) {
    return {
      ok: false,
      reason: "This report has no supported speech content.",
    };
  }

  const selectionKey =
    source.kind === "engineer_query"
      ? `query:${source.report.intent}:${source.report.selected.target_attempt_key}:${source.report.selected.reference_attempt_key ?? ""}:${source.report.selected.region_identifier ?? ""}:${source.report.status}`
      : `debrief:${source.targetAttemptKey}:${source.referenceAttemptKey}:${source.report.analysis_version}:${source.report.status}`;
  const sourceIdentity = stableJson(source.report);
  if (sourceIdentity === null) {
    return {
      ok: false,
      reason:
        "The displayed report identity is malformed or exceeds its limits.",
    };
  }
  const sourceKey = `${selectionKey}:${sourceIdentity}:${JSON.stringify(chunks)}`;

  return {
    ok: true,
    plan: {
      version: RECORDED_SPEECH_PLAN_VERSION,
      source_key: sourceKey,
      chunks,
      character_count: characterCount,
    },
  };
}

type SegmentsResult =
  { ok: true; value: string[] } | { ok: false; reason: string };

function engineerQuerySegments(report: EngineerQueryReport): SegmentsResult {
  if (
    report?.schema_version !== 1 ||
    report.analysis_version !== "engineer-query-v1" ||
    report.artifact_kind !== "engineer_query" ||
    (report.status !== "available" &&
      report.status !== "partial" &&
      report.status !== "unavailable") ||
    report.diagnostic_only !== true ||
    report.coaching_eligible !== false ||
    report.ranking_eligible !== false ||
    !isText(report.selected?.target_attempt_key, 256) ||
    !Array.isArray(report.facts) ||
    report.facts.length > 6 ||
    !Array.isArray(report.warnings) ||
    report.warnings.length > 8 ||
    !isCount(report.omitted_fact_count) ||
    !isCount(report.omitted_warning_count) ||
    !Array.isArray(report.reason_codes) ||
    report.reason_codes.length > 16
  ) {
    return {
      ok: false,
      reason: "The displayed engineer report is incomplete or unsupported.",
    };
  }

  if (
    (report.selected.reference_attempt_key !== undefined &&
      !isText(report.selected.reference_attempt_key, 256)) ||
    (report.selected.region_identifier !== undefined &&
      !isText(report.selected.region_identifier, 256)) ||
    (report.selected.comparison_policy !== undefined &&
      report.selected.comparison_policy !== "time_trial" &&
      report.selected.comparison_policy !== "practice_qualifying")
  ) {
    return {
      ok: false,
      reason: "The selected engineer report contains malformed selection data.",
    };
  }

  if (
    report.intent === "region_comparison" &&
    (!isText(report.selected.reference_attempt_key, 256) ||
      !isText(report.selected.region_identifier, 256))
  ) {
    return {
      ok: false,
      reason: "The selected region report is missing its explicit comparison.",
    };
  }
  if (
    report.intent !== "attempt_summary" &&
    report.intent !== "region_comparison"
  ) {
    return {
      ok: false,
      reason: "The displayed engineer intent is unsupported.",
    };
  }

  const segments = [
    `Selected target attempt ${report.selected.target_attempt_key}.`,
  ];
  if (report.selected.reference_attempt_key) {
    segments.push(
      `Selected reference attempt ${report.selected.reference_attempt_key}.`,
    );
  }
  if (report.selected.region_identifier) {
    segments.push(`Selected region ${report.selected.region_identifier}.`);
  }
  if (report.selected.comparison_policy) {
    segments.push(
      `Comparison policy ${report.selected.comparison_policy.replaceAll("_", " ")}.`,
    );
  }

  segments.push(statusQualification(report.status));
  segments.push(
    "Recorded evidence only. This report does not provide coaching or rank a driving change.",
  );

  if (report.reason_codes.length > 0) {
    const reasons = report.reason_codes.map((reason) => safeText(reason, 120));
    if (reasons.some((reason) => reason === null)) {
      return {
        ok: false,
        reason: "The report contains an unsupported reason code.",
      };
    }
    segments.push(
      `Unavailable evidence reasons: ${reasons.join(", ").replaceAll("_", " ")}.`,
    );
  }

  for (const warning of report.warnings) {
    if (
      !warning ||
      !isText(warning.code, 120) ||
      !isText(warning.text, RECORDED_SPEECH_MAX_CHARACTERS) ||
      !Array.isArray(warning.source_fields) ||
      warning.source_fields.length > 32 ||
      warning.source_fields.some((field) => !isText(field, 120)) ||
      (warning.sides !== undefined &&
        (!Array.isArray(warning.sides) ||
          warning.sides.length > 2 ||
          warning.sides.some(
            (side) => side !== "target" && side !== "reference",
          ) ||
          new Set(warning.sides).size !== warning.sides.length))
    ) {
      return {
        ok: false,
        reason: "The report contains an unsupported qualification.",
      };
    }
    const sides = warning.sides?.length
      ? `${warning.sides.map(capitalizeSide).join(" and ")} qualification: `
      : "Qualification: ";
    segments.push(`${sides}${warning.text}`);
  }
  if (report.omitted_warning_count > 0) {
    segments.push(
      `${report.omitted_warning_count} additional qualifications were omitted by the report limits.`,
    );
  }
  if (report.omitted_fact_count > 0) {
    segments.push(
      `${report.omitted_fact_count} additional measured facts were omitted by the report limits.`,
    );
  }

  for (const fact of report.facts) {
    if (
      !fact ||
      !isText(fact.kind, 120) ||
      !isText(fact.text, RECORDED_SPEECH_MAX_CHARACTERS) ||
      !Array.isArray(fact.source_fields) ||
      fact.source_fields.length > 32 ||
      fact.source_fields.some((field) => !isText(field, 120))
    ) {
      return {
        ok: false,
        reason: "The report contains an unsupported measured fact.",
      };
    }
    segments.push(`${fact.kind.replaceAll("_", " ")}: ${fact.text}`);
  }

  return { ok: true, value: segments };
}

function lapDebriefSegments(
  source: Extract<RecordedSpeechSource, { kind: "lap_debrief" }>,
): SegmentsResult {
  const { report } = source;
  if (
    !isText(source.targetAttemptKey, 256) ||
    !isText(source.referenceAttemptKey, 256) ||
    report?.schema_version !== 1 ||
    report.analysis_version !== "lap-debrief-v1" ||
    (report.status !== "available" &&
      report.status !== "partial" &&
      report.status !== "abstained") ||
    (report.comparison_policy !== null &&
      report.comparison_policy !== "time_trial" &&
      report.comparison_policy !== "practice_qualifying") ||
    report.diagnostic_only !== true ||
    report.coaching_eligible !== false ||
    !isText(report.text, 2_400) ||
    !Array.isArray(report.limitations) ||
    report.limitations.length > 8 ||
    !Array.isArray(report.ranked_regions) ||
    report.ranked_regions.length > 3 ||
    !isCount(report.omitted_region_count) ||
    !isCount(report.omitted_limitation_count)
  ) {
    return {
      ok: false,
      reason: "The displayed lap debrief is incomplete or unsupported.",
    };
  }

  const segments = [
    `Selected target attempt ${source.targetAttemptKey}.`,
    `Selected reference attempt ${source.referenceAttemptKey}.`,
    ...(report.comparison_policy
      ? [`Comparison policy ${report.comparison_policy.replaceAll("_", " ")}.`]
      : []),
    statusQualification(report.status),
    "Recorded measurements only. They do not establish causes or recommend driving changes.",
  ];

  for (const limitation of report.limitations) {
    if (
      !limitation ||
      !isText(limitation.code, 120) ||
      !isText(limitation.text, RECORDED_SPEECH_MAX_CHARACTERS)
    ) {
      return {
        ok: false,
        reason: "The lap debrief contains an unsupported qualification.",
      };
    }
    segments.push(`Qualification: ${limitation.text}`);
  }
  if (report.omitted_limitation_count > 0) {
    segments.push(
      `${report.omitted_limitation_count} additional limitations were omitted by the report limits.`,
    );
  }
  if (report.omitted_region_count > 0) {
    segments.push(
      `${report.omitted_region_count} additional ranked regions were omitted by the report limits.`,
    );
  }

  if (report.official_lap_time !== null) {
    if (
      !report.official_lap_time ||
      !Number.isFinite(report.official_lap_time.value_s) ||
      report.official_lap_time.direction !== "target_minus_reference" ||
      !isText(report.official_lap_time.text, RECORDED_SPEECH_MAX_CHARACTERS)
    ) {
      return {
        ok: false,
        reason: "The lap debrief contains an unsupported lap-time fact.",
      };
    }
    segments.push(report.official_lap_time.text);
  } else {
    segments.push(
      "A consistent official lap-time difference was not verified.",
    );
  }

  for (const region of report.ranked_regions) {
    if (
      !region ||
      !Number.isInteger(region.rank) ||
      region.rank < 1 ||
      region.rank > 3 ||
      !isText(region.region_id, 256) ||
      !isText(region.text, RECORDED_SPEECH_MAX_CHARACTERS)
    ) {
      return {
        ok: false,
        reason: "The lap debrief contains an unsupported ranked-region fact.",
      };
    }
    segments.push(
      `Recorded region ${region.rank}, ${region.region_id}: ${region.text}`,
    );
  }
  if (report.ranked_regions.length === 0) {
    segments.push(
      "No ranked region measurements qualified for this comparison.",
    );
  }

  return { ok: true, value: segments };
}

function splitSpeechSegment(value: string): string[] | null {
  if (!isText(value, RECORDED_SPEECH_MAX_CHARACTERS)) return null;
  const words = value.trim().split(/\s+/);
  const chunks: string[] = [];
  let current = "";
  for (const word of words) {
    if (word.length > RECORDED_SPEECH_MAX_CHUNK_CHARACTERS) return null;
    const candidate = current ? `${current} ${word}` : word;
    if (candidate.length > RECORDED_SPEECH_MAX_CHUNK_CHARACTERS) {
      chunks.push(current);
      current = word;
    } else {
      current = candidate;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

function statusQualification(
  status: "available" | "partial" | "unavailable" | "abstained",
): string {
  switch (status) {
    case "available":
      return "Evidence status: available.";
    case "partial":
      return "Evidence status: partial; some measurements or qualifications may be unavailable.";
    case "unavailable":
      return "Evidence status: unavailable for the selected request.";
    case "abstained":
      return "Evidence status: abstained because the required gates did not pass.";
  }
}

function isCount(value: unknown): value is number {
  return Number.isInteger(value) && typeof value === "number" && value >= 0;
}

function isText(value: unknown, maximumLength: number): value is string {
  return safeText(value, maximumLength) !== null;
}

function safeText(value: unknown, maximumLength: number): string | null {
  return typeof value === "string" &&
    value.trim().length > 0 &&
    value.length <= maximumLength
    ? value.trim()
    : null;
}

function capitalizeSide(side: string): string {
  return side === "target" ? "Target" : "Reference";
}

function stableJson(value: unknown): string | null {
  let nodeCount = 0;
  let characterCount = 0;
  const ancestors = new Set<object>();

  const append = (piece: string): string => {
    characterCount += piece.length;
    if (characterCount > MAX_SOURCE_IDENTITY_CHARACTERS) {
      throw new Error("source identity exceeds its size limit");
    }
    return piece;
  };

  const serialize = (entry: unknown, depth: number): string => {
    nodeCount += 1;
    if (
      nodeCount > MAX_SOURCE_IDENTITY_NODES ||
      depth > MAX_SOURCE_IDENTITY_DEPTH
    ) {
      throw new Error("source identity exceeds its structural limit");
    }
    if (entry === null) return append("null");
    if (typeof entry === "string") {
      if (entry.length > MAX_SOURCE_IDENTITY_CHARACTERS) {
        throw new Error("source identity string exceeds its limit");
      }
      return append(JSON.stringify(entry));
    }
    if (typeof entry === "boolean") return append(entry ? "true" : "false");
    if (typeof entry === "number") {
      if (!Number.isFinite(entry))
        throw new Error("non-finite identity number");
      return append(JSON.stringify(entry));
    }
    if (typeof entry !== "object")
      throw new Error("unsupported identity value");
    if (ancestors.has(entry)) throw new Error("cyclic source identity");
    ancestors.add(entry);
    try {
      if (Array.isArray(entry)) {
        if (entry.length > MAX_SOURCE_IDENTITY_NODES) {
          throw new Error("source identity array exceeds its limit");
        }
        const parts = [append("[")];
        entry.forEach((child, index) => {
          if (index > 0) parts.push(append(","));
          parts.push(serialize(child, depth + 1));
        });
        parts.push(append("]"));
        return parts.join("");
      }
      const prototype = Object.getPrototypeOf(entry);
      if (prototype !== Object.prototype && prototype !== null) {
        throw new Error("unsupported source identity object");
      }
      const keys = Object.keys(entry).sort();
      if (keys.length > MAX_SOURCE_IDENTITY_NODES) {
        throw new Error("source identity object exceeds its limit");
      }
      const parts = [append("{")];
      keys.forEach((key, index) => {
        if (key.length > MAX_SOURCE_IDENTITY_CHARACTERS) {
          throw new Error("source identity key exceeds its limit");
        }
        if (index > 0) parts.push(append(","));
        parts.push(append(JSON.stringify(key)), append(":"));
        parts.push(
          serialize((entry as Record<string, unknown>)[key], depth + 1),
        );
      });
      parts.push(append("}"));
      return parts.join("");
    } finally {
      ancestors.delete(entry);
    }
  };

  try {
    return serialize(value, 0);
  } catch {
    return null;
  }
}
