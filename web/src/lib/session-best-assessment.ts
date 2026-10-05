import type { LapRecord, SessionBestOverview } from "@/lib/api";

const SESSION_BEST_POLICY_VERSION = "run-session-player-best-v1";
const MAX_SCOPE_ATTEMPTS = 256;
const MAX_CANDIDATE_ROWS = 32;
const MAX_TEXT_LENGTH = 256;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;

export type LapOrderAssessmentState = {
  requested: boolean;
  anchorAttempt: LapRecord | null;
  report: SessionBestOverview | null;
  unavailableReason: string | null;
};

type LapOrderAnchor = Pick<
  LapRecord,
  | "attempt_key"
  | "run_id"
  | "session_uid"
  | "car_index"
  | "attempt_number"
  | "context"
>;

export function resolveLapOrderAnchor(
  value: unknown,
  attemptKey: string,
  runId: string,
  sessionUid: string,
): LapRecord | null {
  const page = record(value);
  if (
    !page ||
    page.run_id !== runId ||
    page.session_uid !== sessionUid ||
    !Array.isArray(page.items) ||
    page.items.length > 1 ||
    !page.items.every((entry) => {
      const attempt = record(entry);
      return Boolean(
        attempt &&
          typeof attempt.attempt_key === "string" &&
          attempt.run_id === runId &&
          attempt.session_uid === sessionUid,
      );
    }) ||
    !Array.isArray(page.selected_attempts) ||
    page.selected_attempts.length !== 1
  ) {
    return null;
  }
  const selection = record(page.selected_attempts[0]);
  const selectedAttempt = record(selection?.attempt);
  if (
    !selection ||
    selection.requested_attempt_key !== attemptKey ||
    !selectedAttempt ||
    selectedAttempt.attempt_key !== attemptKey ||
    selectedAttempt.run_id !== runId ||
    selectedAttempt.session_uid !== sessionUid ||
    !boundedIndex(selectedAttempt.car_index) ||
    !boundedPositiveCount(selectedAttempt.attempt_number, 100_000) ||
    !attemptKeyMatches(
      selectedAttempt.attempt_key,
      selectedAttempt.attempt_number,
      selectedAttempt.run_id,
      selectedAttempt.session_uid,
      selectedAttempt.car_index,
    ) ||
    !validOptionalContext(selectedAttempt.context)
  ) {
    return null;
  }
  return selectedAttempt as unknown as LapRecord;
}

export function sessionBestOverviewMatchesAnchor(
  value: unknown,
  anchor: LapOrderAnchor,
): value is SessionBestOverview {
  const report = record(value);
  const scope = record(report?.scope);
  const ordering = record(report?.recorded_time_ordering);
  const timeTrialBest = record(report?.eligible_time_trial_best);
  const limits = record(report?.limits);
  if (
    !report ||
    report.policy_version !== SESSION_BEST_POLICY_VERSION ||
    report.anchor_attempt_key !== anchor.attempt_key ||
    !isOneOf(report.status, ["assessed", "abstained", "anchor_unavailable"]) ||
    !ordering ||
    !timeTrialBest ||
    !limits ||
    Object.keys(limits).length > 16 ||
    !safeLimits(limits) ||
    !Array.isArray(report.candidates) ||
    report.candidates.length > MAX_CANDIDATE_ROWS ||
    !safeCandidates(
      report.candidates,
      report.candidates.length,
      anchor,
      ordering,
    ) ||
    !Array.isArray(report.reasons) ||
    report.reasons.length > 64 ||
    !report.reasons.every((reason) => boundedText(reason, 128)) ||
    !safeOrdering(ordering, limits, report.candidates.length) ||
    !safeEligibleBest(timeTrialBest, anchor, report)
  ) {
    return false;
  }
  if (scope) {
    if (
      scope.type !== "same_run_session_player" ||
      scope.run_id !== anchor.run_id ||
      scope.session_uid !== anchor.session_uid ||
      scope.car_index !== anchor.car_index ||
      scope.context_anchor_attempt_key !== anchor.attempt_key
    ) {
      return false;
    }
    return ordering.status === "unavailable"
      ? report.status === "anchor_unavailable" &&
          unavailableOutcomeMatches(ordering, timeTrialBest, report)
      : report.status !== "anchor_unavailable";
  }
  if (report.scope !== null) return false;
  if (report.status === "anchor_unavailable") {
    return unavailableOutcomeMatches(ordering, timeTrialBest, report);
  }
  return (
    report.status === "abstained" &&
    unavailableOutcomeMatches(ordering, timeTrialBest, report) &&
    (report.reasons as unknown[]).some(
      (reason) =>
        typeof reason === "string" &&
        /^candidate_(scope_attempts|prior_attempts|context_segments|context_bytes)_limit_exceeded$/.test(
          reason,
        ),
    )
  );
}

