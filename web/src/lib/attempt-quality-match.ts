import type { AttemptQualityReport, LapRecord } from "@/lib/api";

const sourceRowLimit = 100_000;
const contextSegmentLimit = 1_024;
const displayChannels = [
  "time_s",
  "speed_mps",
  "throttle",
  "brake",
  "steering",
  "gear",
  "drs_active",
] as const;
const qualityChannels = [
  ...displayChannels,
  "motion_available",
  "world_position",
  "world_velocity",
] as const;
const uint32Maximum = 0xffff_ffff;

export function attemptQualityReportMatchesSelection(
  value: unknown,
  attempt: LapRecord,
): value is AttemptQualityReport {
  const report = record(value);
  const identity = record(report?.identity);
  const reportAttempt = record(report?.attempt);
  const context = record(report?.context);
  const evidence = record(report?.evidence);
  const channels = record(report?.channels);
  const continuity = record(report?.continuity);
  const distanceSupport = record(report?.distance_support);
  const rowCount = integer(attempt.trace_row_count);

  if (
    !report ||
    !identity ||
    !reportAttempt ||
    !context ||
    !evidence ||
    !channels ||
    !continuity ||
    !distanceSupport ||
    rowCount === null ||
    rowCount > sourceRowLimit ||
    report.report_version !== 2 ||
    report.analysis_version !== "attempt-telemetry-quality-v3-car-damage" ||
    !boundedJson(report) ||
    !matchesIdentity(identity, attempt, rowCount) ||
    !matchesAttempt(reportAttempt, attempt) ||
    !validContext(context) ||
    !validEvidence(evidence) ||
    !qualityChannels.every((key) =>
      validAvailability(channels[key], rowCount),
    ) ||
    !validContinuity(continuity, rowCount) ||
    !validObservedStatus(report.observed_status, rowCount) ||
    (report.car_damage_observations !== undefined &&
      !validDamageObservations(report.car_damage_observations, rowCount)) ||
    !validDistanceSupport(distanceSupport, rowCount)
  ) {
    return false;
  }

  return true;
}

function matchesIdentity(
  identity: Record<string, unknown>,
  attempt: LapRecord,
  rowCount: number,
): boolean {
  return (
    identity.attempt_key === attempt.attempt_key &&
    identity.run_id === attempt.run_id &&
    identity.session_uid === attempt.session_uid &&
    identity.car_index === attempt.car_index &&
    identity.attempt_number === attempt.attempt_number &&
    identity.trace_sha256 === attempt.trace_sha256 &&
    identity.trace_schema_version === attempt.trace_schema_version &&
    identity.trace_row_count === rowCount &&
    identity.trace_checksum_verified === true &&
    integer(identity.car_index) !== null &&
    integer(identity.attempt_number) !== null &&
    integer(identity.trace_schema_version) !== null &&
    identity.trace_schema_version >= 1 &&
    identity.trace_schema_version <= 4 &&
    isSha256(identity.trace_sha256)
  );
}

function matchesAttempt(
  value: Record<string, unknown>,
  attempt: LapRecord,
): boolean {
  return (
    value.disposition === attempt.disposition &&
    value.lap_time_ms === attempt.lap_time_ms &&
    value.game_valid === attempt.game_valid &&
    value.reference_eligible === attempt.reference_eligible &&
    value.start_observed === attempt.start_observed &&
    value.pit_encountered === attempt.pit_encountered &&
    sameStringArray(value.exclusion_reasons, attempt.exclusion_reasons) &&
    shortString(value.disposition, 64) &&
    nullableCount(value.lap_time_ms) !== false &&
    nullableBoolean(value.game_valid) &&
    typeof value.reference_eligible === "boolean" &&
    typeof value.start_observed === "boolean" &&
    typeof value.pit_encountered === "boolean"
  );
}

function validContext(value: Record<string, unknown>): boolean {
  const segments = array(value.segments);
  return Boolean(
    segments &&
    segments.length <= contextSegmentLimit &&
    validStringArray(value.session_types, contextSegmentLimit, 256) &&
    validStringArray(value.game_modes, contextSegmentLimit, 256) &&
    validStringArray(value.track_names, contextSegmentLimit, 256) &&
    segments.every((entry) => {
      const segment = record(entry);
      const context = segment?.context;
      return Boolean(
        segment &&
        uint32(segment.from_frame_identifier) &&
        (context === null || validSessionContext(context)),
      );
    }),
  );
}

