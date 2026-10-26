import type { AttemptTraceChannelKey, LapRecord } from "@/lib/api";

const sourceRowLimit = 100_000;
const pointLimitPerChannel = 2_000;
const runLimitPerChannel = 256;
const contextSegmentLimit = 1_024;
const sourceByteLimit = 64 * 1024 * 1024;
const contextByteLimit = 4 * 1024 * 1024;
const uint32Maximum = 0xffff_ffff;

const channelContracts: Record<
  AttemptTraceChannelKey,
  {
    label: string;
    sourceField: string;
    sourceUnit: string;
    unit: string;
  }
> = {
  speed: {
    label: "Speed",
    sourceField: "speed_mps",
    sourceUnit: "m/s",
    unit: "km/h",
  },
  throttle: {
    label: "Throttle",
    sourceField: "throttle",
    sourceUnit: "ratio",
    unit: "percent",
  },
  brake: {
    label: "Brake",
    sourceField: "brake",
    sourceUnit: "ratio",
    unit: "percent",
  },
  steering: {
    label: "Steering input",
    sourceField: "steering",
    sourceUnit: "ratio",
    unit: "normalized",
  },
};

const channelKeys = Object.keys(channelContracts) as AttemptTraceChannelKey[];

export function attemptTraceChartReportMatchesSelection(
  value: unknown,
  attempt: LapRecord,
): boolean {
  const report = record(value);
  const source = record(report?.source);
  const context = record(report?.context);
  const preview = record(report?.preview);
  const continuity = record(report?.continuity_policy);
  const channels = record(report?.channels);
  const contextSegments = array(context?.segments);
  const sourceRowCount = attempt.trace_row_count;

  if (
    !report ||
    !source ||
    !context ||
    !preview ||
    !continuity ||
    !channels ||
    !Number.isSafeInteger(sourceRowCount) ||
    sourceRowCount < 0 ||
    sourceRowCount > sourceRowLimit ||
    report.report_version !== 1 ||
    report.artifact_kind !== "single_attempt_player_trace_preview" ||
    report.diagnostic_only !== true ||
    !matchesSource(source, attempt, sourceRowCount) ||
    !validContext(context, contextSegments) ||
    preview.point_limit_per_channel !== pointLimitPerChannel ||
    preview.run_limit_per_channel !== runLimitPerChannel ||
    preview.source_row_limit !== sourceRowLimit ||
    preview.source_byte_limit !== sourceByteLimit ||
    preview.source_context_segment_limit !== contextSegmentLimit ||
    preview.source_context_byte_limit !== contextByteLimit ||
    preview.thinning_method !== "evenly_spaced_per_run_with_endpoints" ||
    continuity.frame_delta !== "exactly one, with uint32 wrap allowed" ||
    continuity.maximum_session_time_gap_s !== 0.1 ||
    continuity.session_time_tolerance !==
      "one float32 ULP at the larger endpoint magnitude" ||
    continuity.missing_channel_values !==
      "break only that channel's displayed run" ||
    continuity.coordinate !==
      "stored session_time_s; source sample order preserved"
  ) {
    return false;
  }

  const channelReports = channelKeys.map((key) => ({
    key,
    value: record(channels[key]),
  }));
  if (
    channelReports.some(
      ({ key, value }) =>
        !value || !validChannel(value, channelContracts[key], sourceRowCount),
    )
  ) {
    return false;
  }

  const anyObserved = channelReports.some(
    ({ value }) => (value?.observed_sample_count as number) > 0,
  );
  return report.status === (anyObserved ? "observed" : "no_chartable_samples");
}

function matchesSource(
  source: Record<string, unknown>,
  attempt: LapRecord,
  sourceRowCount: number,
): boolean {
  return Boolean(
    source.attempt_key === attempt.attempt_key &&
    source.run_id === attempt.run_id &&
    source.session_uid === attempt.session_uid &&
    source.car_index === attempt.car_index &&
    source.attempt_number === attempt.attempt_number &&
    source.disposition === attempt.disposition &&
    source.lap_time_ms === attempt.lap_time_ms &&
    source.game_valid === attempt.game_valid &&
    source.reference_eligible === attempt.reference_eligible &&
    source.start_observed === attempt.start_observed &&
    source.pit_encountered === attempt.pit_encountered &&
    sameStringArray(source.exclusion_reasons, attempt.exclusion_reasons) &&
    source.trace_sha256 === attempt.trace_sha256 &&
    source.trace_schema_version === attempt.trace_schema_version &&
    source.trace_row_count === sourceRowCount &&
    source.trace_checksum_verified === true,
  );
}