function unavailableOutcomeMatches(
  ordering: Record<string, unknown>,
  best: Record<string, unknown>,
  report: Record<string, unknown>,
) {
  return (
    ordering.status === "unavailable" &&
    ordering.candidate_count === null &&
    ordering.timed_candidate_count === null &&
    ordering.candidates_omitted_count === null &&
    Array.isArray(report.candidates) &&
    report.candidates.length === 0 &&
    best.attempt === null &&
    (report.status === "abstained"
      ? best.status === "assessment_limit_exceeded"
      : best.status === "anchor_unavailable")
  );
}

function safeLimits(limits: Record<string, unknown>) {
  return (
    Object.values(limits).every(
      (value) => boundedCount(value, 100_000_000) && value > 0,
    ) &&
    boundedCount(limits.scope_attempts, MAX_SCOPE_ATTEMPTS) &&
    boundedCount(limits.candidate_rows, MAX_CANDIDATE_ROWS)
  );
}

function safeOrdering(
  ordering: Record<string, unknown>,
  limits: Record<string, unknown>,
  candidateLength: number,
) {
  if (
    ordering.diagnostic_only !== true ||
    ordering.sort !== "reported_lap_time_ms_ascending_then_attempt_number"
  ) {
    return false;
  }
  if (
    ordering.candidate_count === null ||
    ordering.timed_candidate_count === null ||
    ordering.candidates_omitted_count === null
  ) {
    return (
      ordering.status === "unavailable" &&
      ordering.candidate_count === null &&
      ordering.timed_candidate_count === null &&
      ordering.candidates_omitted_count === null &&
      candidateLength === 0
    );
  }
  const candidateCount = ordering.candidate_count;
  const timedCount = ordering.timed_candidate_count;
  const omittedCount = ordering.candidates_omitted_count;
  if (
    !boundedCount(candidateCount, MAX_SCOPE_ATTEMPTS) ||
    !boundedCount(timedCount, candidateCount) ||
    !boundedCount(omittedCount, MAX_SCOPE_ATTEMPTS) ||
    candidateCount > (limits.scope_attempts as number) ||
    candidateLength > (limits.candidate_rows as number) ||
    candidateLength !== Math.min(candidateCount, limits.candidate_rows as number) ||
    omittedCount !== candidateCount - candidateLength
  ) {
    return false;
  }
  return ordering.status === (timedCount > 0 ? "available" : "no_recorded_times");
}

