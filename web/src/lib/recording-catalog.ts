import type { RecordingSourcePageRecord, RecordingSourceRecord } from "./api";

const captureIdPattern = /^[a-f0-9]{32}$/;
const runIdPattern = /^[a-f0-9]{64}$/;
const importStatuses = new Set([
  "queued",
  "running",
  "complete",
  "failed",
  "interrupted",
]);
const catalogStatuses = new Set([
  "all",
  "none",
  "queued",
  "running",
  "complete",
  "failed",
  "interrupted",
]);
const availabilityFilters = new Set(["all", "available", "missing"]);

export function readRecordingSourcePage(
  value: unknown,
): RecordingSourcePageRecord | null {
  if (
    !isRecord(value) ||
    !Array.isArray(value.items) ||
    !isRecord(value.filters)
  ) {
    return null;
  }
  const total = boundedInteger(value.total_count, 0, 10_000);
  const limit = boundedInteger(value.limit, 1, 50);
  const offset = boundedInteger(value.offset, 0, 100_000);
  const filters = value.filters;
  if (
    total === null ||
    limit === null ||
    offset === null ||
    typeof value.has_more !== "boolean" ||
    value.items.length > limit ||
    offset + value.items.length < total !== value.has_more ||
    typeof filters.query !== "string" ||
    filters.query.length > 128 ||
    /[\u0000-\u001f\u007f]/.test(filters.query) ||
    typeof filters.latest_job_status !== "string" ||
    !catalogStatuses.has(filters.latest_job_status) ||
    typeof filters.availability !== "string" ||
    !availabilityFilters.has(filters.availability) ||
    (filters.selected_capture_id !== null &&
      (typeof filters.selected_capture_id !== "string" ||
        !captureIdPattern.test(filters.selected_capture_id)))
  ) {
    return null;
  }
  const items = value.items.map(readRecordingSource);
  if (items.some((item) => item === null)) return null;
  const normalizedItems = items as RecordingSourceRecord[];
  if (
    new Set(normalizedItems.map((item) => item.capture_id)).size !==
    normalizedItems.length
  ) {
    return null;
  }
  const selectedCapture =
    value.selected_capture === null
      ? null
      : readRecordingSource(value.selected_capture);
  if (value.selected_capture !== null && selectedCapture === null) return null;
  if (
    (filters.selected_capture_id === null && selectedCapture !== null) ||
    (filters.selected_capture_id !== null &&
      selectedCapture !== null &&
      selectedCapture.capture_id !== filters.selected_capture_id)
  ) {
    return null;
  }
  const selectedInPage = normalizedItems.find(
    (item) => item.capture_id === selectedCapture?.capture_id,
  );
  if (
    selectedInPage &&
    JSON.stringify(selectedInPage) !== JSON.stringify(selectedCapture)
  ) {
    return null;
  }
  return {
    items: normalizedItems,
    selected_capture: selectedCapture,
    total_count: total,
    limit,
    offset,
    has_more: value.has_more,
    filters: {
      query: filters.query,
      latest_job_status:
        filters.latest_job_status as RecordingSourcePageRecord["filters"]["latest_job_status"],
      availability:
        filters.availability as RecordingSourcePageRecord["filters"]["availability"],
      selected_capture_id: filters.selected_capture_id as string | null,
    },
  };
}

function readRecordingSource(value: unknown): RecordingSourceRecord | null {
  if (!isRecord(value)) return null;
  const jobId = value.latest_job_id;
  const jobStatus = value.latest_job_status;
  const runId = value.latest_job_run_id;
  if (
    typeof value.capture_id !== "string" ||
    !captureIdPattern.test(value.capture_id) ||
    typeof value.display_name !== "string" ||
    value.display_name.length === 0 ||
    value.display_name.length > 512 ||
    value.display_name.includes("/") ||
    value.display_name.includes("\\") ||
    typeof value.byte_size !== "number" ||
    !Number.isSafeInteger(value.byte_size) ||
    value.byte_size < 0 ||
    typeof value.modified_at_utc !== "string" ||
    value.modified_at_utc.length > 64 ||
    !/^\d{4}-\d{2}-\d{2}T/.test(value.modified_at_utc) ||
    !Number.isFinite(Date.parse(value.modified_at_utc)) ||
    (jobId !== null &&
      (typeof jobId !== "string" || !captureIdPattern.test(jobId))) ||
    (jobStatus !== null &&
      (typeof jobStatus !== "string" || !importStatuses.has(jobStatus))) ||
    (runId !== null &&
      (typeof runId !== "string" || !runIdPattern.test(runId))) ||
    typeof value.available !== "boolean"
  ) {
    return null;
  }
  if (
    (jobId === null) !== (jobStatus === null) ||
    (runId !== null && jobStatus !== "complete")
  ) {
    return null;
  }
  return {
    capture_id: value.capture_id,
    display_name: value.display_name,
    byte_size: value.byte_size,
    modified_at_utc: value.modified_at_utc,
    latest_job_id: jobId as string | null,
    latest_job_status: jobStatus as string | null,
    latest_job_run_id: runId as string | null,
    available: value.available,
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
