import type { LapAttemptPage, LapRecord } from "@/lib/api";

export const ATTEMPT_INVENTORY_PAGE_SIZE = 50;

export function attemptInventoryUrl({
  runId,
  sessionUid,
  offset,
  limit = ATTEMPT_INVENTORY_PAGE_SIZE,
  targetAttemptKey,
  referenceAttemptKey,
}: {
  runId: string;
  sessionUid: string;
  offset: number;
  limit?: number;
  targetAttemptKey?: string;
  referenceAttemptKey?: string;
}) {
  const query = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (targetAttemptKey) {
    query.set("selected_target_attempt_key", targetAttemptKey);
  }
  if (referenceAttemptKey) {
    query.set("selected_reference_attempt_key", referenceAttemptKey);
  }
  return `/api/v1/processing-runs/${encodeURIComponent(runId)}/sessions/${encodeURIComponent(sessionUid)}/lap-attempts?${query.toString()}`;
}

export function lapAttemptPageMatchesScope(
  page: LapAttemptPage | null,
  runId: string,
  sessionUid: string,
) {
  return Boolean(
    page &&
      page.run_id === runId &&
      page.session_uid === sessionUid &&
      new Set(page.items.map((attempt) => attempt.attempt_key)).size === page.items.length &&
      new Set(page.selected_attempts.map((selection) => selection.requested_attempt_key)).size === page.selected_attempts.length &&
      page.items.every((attempt) => matchesAttemptScope(attempt, runId, sessionUid)) &&
      page.selected_attempts.every(
        (selection) =>
          selection.attempt === null ||
          (selection.attempt.attempt_key === selection.requested_attempt_key &&
            matchesAttemptScope(selection.attempt, runId, sessionUid)),
      ),
  );
}

export function exactLapAttempt(
  page: LapAttemptPage | null,
  attemptKey: string | undefined,
  runId: string,
  sessionUid: string,
): LapRecord | null {
  if (!attemptKey || !lapAttemptPageMatchesScope(page, runId, sessionUid)) {
    return null;
  }
  if (!page) return null;
  const matches = page.selected_attempts.filter(
    (item) => item.requested_attempt_key === attemptKey,
  );
  if (matches.length !== 1) return null;
  const selected = matches[0]?.attempt;
  return selected?.attempt_key === attemptKey ? selected : null;
}

function matchesAttemptScope(
  attempt: LapRecord,
  runId: string,
  sessionUid: string,
) {
  return attempt.run_id === runId && attempt.session_uid === sessionUid;
}
