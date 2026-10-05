import type { LapRecord, ObservedTrajectoryComparisonPreview } from "@/lib/api";

const maxPointsPerAttempt = 2_000;
const maxSegmentsPerAttempt = 256;
const maxCombinedPoints = 4_000;

type PlotBounds = {
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
};

export function trajectoryComparisonReportMatchesSelection(
  report: ObservedTrajectoryComparisonPreview,
  selection: {
    target: LapRecord;
    reference: LapRecord;
    comparisonPolicy: "time_trial" | "practice_qualifying";
    requestedProbeDistanceM: number | null;
  },
): boolean {
  const candidate = record(report);
  const projection = record(candidate?.coordinate_projection);
  const bounds = record(candidate?.plot_bounds_world_xz_m);
  const paths = record(candidate?.paths);
  const targetPath = record(paths?.target);
  const referencePath = record(paths?.reference);
  const targetSource = record(targetPath?.source);
  const referenceSource = record(referencePath?.source);
  const targetPathValid = pathMatchesAttempt(
    targetPath,
    selection.target,
    selection.requestedProbeDistanceM,
  );
  const referencePathValid = pathMatchesAttempt(
    referencePath,
    selection.reference,
    selection.requestedProbeDistanceM,
  );
  const plotBounds = validEqualScaleBounds(bounds);
  const limits = record(candidate?.limits);
  const totalPoints = (targetPathValid ? pointCount(targetPath) : null) ?? 0;
  const referencePoints =
    (referencePathValid ? pointCount(referencePath) : null) ?? 0;
  const targetSegments = array(targetPath?.segments)?.length ?? -1;
  const referenceSegments = array(referencePath?.segments)?.length ?? -1;
  const positionProbe = record(candidate?.position_probe);

  return Boolean(
    candidate?.schema_version === 1 &&
    candidate.analysis_version === "trajectory-comparison-preview-v1" &&
    candidate.artifact_kind === "observed_trajectory_comparison_preview" &&
    candidate.status === "available" &&
    candidate.comparison_policy === selection.comparisonPolicy &&
    candidate.diagnostic_only === true &&
    candidate.is_centreline === false &&
    projection?.horizontal_axis === "world_x" &&
    projection.vertical_axis === "world_z" &&
    projection.units === "m" &&
    projection.orientation_claim === null &&
    projection.equal_scale === true &&
    plotBounds &&
    targetSource &&
    sourceMatchesAttempt(targetSource, selection.target) &&
    referenceSource &&
    sourceMatchesAttempt(referenceSource, selection.reference) &&
    targetPathValid &&
    referencePathValid &&
    limits?.points_per_attempt === maxPointsPerAttempt &&
    limits.segments_per_attempt === maxSegmentsPerAttempt &&
    limits.combined_points === maxCombinedPoints &&
    totalPoints + referencePoints <= maxCombinedPoints &&
    targetSegments <= maxSegmentsPerAttempt &&
    referenceSegments <= maxSegmentsPerAttempt &&
    coordinatesWithinPlot(targetPath, plotBounds) &&
    coordinatesWithinPlot(referencePath, plotBounds) &&
    probeCoordinatesWithinPlot(positionProbe, plotBounds) &&
    positionProbeMatchesSelection(
      positionProbe,
      selection,
      candidate.position_probe !== undefined,
    ) &&
    pathPositionProbesMatchSelection(
      targetPath,
      referencePath,
      selection.requestedProbeDistanceM,
    ),
  );
}

