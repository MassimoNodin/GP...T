export interface ApiResponse<T> {
  api_version: "v1";
  status: "ok" | "unavailable";
  data: T | null;
  reason: string | null;
}

export interface RecordingSourceRecord {
  capture_id: string;
  display_name: string;
  byte_size: number;
  modified_at_utc: string;
  latest_job_id: string | null;
  latest_job_status: string | null;
  latest_job_run_id: string | null;
  available: boolean;
}

export interface ImportProgressRecord {
  phase: string;
  packets_processed: number;
  bytes_read: number;
  total_bytes: number;
}

export interface ImportJobRecord {
  job_id: string;
  capture_id: string;
  status: "queued" | "running" | "complete" | "failed" | "interrupted";
  phase: string;
  attempt_count: number;
  created_at_utc: string;
  updated_at_utc: string;
  started_at_utc: string | null;
  finished_at_utc: string | null;
  result: Record<string, unknown> | null;
  failure_reason: string | null;
  progress: ImportProgressRecord | null;
}

export interface RecordingProgressRecord {
  state: string;
  elapsed_ms: number;
  received: number;
  queued: number;
  recorded: number;
  queue_dropped: number;
  socket_errors: number;
  latest_context: SessionContext | null;
  live_telemetry: LiveTelemetryRecord;
}

export interface LiveTelemetryRecord {
  status: "waiting" | "fresh" | "stale" | "unsupported" | "unavailable";
  reason: string | null;
  age_ms: number | null;
  session_uid?: string | null;
  frame_identifier?: number | null;
  packet_format?: number | null;
  player_car_index?: number | null;
  lap_number?: number | null;
  lap_time_ms?: number | null;
  game_invalid?: boolean | null;
  pit_status_id?: number | null;
  driver_status_id?: number | null;
  speed_kph?: number | null;
  gear?: number | null;
  engine_rpm?: number | null;
  throttle?: number | null;
  brake?: number | null;
}

export interface RecordingJobRecord {
  recording_id: string;
  status:
    | "starting"
    | "recording"
    | "stopping"
    | "complete"
    | "failed"
    | "interrupted";
  bind_host: string;
  bind_port: number;
  created_at_utc: string;
  updated_at_utc: string;
  started_at_utc: string | null;
  finished_at_utc: string | null;
  summary: Record<string, unknown> | null;
  failure_reason: string | null;
  published: boolean;
  progress: RecordingProgressRecord | null;
}

export interface SessionContext {
  session_uid?: string;
  track_name?: string | null;
  track_length_m?: number | null;
  session_type?: string | null;
  game_mode?: string | null;
  weather_name?: string | null;
}

export interface SessionRecord {
  session_key: string;
  run_id: string;
  session_uid: string;
  packet_format: number;
  context: SessionContext | null;
  run_status: string;
  capture_quality: Record<string, unknown> | null;
  capture_sha256: string;
  pipeline_version: string;
  started_at_utc: string;
  finished_at_utc: string | null;
  lap_attempts: number;
}

export interface TrackModelRecord {
  model_id: string;
  revision: number;
  packet_format: number;
  track_id: number;
  track_name: string;
  layout_id: string;
  track_length_m: number;
  validation_status: "draft" | "validated";
  provenance: string;
  region_count?: number;
}

export interface LapRecord {
  attempt_key: string;
  run_id: string;
  session_uid: string;
  car_index: number;
  attempt_number: number;
  lap_number: number;
  disposition: string;
  lap_time_ms: number | null;
  game_valid: boolean | null;
  reference_eligible: boolean;
  start_observed: boolean;
  pit_encountered: boolean;
  sample_count: number;
  trace_row_count: number;
  trace_schema_version: number;
  trace_sha256: string;
  context: SessionContext | null;
  quality: Record<string, unknown>;
  exclusion_reasons: string[];
}

export interface AttemptQualityAvailability {
  status: string;
  available_samples: number | null;
  sample_count: number;
  missing_samples: number | null;
}

