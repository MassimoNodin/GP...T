import type { LapRecord } from "@/lib/api";

type ComparisonAttemptIdentity = Pick<
  LapRecord,
  | "attempt_key"
  | "run_id"
  | "session_uid"
  | "car_index"
  | "trace_sha256"
  | "trace_schema_version"
>;

export function comparisonReportMatchesAttempts(
  value: unknown,
  target: ComparisonAttemptIdentity,
  reference: ComparisonAttemptIdentity,
): boolean {
  const report = record(value);
  return Boolean(
    report &&
    matchesSide(report.target, target) &&
    matchesSide(report.reference, reference),
  );
}

function matchesSide(value: unknown, expected: ComparisonAttemptIdentity) {
  const actual = record(value);
  return Boolean(
    actual &&
    actual.attempt_key === expected.attempt_key &&
    actual.run_id === expected.run_id &&
    actual.session_uid === expected.session_uid &&
    actual.car_index === expected.car_index &&
    actual.trace_sha256 === expected.trace_sha256 &&
    actual.trace_schema_version === expected.trace_schema_version,
  );
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
