/** Validate the exact attempt scope and every bounded archived observation row. */
export function hasUnambiguousCarLapObservationScope({
  runIds,
  sessionUids,
  carIndexes,
  attemptKeys,
  observationOffsets,
}) {
  const exactlyOneValue = (values) =>
    Array.isArray(values) &&
    values.length === 1 &&
    typeof values[0] === "string" &&
    values[0].length > 0;
  return (
    exactlyOneValue(runIds) &&
    exactlyOneValue(sessionUids) &&
    exactlyOneValue(carIndexes) &&
    exactlyOneValue(attemptKeys) &&
    Array.isArray(observationOffsets) && observationOffsets.length <= 1
  );
}

export function isMatchingCarLapObservationPage(
  value,
  runId,
  sessionUid,
  carIndex,
  attemptKey,
  offset,
) {
  if (
    !isRecord(value) ||
    value.schema_version !== 1 ||
    value.run_id !== runId ||
    value.session_uid !== sessionUid ||
    value.car_index !== carIndex ||
    value.status !== "available" ||
    value.verification_scope !== "exact_admitted_slot_lap_observations" ||
    value.opponent_eligibility !== "not_assessed" ||
    value.reference_eligible !== false ||
    value.coaching_eligible !== false ||
    !isRecord(value.attempt) ||
    value.attempt.attempt_key !== attemptKey ||
    !isRecord(value.capture) ||
    value.capture.archive_status !== "available" ||
    typeof value.capture.complete !== "boolean" ||
    !nullableShortString(value.capture.footer_status, 96) ||
    !isRecord(value.capture.replay_quality) ||
    !isRecord(value.observations) ||
    value.observations.limit !== 50 ||
    value.observations.offset !== offset ||
    !isSafeCount(value.observations.total, 4_194_304) ||
    value.observations.verified_row_count !== value.observations.total ||
    !Array.isArray(value.observations.items) ||
    value.observations.items.length > 50 ||
    value.observations.returned !== value.observations.items.length ||
    value.observations.returned >
      Math.max(0, value.observations.total - offset) ||
    !isSafeCount(value.observations.source_chunks_read, 256) ||
    !Array.isArray(value.source_chunks) ||
    value.source_chunks.length < 1 ||
    value.source_chunks.length > 256 ||
    value.observations.source_chunks_read !== value.source_chunks.length
  ) {
    return false;
  }

  const attempt = value.attempt;
  if (
    (attempt.packet_format !== 2025 && attempt.packet_format !== 2026) ||
    !isSafeCount(attempt.lifecycle_epoch, Number.MAX_SAFE_INTEGER) ||
    attempt.car_index !== carIndex ||
    !isSafeCount(attempt.tenure_ordinal, 100_000) ||
    attempt.tenure_ordinal === 0 ||
    !isSafeCount(attempt.attempt_number, 100_000) ||
    attempt.attempt_number === 0 ||
    !isSafeCount(attempt.lap_number, Number.MAX_SAFE_INTEGER) ||
    !["completed", "partial", "abandoned"].includes(attempt.disposition) ||
    (attempt.lap_time_ms !== null &&
      !isSafeCount(attempt.lap_time_ms, Number.MAX_SAFE_INTEGER)) ||
    (attempt.game_valid !== null && typeof attempt.game_valid !== "boolean") ||
    typeof attempt.start_observed !== "boolean" ||
    typeof attempt.pit_encountered !== "boolean" ||
    !isSafeCount(attempt.sample_count, Number.MAX_SAFE_INTEGER) ||
    attempt.reference_eligible !== false ||
    attempt.coaching_eligible !== false ||
    !Array.isArray(attempt.exclusion_reasons) ||
    attempt.exclusion_reasons.length > 64 ||
    !attempt.exclusion_reasons.every((reason) => isShortString(reason, 96)) ||
    !isSafeCount(attempt.start_frame_ordinal, Number.MAX_SAFE_INTEGER) ||
    attempt.start_frame_ordinal === 0 ||
    !isSafeCount(attempt.end_frame_ordinal, Number.MAX_SAFE_INTEGER) ||
    attempt.end_frame_ordinal < attempt.start_frame_ordinal ||
    (attempt.completion_frame_ordinal !== null &&
      (!isSafeCount(attempt.completion_frame_ordinal, Number.MAX_SAFE_INTEGER) ||
        attempt.completion_frame_ordinal <= attempt.end_frame_ordinal)) ||
    !isRecord(attempt.tenure)
  ) {
    return false;
  }

  const tenure = attempt.tenure;
  const expectedAttemptKey =
    `${runId}:car-lap:${sessionUid}:${attempt.packet_format}:` +
    `${attempt.lifecycle_epoch}:${carIndex}:${attempt.tenure_ordinal}:` +
    `${attempt.attempt_number}`;
  if (
    attempt.attempt_key !== expectedAttemptKey ||
    !isSafeCount(tenure.ordinal, 100_000) ||
    tenure.ordinal !== attempt.tenure_ordinal ||
    !isSafeCount(tenure.start_frame_ordinal, Number.MAX_SAFE_INTEGER) ||
    tenure.start_frame_ordinal === 0 ||
    !isSafeCount(
      tenure.end_frame_ordinal_exclusive,
      Number.MAX_SAFE_INTEGER,
    ) ||
    tenure.end_frame_ordinal_exclusive <= tenure.start_frame_ordinal ||
    tenure.start_frame_ordinal > attempt.start_frame_ordinal ||
    attempt.end_frame_ordinal >= tenure.end_frame_ordinal_exclusive ||
    !isSafeCount(tenure.participant_frame_identifier, Number.MAX_SAFE_INTEGER) ||
    typeof tenure.participant_packet_fingerprint !== "string" ||
    !/^[a-f0-9]{64}$/.test(tenure.participant_packet_fingerprint) ||
    !isShortString(tenure.close_reason, 96)
  ) {
    return false;
  }

  const { replay_quality: replayQuality } = value.capture;
  if (
    ![
      "late_packets_ignored",
      "frame_overflow_packets_dropped",
      "conflicting_observation_frames",
    ].every((key) => nullableSafeCount(replayQuality[key]))
  ) {
    return false;
  }

  const chunkKeys = new Set();
  for (const chunk of value.source_chunks) {
    if (
      !isRecord(chunk) ||
      chunk.packet_format !== attempt.packet_format ||
      chunk.lifecycle_epoch !== attempt.lifecycle_epoch ||
      !isSafeCount(chunk.chunk_ordinal, Number.MAX_SAFE_INTEGER) ||
      !isSafeCount(chunk.row_count, 131_072) ||
      typeof chunk.sha256 !== "string" ||
      !/^[a-f0-9]{64}$/.test(chunk.sha256) ||
      chunk.schema_version !== 1
    ) {
      return false;
    }
    const chunkKey = `${chunk.packet_format}:${chunk.lifecycle_epoch}:${chunk.chunk_ordinal}`;
    if (chunkKeys.has(chunkKey)) return false;
    chunkKeys.add(chunkKey);
  }

  let previousFrameOrdinal = null;
  return value.observations.items.every((item) => {
    if (!isObservationRow(item, value, attempt)) return false;
    if (
      previousFrameOrdinal !== null &&
      item.frame_ordinal < previousFrameOrdinal
    ) {
      return false;
    }
    previousFrameOrdinal = item.frame_ordinal;
    return true;
  });
}