export interface AttemptObservedStatus {
  status: string;
  matched_sample_count: number | null;
  sample_count: number;
  missing_join_sample_count: number | null;
  unavailable_reason_counts: Record<string, number> | null;
  fields: Record<string, {
    valid_count: number;
    missing_count: number;
    invalid_count: number;
  }> | null;
  first_last_observed: Record<string, {
    first: { value: unknown; frame_identifier: number; session_time_s: number } | null;
    last: { value: unknown; frame_identifier: number; session_time_s: number } | null;
  }> | null;
  discrete_changes: Array<{
    field: string;
    from_frame_identifier: number;
    to_frame_identifier: number;
    from_value: unknown;
    to_value: unknown;
  }> | null;
  discrete_changes_truncated: boolean | null;
  distinct_compounds: {
    actual: Array<{ raw_id: number; formula_id: number | null; label: string | null }>;
    visual: Array<{ raw_id: number; formula_id: number | null; label: string | null }>;
  } | null;
  fuel_quantity_unit_note: string;
}

export interface ObservedTrajectoryPoint {
  frame_identifier: number;
  lap_distance_m: number;
  session_time_s: number;
  lap_time_s: number;
  lap_time_ms: number;
  world_position_m: { x: number; y: number; z: number };
}

export interface ObservedTrajectoryBreakExample {
  reason: string;
  frame_identifier?: number | null;
  after?: { frame_identifier: number | null };
  before?: { frame_identifier: number | null };
}

export interface AttemptTrajectoryPreview {
  schema_version: number;
  artifact_kind: "observed_driven_trajectory_preview";
  diagnostic_only: true;
  is_centreline: false;
  coordinate_projection: {
    horizontal_axis: "world_x";
    vertical_axis: "world_z";
    units: "m";
    orientation_claim: null;
  };
  source: {
    attempt_key: string;
    run_id: string;
    session_uid: string;
    car_index: number;
    disposition: string;
    lap_time_ms: number | null;
    game_valid: boolean | null;
    reference_eligible: boolean;
    exclusion_reasons: string[];
    trace_sha256: string;
    trace_schema_version: number;
    context_segments: Array<{ from_frame_identifier: number; context: Record<string, unknown> | null }>;
  };
  coverage: {
    source_sample_count: number;
    position_sample_count: number;
    position_sample_coverage: number;
    unsupported_sample_count: number;
    segment_count: number;
    discontinuity_count: number;
    observed_lap_distance_range_m: [number, number] | null;
  };
  segments: Array<{
    segment_index: number;
    break_before_reasons: string[];
    sample_count: number;
    rendered_point_count: number;
    start_anchor: Record<string, number | null>;
    end_anchor: Record<string, number | null>;
    points: ObservedTrajectoryPoint[];
  }>;
  break_examples: ObservedTrajectoryBreakExample[];
  unsupported_examples: Array<ObservedTrajectoryBreakExample & { frame_identifier: number | null }>;
  preview: {
    point_limit: number;
    source_position_point_count: number;
    rendered_point_count: number;
    omitted_position_point_count: number;
    source_segment_count: number;
    rendered_segment_count: number;
    segment_limit: number;
    break_example_limit: number;
    break_examples_omitted_count: number;
    unsupported_example_limit: number;
    unsupported_examples_omitted_count: number;
    thinning_method: string;
  };
}

export type AttemptTraceChannelKey = "speed" | "throttle" | "brake" | "steering";

export interface AttemptTraceChartPoint {
  frame_identifier: number;
  session_time_s: number;
  lap_distance_m: number | null;
  value: number;
}

export interface AttemptTraceChannelPreview {
  label: string;
  source_field: string;
  source_unit: string;
  unit: string;
  source_sample_count: number;
  observed_sample_count: number;
  unsupported_sample_count: number;
  missing_value_sample_count: number;
  invalid_anchor_sample_count: number;
  observed_value_range: [number, number] | null;
  source_run_count: number;
  rendered_run_count: number;
  omitted_run_count: number;
  rendered_point_count: number;
  omitted_point_count: number;
  thinned_sample_count: number;
  segments: Array<{
    run_index: number;
    break_before_reasons: string[];
    source_sample_count: number;
    rendered_point_count: number;
    start_anchor: Omit<AttemptTraceChartPoint, "value">;
    end_anchor: Omit<AttemptTraceChartPoint, "value">;
    points: AttemptTraceChartPoint[];
  }>;
}

