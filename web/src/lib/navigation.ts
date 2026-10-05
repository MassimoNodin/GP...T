export type AppScreen = "dashboard" | "recordings" | "sessions";
export type ImportReturnScreen = "dashboard" | "recordings";

export type AppSearchParams = Record<string, string | string[] | undefined>;

const retainedKeys = [
  "session_key",
  "target_attempt_key",
  "reference_choice",
  "comparison_policy",
  "window_start_m",
  "window_end_m",
  "observation_attempt_key",
  "track_model_key",
  "position_probe_m",
  "import_job_id",
  "import_error",
  "run_id",
  "run_offset",
  "session_offset",
  "attempt_offset",
  "lifecycle_event_offset",
  "observation_session_uid",
  "observation_car_index",
  "observation_offset",
  "engineer_intent",
  "engineer_region_identifier",
] as const;

const retainedKeySet = new Set<string>(retainedKeys);
const maximumStateQueryLength = 4096;
const maximumValueLength = 512;
const maximumValuesPerKey = 8;
const blockedTransferQuery = "selection_transfer=blocked";

export function preservedAppStateQuery(values: AppSearchParams): string {
  if (values.selection_transfer !== undefined) return blockedTransferQuery;
  const query = new URLSearchParams();
  for (const key of retainedKeys) {
    const value = values[key];
    const entries =
      value === undefined ? [] : Array.isArray(value) ? value : [value];
    if (entries.length > maximumValuesPerKey) return blockedTransferQuery;
    for (const entry of entries) {
      if (entry.length > maximumValueLength) return blockedTransferQuery;
      query.append(key, entry);
      if (query.toString().length > maximumStateQueryLength)
        return blockedTransferQuery;
    }
  }
  return query.toString();
}

export function sanitizeAppStateQuery(value: string | null): string {
  if (!value) return "";
  if (value.length > maximumStateQueryLength) return blockedTransferQuery;
  const incoming = new URLSearchParams(
    value.startsWith("?") ? value.slice(1) : value,
  );
  if (incoming.has("selection_transfer")) return blockedTransferQuery;
  const query = new URLSearchParams();
  for (const key of retainedKeys) {
    const entries = incoming.getAll(key);
    if (entries.length > maximumValuesPerKey) return blockedTransferQuery;
    for (const entry of entries) {
      if (entry.length > maximumValueLength) return blockedTransferQuery;
      query.append(key, entry);
      if (query.toString().length > maximumStateQueryLength)
        return blockedTransferQuery;
    }
  }
  return query.toString();
}

export function isSelectionTransferBlocked(value: string): boolean {
  return sanitizeAppStateQuery(value) === blockedTransferQuery;
}

export function appScreenPath(screen: AppScreen): string {
  if (screen === "recordings") return "/recordings";
  if (screen === "sessions") return "/sessions";
  return "/";
}

export function appScreenFrom(
  value: FormDataEntryValue | null,
): ImportReturnScreen {
  return value === "recordings" ? "recordings" : "dashboard";
}

export function appScreenHref(
  screen: AppScreen,
  stateQuery = "",
  overrides: Partial<Record<(typeof retainedKeys)[number], string>> = {},
): string | null {
  const query = new URLSearchParams(sanitizeAppStateQuery(stateQuery));
  for (const [key, value] of Object.entries(overrides)) {
    if (!retainedKeySet.has(key) || typeof value !== "string") return null;
    if (value.length > maximumValueLength) return null;
    query.set(key, value);
  }
  if (Object.hasOwn(overrides, "import_job_id")) {
    query.delete("import_error");
  }
  if (Object.hasOwn(overrides, "import_error")) {
    query.delete("import_job_id");
  }
  const encoded = query.toString();
  if (encoded.length > maximumStateQueryLength) return null;
  return encoded
    ? `${appScreenPath(screen)}?${encoded}`
    : appScreenPath(screen);
}