function safeCandidates(
  value: unknown[],
  candidateLength: number,
  anchor: LapOrderAnchor,
  ordering: Record<string, unknown>,
) {
  if (value.length !== candidateLength) return false;
  const keys = new Set<string>();
  const ranks = new Set<number>();
  let expectedRank = 0;
  let previous: Record<string, unknown> | null = null;
  return value.every((entry) => {
    const candidate = record(entry);
    const timed = candidate && positiveLapTime(candidate.lap_time_ms);
    if (
      !candidate ||
      !boundedText(candidate.attempt_key, MAX_TEXT_LENGTH) ||
      keys.has(candidate.attempt_key) ||
      !boundedCount(candidate.attempt_number, 100_000) ||
      candidate.attempt_number === 0 ||
      !attemptKeyMatches(
        candidate.attempt_key,
        candidate.attempt_number,
        anchor.run_id,
        anchor.session_uid,
        anchor.car_index,
      ) ||
      !boundedText(candidate.disposition, 64) ||
      !nullableCount(candidate.lap_time_ms, 3_600_000) ||
      !nullableCount(candidate.recorded_time_rank, MAX_SCOPE_ATTEMPTS) ||
      (timed
        ? candidate.recorded_time_rank !== ++expectedRank ||
          ranks.has(candidate.recorded_time_rank as number)
        : candidate.recorded_time_rank !== null) ||
      !isOneOf(candidate.time_trial_eligibility, [
        "eligible",
        "excluded",
        "not_assessed",
      ]) ||
      !nullableSha256(candidate.trace_sha256) ||
      !Array.isArray(candidate.exclusion_reasons) ||
      candidate.exclusion_reasons.length > 64 ||
      !candidate.exclusion_reasons.every((reason) => boundedText(reason, 128))
    ) {
      return false;
    }
    if (
      candidate.time_trial_eligibility === "eligible" &&
      (!timed ||
        !isSha256(candidate.trace_sha256) ||
        candidate.disposition !== "completed" ||
        candidate.exclusion_reasons.length !== 0)
    ) {
      return false;
    }
    if (previous && compareRecordedOrder(previous, candidate) >= 0) return false;
    keys.add(candidate.attempt_key);
    if (typeof candidate.recorded_time_rank === "number") {
      ranks.add(candidate.recorded_time_rank);
    }
    previous = candidate;
    return true;
  }) &&
    (ordering.status === "unavailable" ||
      expectedRank ===
        Math.min(
          ordering.timed_candidate_count as number,
          candidateLength,
        ));
}

function safeEligibleBest(
  best: Record<string, unknown>,
  anchor: LapOrderAnchor,
  report: Record<string, unknown>,
) {
  const allowedStatuses = [
    "selected",
    "no_eligible_lap",
    "unsupported_mode",
    "unknown_mode",
    "context_unavailable",
    "assessment_limit_exceeded",
    "anchor_unavailable",
  ];
  if (!isOneOf(best.status, allowedStatuses)) return false;
  const selected = best.attempt === null ? null : record(best.attempt);
  if (best.attempt !== null && !selected) return false;
  if (
    report.status === "assessed" &&
    (report.candidates as unknown[]).some((value) => {
      const candidate = record(value);
      return candidate?.time_trial_eligibility === "eligible";
    }) &&
    best.status !== "selected"
  ) {
    return false;
  }
  if (best.status === "selected") {
    if (
      report.status !== "assessed" ||
      !selected ||
      !anchorIsKnownTimeTrial(anchor) ||
      !boundedText(selected.attempt_key, MAX_TEXT_LENGTH) ||
      !boundedCount(selected.attempt_number, 100_000) ||
      selected.attempt_number === 0 ||
      !attemptKeyMatches(
        selected.attempt_key,
        selected.attempt_number,
        anchor.run_id,
        anchor.session_uid,
        anchor.car_index,
      ) ||
      !boundedCount(selected.lap_time_ms, 3_600_000) ||
      !positiveLapTime(selected.lap_time_ms) ||
      !isSha256(selected.trace_sha256) ||
      !boundedPositiveCount(selected.trace_schema_version, 10_000)
    ) {
      return false;
    }
    const matchingCandidates = (report.candidates as unknown[]).filter(
      (value) => {
        const candidate = record(value);
        return candidate?.attempt_key === selected.attempt_key;
      },
    );
    if (matchingCandidates.length > 1) return false;
    if (matchingCandidates.length === 1) {
      const candidate = record(matchingCandidates[0]);
      if (
        !candidate ||
        candidate.time_trial_eligibility !== "eligible" ||
        candidate.attempt_number !== selected.attempt_number ||
        candidate.lap_time_ms !== selected.lap_time_ms ||
        candidate.trace_sha256 !== selected.trace_sha256
      ) {
        return false;
      }
      return !(report.candidates as unknown[]).some((value) => {
        const row = record(value);
        return Boolean(
          row?.time_trial_eligibility === "eligible" &&
            compareBestOrder(row, selected) < 0,
        );
      });
    }
    const ordering = record(report.recorded_time_ordering);
    const candidates = report.candidates as unknown[];
    const lastCandidate = record(candidates.at(-1));
    return (
      typeof ordering?.candidates_omitted_count === "number" &&
      ordering.candidates_omitted_count > 0 &&
      lastCandidate !== null &&
      compareRecordedOrder(lastCandidate, selected) < 0 &&
      !candidates.some((value) => {
        const row = record(value);
        return Boolean(
          row?.time_trial_eligibility === "eligible" &&
            compareBestOrder(row, selected) < 0,
        );
      })
    );
  }
  if (selected !== null) return false;
  if (report.status === "abstained") {
    return best.status === "assessment_limit_exceeded";
  }
  if (report.status === "anchor_unavailable") {
    return best.status === "anchor_unavailable";
  }
  return best.status !== "assessment_limit_exceeded" && best.status !== "anchor_unavailable";
}