function isObservationRow(item, envelope, attempt) {
  return (
    isRecord(item) &&
    item.session_uid === envelope.session_uid &&
    item.car_index === envelope.car_index &&
    item.packet_format === attempt.packet_format &&
    item.lifecycle_epoch === attempt.lifecycle_epoch &&
    isSafeCount(item.frame_identifier, Number.MAX_SAFE_INTEGER) &&
    isSafeCount(item.frame_ordinal, Number.MAX_SAFE_INTEGER) &&
    item.frame_ordinal >= attempt.start_frame_ordinal &&
    item.frame_ordinal <= attempt.end_frame_ordinal &&
    item.frame_ordinal >= attempt.tenure.start_frame_ordinal &&
    item.frame_ordinal < attempt.tenure.end_frame_ordinal_exclusive &&
    item.frame_ordinal !== attempt.completion_frame_ordinal &&
    typeof item.session_time_s === "number" &&
    Number.isFinite(item.session_time_s) &&
    isSafeCount(item.lap_number, Number.MAX_SAFE_INTEGER) &&
    isSafeCount(item.current_lap_time_ms, Number.MAX_SAFE_INTEGER) &&
    nullableFinite(item.lap_distance_m) &&
    nullableFinite(item.total_distance_m) &&
    nullableFinite(item.speed_mps) &&
    nullableFinite(item.throttle) &&
    nullableFinite(item.brake) &&
    nullableFinite(item.steering) &&
    nullableSafeInteger(item.gear) &&
    nullableSafeCount(item.engine_rpm) &&
    (item.drs_active === null || typeof item.drs_active === "boolean") &&
    typeof item.car_telemetry_available === "boolean" &&
    nullableShortString(item.car_telemetry_unavailable_reason, 96) &&
    typeof item.motion_available === "boolean" &&
    nullableShortString(item.motion_unavailable_reason, 96) &&
    [
      item.world_position_x_m,
      item.world_position_y_m,
      item.world_position_z_m,
      item.world_velocity_x_mps,
      item.world_velocity_y_mps,
      item.world_velocity_z_mps,
      item.g_force_lateral,
      item.g_force_longitudinal,
      item.g_force_vertical,
    ].every(nullableFinite) &&
    (item.context === null || isContext(item.context)) &&
    Array.isArray(item.validation_flags) &&
    item.validation_flags.length <= 64 &&
    item.validation_flags.every((flag) => isShortString(flag, 96))
  );
}

function isContext(value) {
  return (
    isRecord(value) &&
    Object.keys(value).length <= 24 &&
    Object.values(value).every(
      (field) =>
        field === null ||
        (typeof field === "string" && field.length <= 128) ||
        (typeof field === "number" && Number.isSafeInteger(field)),
    )
  );
}

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSafeCount(value, maximum) {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0 &&
    value <= maximum
  );
}

function nullableSafeCount(value) {
  return value === null || isSafeCount(value, Number.MAX_SAFE_INTEGER);
}

function nullableSafeInteger(value) {
  return (
    value === null ||
    (typeof value === "number" && Number.isSafeInteger(value))
  );
}

function nullableFinite(value) {
  return value === null || (typeof value === "number" && Number.isFinite(value));
}

function isShortString(value, maximumLength) {
  return typeof value === "string" && value.length <= maximumLength;
}

function nullableShortString(value, maximumLength) {
  return value === null || isShortString(value, maximumLength);
}
