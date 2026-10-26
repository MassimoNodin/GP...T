/** Check the exact page identity and bounds before Sessions renders the response. */
export function isMatchingCarLapInventory(value, runId, sessionUid, carIndex, offset) {
  if (
    !isRecord(value) ||
    value.run_id !== runId ||
    value.session_uid !== sessionUid ||
    value.car_index !== carIndex ||
    !["not_assessed", "assessed", "truncated"].includes(value.status) ||
    value.verification_scope !== "admitted_participants_and_lap_data" ||
    value.reference_eligibility !== "not_assessed" ||
    !isRecord(value.attempts) ||
    value.attempts.limit !== 50 ||
    value.attempts.offset !== offset ||
    !isSafeCount(value.attempts.total, 100_000) ||
    !Array.isArray(value.attempts.items) ||
    value.attempts.items.length > 50 ||
    value.attempts.returned !== value.attempts.items.length ||
    value.attempts.returned > Math.max(0, value.attempts.total - offset) ||
    !Array.isArray(value.unassociated_lap_observation_counts) ||
    value.unassociated_lap_observation_counts.length > 8
  ) {
    return false;
  }

  if (value.status === "not_assessed") {
    if (
      value.attempts.total !== 0 ||
      value.attempts.returned !== 0 ||
      value.attempts.items.length !== 0 ||
      value.unassociated_lap_observation_counts.length !== 0
    ) {
      return false;
    }
  } else if (
    (value.coverage_status !== "partial" && value.coverage_status !== "bounded") ||
    value.coaching_eligible !== false ||
    !isSafeCount(value.tenure_count, 100_000)
  ) {
    return false;
  }

  return (
    value.attempts.items.every((item) =>
      isCarLapInventoryAttempt(item, runId, sessionUid, carIndex),
    ) &&
    value.unassociated_lap_observation_counts.every(
      (item) =>
        isRecord(item) &&
        typeof item.reason === "string" &&
        item.reason.length <= 96 &&
        isSafeCount(item.count, Number.MAX_SAFE_INTEGER),
    )
  );
}

function isCarLapInventoryAttempt(value, runId, sessionUid, carIndex) {
  if (
    !isRecord(value) ||
    typeof value.attempt_key !== "string" ||
    value.attempt_key.length < 1 ||
    value.attempt_key.length > 512 ||
    (value.packet_format !== 2025 && value.packet_format !== 2026) ||
    !isSafeCount(value.lifecycle_epoch, Number.MAX_SAFE_INTEGER) ||
    value.car_index !== carIndex ||
    !isSafeCount(value.tenure_ordinal, 100_000) ||
    value.tenure_ordinal === 0 ||
    !isSafeCount(value.attempt_number, 100_000) ||
    value.attempt_number === 0 ||
    !isSafeCount(value.lap_number, Number.MAX_SAFE_INTEGER) ||
    !["completed", "partial", "abandoned"].includes(value.disposition) ||
    (value.lap_time_ms !== null && !isSafeCount(value.lap_time_ms, Number.MAX_SAFE_INTEGER)) ||
    (value.game_valid !== null && typeof value.game_valid !== "boolean") ||
    typeof value.start_observed !== "boolean" ||
    typeof value.pit_encountered !== "boolean" ||
    !isSafeCount(value.sample_count, Number.MAX_SAFE_INTEGER) ||
    value.reference_eligible !== false ||
    value.coaching_eligible !== false ||
    !Array.isArray(value.exclusion_reasons) ||
    value.exclusion_reasons.length > 64 ||
    !value.exclusion_reasons.every(
      (reason) => typeof reason === "string" && reason.length <= 96,
    ) ||
    !Array.isArray(value.context_segments) ||
    value.context_segments.length > 64 ||
    !isRecord(value.source_frame_ordinals) ||
    !isSafeCount(value.source_frame_ordinals.start, Number.MAX_SAFE_INTEGER) ||
    !nullableSafeCount(value.source_frame_ordinals.end) ||
    !nullableSafeCount(value.source_frame_ordinals.completion) ||
    !isRecord(value.tenure)
  ) {
    return false;
  }
  if (
    value.source_frame_ordinals.start === 0 ||
    !isSafeCount(value.tenure.ordinal, 100_000) ||
    value.tenure.ordinal !== value.tenure_ordinal ||
    !isSafeCount(value.tenure.start_frame_ordinal, Number.MAX_SAFE_INTEGER) ||
    value.tenure.start_frame_ordinal === 0 ||
    !isSafeCount(value.tenure.end_frame_ordinal_exclusive, Number.MAX_SAFE_INTEGER) ||
    value.tenure.end_frame_ordinal_exclusive <= value.tenure.start_frame_ordinal ||
    !isSafeCount(value.tenure.participant_frame_identifier, Number.MAX_SAFE_INTEGER) ||
    typeof value.tenure.participant_packet_fingerprint !== "string" ||
    !/^[a-f0-9]{64}$/.test(value.tenure.participant_packet_fingerprint) ||
    typeof value.tenure.close_reason !== "string" ||
    value.tenure.close_reason.length > 96
  ) {
    return false;
  }
  const expectedAttemptKey =
    `${runId}:car-lap:${sessionUid}:${value.packet_format}:` +
    `${value.lifecycle_epoch}:${carIndex}:${value.tenure_ordinal}:${value.attempt_number}`;
  const tenureStart = value.tenure.start_frame_ordinal;
  const tenureEnd = value.tenure.end_frame_ordinal_exclusive;
  if (
    value.attempt_key !== expectedAttemptKey ||
    value.source_frame_ordinals.start < tenureStart ||
    value.source_frame_ordinals.start >= tenureEnd ||
    (value.source_frame_ordinals.end !== null &&
      (value.source_frame_ordinals.end < value.source_frame_ordinals.start ||
        value.source_frame_ordinals.end >= tenureEnd)) ||
    (value.source_frame_ordinals.completion !== null &&
      (value.source_frame_ordinals.completion < value.source_frame_ordinals.start ||
        value.source_frame_ordinals.completion >= tenureEnd))
  ) {
    return false;
  }
  return value.context_segments.every(
    (segment) =>
      isRecord(segment) &&
      isSafeCount(segment.from_frame_identifier, Number.MAX_SAFE_INTEGER) &&
      (segment.context === null ||
        (isRecord(segment.context) &&
          Object.keys(segment.context).length <= 24 &&
          Object.values(segment.context).every(
            (field) =>
              field === null ||
              (typeof field === "string" && field.length <= 128) ||
              (typeof field === "number" && Number.isSafeInteger(field)),
          ))),
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
