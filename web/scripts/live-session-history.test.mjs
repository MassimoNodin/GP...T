import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/lib/live.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const live = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

function row(lapIndex) {
  return {
    lap_index: lapIndex,
    lap_number: lapIndex + 1,
    lap_time_ms: 90_000,
    lap_time_available: true,
    lap_time_unavailable_reason: null,
    sector1_time_ms: 25_000,
    sector1_time_ms_part: 25_000,
    sector1_time_minutes_part: 0,
    sector1_time_available: true,
    sector1_time_unavailable_reason: null,
    sector2_time_ms: 30_000,
    sector2_time_ms_part: 30_000,
    sector2_time_minutes_part: 0,
    sector2_time_available: true,
    sector2_time_unavailable_reason: null,
    sector3_time_ms: 35_000,
    sector3_time_ms_part: 35_000,
    sector3_time_minutes_part: 0,
    sector3_time_available: true,
    sector3_time_unavailable_reason: null,
    validity_flags: 0x0f,
    lap_valid: true,
    sector1_valid: true,
    sector2_valid: true,
    sector3_valid: true,
    unknown_validity_bits: 0,
  };
}

function monitor(overrides = {}) {
  return {
    status: "fresh",
    reason: null,
    age_ms: 0,
    observation_count: 3,
    session_uid: "18446744073709551615",
    packet_format: 2026,
    player_car_index: 23,
    frame_identifier: 200,
    source_frame_identifier: 200,
    session_time_s: 101.5,
    populated_row_count: 100,
    omitted_row_count: 90,
    rows: Array.from({ length: 10 }, (_, index) => row(index + 90)),
    ...overrides,
  };
}

test("accepts only the latest bounded source rows with complete provenance", () => {
  const parsed = live.readSessionHistoryMonitor(monitor());
  assert.equal(parsed.rows.length, 10);
  assert.equal(parsed.rows[0].lap_number, 91);
  assert.equal(parsed.rows[9].lap_number, 100);
  assert.equal(parsed.player_car_index, 23);
});

test("accepts empty stale errors with provenance and preserves long session times", () => {
  const staleError = live.readSessionHistoryMonitor(monitor({
    status: "stale",
    reason: "conflicting_selected_player_histories",
    age_ms: 501,
    session_time_s: null,
    populated_row_count: 0,
    omitted_row_count: 0,
    rows: [],
  }));
  assert.equal(staleError.reason, "conflicting_selected_player_histories");
  assert.deepEqual(staleError.rows, []);

  const longSession = live.readSessionHistoryMonitor(monitor({ session_time_s: 90_000 }));
  assert.equal(longSession.session_time_s, 90_000);
});

test("rejects older rows presented as the latest ten", () => {
  assert.equal(
    live.readSessionHistoryMonitor(
      monitor({ rows: Array.from({ length: 10 }, (_, index) => row(index + 1)) }),
    ),
    null,
  );
});

test("rejects sector values that disagree with reported components or availability", () => {
  const inconsistentTotal = row(0);
  inconsistentTotal.sector1_time_ms = 25_001;
  assert.equal(
    live.readSessionHistoryMonitor(monitor({
      populated_row_count: 1,
      omitted_row_count: 0,
      rows: [inconsistentTotal],
    })),
    null,
  );

  const falseUnavailable = row(0);
  falseUnavailable.sector1_time_ms = null;
  falseUnavailable.sector1_time_available = false;
  falseUnavailable.sector1_time_unavailable_reason = "reported_zero";
  assert.equal(
    live.readSessionHistoryMonitor(monitor({
      populated_row_count: 1,
      omitted_row_count: 0,
      rows: [falseUnavailable],
    })),
    null,
  );
});

test("fails closed on missing fresh identity and unbounded rows", () => {
  assert.equal(
    live.readSessionHistoryMonitor(monitor({ session_uid: null })),
    null,
  );
  assert.equal(
    live.readSessionHistoryMonitor(monitor({ rows: Array.from({ length: 11 }, (_, i) => row(i)) })),
    null,
  );
});