export interface AttemptTraceChartReport {
  report_version: 1;
  artifact_kind: "single_attempt_player_trace_preview";
  diagnostic_only: true;
  status: "observed" | "no_chartable_samples";
  source: {
    attempt_key: string;
    run_id: string;
    session_uid: string;
    car_index: number;
    attempt_number: number;
    disposition: string;
    lap_time_ms: number | null;
    game_valid: boolean | null;
    reference_eligible: boolean;
    start_observed: boolean;
    pit_encountered: boolean;
    exclusion_reasons: string[];
    trace_sha256: string;
    trace_schema_version: number;
    trace_row_count: number;
    trace_checksum_verified: boolean;
  };
  context: {
    segments: Array<{
      from_frame_identifier: number;
      game_mode: string | null;
      session_type: string | null;
      track_name: string | null;
    }>;
    game_modes: string[];
    session_types: string[];
    track_names: string[];
  };
  channels: Record<AttemptTraceChannelKey, AttemptTraceChannelPreview>;
  preview: {
    point_limit_per_channel: number;
    run_limit_per_channel: number;
    source_row_limit: number;
    source_byte_limit: number;
    source_context_segment_limit: number;
    source_context_byte_limit: number;
    thinning_method: string;
  };
  continuity_policy: {
    frame_delta: string;
    maximum_session_time_gap_s: number;
    session_time_tolerance: string;
    missing_channel_values: string;
    coordinate: string;
  };
}

export interface ProcessingRunSummary {
  run_id: string;
  capture: {
    sha256: string;
    byte_size: number | null;
    complete: boolean | null;
    footer_status: string | null;
    recording_counters: Record<string, number | null> | null;
    recording_observer_counters: Record<string, number | null> | null;
  };
  processing: {
    status: string;
    pipeline_version: string;
    config: Record<string, unknown> | null;
    started_at_utc: string;
    finished_at_utc: string | null;
    error: string | null;
    metrics_available: boolean;
    import_counters: Record<string, number | null> | null;
    replay_counters: Record<string, number | null> | null;
    capture_quality_available: boolean;
  };
  totals: {
    session_count: number;
    attempt_count: number;
    disposition_counts: Record<string, number>;
    game_validity_counts: { valid: number; invalid: number; unknown: number };
    stored_reference_eligible_count: number;
    exclusion_reason_counts: Array<{ reason: string; attempt_count: number }>;
    exclusion_reasons_complete: boolean;
  };
}

export interface ProcessingRunPage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ProcessingRunSession {
  session_key: string;
  session_uid: string;
  packet_format: number;
  latest_context_snapshot: Record<string, unknown> | null;
  context_snapshot_status: "complete" | "incomplete" | "missing";
  context_update_count: number;
  context_invalidation_count: number;
  attempt_count: number;
}

export interface ProcessingRunAttempt {
  attempt_key: string;
  session_key: string;
  session_uid: string;
  car_index: number;
  attempt_number: number;
  disposition: string;
  lap_time_ms: number | null;
  game_valid: boolean | null;
  reference_eligible: boolean;
  sample_count: number;
  exclusion_reasons: string[] | null;
  first_context_snapshot: Record<string, unknown> | null;
  context_segment_count: number;
  missing_context_segment_count: number;
}

export interface ProcessingRunDetail {
  summary: ProcessingRunSummary;
  sessions: ProcessingRunPage<ProcessingRunSession>;
  attempts: ProcessingRunPage<ProcessingRunAttempt>;
}

