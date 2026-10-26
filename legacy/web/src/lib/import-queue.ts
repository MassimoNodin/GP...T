import type { ImportQueueRecord } from "@/lib/api";

const identifierPattern = /^[a-f0-9]{32}$/;
const reservations = new Set([
  "idle",
  "recording",
  "import",
  "replay",
  "upload",
]);

export function readImportQueueEnvelope(
  responseOk: boolean,
  body: unknown,
): ImportQueueRecord | null {
  if (!responseOk || !isRecord(body)) return null;
  if (body.api_version !== "v1" || body.status !== "ok") return null;
  return readQueue(body.data);
}

function readQueue(value: unknown): ImportQueueRecord | null {
  if (!isRecord(value)) return null;
  const waitingCount = boundedInteger(value.waiting_count, 0, 16);
  if (
    waitingCount === null ||
    !Array.isArray(value.waiting_jobs) ||
    value.waiting_jobs.length > 16 ||
    typeof value.blocking_reservation !== "string" ||
    !reservations.has(value.blocking_reservation)
  ) {
    return null;
  }
  const waiting = value.waiting_jobs.map((row, index) =>
    readQueueJob(row, index + 1, "queued"),
  );
  if (waiting.some((row) => row === null) || waiting.length !== waitingCount)
    return null;
  const running =
    value.running_job === null
      ? null
      : readQueueJob(value.running_job, null, "running");
  if (value.running_job !== null && running === null) return null;
  return {
    waiting_count: waitingCount,
    waiting_jobs: waiting as ImportQueueRecord["waiting_jobs"],
    running_job: running,
    blocking_reservation:
      value.blocking_reservation as ImportQueueRecord["blocking_reservation"],
  };
}

function readQueueJob(
  value: unknown,
  position: number | null,
  expectedStatus: "queued" | "running",
) {
  if (!isRecord(value)) return null;
  const queuePosition = value.queue_position;
  if (
    typeof value.job_id !== "string" ||
    !identifierPattern.test(value.job_id) ||
    typeof value.capture_id !== "string" ||
    !identifierPattern.test(value.capture_id) ||
    value.status !== expectedStatus ||
    typeof value.phase !== "string" ||
    value.phase.length > 64 ||
    boundedInteger(value.attempt_count, 1, 1_000_000) === null ||
    typeof value.created_at_utc !== "string" ||
    value.created_at_utc.length > 40 ||
    typeof value.updated_at_utc !== "string" ||
    value.updated_at_utc.length > 40 ||
    (position === null
      ? queuePosition !== null
      : boundedInteger(queuePosition, position, position) === null)
  ) {
    return null;
  }
  return {
    job_id: value.job_id,
    capture_id: value.capture_id,
    status: expectedStatus,
    phase: value.phase,
    attempt_count: value.attempt_count as number,
    created_at_utc: value.created_at_utc,
    updated_at_utc: value.updated_at_utc,
    queue_position: position,
  };
}

function boundedInteger(value: unknown, minimum: number, maximum: number) {
  return Number.isSafeInteger(value) &&
    (value as number) >= minimum &&
    (value as number) <= maximum
    ? (value as number)
    : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
