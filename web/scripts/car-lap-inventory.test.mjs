import assert from "node:assert/strict";
import test from "node:test";
import { isMatchingCarLapInventory } from "../src/lib/car-lap-inventory.mjs";

function attempt(overrides = {}) {
  return {
    attempt_key: "r".repeat(64) + ":car-lap:18446744073709550001:2026:0:23:1:1",
    packet_format: 2026,
    lifecycle_epoch: 0,
    car_index: 23,
    tenure_ordinal: 1,
    attempt_number: 1,
    lap_number: 2,
    disposition: "completed",
    lap_time_ms: 91250,
    game_valid: true,
    start_observed: true,
    pit_encountered: false,
    sample_count: 120,
    source_frame_ordinals: { start: 100, end: 200, completion: 200 },
    tenure: {
      ordinal: 1,
      start_frame_ordinal: 90,
      end_frame_ordinal_exclusive: 220,
      participant_frame_identifier: 500,
      participant_packet_fingerprint: "a".repeat(64),
      close_reason: "capture_ended",
    },
    exclusion_reasons: [],
    context_segments: [],
    reference_eligible: false,
    coaching_eligible: false,
    ...overrides,
  };
}

function inventory(overrides = {}) {
  return {
    run_id: "r".repeat(64),
    session_uid: "18446744073709550001",
    car_index: 23,
    status: "assessed",
    coverage_status: "bounded",
    verification_scope: "admitted_participants_and_lap_data",
    reference_eligibility: "not_assessed",
    coaching_eligible: false,
    tenure_count: 1,
    attempts: {
      limit: 50,
      offset: 0,
      total: 1,
      returned: 1,
      items: [attempt()],
    },
    unassociated_lap_observation_counts: [],
    ...overrides,
  };
}

test("accepts a bounded diagnostic page only for the exact selected identity", () => {
  assert.equal(
    isMatchingCarLapInventory(
      inventory(),
      "r".repeat(64),
      "18446744073709550001",
      23,
      0,
    ),
    true,
  );
});

test("accepts the not-assessed state for older imports", () => {
  assert.equal(
    isMatchingCarLapInventory(
      inventory({
        status: "not_assessed",
        attempts: { limit: 50, offset: 0, total: 0, returned: 0, items: [] },
        unassociated_lap_observation_counts: [],
      }),
      "r".repeat(64),
      "18446744073709550001",
      23,
      0,
    ),
    true,
  );
});

test("rejects stale paging, changed identity, authority flags, and malformed rows", () => {
  const expected = ["r".repeat(64), "18446744073709550001", 23, 0];
  const wrongPages = [
    inventory({ attempts: { ...inventory().attempts, offset: 50 } }),
    inventory({ attempts: { ...inventory().attempts, limit: 100 } }),
    inventory({ ...inventory(), car_index: 22 }),
  ];
  for (const value of wrongPages) {
    assert.equal(isMatchingCarLapInventory(value, ...expected), false);
  }
  assert.equal(
    isMatchingCarLapInventory(
      inventory({
        attempts: {
          ...inventory().attempts,
          items: [attempt({ coaching_eligible: true })],
        },
      }),
      ...expected,
    ),
    false,
  );
  assert.equal(
    isMatchingCarLapInventory(
      inventory({
        attempts: {
          ...inventory().attempts,
          items: [attempt({ tenure: { ordinal: 1 } })],
        },
      }),
      ...expected,
    ),
    false,
  );
  const invalidPages = [
    inventory({ attempts: { ...inventory().attempts, total: 0 } }),
    inventory({
      attempts: {
        ...inventory().attempts,
        offset: 50,
        total: 1,
        returned: 1,
        items: [attempt()],
      },
    }),
    inventory({
      attempts: {
        ...inventory().attempts,
        items: [attempt({ attempt_key: "other-run:car-lap:18446744073709550001:2026:0:23:1:1" })],
      },
    }),
    inventory({
      attempts: {
        ...inventory().attempts,
        items: [attempt({ source_frame_ordinals: { start: 80, end: 200, completion: 200 } })],
      },
    }),
    inventory({
      attempts: {
        ...inventory().attempts,
        items: [attempt({ source_frame_ordinals: { start: 100, end: 220, completion: 220 } })],
      },
    }),
  ];
  for (const value of invalidPages) {
    assert.equal(isMatchingCarLapInventory(value, ...expected), false);
  }
});