function validSessionContext(value: unknown): boolean {
  const context = record(value);
  if (!context || Object.keys(context).length > 32) return false;
  const nullableTextFields = [
    "track_name",
    "session_type",
    "game_mode",
    "rule_set",
    "weather_name",
  ];
  const nullableNumberFields = ["packet_format", "track_id", "track_length_m"];
  return (
    (!Object.hasOwn(context, "session_uid") ||
      countOrNumericString(context.session_uid)) &&
    nullableTextFields.every(
      (key) =>
        !Object.hasOwn(context, key) || nullableShortString(context[key]),
    ) &&
    nullableNumberFields.every(
      (key) =>
        !Object.hasOwn(context, key) ||
        context[key] === null ||
        finiteNumber(context[key]) !== null,
    ) &&
    Object.values(context).every(
      (entry) =>
        entry === null ||
        typeof entry === "string" ||
        typeof entry === "boolean" ||
        finiteNumber(entry) !== null,
    )
  );
}

function validEvidence(value: Record<string, unknown>): boolean {
  const recording = record(value.recording);
  const captureDecode = record(value.capture_decode);
  const assembly = record(value.replay_assembly);
  const recordingObserver = record(value.recording_observer);
  const attemptTrace = record(value.attempt_trace);
  const counters = record(recording?.counters);
  const counterEvidence = record(recording?.counter_evidence);
  const captureCounters = record(captureDecode?.capture_wide_counters);
  const assemblyCounters = record(assembly?.counters);
  const assemblyEvidence = record(assembly?.counter_evidence);
  const observerCounters = record(recordingObserver?.counters);
  const observerEvidence = record(recordingObserver?.counter_evidence);
  return Boolean(
    recording &&
    captureDecode &&
    assembly &&
    recordingObserver &&
    attemptTrace &&
    typeof recording.capture_complete === "boolean" &&
    typeof recording.footer_available === "boolean" &&
    shortString(recording.footer_evidence_status, 64) &&
    nullableShortString(recording.footer_status) &&
    shortString(recording.footer_status_evidence, 64) &&
    numericNullableRecord(counters, 32) &&
    shortStringRecord(counterEvidence, 32) &&
    typeof captureDecode.available === "boolean" &&
    captureCounters !== null &&
    Object.keys(captureCounters).length <= 256 &&
    typeof assembly.available === "boolean" &&
    numericNullableRecord(assemblyCounters, 32) &&
    shortStringRecord(assemblyEvidence, 32) &&
    typeof recordingObserver.available === "boolean" &&
    recordingObserver.source_scope === "raw_capture_processing_before_import" &&
    numericNullableRecord(observerCounters, 32) &&
    shortStringRecord(observerEvidence, 32),
  );
}

function validAvailability(value: unknown, rowCount: number): boolean {
  const channel = record(value);
  if (!channel) return false;
  const available = nullableCount(channel.available_samples);
  const missing = nullableCount(channel.missing_samples);
  if (channel.status === "unavailable_in_trace_schema") {
    return (
      channel.sample_count === rowCount &&
      integer(channel.sample_count) !== null &&
      available === null &&
      missing === null
    );
  }
  if (
    channel.status !== "available" &&
    channel.status !== "partial" &&
    channel.status !== "unavailable"
  ) {
    return false;
  }
  return Boolean(
    shortString(channel.status, 64) &&
    channel.sample_count === rowCount &&
    integer(channel.sample_count) !== null &&
    available !== false &&
    missing !== false &&
    available !== null &&
    missing !== null &&
    available <= rowCount &&
    missing <= rowCount &&
    available + missing === rowCount &&
    (channel.status === "available"
      ? rowCount > 0 && available === rowCount
      : channel.status === "partial"
        ? available > 0 && available < rowCount
        : rowCount === 0 || available === 0),
  );
}

