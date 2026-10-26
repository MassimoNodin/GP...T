import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(
  resolve(webRoot, "src/lib/attempt-trace-chart-match.ts"),
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
const { attemptTraceChartReportMatchesSelection } = commonJsModule.exports;

const selectedAttempt = attempt();
assert.equal(
  attemptTraceChartReportMatchesSelection(report(), selectedAttempt),
  true,
);
const attemptWithDifferentAggregateCount = attempt({ sample_count: 12 });
assert.equal(
  attemptTraceChartReportMatchesSelection(
    report(attemptWithDifferentAggregateCount),
    attemptWithDifferentAggregateCount,
  ),
  true,
);

for (const change of [
  (candidate) => {
    candidate.report_version = 2;
  },
  (candidate) => {
    candidate.diagnostic_only = false;
  },
  (candidate) => {
    candidate.source.attempt_number += 1;
  },
  (candidate) => {
    candidate.source.run_id = "f".repeat(64);
  },
  (candidate) => {
    candidate.source.session_uid = "999";
  },
  (candidate) => {
    candidate.source.car_index = 1;
  },
  (candidate) => {
    candidate.source.trace_sha256 = "c".repeat(64);
  },
  (candidate) => {
    candidate.source.trace_schema_version = 2;
  },
  (candidate) => {
    candidate.source.trace_row_count += 1;
  },
  (candidate) => {
    candidate.source.trace_checksum_verified = false;
  },
  (candidate) => {
    candidate.preview.point_limit_per_channel = 20_000;
  },
  (candidate) => {
    candidate.channels.speed.unit = "m/s";
  },
  (candidate) => {
    candidate.channels.throttle.source_field = "brake";
  },
  (candidate) => {
    candidate.channels.steering.observed_value_range = [-1e308, 1e308];
  },
  (candidate) => {
    candidate.channels.speed.segments[0].points[0].value = Infinity;
  },
  (candidate) => {
    candidate.channels.brake.segments[0].points[0].session_time_s = -1;
  },
  (candidate) => {
    candidate.channels.throttle.segments[0].points[1].session_time_s = 1e9;
  },
  (candidate) => {
    candidate.channels.speed.segments[0].points[0].frame_identifier = 9;
  },
  (candidate) => {
    candidate.channels.speed.segments[0].start_anchor.frame_identifier = 99;
  },
  (candidate) => {
    candidate.channels.speed.segments[0].points[1].frame_identifier = 1;
  },
  (candidate) => {
    candidate.channels.speed.rendered_point_count = 3;
  },
  (candidate) => {
    candidate.channels.brake.missing_value_sample_count = 1;
  },
  (candidate) => {
    candidate.channels.steering.rendered_run_count = 257;
  },
  (candidate) => {
    candidate.context.game_modes = [null];
  },
]) {
  const candidate = report();
  change(candidate);
  assert.equal(
    attemptTraceChartReportMatchesSelection(candidate, selectedAttempt),
    false,
  );
}

const raceAttempt = attempt({
  disposition: "abandoned",
  game_valid: false,
  reference_eligible: false,
});
const raceReport = report(raceAttempt);
raceReport.context.game_modes = ["race"];
raceReport.context.session_types = ["race"];
assert.equal(
  attemptTraceChartReportMatchesSelection(raceReport, raceAttempt),
  true,
);

const fastTraceReport = report();
fastTraceReport.channels.speed.segments[0].points[0].value = 1_200;
fastTraceReport.channels.speed.segments[0].points[1].value = 1_500;
fastTraceReport.channels.speed.observed_value_range = [1_200, 1_500];
assert.equal(
  attemptTraceChartReportMatchesSelection(fastTraceReport, selectedAttempt),
  true,
);

const longSessionReport = report();
for (const channel of Object.values(longSessionReport.channels)) {
  const segment = channel.segments[0];
  segment.points[0].session_time_s = 604_801;
  segment.points[1].session_time_s = 604_801.016;
  segment.start_anchor = anchor(segment.points[0]);
  segment.end_anchor = anchor(segment.points[1]);
}
assert.equal(
  attemptTraceChartReportMatchesSelection(longSessionReport, selectedAttempt),
  true,
);

console.log(
  "Attempt trace chart identity, checksum, units, bounds, and shape checks passed.",
);

function attempt(overrides = {}) {
  const runId = "a".repeat(64);
  const traceSha256 = "b".repeat(64);
  return {
    attempt_key: `${runId}:14237356543050158953:0:2`,
    run_id: runId,
    session_uid: "14237356543050158953",
    car_index: 0,
    attempt_number: 2,
    disposition: "completed",
    lap_time_ms: 100_000,
    game_valid: true,
    reference_eligible: true,
    start_observed: true,
    pit_encountered: false,
    exclusion_reasons: [],
    sample_count: 2,
    trace_row_count: 2,
    trace_schema_version: 3,
    trace_sha256: traceSha256,
    ...overrides,
  };
}

function report(selected = attempt()) {
  const points = [
    { frame_identifier: 1, session_time_s: 0.016, lap_distance_m: 0, value: 0 },
    { frame_identifier: 2, session_time_s: 0.032, lap_distance_m: 1, value: 1 },
  ];
  return {
    report_version: 1,
    artifact_kind: "single_attempt_player_trace_preview",
    diagnostic_only: true,
    status: "observed",
    source: {
      attempt_key: selected.attempt_key,
      run_id: selected.run_id,
      session_uid: selected.session_uid,
      car_index: selected.car_index,
      attempt_number: selected.attempt_number,
      disposition: selected.disposition,
      lap_time_ms: selected.lap_time_ms,
      game_valid: selected.game_valid,
      reference_eligible: selected.reference_eligible,
      start_observed: selected.start_observed,
      pit_encountered: selected.pit_encountered,
      exclusion_reasons: selected.exclusion_reasons,
      trace_sha256: selected.trace_sha256,
      trace_schema_version: selected.trace_schema_version,
      trace_row_count: selected.trace_row_count,
      trace_checksum_verified: true,
    },
    context: {
      segments: [
        {
          from_frame_identifier: 1,
          game_mode: "grand_prix",
          session_type: "time_trial",
          track_name: "Test Circuit",
        },
      ],
      game_modes: ["grand_prix"],
      session_types: ["time_trial"],
      track_names: ["Test Circuit"],
    },
    channels: {
      speed: channel("Speed", "speed_mps", "m/s", "km/h", [20, 30]),
      throttle: channel("Throttle", "throttle", "ratio", "percent", [0, 100]),
      brake: channel("Brake", "brake", "ratio", "percent", [0, 50]),
      steering: channel(
        "Steering input",
        "steering",
        "ratio",
        "normalized",
        [-1, 1],
      ),
    },
    preview: {
      point_limit_per_channel: 2000,
      run_limit_per_channel: 256,
      source_row_limit: 100_000,
      source_byte_limit: 64 * 1024 * 1024,
      source_context_segment_limit: 1024,
      source_context_byte_limit: 4 * 1024 * 1024,
      thinning_method: "evenly_spaced_per_run_with_endpoints",
    },
    continuity_policy: {
      frame_delta: "exactly one, with uint32 wrap allowed",
      maximum_session_time_gap_s: 0.1,
      session_time_tolerance:
        "one float32 ULP at the larger endpoint magnitude",
      missing_channel_values: "break only that channel's displayed run",
      coordinate: "stored session_time_s; source sample order preserved",
    },
  };

  function channel(label, sourceField, sourceUnit, unit, values) {
    const channelPoints = points.map((point, index) => ({
      ...point,
      value: values[index] ?? point.value,
    }));
    const startAnchor = anchor(channelPoints[0]);
    const endAnchor = anchor(channelPoints.at(-1));
    const minimum = Math.min(...channelPoints.map((point) => point.value));
    const maximum = Math.max(...channelPoints.map((point) => point.value));
    return {
      label,
      source_field: sourceField,
      source_unit: sourceUnit,
      unit,
      source_sample_count: 2,
      observed_sample_count: 2,
      unsupported_sample_count: 0,
      missing_value_sample_count: 0,
      invalid_anchor_sample_count: 0,
      observed_value_range: [minimum, maximum],
      source_run_count: 1,
      rendered_run_count: 1,
      omitted_run_count: 0,
      rendered_point_count: 2,
      omitted_point_count: 0,
      thinned_sample_count: 0,
      segments: [
        {
          run_index: 0,
          break_before_reasons: ["start"],
          source_sample_count: 2,
          rendered_point_count: 2,
          start_anchor: startAnchor,
          end_anchor: endAnchor,
          points: channelPoints,
        },
      ],
    };
  }
}

function anchor(point) {
  return {
    frame_identifier: point.frame_identifier,
    session_time_s: point.session_time_s,
    lap_distance_m: point.lap_distance_m,
  };
}
