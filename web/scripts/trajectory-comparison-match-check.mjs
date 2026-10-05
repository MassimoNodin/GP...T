import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(
  resolve(webRoot, "src/lib/trajectory-comparison-match.ts"),
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
const { trajectoryComparisonReportMatchesSelection } = commonJsModule.exports;

const runId = "a".repeat(64);
const targetHash = "b".repeat(64);
const referenceHash = "c".repeat(64);
const target = attempt("target", targetHash);
const reference = attempt("reference", referenceHash);
const selection = {
  target,
  reference,
  comparisonPolicy: "practice_qualifying",
  requestedProbeDistanceM: null,
};

assert.equal(
  trajectoryComparisonReportMatchesSelection(report(), selection),
  true,
);

for (const change of [
  (candidate) => {
    candidate.analysis_version = "unexpected";
  },
  (candidate) => {
    candidate.comparison_policy = "time_trial";
  },
  (candidate) => {
    candidate.diagnostic_only = false;
  },
  (candidate) => {
    candidate.is_centreline = true;
  },
  (candidate) => {
    candidate.paths.reference.source.trace_sha256 = targetHash;
  },
  (candidate) => {
    candidate.paths.target.source.trace_schema_version = 1;
  },
  (candidate) => {
    candidate.paths.target.source.car_index = 1;
  },
  (candidate) => {
    candidate.plot_bounds_world_xz_m.world_x = [0, Infinity];
  },
  (candidate) => {
    candidate.plot_bounds_world_xz_m.world_z = [-5, 6];
  },
  (candidate) => {
    candidate.plot_bounds_world_xz_m = {
      world_x: [-1e308, 1e308],
      world_z: [0, 1],
    };
  },
  (candidate) => {
    candidate.paths.target.segments[0].points[0].world_position_m.x = 6;
  },
  (candidate) => {
    candidate.paths.target.segments[0].points[0].world_position_m.x = NaN;
  },
  (candidate) => {
    candidate.paths.reference.coverage.position_sample_coverage = 1.1;
  },
  (candidate) => {
    candidate.paths.target.attempt_evidence.lifecycle_exclusions = [null];
  },
  (candidate) => {
    candidate.paths.target.segments = Array.from(
      { length: 257 },
      (_, index) => ({
        segment_index: index,
        break_before_reasons: [],
        sample_count: 1,
        rendered_point_count: 1,
        points: [point(index)],
      }),
    );
    candidate.paths.target.preview.rendered_point_count = 257;
  },
]) {
  const candidate = report();
  change(candidate);
  assert.equal(
    trajectoryComparisonReportMatchesSelection(candidate, selection),
    false,
  );
}

const probed = report();
probed.plot_bounds_world_xz_m = {
  world_x: [-5, 20],
  world_z: [-5, 20],
};
probed.position_probe = pairedProbe();
probed.paths.target.position_probe = probe(target, 12.5, 15);
probed.paths.reference.position_probe = probe(reference, 12.5, 10);
assert.equal(
  trajectoryComparisonReportMatchesSelection(probed, {
    ...selection,
    requestedProbeDistanceM: 12.5,
  }),
  true,
);

const wrongProbeSource = structuredClone(probed);
wrongProbeSource.position_probe.reference.source.trace_sha256 = targetHash;
assert.equal(
  trajectoryComparisonReportMatchesSelection(wrongProbeSource, {
    ...selection,
    requestedProbeDistanceM: 12.5,
  }),
  false,
);

const wrongProbeDistance = structuredClone(probed);
wrongProbeDistance.position_probe.requested_distance_m = 12.6;
assert.equal(
  trajectoryComparisonReportMatchesSelection(wrongProbeDistance, {
    ...selection,
    requestedProbeDistanceM: 12.5,
  }),
  false,
);

const wrongTargetProbeDistance = structuredClone(probed);
wrongTargetProbeDistance.position_probe.target.requested_distance_m = 12.6;
assert.equal(
  trajectoryComparisonReportMatchesSelection(wrongTargetProbeDistance, {
    ...selection,
    requestedProbeDistanceM: 12.5,
  }),
  false,
);

const outOfPlotProbe = structuredClone(probed);
outOfPlotProbe.position_probe.reference.position_world_xyz_m.x = 21;
assert.equal(
  trajectoryComparisonReportMatchesSelection(outOfPlotProbe, {
    ...selection,
    requestedProbeDistanceM: 12.5,
  }),
  false,
);

assert.equal(
  trajectoryComparisonReportMatchesSelection(probed, selection),
  false,
);

console.log(
  "Trajectory comparison identity, bounds, shape, and probe checks passed.",
);

function attempt(name, traceSha256) {
  return {
    attempt_key: `${runId}:14237356543050158953:0:${name === "target" ? 2 : 1}`,
    run_id: runId,
    session_uid: "14237356543050158953",
    car_index: 0,
    trace_sha256: traceSha256,
    trace_schema_version: 3,
  };
}

function sourceRecord(selected) {
  return {
    attempt_key: selected.attempt_key,
    run_id: selected.run_id,
    session_uid: selected.session_uid,
    car_index: selected.car_index,
    disposition: "completed",
    lap_time_ms: 100000,
    game_valid: true,
    reference_eligible: true,
    exclusion_reasons: [],
    trace_sha256: selected.trace_sha256,
    trace_schema_version: selected.trace_schema_version,
  };
}

function point(frame, distance = frame - 1, x = frame - 1, z = frame - 1) {
  return {
    frame_identifier: frame,
    lap_distance_m: distance,
    session_time_s: frame / 60,
    lap_time_s: frame / 60,
    lap_time_ms: (frame * 1000) / 60,
    world_position_m: { x, y: 0, z },
  };
}

function path(selected, offset) {
  const points = [
    point(1, 0, offset, offset),
    point(2, 1, offset + 1, offset + 1),
  ];
  return {
    source: sourceRecord(selected),
    attempt_evidence: {
      disposition: "completed",
      lap_time_ms: 100000,
      game_valid: true,
      reference_eligible: true,
      superseded: false,
      lifecycle_assessed: true,
      lifecycle_exclusions: [],
      exclusion_reasons: [],
      source_sample_count: 2,
    },
    capture_evidence: {
      complete: false,
      footer_status: "incomplete",
      recording_counters: {},
      recording_observer_counters: {},
    },
    coverage: { position_sample_coverage: 1 },
    preview: { source_position_point_count: 2, rendered_point_count: 2 },
    segments: [
      {
        segment_index: 0,
        break_before_reasons: [],
        sample_count: 2,
        rendered_point_count: 2,
        points,
      },
    ],
  };
}

function report() {
  return {
    schema_version: 1,
    analysis_version: "trajectory-comparison-preview-v1",
    artifact_kind: "observed_trajectory_comparison_preview",
    status: "available",
    comparison_policy: "practice_qualifying",
    diagnostic_only: true,
    is_centreline: false,
    coordinate_projection: {
      horizontal_axis: "world_x",
      vertical_axis: "world_z",
      units: "m",
      orientation_claim: null,
      equal_scale: true,
    },
    plot_bounds_world_xz_m: { world_x: [-5, 5], world_z: [-5, 5] },
    paths: { target: path(target, 1), reference: path(reference, 0) },
    limits: {
      points_per_attempt: 2000,
      segments_per_attempt: 256,
      combined_points: 4000,
    },
  };
}

function probe(selected, distance, x) {
  return {
    schema_version: 1,
    analysis_version: "observed-position-probe-v1",
    status: "available",
    requested_distance_m: distance,
    method: "linear_interpolation",
    segment_index: 0,
    interpolation_fraction: 0.5,
    position_world_xyz_m: { x, y: 0, z: x },
    source_anchors: [
      {
        frame_identifier: 10,
        lap_distance_m: 12,
        session_time_s: 1,
        world_position_m: { x: x - 1, y: 0, z: x - 1 },
      },
      {
        frame_identifier: 11,
        lap_distance_m: 13,
        session_time_s: 1.02,
        world_position_m: { x: x + 1, y: 0, z: x + 1 },
      },
    ],
    source: sourceRecord(selected),
    reason_code: null,
  };
}

function pairedProbe() {
  return {
    schema_version: 1,
    analysis_version: "paired-observed-position-probe-v1",
    status: "available",
    requested_distance_m: 12.5,
    target: probe(target, 12.5, 15),
    reference: probe(reference, 12.5, 10),
    difference: {
      status: "available",
      world_x_m: 5,
      world_z_m: 5,
      horizontal_separation_m: Math.sqrt(50),
      reason_code: null,
    },
  };
}