export interface AttemptQualityReport {
  report_version: number;
  analysis_version: string;
  identity: {
    attempt_key: string;
    run_id: string;
    session_uid: string;
    car_index: number;
    attempt_number: number;
    trace_sha256: string;
    trace_schema_version: number;
    trace_row_count: number;
    trace_checksum_verified: boolean;
  };
  attempt: {
    disposition: string;
    lap_time_ms: number | null;
    game_valid: boolean | null;
    reference_eligible: boolean;
    start_observed: boolean;
    pit_encountered: boolean;
    exclusion_reasons: string[];
  };
  context: {
    segments: Array<{
      from_frame_identifier: number;
      context: SessionContext | null;
    }>;
    session_types: string[];
    game_modes: string[];
    track_names: string[];
  };
  evidence: {
    recording: {
      capture_complete: boolean;
      footer_available: boolean;
      footer_evidence_status: string;
      footer_status: string | null;
      footer_status_evidence: string;
      counters: Record<string, number | null>;
      counter_evidence: Record<string, string>;
    };
    capture_decode: {
      available: boolean;
      capture_wide_counters: Record<string, unknown>;
    };
    replay_assembly: {
      available: boolean;
      counters: Record<string, number | null>;
      counter_evidence: Record<string, string>;
    };
    recording_observer: {
      available: boolean;
      source_scope: string;
      counters: Record<string, number | null>;
      counter_evidence: Record<string, string>;
    };
    attempt_trace: Record<string, unknown>;
  };
  channels: Record<string, AttemptQualityAvailability>;
  observed_status: AttemptObservedStatus;
  continuity: {
    frame_gaps: {
      count: number;
      largest_missing_frame_count: number;
      order_discontinuity_count: number;
    };
    lap_clock: {
      gap_threshold_s: number;
      gap_count: number;
      regression_count: number;
      largest_nonnegative_gap_s: number;
    };
    session_time: {
      gap_threshold_s: number;
      gap_count: number;
      regression_count: number;
      largest_nonnegative_gap_s: number;
    };
    lap_distance: {
      gap_threshold_m: number;
      gap_count: number;
      regression_count: number;
      largest_nonnegative_gap_m: number;
    };
  };
  distance_support: {
    resolution_m: number;
    track_length_m: number | null;
    track_length_source: string | null;
    track_length_unavailable_reason: string | null;
    observed_distance_range_m: [number, number] | null;
    status: string;
    reason: string | null;
    channels: Record<
      string,
      {
        observed_range_coverage: number | null;
        observed_grid_point_count?: number;
        full_track_coverage: number | null;
        full_track_grid_point_count?: number | null;
      }
    >;
  };
}

export interface ReferenceCandidate {
  attempt_key: string;
  attempt_number: number;
  lap_time_ms: number | null;
  eligible: boolean;
  selected: boolean;
  trace_sha256: string | null;
  exclusion_reasons: string[];
}

export interface SelectedReference {
  attempt_key?: string;
  attempt_number?: number;
  lap_time_ms?: number | null;
  trace_sha256?: string;
  trace_schema_version?: number;
}

export interface ReferenceSelection {
  reference_kind: string;
  status: string;
  policy_version: string;
  target: Record<string, unknown>;
  scope: Record<string, unknown> | null;
  selected_reference: SelectedReference | null;
  candidates: ReferenceCandidate[];
  reasons: string[];
}

export interface ResampledTrace {
  values: Record<string, Array<number | boolean | null>>;
  masks: Record<string, boolean[]>;
  source_sample_count: number;
  excluded_spans: Array<{
    start_distance_m: number;
    end_distance_m: number;
    reason: string;
    channel: string | null;
  }>;
}

export interface RegionEventEpisode {
  start_distance_m: number;
  start_distance_bracket_m: number[] | null;
  end_distance_m: number;
  end_distance_bracket_m: number[] | null;
  duration_s: number;
  peak_value: number;
  left_censored: boolean;
  right_censored: boolean;
}

export interface RegionEvent {
  status: string;
  distance_m: number | null;
  distance_bracket_m: number[] | null;
  reason?: string;
  events: RegionEventEpisode[];
  event_count?: number;
  events_truncated?: boolean;
}

export interface RegionAttempt {
  braking: RegionEvent;
  event_channel_coverage: {
    brake: number;
    steering: number;
    throttle: number;
  };
  minimum_speed: {
    status: string;
    speed_kph: number | null;
    distance_m: number | null;
    supported_grid_coverage: number;
    observed_sample_count?: number;
  };
  turn_in_proxy: RegionEvent & { interpretation: string };
  driver_apex: { status: string; reason: string };
  throttle_pickup: Record<string, RegionEvent>;
  exit_speeds: Array<{
    offset_m: number;
    distance_m: number;
    status: string;
    speed_kph: number | null;
  }>;
}

export interface CornerRegion {
  identifier: string;
  label: string;
  analysis_window_m: [number, number];
  diagnostic_only: boolean;
  target: RegionAttempt;
  reference: RegionAttempt;
  differences: {
    minimum_speed_kph: number | null;
    region_delta_change_s: number | null;
    exit_speed_kph: Array<{
      offset_m: number;
      target_minus_reference: number | null;
    }>;
    braking_onset_distance_m: number | null;
    throttle_50_distance_m: number | null;
  };
  delta_change: {
    status: string;
    entry_delta_s: number | null;
    exit_delta_s: number | null;
    delta_change_s: number | null;
    direction: string;
  };
}

