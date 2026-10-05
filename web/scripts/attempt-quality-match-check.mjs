import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(
  resolve(webRoot, "src/lib/attempt-quality-match.ts"),
  "utf8",
);
const javascript = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const commonJsModule = { exports: {} };
new Function("module", "exports", javascript)(
  commonJsModule,
  commonJsModule.exports,
);
const { attemptQualityReportMatchesSelection } = commonJsModule.exports;

const attempt = selectedAttempt();
const valid = report(attempt);
assert.equal(attemptQualityReportMatchesSelection(valid, attempt), true);

const stale = structuredClone(valid);
stale.identity.trace_sha256 = "b".repeat(64);
assert.equal(attemptQualityReportMatchesSelection(stale, attempt), false);

const malformed = structuredClone(valid);
malformed.evidence.recording_observer.counters = [];
assert.equal(attemptQualityReportMatchesSelection(malformed, attempt), false);

const overLimit = structuredClone(valid);
overLimit.context.segments = Array.from({ length: 1_025 }, () => ({
  from_frame_identifier: 1,
  context: null,
}));
assert.equal(attemptQualityReportMatchesSelection(overLimit, attempt), false);

const wrongAttempt = { ...attempt, attempt_key: "another-attempt" };
assert.equal(attemptQualityReportMatchesSelection(valid, wrongAttempt), false);

const impossibleChannelCount = structuredClone(valid);
impossibleChannelCount.channels.speed_mps.available_samples = 999_999;
assert.equal(
  attemptQualityReportMatchesSelection(impossibleChannelCount, attempt),
  false,
);

const unknownChannelStatus = structuredClone(valid);
unknownChannelStatus.channels.speed_mps.status = "mystery";
assert.equal(
  attemptQualityReportMatchesSelection(unknownChannelStatus, attempt),
  false,
);

console.log(
  "Attempt quality selection, schema, nested-shape, and resource-bound checks passed.",
);

function selectedAttempt() {
  return {
    attempt_key: "run:42:0:1",
    run_id: "run",
    session_uid: "42",
    car_index: 0,
    attempt_number: 1,
    disposition: "partial",
    lap_time_ms: null,
    game_valid: false,
    reference_eligible: false,
    start_observed: true,
    pit_encountered: false,
    exclusion_reasons: ["lap_not_completed"],
    trace_row_count: 4,
    trace_schema_version: 4,
    trace_sha256: "a".repeat(64),
  };
}