function pathMatchesAttempt(
  path: Record<string, unknown> | null,
  attempt: LapRecord,
  requestedProbeDistanceM: number | null,
): path is Record<string, unknown> {
  if (!path) return false;
  const segments = array(path.segments);
  if (
    !segments ||
    segments.length === 0 ||
    segments.length > maxSegmentsPerAttempt
  ) {
    return false;
  }
  const source = record(path.source);
  const evidence = record(path.attempt_evidence);
  const coverage = record(path.coverage);
  const preview = record(path.preview);
  const capture = path.capture_evidence;
  const points = pointCount(path);
  return Boolean(
    source &&
    sourceMatchesAttempt(source, attempt) &&
    evidence &&
    typeof evidence.disposition === "string" &&
    nullableFiniteNumber(evidence.lap_time_ms) &&
    nullableBoolean(evidence.game_valid) &&
    typeof evidence.reference_eligible === "boolean" &&
    nullableBoolean(evidence.superseded) &&
    typeof evidence.lifecycle_assessed === "boolean" &&
    stringArray(evidence.lifecycle_exclusions) &&
    stringArray(evidence.exclusion_reasons) &&
    nonNegativeInteger(evidence.source_sample_count) &&
    coverage &&
    finiteNumber(coverage.position_sample_coverage) !== null &&
    finiteNumber(coverage.position_sample_coverage)! >= 0 &&
    finiteNumber(coverage.position_sample_coverage)! <= 1 &&
    preview &&
    nonNegativeInteger(preview.source_position_point_count) &&
    nonNegativeInteger(preview.rendered_point_count) &&
    preview.rendered_point_count === points &&
    points !== null &&
    points > 0 &&
    points <= maxPointsPerAttempt &&
    segments &&
    segments.length > 0 &&
    segments.length <= maxSegmentsPerAttempt &&
    segments.every(validSegment) &&
    (capture === null || validCaptureEvidence(capture)) &&
    (path.position_probe === undefined ||
      (requestedProbeDistanceM !== null &&
        validPositionProbe(
          path.position_probe,
          attempt,
          requestedProbeDistanceM,
        ))),
  );
}

function validSegment(value: unknown): boolean {
  const segment = record(value);
  const points = array(segment?.points);
  return Boolean(
    segment &&
    nonNegativeInteger(segment.segment_index) &&
    stringArray(segment.break_before_reasons) &&
    nonNegativeInteger(segment.sample_count) &&
    nonNegativeInteger(segment.rendered_point_count) &&
    points &&
    points.length > 0 &&
    points.length === segment.rendered_point_count &&
    points.every(validTrajectoryPoint),
  );
}

function validTrajectoryPoint(value: unknown): boolean {
  const point = record(value);
  const position = record(point?.world_position_m);
  return Boolean(
    point &&
    nonNegativeInteger(point.frame_identifier) &&
    finiteNumber(point.lap_distance_m) !== null &&
    finiteNumber(point.session_time_s) !== null &&
    finiteNumber(point.lap_time_s) !== null &&
    finiteNumber(point.lap_time_ms) !== null &&
    position &&
    finiteNumber(position.x) !== null &&
    finiteNumber(position.y) !== null &&
    finiteNumber(position.z) !== null,
  );
}

function validCaptureEvidence(value: unknown): boolean {
  const capture = record(value);
  return Boolean(
    capture &&
    nullableBoolean(capture.complete) &&
    (capture.footer_status === null ||
      typeof capture.footer_status === "string") &&
    record(capture.recording_counters) &&
    record(capture.recording_observer_counters),
  );
}

function positionProbeMatchesSelection(
  value: Record<string, unknown> | null,
  selection: {
    target: LapRecord;
    reference: LapRecord;
    requestedProbeDistanceM: number | null;
  },
  isPresent: boolean,
) {
  if (!isPresent)
    return value === null && selection.requestedProbeDistanceM === null;
  if (!value || selection.requestedProbeDistanceM === null) return false;
  const targetProbe = record(value.target);
  const referenceProbe = record(value.reference);
  const difference = record(value.difference);
  return Boolean(
    value.schema_version === 1 &&
    value.analysis_version === "paired-observed-position-probe-v1" &&
    (value.status === "available" || value.status === "unavailable") &&
    value.requested_distance_m === selection.requestedProbeDistanceM &&
    targetProbe &&
    validPositionProbe(
      targetProbe,
      selection.target,
      selection.requestedProbeDistanceM,
    ) &&
    referenceProbe &&
    validPositionProbe(
      referenceProbe,
      selection.reference,
      selection.requestedProbeDistanceM,
    ) &&
    difference &&
    difference.status === value.status &&
    (difference.status === "available"
      ? targetProbe.status === "available" &&
        referenceProbe.status === "available" &&
        finiteNumber(difference.world_x_m) !== null &&
        finiteNumber(difference.world_z_m) !== null &&
        finiteNumber(difference.horizontal_separation_m) !== null &&
        difference.reason_code === null
      : difference.status === "unavailable" &&
        (targetProbe.status === "unavailable" ||
          referenceProbe.status === "unavailable") &&
        difference.world_x_m === null &&
        difference.world_z_m === null &&
        difference.horizontal_separation_m === null &&
        typeof difference.reason_code === "string"),
  );
}

