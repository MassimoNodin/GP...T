// Storage usage as the Data & Storage tab shows it: legend rows with shares and
// the donut segments. Pure (no server imports) so the tab can rebuild it from a
// client-side refresh as well as from the server load.

import { describeStorageReason, formatStorageBytes } from "@/lib/storage-usage.mjs";
import type { StorageUsage } from "@/lib/storage-usage.mjs";

export type StorageScopeView = {
  key: string;
  label: string;
  value: string;
  detail: string | null;
  /** Share of the measured recordings total, 0 to 1; null when unmeasured. */
  share: number | null;
  /** Measured bytes; null when the scope could not be measured. */
  bytes: number | null;
  unavailable: boolean;
};

export type StorageView = {
  used: string;
  available: string;
  capacity: string | null;
  /** Fraction of the volume in use, 0 to 1; null when the volume is unknown. */
  volumeShare: number | null;
  scopes: StorageScopeView[];
  note: string;
  complete: boolean;
  /** ISO time the engine finished measuring. */
  measuredAtUtc: string;
};

export const SCOPE_LABELS: Record<string, string> = {
  database: "Session database",
  finalized_captures: "Recorded captures",
  recorder_staging: "Recorder staging",
  imported_traces: "Imported traces",
};

export function buildStorageView(storage: StorageUsage): StorageView {
  let used = 0;
  let complete = true;
  const measured = Object.entries(storage.scopes).map(([key, scope]) => {
    const bytes = scope.logical_bytes;
    if (bytes === null) complete = false;
    else used += bytes;
    return { key, scope, bytes };
  });
  const scopes = measured.map(({ key, scope, bytes }): StorageScopeView => {
    const files = scope.regular_file_count;
    return {
      key,
      label: SCOPE_LABELS[key] ?? key,
      value: bytes === null ? describeStorageReason(scope.reason) : formatStorageBytes(bytes),
      detail: files === null ? null : `${files} ${files === 1 ? "file" : "files"}`,
      share: bytes === null ? null : used > 0 ? bytes / used : 0,
      bytes,
      unavailable: bytes === null,
    };
  });
  const volume = storage.volumes.recordings_location;
  const free = volume.free_bytes;
  const total = volume.total_bytes;
  return {
    used: `${formatStorageBytes(used)}${complete ? "" : "+"} used`,
    available: free === null ? "Free space unknown" : `${formatStorageBytes(free)} available`,
    capacity: total === null ? null : `${formatStorageBytes(total)} volume`,
    volumeShare: total && free !== null ? (total - free) / total : null,
    scopes,
    note: storage.measurement_note,
    complete,
    measuredAtUtc: storage.measurement_completed_at_utc,
  };
}

/* ------------------------------------------------------------------ donut */

export const DONUT_RADIUS = 46;
export const DONUT_CIRCUMFERENCE = 2 * Math.PI * DONUT_RADIUS;

/** One legend colour per scope (design tokens). */
export const SCOPE_COLORS = ["var(--blue)", "var(--purple)", "var(--amber)", "var(--green)"] as const;

export type DonutSegment = {
  key: string;
  label: string;
  color: string;
  /** Visible arc length along the circle, in SVG units. */
  length: number;
  /** Start of the arc along the circle, in SVG units. */
  start: number;
  share: number;
};

/** Colour for the n-th scope; scopes beyond the palette repeat it. */
export function scopeColor(index: number): string {
  return SCOPE_COLORS[index % SCOPE_COLORS.length];
}

/**
 * Arcs for the measured scopes, proportional to their share of the measured
 * total. Unmeasured scopes and empty scopes get no arc (the legend still lists
 * them). A tiny gap separates neighbouring arcs when there is more than one.
 */
export function donutSegments(scopes: readonly StorageScopeView[]): DonutSegment[] {
  const drawn = scopes
    .map((scope, index) => ({ scope, index }))
    .filter(({ scope }) => scope.share !== null && scope.share > 0);
  const gap = drawn.length > 1 ? 1.5 : 0;
  let start = 0;
  return drawn.map(({ scope, index }) => {
    const span = (scope.share as number) * DONUT_CIRCUMFERENCE;
    const segment: DonutSegment = {
      key: scope.key,
      label: scope.label,
      color: scopeColor(index),
      length: Math.max(0, span - gap),
      start,
      share: scope.share as number,
    };
    start += span;
    return segment;
  });
}

/** Accessible name listing every scope, measured or not. */
export function donutLabel(view: StorageView): string {
  const parts = view.scopes.map((scope) =>
    scope.unavailable ? `${scope.label} not measured` : `${scope.label} ${scope.value}`,
  );
  return `Storage by location. ${view.used}. ${parts.join(", ")}.`;
}