function validContext(
  context: Record<string, unknown>,
  segments: unknown[] | null,
): boolean {
  if (
    !segments ||
    segments.length > contextSegmentLimit ||
    !stringArray(context.game_modes, contextSegmentLimit) ||
    context.game_modes.length === 0 ||
    !stringArray(context.session_types, contextSegmentLimit) ||
    context.session_types.length === 0 ||
    !stringArray(context.track_names, contextSegmentLimit) ||
    context.track_names.length === 0
  ) {
    return false;
  }
  return segments.every((entry) => {
    const segment = record(entry);
    return Boolean(
      segment &&
      uint32(segment.from_frame_identifier) &&
      nullableShortString(segment.game_mode) &&
      nullableShortString(segment.session_type) &&
      nullableShortString(segment.track_name),
    );
  });
}

function validChannel(
  channel: Record<string, unknown>,
  contract: (typeof channelContracts)[AttemptTraceChannelKey],
  sourceRowCount: number,
): boolean {
  const segments = array(channel.segments);
  const observed = integer(channel.observed_sample_count);
  const unsupported = integer(channel.unsupported_sample_count);
  const missing = integer(channel.missing_value_sample_count);
  const invalidAnchors = integer(channel.invalid_anchor_sample_count);
  const sourceRunCount = integer(channel.source_run_count);
  const renderedRuns = integer(channel.rendered_run_count);
  const omittedRuns = integer(channel.omitted_run_count);
  const renderedPoints = integer(channel.rendered_point_count);
  const omittedPoints = integer(channel.omitted_point_count);
  const thinned = integer(channel.thinned_sample_count);
  const renderedSourceCount = segments?.reduce<number>(
    (total, entry) =>
      total + (integer(record(entry)?.source_sample_count) ?? 0),
    0,
  );
  const range = array(channel.observed_value_range);

  if (
    channel.label !== contract.label ||
    channel.source_field !== contract.sourceField ||
    channel.source_unit !== contract.sourceUnit ||
    channel.unit !== contract.unit ||
    channel.source_sample_count !== sourceRowCount ||
    observed === null ||
    unsupported === null ||
    missing === null ||
    invalidAnchors === null ||
    sourceRunCount === null ||
    renderedRuns === null ||
    omittedRuns === null ||
    renderedPoints === null ||
    omittedPoints === null ||
    thinned === null ||
    !segments ||
    segments.length > runLimitPerChannel ||
    observed + unsupported !== sourceRowCount ||
    missing + invalidAnchors !== unsupported ||
    sourceRunCount > observed ||
    renderedRuns !== segments.length ||
    omittedRuns !== sourceRunCount - renderedRuns ||
    renderedPoints > pointLimitPerChannel ||
    renderedPoints !==
      segments.reduce<number>(
        (total, entry) => total + (array(record(entry)?.points)?.length ?? 0),
        0,
      ) ||
    omittedPoints !== observed - renderedPoints ||
    renderedSourceCount === undefined ||
    renderedSourceCount > observed ||
    thinned !== renderedSourceCount - renderedPoints ||
    !validRange(range, observed) ||
    !segments.every((entry, index) =>
      validSegment(entry, index, sourceRowCount, range),
    )
  ) {
    return false;
  }
  return true;
}

function validRange(value: unknown[] | null, observedCount: number): boolean {
  if (observedCount === 0) return value === null;
  if (!value || value.length !== 2) return false;
  const minimum = finiteNumber(value[0]);
  const maximum = finiteNumber(value[1]);
  return Boolean(
    minimum !== null &&
    maximum !== null &&
    minimum <= maximum &&
    chartRangeIsFinite(minimum, maximum),
  );
}

function chartRangeIsFinite(minimum: number, maximum: number): boolean {
  const rawRange = maximum - minimum;
  if (!Number.isFinite(rawRange)) return false;
  const padding =
    rawRange > 0 ? rawRange * 0.05 : Math.max(Math.abs(minimum) * 0.05, 0.1);
  const chartMinimum = minimum - padding;
  const chartMaximum = maximum + padding;
  return (
    Number.isFinite(padding) &&
    Number.isFinite(chartMinimum) &&
    Number.isFinite(chartMaximum) &&
    Number.isFinite(chartMaximum - chartMinimum)
  );
}