function validContinuity(
  value: Record<string, unknown>,
  rowCount: number,
): boolean {
  const frameGaps = record(value.frame_gaps);
  const lapClock = record(value.lap_clock);
  const sessionTime = record(value.session_time);
  const lapDistance = record(value.lap_distance);
  const examples = array(value.examples);
  return Boolean(
    integer(value.sample_adjacency_count) === Math.max(0, rowCount - 1) &&
    frameGaps &&
    countWithin(frameGaps.count, rowCount) &&
    countWithin(frameGaps.largest_missing_frame_count, uint32Maximum) &&
    countWithin(frameGaps.order_discontinuity_count, rowCount) &&
    validContinuityMetric(lapClock, rowCount) &&
    validContinuityMetric(sessionTime, rowCount) &&
    validContinuityMetric(lapDistance, rowCount) &&
    examples &&
    examples.length <= 20 &&
    typeof value.examples_truncated === "boolean",
  );
}

function validContinuityMetric(
  value: Record<string, unknown> | null,
  rowCount: number,
): boolean {
  return Boolean(
    value &&
    finiteNumber(value.gap_threshold_s ?? value.gap_threshold_m) !== null &&
    countWithin(value.gap_count, rowCount) &&
    countWithin(value.regression_count, rowCount) &&
    nonnegativeFinite(
      value.largest_nonnegative_gap_s ?? value.largest_nonnegative_gap_m,
    ),
  );
}

function validObservedStatus(value: unknown, rowCount: number): boolean {
  const status = record(value);
  if (!status || !shortString(status.status, 64)) return false;
  const matched = nullableCount(status.matched_sample_count);
  const missing = nullableCount(status.missing_join_sample_count);
  if (
    status.sample_count !== rowCount ||
    integer(status.sample_count) === null ||
    matched === false ||
    missing === false ||
    !nullableCountRecord(status.unavailable_reason_counts, 64) ||
    !shortString(status.fuel_quantity_unit_note, 256)
  ) {
    return false;
  }
  const fields = status.fields;
  const anchors = status.first_last_observed;
  const changes = array(status.discrete_changes);
  const compounds = record(status.distinct_compounds);
  if (status.status !== "available") {
    return (
      matched === null &&
      missing === null &&
      fields === null &&
      anchors === null &&
      changes === null &&
      status.discrete_changes_truncated === null &&
      compounds === null
    );
  }
  const fieldRecords = record(fields);
  const anchorRecords = record(anchors);
  const actualCompounds = array(compounds?.actual);
  const visualCompounds = array(compounds?.visual);
  return Boolean(
    matched !== null &&
    missing !== null &&
    matched + missing === rowCount &&
    validFieldCounts(fieldRecords, rowCount, 64) &&
    validAnchorsByField(
      anchorRecords,
      Object.keys(fieldRecords ?? {}),
      false,
    ) &&
    changes &&
    changes.length <= 20 &&
    changes.every(validStatusChange) &&
    typeof status.discrete_changes_truncated === "boolean" &&
    actualCompounds &&
    actualCompounds.length <= 64 &&
    actualCompounds.every(validCompound) &&
    visualCompounds &&
    visualCompounds.length <= 64 &&
    visualCompounds.every(validCompound),
  );
}

function validDamageObservations(value: unknown, rowCount: number): boolean {
  const damage = record(value);
  if (!damage) return false;
  const matched = nullableCount(damage.matched_sample_count);
  const missing = nullableCount(damage.missing_join_sample_count);
  const fields = record(damage.fields);
  const anchors = record(damage.first_last_observed);
  return Boolean(
    damage.version === "car-damage-observations-v1" &&
    damage.authority === "diagnostic_only_sparse_observation" &&
    shortString(damage.observation_note, 512) &&
    shortString(damage.status, 64) &&
    damage.sample_count === rowCount &&
    integer(damage.sample_count) !== null &&
    matched !== false &&
    missing !== false &&
    nullableCountRecord(damage.unavailable_reason_counts, 64) &&
    (damage.status === "unavailable_in_trace_schema"
      ? matched === null &&
        missing === null &&
        fields === null &&
        anchors === null
      : damage.status === "available" || damage.status === "no_joined_samples"
        ? matched !== null &&
          missing !== null &&
          matched + missing === rowCount &&
          validFieldCounts(fields, rowCount, 64) &&
          validAnchorsByField(anchors, Object.keys(fields ?? {}), true)
        : false),
  );
}

