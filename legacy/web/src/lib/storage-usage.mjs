export const STORAGE_SCOPE_KEYS = [
  "database",
  "finalized_captures",
  "recorder_staging",
  "imported_traces",
];

export const STORAGE_VOLUME_KEYS = ["database_location", "recordings_location"];

function isObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonNegativeSafeInteger(value) {
  return Number.isSafeInteger(value) && value >= 0;
}

function isTimestamp(value) {
  return (
    typeof value === "string" &&
    value.length <= 40 &&
    value.endsWith("Z") &&
    Number.isFinite(Date.parse(value))
  );
}

function parseScope(value) {
  if (!isObject(value)) return null;
  if (value.status === "available") {
    if (
      !isNonNegativeSafeInteger(value.logical_bytes) ||
      !isNonNegativeSafeInteger(value.regular_file_count) ||
      !isNonNegativeSafeInteger(value.excluded_entry_count) ||
      value.reason !== null
    ) {
      return null;
    }
    return {
      status: "available",
      logical_bytes: value.logical_bytes,
      regular_file_count: value.regular_file_count,
      excluded_entry_count: value.excluded_entry_count,
      reason: null,
    };
  }
  if (
    value.status === "unavailable" &&
    value.logical_bytes === null &&
    value.regular_file_count === null &&
    value.excluded_entry_count === null &&
    typeof value.reason === "string" &&
    value.reason.length > 0 &&
    value.reason.length <= 80
  ) {
    return {
      status: "unavailable",
      logical_bytes: null,
      regular_file_count: null,
      excluded_entry_count: null,
      reason: value.reason,
    };
  }
  return null;
}

function parseVolume(value) {
  if (!isObject(value)) return null;
  if (value.status === "available") {
    if (
      !isNonNegativeSafeInteger(value.total_bytes) ||
      !isNonNegativeSafeInteger(value.free_bytes) ||
      value.free_bytes > value.total_bytes ||
      value.reason !== null
    ) {
      return null;
    }
    return {
      status: "available",
      total_bytes: value.total_bytes,
      free_bytes: value.free_bytes,
      reason: null,
    };
  }
  if (
    value.status === "unavailable" &&
    value.total_bytes === null &&
    value.free_bytes === null &&
    typeof value.reason === "string" &&
    value.reason.length > 0 &&
    value.reason.length <= 80
  ) {
    return {
      status: "unavailable",
      total_bytes: null,
      free_bytes: null,
      reason: value.reason,
    };
  }
  return null;
}

export function parseStorageUsageResponse(value) {
  if (!isObject(value) || value.api_version !== "v1" || value.status !== "ok") {
    return null;
  }
  const data = value.data;
  if (
    !isObject(data) ||
    !isTimestamp(data.measurement_started_at_utc) ||
    !isTimestamp(data.measurement_completed_at_utc) ||
    typeof data.measurement_note !== "string" ||
    data.measurement_note.length > 240 ||
    !isObject(data.scopes) ||
    !isObject(data.volumes)
  ) {
    return null;
  }
  const scopes = {};
  for (const key of STORAGE_SCOPE_KEYS) {
    const scope = parseScope(data.scopes[key]);
    if (!scope) return null;
    scopes[key] = scope;
  }
  const volumes = {};
  for (const key of STORAGE_VOLUME_KEYS) {
    const volume = parseVolume(data.volumes[key]);
    if (!volume) return null;
    volumes[key] = volume;
  }
  return {
    measurement_started_at_utc: data.measurement_started_at_utc,
    measurement_completed_at_utc: data.measurement_completed_at_utc,
    measurement_note: data.measurement_note,
    scopes,
    volumes,
  };
}

export function formatStorageBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB", "PiB"];
  let value = bytes;
  let unitIndex = -1;
  do {
    value /= 1024;
    unitIndex += 1;
  } while (value >= 1024 && unitIndex < units.length - 1);
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value)} ${units[unitIndex]}`;
}

export function describeStorageReason(reason) {
  if (!reason) return "Unavailable";
  const labels = {
    database_unavailable: "Database file unavailable",
    database_location_unavailable: "Database location unavailable",
    database_not_regular_file: "Database is not a regular file",
    recordings_root_missing: "Recordings folder unavailable",
    trace_namespace_missing: "Trace folder unavailable",
    trace_namespace_unavailable: "Trace folder unavailable",
    directory_unavailable: "Configured folder unavailable",
    permission_denied: "Access denied",
    file_disappeared: "A file changed during measurement",
    directory_changed: "A folder changed during measurement",
    entry_limit_exceeded: "Scan limit reached; complete size unavailable",
    directory_depth_limit_exceeded: "Folder depth limit reached; complete size unavailable",
    path_outside_configured_root: "Entry outside the configured location",
    volume_location_missing: "Storage location unavailable",
    volume_unavailable: "Volume capacity unavailable",
    filesystem_error: "Filesystem could not be read",
  };
  return Object.hasOwn(labels, reason) ? labels[reason] : "Measurement unavailable";
}