function pathPositionProbesMatchSelection(
  targetPath: Record<string, unknown> | null,
  referencePath: Record<string, unknown> | null,
  requestedDistanceM: number | null,
) {
  if (!targetPath || !referencePath) return false;
  const targetProbe = record(targetPath.position_probe);
  const referenceProbe = record(referencePath.position_probe);
  if (requestedDistanceM === null) {
    return (
      targetPath.position_probe === undefined &&
      referencePath.position_probe === undefined
    );
  }
  return Boolean(
    targetProbe &&
    referenceProbe &&
    targetProbe.requested_distance_m === requestedDistanceM &&
    referenceProbe.requested_distance_m === requestedDistanceM,
  );
}

function validPositionProbe(
  value: unknown,
  attempt: LapRecord,
  requestedDistanceM: number,
): boolean {
  const probe = record(value);
  const source = record(probe?.source);
  const anchors = array(probe?.source_anchors);
  const position =
    probe?.position_world_xyz_m === null
      ? null
      : record(probe?.position_world_xyz_m);
  if (
    !probe ||
    probe.schema_version !== 1 ||
    probe.analysis_version !== "observed-position-probe-v1" ||
    (probe.status !== "available" && probe.status !== "unavailable") ||
    !source ||
    !sourceMatchesAttempt(source, attempt) ||
    !anchors ||
    anchors.length > 2 ||
    probe.requested_distance_m !== requestedDistanceM
  ) {
    return false;
  }
  if (probe.status === "unavailable") {
    return Boolean(
      typeof probe.reason_code === "string" &&
      probe.reason_code.length > 0 &&
      anchors.length === 0 &&
      probe.method === null &&
      probe.segment_index === null &&
      probe.interpolation_fraction === null &&
      probe.position_world_xyz_m === null,
    );
  }
  const expectedAnchorCount =
    probe.method === "exact_source_observation"
      ? 1
      : probe.method === "linear_interpolation"
        ? 2
        : -1;
  return Boolean(
    position &&
    finiteNumber(position.x) !== null &&
    finiteNumber(position.y) !== null &&
    finiteNumber(position.z) !== null &&
    nonNegativeInteger(probe.segment_index) &&
    finiteNumber(probe.interpolation_fraction) !== null &&
    finiteNumber(probe.interpolation_fraction)! >= 0 &&
    finiteNumber(probe.interpolation_fraction)! <= 1 &&
    finiteNumber(probe.requested_distance_m) !== null &&
    probe.reason_code === null &&
    anchors.length === expectedAnchorCount &&
    anchors.every(validProbeAnchor),
  );
}

function validProbeAnchor(value: unknown): boolean {
  const anchor = record(value);
  const position = record(anchor?.world_position_m);
  return Boolean(
    anchor &&
    nonNegativeInteger(anchor.frame_identifier) &&
    finiteNumber(anchor.lap_distance_m) !== null &&
    finiteNumber(anchor.session_time_s) !== null &&
    position &&
    finiteNumber(position.x) !== null &&
    finiteNumber(position.y) !== null &&
    finiteNumber(position.z) !== null,
  );
}

