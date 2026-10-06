import assert from "node:assert/strict";
import test from "node:test";
import {
  hasUnambiguousCarLapObservationScope,
  isMatchingCarLapObservationPage,
} from "../src/lib/car-lap-observations.mjs";

const runId = "a".repeat(64);
const sessionUid = "18446744073709550001";
const carIndex = 23;
const attemptKey = `${runId}:car-lap:${sessionUid}:2026:2:23:3:5`;

function validPage() {
  const tenure = {
    ordinal: 3,
    start_frame_ordinal: 90,
    end_frame_ordinal_exclusive: 220,
    participant_frame_identifier: 17,
    participant_packet_fingerprint: "b".repeat(64),
    close_reason: "session_end",
  };
  const attempt = {
    attempt_key: attemptKey,
    packet_format: 2026,
    lifecycle_epoch: 2,
    car_index: carIndex,
    tenure_ordinal: 3,
    attempt_number: 5,
    lap_number: 7,
    disposition: "completed",
    lap_time_ms: 91_234,
    game_valid: true,
    start_observed: true,
    pit_encountered: false,
    sample_count: 2,
    reference_eligible: false,
    coaching_eligible: false,
    exclusion_reasons: [],
    start_frame_ordinal: 100,
    end_frame_ordinal: 200,
    completion_frame_ordinal: 201,
    tenure,
  };
  const row = (frameOrdinal, frameIdentifier) => ({
    session_uid: sessionUid,
    frame_identifier: frameIdentifier,
    frame_ordinal: frameOrdinal,
    session_time_s: frameOrdinal / 10,
    car_index: carIndex,
    header_player_car_index: 0,
    packet_format: 2026,
    lifecycle_epoch: 2,
    lap_number: 7,
    lap_distance_m: frameOrdinal === 100 ? 0 : 999,
    total_distance_m: null,
    current_lap_time_ms: frameOrdinal * 10,
    speed_mps: 50,
    throttle: 0.8,
    brake: 0,
    steering: 0,
    gear: 5,
    engine_rpm: 9000,
    drs_active: false,
    car_telemetry_available: true,
    car_telemetry_unavailable_reason: null,
    motion_available: false,
    motion_unavailable_reason: "missing_motion_packet",
    world_position_x_m: null,
    world_position_y_m: null,
    world_position_z_m: null,
    world_velocity_x_mps: null,
    world_velocity_y_mps: null,
    world_velocity_z_mps: null,
    g_force_lateral: null,
    g_force_longitudinal: null,
    g_force_vertical: null,
    context: null,
    validation_flags: [],
  });
  return {
    schema_version: 1,
    run_id: runId,
    session_uid: sessionUid,
    car_index: carIndex,
    status: "available",
    verification_scope: "exact_admitted_slot_lap_observations",
    opponent_eligibility: "not_assessed",
    reference_eligible: false,
    coaching_eligible: false,
    attempt,
    capture: {
      archive_status: "available",
      complete: false,
      footer_status: "truncated_footer",
      replay_quality: {
        late_packets_ignored: 0,
        frame_overflow_packets_dropped: 1,
        conflicting_observation_frames: null,
      },
    },
    source_chunks: [
      {
        packet_format: 2026,
        lifecycle_epoch: 2,
        chunk_ordinal: 9,
        row_count: 10_000,
        sha256: "c".repeat(64),
        schema_version: 1,
      },
    ],
    observations: {
      limit: 50,
      offset: 0,
      total: 2,
      returned: 2,
      verified_row_count: 2,
      source_chunks_read: 1,
      items: [row(100, 10_000), row(200, 10_100)],
    },
  };
}

test("accepts one bounded page bound to an exact slot lap", () => {
  assert.equal(
    isMatchingCarLapObservationPage(
      validPage(),
      runId,
      sessionUid,
      carIndex,
      attemptKey,
      0,
    ),
    true,
  );
});

test("requires one run, session, slot, and attempt selector", () => {
  const scope = {
    runIds: [runId],
    sessionUids: [sessionUid],
    carIndexes: [String(carIndex)],
    attemptKeys: [attemptKey],
    observationOffsets: [],
  };
  assert.equal(hasUnambiguousCarLapObservationScope(scope), true);
  for (const key of ["runIds", "sessionUids", "carIndexes", "attemptKeys"]) {
    assert.equal(
      hasUnambiguousCarLapObservationScope({ ...scope, [key]: ["one", "two"] }),
      false,
    );
  }
  assert.equal(
    hasUnambiguousCarLapObservationScope({
      ...scope,
      observationOffsets: ["0", "50"],
    }),
    false,
  );
});

test("rejects substituted envelope identities, attempt keys, and offsets", () => {
  const page = validPage();
  assert.equal(isMatchingCarLapObservationPage(page, "d".repeat(64), sessionUid, carIndex, attemptKey, 0), false);
  assert.equal(isMatchingCarLapObservationPage(page, runId, "42", carIndex, attemptKey, 0), false);
  assert.equal(isMatchingCarLapObservationPage(page, runId, sessionUid, 22, attemptKey, 0), false);
  assert.equal(isMatchingCarLapObservationPage(page, runId, sessionUid, carIndex, "wrong", 0), false);
  assert.equal(isMatchingCarLapObservationPage(page, runId, sessionUid, carIndex, attemptKey, 50), false);
});

test("rejects rows outside the exact attempt and tenure, including its completion transition", () => {
  for (const frameOrdinal of [89, 201, 220]) {
    const page = validPage();
    page.observations.items[1].frame_ordinal = frameOrdinal;
    assert.equal(isMatchingCarLapObservationPage(page, runId, sessionUid, carIndex, attemptKey, 0), false);
  }
  const wrongUid = validPage();
  wrongUid.observations.items[0].session_uid = "42";
  assert.equal(isMatchingCarLapObservationPage(wrongUid, runId, sessionUid, carIndex, attemptKey, 0), false);
});

test("rejects unverified totals, malformed provenance, and source chunk identity drift", () => {
  const wrongCount = validPage();
  wrongCount.observations.verified_row_count += 1;
  assert.equal(isMatchingCarLapObservationPage(wrongCount, runId, sessionUid, carIndex, attemptKey, 0), false);

  const wrongFingerprint = validPage();
  wrongFingerprint.attempt.tenure.participant_packet_fingerprint = null;
  assert.equal(isMatchingCarLapObservationPage(wrongFingerprint, runId, sessionUid, carIndex, attemptKey, 0), false);

  const wrongChunk = validPage();
  wrongChunk.source_chunks[0].sha256 = null;
  assert.equal(isMatchingCarLapObservationPage(wrongChunk, runId, sessionUid, carIndex, attemptKey, 0), false);
});