function report(attempt) {
  const rowCount = attempt.trace_row_count;
  const channels = {};
  for (const key of [
    "time_s",
    "speed_mps",
    "throttle",
    "brake",
    "steering",
    "gear",
    "drs_active",
    "motion_available",
    "world_position",
    "world_velocity",
  ]) {
    channels[key] = {
      status: "available",
      available_samples: rowCount,
      sample_count: rowCount,
      missing_samples: 0,
    };
  }

  const statusFields = ["fuel_mix", "actual_tyre_compound"];
  const observedStatus = {};
  const firstLast = {};
  for (const field of statusFields) {
    observedStatus[field] = {
      valid_count: 0,
      missing_count: rowCount,
      invalid_count: 0,
    };
    firstLast[field] = { first: null, last: null };
  }

  const damageFields = ["tyre_wear_rl_percent", "drs_fault"];
  const damageCounts = {};
  const damageAnchors = {};
  for (const field of damageFields) {
    damageCounts[field] = {
      valid_count: 0,
      missing_count: rowCount,
      invalid_count: 0,
    };
    damageAnchors[field] = { first: null, last: null };
  }

  const counters = {
    elapsed_ms: null,
    received: null,
    queued: null,
    recorded: null,
    queue_dropped: null,
    socket_errors: null,
    unpersisted_on_shutdown: null,
  };
  const counterEvidence = Object.fromEntries(
    Object.keys(counters).map((key) => [key, "missing"]),
  );
  const replayCounters = {
    import_late_packets_ignored: 0,
    import_frame_overflow_packets_dropped: 0,
  };
  const replayEvidence = Object.fromEntries(
    Object.keys(replayCounters).map((key) => [key, "available"]),
  );
  const observerCounters = {
    late_packets_ignored: null,
    frame_overflow_packets_dropped: null,
  };
  const observerCounterEvidence = Object.fromEntries(
    Object.keys(observerCounters).map((key) => [key, "missing"]),
  );

  return {
    report_version: 2,
    analysis_version: "attempt-telemetry-quality-v3-car-damage",
    identity: {
      attempt_key: attempt.attempt_key,
      run_id: attempt.run_id,
      session_uid: attempt.session_uid,
      car_index: attempt.car_index,
      attempt_number: attempt.attempt_number,
      trace_sha256: attempt.trace_sha256,
      trace_schema_version: attempt.trace_schema_version,
      trace_row_count: rowCount,
      trace_checksum_verified: true,
    },
    attempt: {
      disposition: attempt.disposition,
      lap_time_ms: attempt.lap_time_ms,
      game_valid: attempt.game_valid,
      reference_eligible: attempt.reference_eligible,
      start_observed: attempt.start_observed,
      pit_encountered: attempt.pit_encountered,
      exclusion_reasons: [...attempt.exclusion_reasons],
    },
    context: {
      segments: [
        {
          from_frame_identifier: 1,
          context: {
            session_uid: attempt.session_uid,
            track_name: "Melbourne",
            track_length_m: 100,
            session_type: "race",
            game_mode: "race",
          },
        },
      ],
      session_types: ["race"],
      game_modes: ["race"],
      track_names: ["Melbourne"],
    },
    evidence: {
      recording: {
        capture_complete: false,
        footer_available: false,
        footer_evidence_status: "unavailable",
        footer_status: null,
        footer_status_evidence: "missing",
        counters,
        counter_evidence: counterEvidence,
      },
      capture_decode: {
        available: true,
        capture_wide_counters: { missing_car_telemetry_lap_sample_count: 1 },
      },
      replay_assembly: {
        available: true,
        counters: replayCounters,
        counter_evidence: replayEvidence,
      },
      recording_observer: {
        available: false,
        source_scope: "raw_capture_processing_before_import",
        counters: observerCounters,
        counter_evidence: observerCounterEvidence,
      },
      attempt_trace: { sample_count: rowCount },
    },
    channels,
    observed_status: {
      status: "available",
      matched_sample_count: 0,
      sample_count: rowCount,
      missing_join_sample_count: rowCount,
      unavailable_reason_counts: { status_reason_unavailable: rowCount },
      fields: observedStatus,
      first_last_observed: firstLast,
      discrete_changes: [],
      discrete_changes_truncated: false,
      distinct_compounds: {
        actual: [
          { raw_id: 16, formula_id: 0, label: "C5" },
          { raw_id: 255, formula_id: null, label: null },
        ],
        visual: [{ raw_id: 18, formula_id: 2, label: "S" }],
      },
      fuel_quantity_unit_note: "reported quantity; unit unspecified",
    },
    car_damage_observations: {
      version: "car-damage-observations-v1",
      authority: "diagnostic_only_sparse_observation",
      observation_note: "Exact-frame observations only.",
      status: "no_joined_samples",
      sample_count: rowCount,
      matched_sample_count: 0,
      missing_join_sample_count: rowCount,
      unavailable_reason_counts: { damage_packet_missing: rowCount },
      fields: damageCounts,
      first_last_observed: damageAnchors,
    },
    continuity: {
      sample_adjacency_count: rowCount - 1,
      frame_gaps: {
        count: 0,
        largest_missing_frame_count: 0,
        order_discontinuity_count: 0,
      },
      lap_clock: {
        gap_threshold_s: 0.1,
        gap_count: 0,
        regression_count: 0,
        largest_nonnegative_gap_s: 0.1,
      },
      session_time: {
        gap_threshold_s: 0.1,
        gap_count: 0,
        regression_count: 0,
        largest_nonnegative_gap_s: 0.1,
      },
      lap_distance: {
        gap_threshold_m: 25,
        gap_count: 0,
        regression_count: 0,
        largest_nonnegative_gap_m: 10,
      },
      examples: [],
      examples_truncated: false,
    },
    distance_support: {
      resolution_m: 1,
      track_length_m: 100,
      track_length_source: "attempt_session_context",
      track_length_unavailable_reason: null,
      observed_distance_range_m: [10, 40],
      status: "available",
      reason: null,
      resampling_config: {
        grid_step_m: 1,
        max_bracket_time_s: 0.1,
        max_bracket_distance_m: 25,
      },
      channels: Object.fromEntries(
        [
          "time_s",
          "speed_mps",
          "throttle",
          "brake",
          "steering",
          "gear",
          "drs_active",
        ].map((key) => [
          key,
          {
            observed_range_coverage: 1,
            observed_grid_point_count: 31,
            full_track_coverage: 31 / 101,
            full_track_grid_point_count: 101,
          },
        ]),
      ),
    },
  };
}
