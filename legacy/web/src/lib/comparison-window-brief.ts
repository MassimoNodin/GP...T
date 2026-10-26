import type {
  Comparison,
  DistanceWindowBrief,
  LapRecord,
} from "@/lib/api";

export function distanceWindowBriefMatches(
  brief: DistanceWindowBrief,
  comparison: Comparison,
  target: LapRecord | null,
  reference: LapRecord | null,
  window: [number, number] | null,
) {
  if (!target || !reference || !window) return false;
  const candidate = brief as unknown as Record<string, unknown>;
  const comparisonTarget = asRecord(comparison.target);
  const comparisonReference = asRecord(comparison.reference);
  const facts = candidate.facts;
  const limitations = candidate.limitations;
  const warnings = candidate.warnings;
  const provenanceMatches = (value: unknown) =>
    briefProvenanceMatches(value, comparison, target, reference, window);
  return (
    candidate.schema_version === 1 &&
    candidate.artifact_kind === "distance_window_brief" &&
    (candidate.status === "available" || candidate.status === "unavailable") &&
    candidate.diagnostic_only === true &&
    candidate.coaching_eligible === false &&
    typeof candidate.analysis_version === "string" &&
    typeof candidate.text === "string" &&
    Array.isArray(facts) &&
    facts.length <= 5 &&
    facts.every((fact) => isDistanceWindowBriefFact(fact, provenanceMatches)) &&
    Array.isArray(limitations) &&
    limitations.length <= 8 &&
    limitations.every(isBriefNote) &&
    Array.isArray(warnings) &&
    warnings.length <= 8 &&
    warnings.every(isBriefNote) &&
    comparisonTarget?.attempt_key === target.attempt_key &&
    comparisonTarget?.run_id === target.run_id &&
    comparisonTarget?.trace_sha256 === target.trace_sha256 &&
    comparisonReference?.attempt_key === reference.attempt_key &&
    comparisonReference?.run_id === reference.run_id &&
    comparisonReference?.trace_sha256 === reference.trace_sha256 &&
    provenanceMatches(candidate.provenance) &&
    windowMatches(candidate.window_m, window)
  );
}

export function distanceBracket(distance: number, bracket: number[] | null) {
  if (
    bracket &&
    bracket.length === 2 &&
    bracket.every(Number.isFinite) &&
    bracket[0] <= bracket[1]
  ) {
    const low = Math.floor(bracket[0] * 10 + 1e-12) / 10;
    const high = Math.ceil(bracket[1] * 10 - 1e-12) / 10;
    return `${low.toFixed(1)}–${high.toFixed(1)} m bracket`;
  }
  return `${distance.toFixed(1)} m; unbracketed`;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function isDistanceWindowBriefFact(
  value: unknown,
  provenanceMatches: (value: unknown) => boolean,
) {
  const fact = asRecord(value);
  const sourceFields = asRecord(fact?.source_fields);
  return Boolean(
    fact &&
      typeof fact.kind === "string" &&
      typeof fact.text === "string" &&
      sourceFields &&
      Object.values(sourceFields).every((field) => typeof field === "string") &&
      provenanceMatches(fact.provenance),
  );
}

function briefProvenanceMatches(
  value: unknown,
  comparison: Comparison,
  target: LapRecord,
  reference: LapRecord,
  window: [number, number],
) {
  const provenance = asRecord(value);
  const targetProvenance = asRecord(provenance?.target);
  const referenceProvenance = asRecord(provenance?.reference);
  return (
    provenance?.comparison_policy === comparison.comparison_policy &&
    targetProvenance?.attempt_key === target.attempt_key &&
    targetProvenance?.run_id === target.run_id &&
    targetProvenance?.trace_sha256 === target.trace_sha256 &&
    referenceProvenance?.attempt_key === reference.attempt_key &&
    referenceProvenance?.run_id === reference.run_id &&
    referenceProvenance?.trace_sha256 === reference.trace_sha256 &&
    windowMatches(provenance?.window_m, window)
  );
}

function isBriefNote(value: unknown) {
  const note = asRecord(value);
  return Boolean(note && typeof note.code === "string" && typeof note.text === "string");
}

function windowMatches(value: unknown, expected: [number, number]) {
  const bounds = asRecord(value);
  return (
    closeEnough(bounds?.start_m, expected[0]) &&
    closeEnough(bounds?.end_m, expected[1])
  );
}

function closeEnough(value: unknown, expected: number) {
  return typeof value === "number" && Number.isFinite(value) && Math.abs(value - expected) <= 1e-9;
}