function validFieldCounts(
  value: Record<string, unknown> | null,
  rowCount: number,
  maximumFields: number,
): value is Record<string, unknown> {
  return Boolean(
    value &&
    Object.keys(value).length <= maximumFields &&
    Object.entries(value).every(([field, rawCounts]) => {
      const counts = record(rawCounts);
      return Boolean(
        shortString(field, 128) &&
        counts &&
        countWithin(counts.valid_count, rowCount) &&
        countWithin(counts.missing_count, rowCount) &&
        countWithin(counts.invalid_count, rowCount) &&
        (counts.valid_count as number) +
          (counts.missing_count as number) +
          (counts.invalid_count as number) ===
          rowCount,
      );
    }),
  );
}

function validAnchorsByField(
  value: Record<string, unknown> | null,
  fields: string[],
  damage: boolean,
): boolean {
  return Boolean(
    value &&
    Object.keys(value).length <= fields.length &&
    fields.every((field) => {
      const pair = record(value[field]);
      return Boolean(
        pair &&
        (pair.first === null || validObservationAnchor(pair.first, damage)) &&
        (pair.last === null || validObservationAnchor(pair.last, damage)),
      );
    }),
  );
}

function validObservationAnchor(value: unknown, damage: boolean): boolean {
  const anchor = record(value);
  if (!anchor || !validJsonScalar(anchor.value)) return false;
  return damage
    ? (anchor.frame_identifier === null || uint32(anchor.frame_identifier)) &&
        nullableFinite(anchor.session_time_s) &&
        nullableFinite(anchor.lap_distance_m)
    : uint32(anchor.frame_identifier) &&
        finiteNumber(anchor.session_time_s) !== null;
}

function validStatusChange(value: unknown): boolean {
  const change = record(value);
  return Boolean(
    change &&
    shortString(change.field, 128) &&
    uint32(change.from_frame_identifier) &&
    uint32(change.to_frame_identifier) &&
    validJsonScalar(change.from_value) &&
    validJsonScalar(change.to_value),
  );
}

function validCompound(value: unknown): boolean {
  const compound = record(value);
  return Boolean(
    compound &&
    integer(compound.raw_id) !== null &&
    nullableCount(compound.formula_id) !== false &&
    nullableShortString(compound.label),
  );
}

function validDistanceSupport(
  value: Record<string, unknown>,
  rowCount: number,
): boolean {
  const channels = record(value.channels);
  const range = value.observed_distance_range_m;
  const validRange =
    range === null ||
    (array(range)?.length === 2 &&
      finiteNumber(array(range)?.[0]) !== null &&
      finiteNumber(array(range)?.[1]) !== null &&
      (array(range)?.[0] as number) >= 0 &&
      (array(range)?.[1] as number) >= (array(range)?.[0] as number));
  return Boolean(
    finiteNumber(value.resolution_m) !== null &&
    (value.resolution_m as number) > 0 &&
    nullablePositiveFinite(value.track_length_m) &&
    nullableShortString(value.track_length_source) &&
    nullableShortString(value.track_length_unavailable_reason) &&
    validRange &&
    (value.status === "available" || value.status === "unavailable") &&
    nullableShortString(value.reason) &&
    channels &&
    displayChannels.every((key) => {
      const channel = record(channels[key]);
      return Boolean(
        channel &&
        nullableCoverage(channel.observed_range_coverage) &&
        nullableCount(channel.observed_grid_point_count) !== false &&
        nullableCoverage(channel.full_track_coverage) &&
        nullableCount(channel.full_track_grid_point_count) !== false &&
        (channel.observed_grid_point_count === null ||
          (channel.observed_grid_point_count as number) <= 100_000) &&
        (channel.full_track_grid_point_count === null ||
          (channel.full_track_grid_point_count as number) <= 100_000),
      );
    }),
  );
}