function validSegment(
  value: unknown,
  expectedIndex: number,
  sourceRowCount: number,
  range: unknown[] | null,
): boolean {
  const segment = record(value);
  const points = array(segment?.points);
  const start = record(segment?.start_anchor);
  const end = record(segment?.end_anchor);
  const sourceSampleCount = integer(segment?.source_sample_count);
  const renderedPointCount = integer(segment?.rendered_point_count);
  if (
    !segment ||
    !points ||
    points.length === 0 ||
    points.length > pointLimitPerChannel ||
    segment.run_index !== expectedIndex ||
    !stringArray(segment.break_before_reasons, 16) ||
    (segment.break_before_reasons as string[]).length === 0 ||
    sourceSampleCount === null ||
    sourceSampleCount < points.length ||
    sourceSampleCount > sourceRowCount ||
    renderedPointCount !== points.length ||
    (sourceSampleCount > 1 && points.length < 2) ||
    start === null ||
    !validAnchor(start) ||
    end === null ||
    !validAnchor(end) ||
    !sameAnchor(start, points[0]) ||
    !sameAnchor(end, points[points.length - 1]) ||
    !points.every((point) => validPoint(point, range))
  ) {
    return false;
  }

  for (let index = 1; index < points.length; index += 1) {
    const previous = record(points[index - 1])!;
    const current = record(points[index])!;
    const frameDelta =
      ((current.frame_identifier as number) -
        (previous.frame_identifier as number) +
        0x1_0000_0000) %
      0x1_0000_0000;
    const previousTime = previous.session_time_s as number;
    const currentTime = current.session_time_s as number;
    if (
      frameDelta === 0 ||
      frameDelta > sourceRowLimit ||
      currentTime <
        previousTime -
          Math.max(float32Ulp(previousTime), float32Ulp(currentTime))
    ) {
      return false;
    }
  }
  return true;
}

function validPoint(value: unknown, range: unknown[] | null): boolean {
  const point = record(value);
  const time = finiteNumber(point?.session_time_s);
  const channelValue = finiteNumber(point?.value);
  const distance = point?.lap_distance_m;
  const rangeMinimum = finiteNumber(range?.[0]);
  const rangeMaximum = finiteNumber(range?.[1]);
  return Boolean(
    point &&
    uint32(point.frame_identifier) &&
    time !== null &&
    time >= 0 &&
    (distance === null || finiteNumber(distance) !== null) &&
    channelValue !== null &&
    rangeMinimum !== null &&
    rangeMaximum !== null &&
    channelValue >= rangeMinimum &&
    channelValue <= rangeMaximum,
  );
}

function validAnchor(value: Record<string, unknown> | null): boolean {
  return Boolean(
    value &&
    uint32(value.frame_identifier) &&
    finiteNumber(value.session_time_s) !== null &&
    (value.session_time_s as number) >= 0 &&
    (value.lap_distance_m === null ||
      finiteNumber(value.lap_distance_m) !== null),
  );
}

function sameAnchor(anchor: Record<string, unknown>, point: unknown): boolean {
  const candidate = record(point);
  return Boolean(
    candidate &&
    anchor.frame_identifier === candidate.frame_identifier &&
    anchor.session_time_s === candidate.session_time_s &&
    anchor.lap_distance_m === candidate.lap_distance_m,
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

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function uint32(value: unknown): boolean {
  return (
    Number.isSafeInteger(value) &&
    (value as number) >= 0 &&
    (value as number) <= uint32Maximum
  );
}

function stringArray(value: unknown, maximum: number): value is string[] {
  return Boolean(
    Array.isArray(value) &&
    value.length <= maximum &&
    value.every(
      (entry) =>
        typeof entry === "string" && entry.length > 0 && entry.length <= 256,
    ),
  );
}

function nullableShortString(value: unknown): boolean {
  return (
    value === null ||
    (typeof value === "string" && value.length > 0 && value.length <= 256)
  );
}

function sameStringArray(value: unknown, expected: string[]): boolean {
  return (
    Array.isArray(value) &&
    value.length === expected.length &&
    value.every((entry, index) => entry === expected[index])
  );
}

function float32Ulp(value: number): number {
  const magnitude = Math.abs(value);
  if (magnitude === 0 || magnitude < 2 ** -126) return 2 ** -149;
  return 2 ** (Math.floor(Math.log2(magnitude)) - 23);
}