function sourceMatchesAttempt(
  source: Record<string, unknown>,
  attempt: LapRecord,
) {
  return Boolean(
    source.attempt_key === attempt.attempt_key &&
    source.run_id === attempt.run_id &&
    source.session_uid === attempt.session_uid &&
    source.car_index === attempt.car_index &&
    source.trace_sha256 === attempt.trace_sha256 &&
    isSha256(source.trace_sha256) &&
    source.trace_schema_version === attempt.trace_schema_version &&
    Number.isSafeInteger(source.trace_schema_version) &&
    Number(source.trace_schema_version) >= 2,
  );
}

function validEqualScaleBounds(
  value: Record<string, unknown> | null,
): PlotBounds | null {
  if (!value) return null;
  const worldX = axisBounds(value.world_x);
  const worldZ = axisBounds(value.world_z);
  if (!worldX || !worldZ) return null;
  const xSpan = worldX[1] - worldX[0];
  const zSpan = worldZ[1] - worldZ[0];
  if (
    !Number.isFinite(xSpan) ||
    !Number.isFinite(zSpan) ||
    xSpan <= 0 ||
    zSpan <= 0 ||
    Math.abs(xSpan - zSpan) > Math.max(xSpan, zSpan) * 1e-9
  ) {
    return null;
  }
  return {
    minX: worldX[0],
    maxX: worldX[1],
    minZ: worldZ[0],
    maxZ: worldZ[1],
  };
}

function axisBounds(value: unknown): [number, number] | null {
  const bounds = array(value);
  if (
    !bounds ||
    bounds.length !== 2 ||
    finiteNumber(bounds[0]) === null ||
    finiteNumber(bounds[1]) === null
  ) {
    return null;
  }
  return [Number(bounds[0]), Number(bounds[1])];
}

function pointCount(path: Record<string, unknown>): number | null {
  const segments = array(path.segments);
  if (!segments || segments.length > maxSegmentsPerAttempt) return null;
  let count = 0;
  for (const segment of segments) {
    const points = array(record(segment)?.points);
    if (!points) return null;
    count += points.length;
    if (count > maxPointsPerAttempt) return null;
  }
  return count;
}

function coordinatesWithinPlot(
  path: Record<string, unknown> | null,
  bounds: PlotBounds,
): boolean {
  const segments = array(path?.segments);
  if (
    !path ||
    !segments ||
    !segments.every((segment) => {
      const points = array(record(segment)?.points);
      return Boolean(
        points &&
        points.every((point) => {
          const position = record(record(point)?.world_position_m);
          return (
            withinBounds(position?.x, bounds.minX, bounds.maxX) &&
            withinBounds(position?.z, bounds.minZ, bounds.maxZ)
          );
        }),
      );
    })
  ) {
    return false;
  }
  const positionProbe = record(path.position_probe);
  if (!positionProbe || positionProbe.status === "unavailable") return true;
  const position = record(positionProbe.position_world_xyz_m);
  return (
    positionProbe.status === "available" &&
    withinBounds(position?.x, bounds.minX, bounds.maxX) &&
    withinBounds(position?.z, bounds.minZ, bounds.maxZ)
  );
}

function probeCoordinatesWithinPlot(
  value: Record<string, unknown> | null,
  bounds: PlotBounds,
): boolean {
  if (!value) return true;
  return (["target", "reference"] as const).every((side) => {
    const probe = record(value[side]);
    if (!probe) return false;
    if (probe.status === "unavailable") return true;
    const position = record(probe.position_world_xyz_m);
    return (
      probe.status === "available" &&
      withinBounds(position?.x, bounds.minX, bounds.maxX) &&
      withinBounds(position?.z, bounds.minZ, bounds.maxZ)
    );
  });
}

function withinBounds(value: unknown, min: number, max: number): boolean {
  const number = finiteNumber(value);
  return number !== null && number >= min && number <= max;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function array(value: unknown): unknown[] | null {
  return Array.isArray(value) ? value : null;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function nullableFiniteNumber(value: unknown): boolean {
  return value === null || finiteNumber(value) !== null;
}

function nullableBoolean(value: unknown): boolean {
  return value === null || typeof value === "boolean";
}

function nonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

function stringArray(value: unknown): value is string[] {
  return (
    Array.isArray(value) && value.every((item) => typeof item === "string")
  );
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
}