function anchorIsKnownTimeTrial(anchor: LapOrderAnchor) {
  const context = record(anchor.context);
  return (
    context?.session_type === "time_trial" &&
    context.game_mode === "time_trial" &&
    context.rule_set === "time_trial"
  );
}

function validOptionalContext(value: unknown) {
  if (value === null) return true;
  const context = record(value);
  return Boolean(
    context &&
      ["session_type", "game_mode", "rule_set"].every(
        (field) =>
          context[field] === undefined ||
          context[field] === null ||
          (typeof context[field] === "string" &&
            (context[field] as string).length <= 64),
      ),
  );
}

function nullableSha256(value: unknown) {
  return value === null || (typeof value === "string" && SHA256_PATTERN.test(value));
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && SHA256_PATTERN.test(value);
}

function positiveLapTime(value: unknown): value is number {
  return typeof value === "number" && value > 0;
}

function compareRecordedOrder(
  left: Record<string, unknown>,
  right: Record<string, unknown>,
) {
  const leftTimed = positiveLapTime(left.lap_time_ms);
  const rightTimed = positiveLapTime(right.lap_time_ms);
  if (leftTimed !== rightTimed) return leftTimed ? -1 : 1;
  if (leftTimed && rightTimed && left.lap_time_ms !== right.lap_time_ms) {
    return (left.lap_time_ms as number) - (right.lap_time_ms as number);
  }
  return (left.attempt_number as number) - (right.attempt_number as number);
}

function compareBestOrder(
  left: Record<string, unknown>,
  right: Record<string, unknown>,
) {
  const timeDifference =
    (left.lap_time_ms as number) - (right.lap_time_ms as number);
  return timeDifference ||
    (left.attempt_number as number) - (right.attempt_number as number);
}

function attemptKeyMatches(
  value: unknown,
  attemptNumber: unknown,
  runId: unknown,
  sessionUid: unknown,
  carIndex: unknown,
) {
  if (
    typeof value !== "string" ||
    typeof runId !== "string" ||
    typeof sessionUid !== "string" ||
    !boundedIndex(carIndex) ||
    !boundedPositiveCount(attemptNumber, 100_000)
  ) {
    return false;
  }
  const match = /^([a-f0-9]{64}):([0-9]+):([0-9]+):([0-9]+)$/.exec(value);
  if (!match) return false;
  const keyCarIndex = Number(match[3]);
  const keyAttemptNumber = Number(match[4]);
  return (
    match[1] === runId &&
    match[2] === sessionUid &&
    Number.isSafeInteger(keyCarIndex) &&
    keyCarIndex === carIndex &&
    Number.isSafeInteger(keyAttemptNumber) &&
    keyAttemptNumber === attemptNumber
  );
}

function nullableCount(value: unknown, maximum: number) {
  return value === null || boundedCount(value, maximum);
}

function boundedIndex(value: unknown): value is number {
  return boundedCount(value, 23);
}

function boundedPositiveCount(value: unknown, maximum: number): value is number {
  return boundedCount(value, maximum) && value > 0;
}

function boundedCount(value: unknown, maximum: number): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0 &&
    value <= maximum
  );
}

function boundedText(value: unknown, maximumLength: number): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= maximumLength
  );
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function isOneOf(value: unknown, values: readonly string[]) {
  return typeof value === "string" && values.includes(value);
}
