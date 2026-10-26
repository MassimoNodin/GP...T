// Capture files (`.f1ecap`) as the Data & Storage tab lists them. Pure mapping
// from the recording catalog page. Nothing here deletes or renames a capture.

import { formatStorageBytes } from "@/lib/storage-usage.mjs";
import type { RecordingCatalogSourceRecord, RecordingSourcePageRecord } from "@/lib/api";

export const CAPTURES_PAGE_SIZE = 10;

export type ImportBadge = {
  label: string;
  tone: "green" | "amber" | "red" | "blue" | "grey";
};

export type CaptureRow = {
  captureId: string;
  name: string;
  size: string;
  bytes: number;
  modifiedAtUtc: string;
  badge: ImportBadge;
  /** Never imported and still on disk: an Import action applies. */
  canImport: boolean;
  /** Present on disk: a Download link applies. */
  downloadHref: string | null;
};

/** Status badge for the latest import job of a capture. */
export function importBadge(source: Pick<RecordingCatalogSourceRecord, "latest_job_status" | "available">): ImportBadge {
  if (!source.available) return { label: "Missing", tone: "red" };
  switch (source.latest_job_status) {
    case "complete":
      return { label: "Complete", tone: "green" };
    case "running":
      return { label: "Importing", tone: "blue" };
    case "queued":
      return { label: "Queued", tone: "amber" };
    case "failed":
      return { label: "Failed", tone: "red" };
    case "interrupted":
      return { label: "Interrupted", tone: "amber" };
    case "cancelled":
      return { label: "Cancelled", tone: "grey" };
    case null:
      return { label: "Not imported", tone: "grey" };
    default:
      return { label: "Unknown", tone: "grey" };
  }
}

export function downloadHref(captureId: string, version: string): string {
  return `/api/recording-sources/${encodeURIComponent(captureId)}/download?version=${encodeURIComponent(version)}`;
}

export function captureRow(source: RecordingCatalogSourceRecord): CaptureRow {
  const badge = importBadge(source);
  return {
    captureId: source.capture_id,
    name: source.display_name,
    size: formatStorageBytes(source.byte_size),
    bytes: source.byte_size,
    modifiedAtUtc: source.modified_at_utc,
    badge,
    canImport: source.available && source.latest_job_status === null,
    downloadHref: source.available ? downloadHref(source.capture_id, source.download_version) : null,
  };
}

export type CapturesModel = {
  rows: CaptureRow[];
  total: number;
  hasMore: boolean;
};

export function capturesModel(page: RecordingSourcePageRecord, previous: readonly CaptureRow[] = []): CapturesModel {
  const seen = new Set(previous.map((row) => row.captureId));
  const added = page.items.map(captureRow).filter((row) => !seen.has(row.captureId));
  return { rows: [...previous, ...added], total: page.total_count, hasMore: page.has_more };
}

/** Header summary such as `10 of 23 captures`. */
export function capturesSummary(model: CapturesModel): string {
  if (model.total === 0) return "No captures";
  const noun = model.total === 1 ? "capture" : "captures";
  return model.rows.length >= model.total
    ? `${model.total} ${noun}`
    : `${model.rows.length} of ${model.total} captures`;
}
