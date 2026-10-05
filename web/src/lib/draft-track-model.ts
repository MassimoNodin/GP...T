import type {
  DraftRegionInput,
  DraftTrackModelBuildResult,
  LapRecord,
} from "@/lib/api";

export type DraftTrackModelRequestIdentity = {
  modelId: string;
  revision: number;
  layoutId: string;
  regions: DraftRegionInput[];
};

function sameOptionalWindow(
  actual: unknown,
  expected: [number, number] | undefined,
) {
  if (expected === undefined) return actual === undefined;
  return (
    Array.isArray(actual) &&
    actual.length === 2 &&
    actual[0] === expected[0] &&
    actual[1] === expected[1]
  );
}

function sameRegions(actual: unknown, expected: DraftRegionInput[]): boolean {
  if (!Array.isArray(actual) || actual.length !== expected.length) return false;
  return actual.every((raw, index) => {
    if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
      return false;
    }
    const corner = raw as Record<string, unknown>;
    const region = expected[index];
    const allowedKeys = new Set([
      "identifier",
      "label",
      "start_distance_m",
      "end_distance_m",
      ...(region.braking_search_window_m ? ["braking_search_window_m"] : []),
      ...(region.turn_in_search_window_m ? ["turn_in_search_window_m"] : []),
      ...(region.throttle_pickup_window_m ? ["throttle_pickup_window_m"] : []),
    ]);
    return (
      Object.keys(corner).every((key) => allowedKeys.has(key)) &&
      Object.keys(corner).length === allowedKeys.size &&
      corner.identifier === region.identifier &&
      corner.label === region.label &&
      corner.start_distance_m === region.start_distance_m &&
      corner.end_distance_m === region.end_distance_m &&
      sameOptionalWindow(
        corner.braking_search_window_m,
        region.braking_search_window_m,
      ) &&
      sameOptionalWindow(
        corner.turn_in_search_window_m,
        region.turn_in_search_window_m,
      ) &&
      sameOptionalWindow(
        corner.throttle_pickup_window_m,
        region.throttle_pickup_window_m,
      )
    );
  });
}

function safeWarnings(
  value: unknown,
): value is DraftTrackModelBuildResult["warnings"] {
  return (
    Array.isArray(value) &&
    value.length <= 16 &&
    value.every(
      (warning) =>
        typeof warning === "object" &&
        warning !== null &&
        !Array.isArray(warning) &&
        typeof warning.code === "string" &&
        warning.code.length > 0 &&
        warning.code.length <= 128 &&
        typeof warning.text === "string" &&
        warning.text.length > 0 &&
        warning.text.length <= 2048,
    )
  );
}

const PRACTICE_QUALIFYING_SESSION_TYPES = new Set([
  "practice_1",
  "practice_2",
  "practice_3",
  "short_practice",
  "qualifying_1",
  "qualifying_2",
  "qualifying_3",
  "short_qualifying",
  "one_shot_qualifying",
  "sprint_shootout_1",
  "sprint_shootout_2",
  "sprint_shootout_3",
  "short_sprint_shootout",
  "one_shot_sprint_shootout",
]);

export function draftTrackModelResultMatches(
  result: DraftTrackModelBuildResult,
  attempt: LapRecord,
  request: DraftTrackModelRequestIdentity,
): boolean {
  const source = result.source;
  const model = result.model;
  const context = attempt.context;
  const expectedContextMode =
    context?.session_type === "time_trial"
      ? "time_trial"
      : typeof context?.session_type === "string" &&
          PRACTICE_QUALIFYING_SESSION_TYPES.has(context.session_type)
        ? "practice_qualifying"
        : null;

  return Boolean(
    expectedContextMode &&
    result.status === "draft_model_built" &&
    result.catalog_installation === "not_performed" &&
    model.schema_version === 1 &&
    model.validation_status === "draft" &&
    model.distance_origin_m === 0 &&
    model.model_id === request.modelId &&
    model.revision === request.revision &&
    model.layout_id === request.layoutId &&
    model.packet_format === context?.packet_format &&
    model.track_id === context?.track_id &&
    model.track_name === context?.track_name &&
    model.track_length_m === context?.track_length_m &&
    sameRegions(model.corners, request.regions) &&
    source.attempt_key === attempt.attempt_key &&
    source.run_id === attempt.run_id &&
    source.session_uid === attempt.session_uid &&
    source.car_index === attempt.car_index &&
    source.attempt_number === attempt.attempt_number &&
    source.source_trace_sha256 === attempt.trace_sha256 &&
    source.source_trace_schema_version === attempt.trace_schema_version &&
    source.context_mode === expectedContextMode &&
    source.packet_format === model.packet_format &&
    source.track_id === model.track_id &&
    source.track_name === model.track_name &&
    source.track_length_m === model.track_length_m &&
    source.scope_matches_attempt === true &&
    source.attempt_metadata_matches_attempt === true &&
    source.trace_metadata_matches_attempt === true &&
    source.context_timeline_matches_attempt === true &&
    source.context_matches_attempt === true &&
    safeWarnings(result.warnings),
  );
}