export interface CornerAnalysis {
  analysis_version: string;
  model: TrackModelRecord;
  layout_validation_status: string;
  attempts_reference_eligible: { target: boolean; reference: boolean };
  diagnostic_only: boolean;
  regions: CornerRegion[];
}

export interface AttemptRegionWindow {
  identifier: string;
  label: string;
  analysis_window_m: [number, number];
  definition_validation_status: string;
  direction: string | null;
  complex_id: string | null;
  observations: RegionAttempt;
  position_evidence: RegionPositionEvidence;
}

export interface RegionPositionAnchor {
  frame_identifier: number;
  lap_distance_m: number;
  session_time_s: number;
  lap_time_s: number;
  world_position_m: { x: number; y: number; z: number };
}

export interface RegionPositionEvidence {
  distance_window_m: [number, number];
  boundary: "inclusive_start_exclusive_end";
  status:
    | "observed_positions"
    | "positions_unavailable_in_window"
    | "no_source_samples_in_window"
    | "motion_unavailable_for_trace_schema";
  source_sample_count_in_window: number;
  source_position_sample_count: number | null;
  unsupported_source_sample_count_in_window: number | null;
  source_fragment_count: number | null;
  fragment_limit: number;
  omitted_fragment_count: number;
  fragments: Array<{
    fragment_index: number;
    segment_index: number;
    break_before_reasons: string[];
    window_membership_break_before: boolean;
    window_membership_break_after: boolean;
    sample_count: number;
    clipped_at_window_start: boolean;
    clipped_at_window_end: boolean;
    start_anchor: RegionPositionAnchor;
    end_anchor: RegionPositionAnchor;
  }>;
}

export interface AttemptRegionReport {
  schema_version: 2;
  artifact_kind: "single_attempt_distance_region_observations";
  diagnostic_only: true;
  config: { grid_step_m: number; max_bracket_time_s: number; max_bracket_distance_m: number };
  source: {
    attempt_key: string;
    run_id: string;
    session_uid: string;
    car_index: number;
    attempt_number: number;
    disposition: string;
    lap_time_ms: number | null;
    game_valid: boolean | null;
    reference_eligible: boolean;
    exclusion_reasons: string[];
    trace_sha256: string;
    trace_schema_version: number;
    source_sample_count: number;
    session_context_segments: Array<{
      from_frame_identifier: number;
      context: Record<string, unknown> | null;
    }>;
  };
  analysis_version: string;
  model: TrackModelRecord & { distance_origin_m: number };
  layout_validation_status: string;
  event_thresholds: Record<string, number>;
  event_example_limit_per_channel_region: number;
  regions: AttemptRegionWindow[];
}

export interface Comparison {
  analysis_version: string;
  comparison_policy: "time_trial" | "practice_qualifying";
  comparison_policy_version: string;
  diagnostic_only: boolean;
  policy_limitations: string[];
  config: { max_bracket_time_s: number; [key: string]: number };
  target: {
    attempt_key: string;
    lap_time_ms: number | null;
    game_valid: boolean | null;
    [key: string]: unknown;
  };
  reference: {
    attempt_key: string;
    lap_time_ms: number | null;
    game_valid: boolean | null;
    [key: string]: unknown;
  };
  processing_run_evidence?: {
    target: ProcessingRunSummary | null;
    reference: ProcessingRunSummary | null;
  };
  track: { track_id: number; track_name: string; track_length_m: number };
  distance_m: number[];
  target_trace: ResampledTrace;
  reference_trace: ResampledTrace;
  delta_s: Array<number | null>;
  delta_mask: boolean[];
  official_lap_time_difference_s: number | null;
  observed_range_delta: {
    coverage: number;
    first_supported_distance_m: number | null;
    last_supported_distance_m: number | null;
    observed_range_change_s: number | null;
  };
  quality: {
    delta_time_coverage: number;
    target_excluded_spans: unknown[];
    reference_excluded_spans: unknown[];
  };
  corner_analysis?: CornerAnalysis;
}

export async function requestApi<T>(
  path: string,
): Promise<ApiResponse<T> | null> {
  const base = (
    process.env.F1_ENGINEER_API_URL ?? "http://127.0.0.1:8765"
  ).replace(/\/+$/, "");
  try {
    const response = await fetch(`${base}${path}`, { cache: "no-store" });
    return (await response.json()) as ApiResponse<T>;
  } catch {
    return null;
  }
}
