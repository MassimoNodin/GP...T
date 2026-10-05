export const STORAGE_SCOPE_KEYS: readonly [
  "database",
  "finalized_captures",
  "recorder_staging",
  "imported_traces",
];
export const STORAGE_VOLUME_KEYS: readonly ["database_location", "recordings_location"];

export type StorageScopeKey = (typeof STORAGE_SCOPE_KEYS)[number];
export type StorageVolumeKey = (typeof STORAGE_VOLUME_KEYS)[number];

export interface StorageScope {
  status: "available" | "unavailable";
  logical_bytes: number | null;
  regular_file_count: number | null;
  excluded_entry_count: number | null;
  reason: string | null;
}

export interface StorageVolume {
  status: "available" | "unavailable";
  total_bytes: number | null;
  free_bytes: number | null;
  reason: string | null;
}

export interface StorageUsage {
  measurement_started_at_utc: string;
  measurement_completed_at_utc: string;
  measurement_note: string;
  scopes: Record<StorageScopeKey, StorageScope>;
  volumes: Record<StorageVolumeKey, StorageVolume>;
}

export function parseStorageUsageResponse(value: unknown): StorageUsage | null;
export function formatStorageBytes(bytes: number): string;
export function describeStorageReason(reason: string | null): string;