function boundedJson(value: unknown): boolean {
  let nodes = 250_000;
  const seen = new WeakSet<object>();
  function visit(item: unknown, depth: number): boolean {
    nodes -= 1;
    if (nodes < 0 || depth > 16) return false;
    if (
      item === null ||
      typeof item === "boolean" ||
      (typeof item === "number" && Number.isFinite(item))
    ) {
      return true;
    }
    if (typeof item === "string") return item.length <= 4_096;
    if (typeof item !== "object") return false;
    if (seen.has(item)) return false;
    seen.add(item);
    if (Array.isArray(item)) {
      return (
        item.length <= 2_048 && item.every((entry) => visit(entry, depth + 1))
      );
    }
    const object = record(item);
    return Boolean(
      object &&
      Object.keys(object).length <= 512 &&
      Object.entries(object).every(
        ([key, entry]) => key.length <= 128 && visit(entry, depth + 1),
      ),
    );
  }
  try {
    return visit(value, 0);
  } catch {
    return false;
  }
}

function numericNullableRecord(
  value: Record<string, unknown> | null,
  maximum: number,
): boolean {
  return Boolean(
    value &&
    Object.keys(value).length <= maximum &&
    Object.entries(value).every(
      ([key, entry]) =>
        shortString(key, 128) && (entry === null || integer(entry) !== null),
    ),
  );
}

function nullableCountRecord(value: unknown, maximum: number): boolean {
  if (value === null) return true;
  const counts = record(value);
  return Boolean(
    counts &&
    Object.keys(counts).length <= maximum &&
    Object.entries(counts).every(
      ([key, entry]) =>
        shortString(key, 128) && countWithin(entry, sourceRowLimit),
    ),
  );
}

function shortStringRecord(
  value: Record<string, unknown> | null,
  maximum: number,
): boolean {
  return Boolean(
    value &&
    Object.keys(value).length <= maximum &&
    Object.entries(value).every(
      ([key, entry]) => shortString(key, 128) && shortString(entry, 64),
    ),
  );
}

function validStringArray(
  value: unknown,
  maximum: number,
  maxStringLength: number,
): value is string[] {
  return Boolean(
    Array.isArray(value) &&
    value.length <= maximum &&
    value.every((entry) => shortString(entry, maxStringLength)),
  );
}

function sameStringArray(value: unknown, expected: string[]): boolean {
  return (
    Array.isArray(value) &&
    value.length === expected.length &&
    value.every((entry, index) => entry === expected[index])
  );
}

function validJsonScalar(value: unknown): boolean {
  return (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    finiteNumber(value) !== null
  );
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function array(value: unknown): unknown[] | null {
  return Array.isArray(value) ? value : null;
}

function integer(value: unknown): number | null {
  return Number.isSafeInteger(value) && (value as number) >= 0
    ? (value as number)
    : null;
}

function nullableCount(value: unknown): number | null | false {
  return value === null ? null : (integer(value) ?? false);
}

function countWithin(value: unknown, maximum: number): boolean {
  const count = integer(value);
  return count !== null && count <= maximum;
}

function nullableBoolean(value: unknown): boolean {
  return value === null || typeof value === "boolean";
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function nonnegativeFinite(value: unknown): boolean {
  const number = finiteNumber(value);
  return number !== null && number >= 0;
}

function nullableFinite(value: unknown): boolean {
  return value === null || finiteNumber(value) !== null;
}

function nullablePositiveFinite(value: unknown): boolean {
  return (
    value === null || (finiteNumber(value) !== null && (value as number) > 0)
  );
}

function nullableCoverage(value: unknown): boolean {
  const number = finiteNumber(value);
  return value === null || (number !== null && number >= 0 && number <= 1);
}

function shortString(value: unknown, maximum: number): value is string {
  return (
    typeof value === "string" && value.length > 0 && value.length <= maximum
  );
}

function nullableShortString(value: unknown): boolean {
  return value === null || shortString(value, 512);
}

function uint32(value: unknown): boolean {
  return (
    Number.isSafeInteger(value) &&
    (value as number) >= 0 &&
    (value as number) <= uint32Maximum
  );
}

function isSha256(value: unknown): boolean {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
}

function countOrNumericString(value: unknown): boolean {
  return (
    integer(value) !== null ||
    (typeof value === "string" && /^\d{1,20}$/.test(value))
  );
}
