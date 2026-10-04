export interface ApiResponse<T> {
  api_version: "v1";
  status: "ok" | "unavailable";
  data: T | null;
  reason: string | null;
}

export interface EngineerQueryFact {
  kind: string;
  text: string;
  source_fields: string[];
}

export interface EngineerQueryWarning {
  code: string;
  text: string;
  source_fields: string[];
  sides?: string[];
}

export interface EngineerQueryReport {
  schema_version: 1;
  analysis_version: "engineer-query-v1";
  artifact_kind: "engineer_query";
  intent: "attempt_summary" | "region_comparison";
  status: "available" | "partial" | "unavailable";
  reason_codes: string[];
  selected: {
    target_attempt_key: string;
    reference_attempt_key?: string;
    comparison_policy?: "time_trial" | "practice_qualifying";
    track_model_id?: string;
    track_model_revision?: number;
    region_identifier?: string;
  };
  facts: EngineerQueryFact[];
  omitted_fact_count: number;
  warnings: EngineerQueryWarning[];
  omitted_warning_count: number;
  provenance: Record<string, unknown>;
  diagnostic_only: true;
  coaching_eligible: false;
  ranking_eligible: false;
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
  live_car_status?: LiveCarStatusRecord | null;
  live_lap_timing?: LiveLapTimingRecord | null;
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

export interface LiveCarStatusRecord {
  status: "waiting" | "fresh" | "stale" | "unsupported" | "unavailable";
  reason: string | null;
  age_ms: number | null;
  session_uid?: string | null;
  frame_identifier?: number | null;
  packet_format?: number | null;
  player_car_index?: number | null;
  fuel_in_tank_reported?: number | null;
  fuel_remaining_laps?: number | null;
  actual_tyre_compound?: number | null;
  visual_tyre_compound?: number | null;
  tyre_age_laps?: number | null;
  front_brake_bias_percent?: number | null;
  pit_limiter_active?: boolean | null;
  validation_flags?: string[];
}

export interface LiveLapTimingRecord {
  status: "waiting" | "fresh" | "stale" | "unsupported" | "unavailable";
  reason: string | null;
  age_ms: number | null;
  session_uid?: string | null;
  frame_identifier?: number | null;
  packet_format?: number | null;
  player_car_index?: number | null;
  lap_number?: number | null;
  current_lap_time_ms?: number | null;
  current_sector?: number | null;
  previous_lap_time_ms?: number | null;
  sector1_time_ms?: number | null;
  sector2_time_ms?: number | null;
  validation_flags?: string[];
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

export interface ReplayRecord {
  source_kind: "replay";
  playback_id: string;
  capture_id: string;
  capture_name: string;
  speed: number;
  state:
    | "starting"
    | "playing"
    | "stopping"
    | "stopped"
    | "completed"
    | "failed";
  elapsed_ms: number;
  datagrams_delivered: number;
  capture_complete: boolean | null;
  capture_completion: Record<string, unknown> | null;
  source_stable: boolean | null;
  latest_context: SessionContext | null;
  live_telemetry: LiveTelemetryRecord;
  live_car_status: LiveCarStatusRecord;
  live_lap_timing: LiveLapTimingRecord;
  failure_reason: string | null;
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
  origin?: "packaged" | "local_draft" | "unattributed";
  content_sha256?: string | null;
  source_filename?: string | null;
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
  start_frame_ordinal: number | null;
  end_frame_ordinal: number | null;
  superseded: boolean | null;
  lifecycle_assessed: boolean;
  lifecycle_exclusions?: string[];
  start_observed: boolean;
  pit_encountered: boolean;
  sample_count: number;
  trace_row_count: number;
  trace_schema_version: number;
  trace_sha256: string;
  context: SessionContext | null;
  quality: Record<string, unknown>;
  exclusion_reasons: string[];
  timing_evidence: AttemptTimingEvidence;
}

export interface AttemptTimingEvidence {
  status: "matched" | "ambiguous" | "conflicting" | "unavailable" | "truncated" | string;
  reasons: string[];
  reported_lap_time_ms?: number | null;
  sector1_time_ms?: number | null;
  sector2_time_ms?: number | null;
  sector3_time_ms?: number | null;
  validity_flags?: number | null;
  lap_valid?: boolean | null;
  sector1_valid?: boolean | null;
  sector2_valid?: boolean | null;
  sector3_valid?: boolean | null;
  sector_sum_residual_ms?: number | null;
  source_lap?: Record<string, unknown> | null;
  source?: Record<string, unknown> | null;
  candidates?: Array<Record<string, unknown>>;
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

export interface CarDamageObservationSummary {
  version: string;
  authority: string;
  observation_note: string;
  status: string;
  sample_count: number;
  matched_sample_count: number | null;
  missing_join_sample_count: number | null;
  unavailable_reason_counts: Record<string, number> | null;
  fields: Record<string, {
    valid_count: number;
    missing_count: number;
    invalid_count: number;
  }> | null;
  first_last_observed: Record<string, {
    first: ObservedConditionAnchor | null;
    last: ObservedConditionAnchor | null;
  }> | null;
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

export interface ObservedTrajectoryComparisonPreview {
  schema_version: 1;
  analysis_version: string;
  artifact_kind: "observed_trajectory_comparison_preview";
  status: "available";
  comparison_policy: "time_trial" | "practice_qualifying";
  diagnostic_only: true;
  is_centreline: false;
  coordinate_projection: {
    horizontal_axis: "world_x";
    vertical_axis: "world_z";
    units: "m";
    orientation_claim: null;
    equal_scale: true;
  };
  plot_bounds_world_xz_m: {
    world_x: [number, number];
    world_z: [number, number];
  };
  paths: Record<"target" | "reference", {
    source: Omit<AttemptTrajectoryPreview["source"], "context_segments">;
    attempt_evidence: {
      disposition: string;
      lap_time_ms: number | null;
      game_valid: boolean | null;
      reference_eligible: boolean;
      superseded: boolean | null;
      lifecycle_assessed: boolean;
      lifecycle_exclusions: string[];
      exclusion_reasons: string[];
      source_sample_count: number;
    };
    capture_evidence: {
      sha256: string | null;
      byte_size: number | null;
      complete: boolean | null;
      footer_status: string | null;
      recording_counters: Record<string, number | null>;
      recording_observer_counters: Record<string, number | null>;
    } | null;
    coverage: AttemptTrajectoryPreview["coverage"];
    preview: AttemptTrajectoryPreview["preview"];
    segments: AttemptTrajectoryPreview["segments"];
    break_examples: AttemptTrajectoryPreview["break_examples"];
    unsupported_examples: AttemptTrajectoryPreview["unsupported_examples"];
    position_probe?: ObservedPositionProbe;
  }>;
  position_probe?: {
    schema_version: 1;
    analysis_version: string;
    status: "available" | "unavailable";
    requested_distance_m: number | null;
    target: ObservedPositionProbe;
    reference: ObservedPositionProbe;
    difference: {
      status: "available" | "unavailable";
      world_x_m: number | null;
      world_z_m: number | null;
      horizontal_separation_m: number | null;
      reason_code: string | null;
    };
  };
  limits: {
    points_per_attempt: number;
    segments_per_attempt: number;
    combined_points: number;
  };
  limits_applied: Record<string, number>;
}

export interface ObservedPositionProbe {
  schema_version: 1;
  analysis_version: string;
  status: "available" | "unavailable";
  requested_distance_m: number | null;
  method: "exact_source_observation" | "linear_interpolation" | null;
  segment_index: number | null;
  interpolation_fraction: number | null;
  position_world_xyz_m: { x: number; y: number; z: number } | null;
  source_anchors: Array<{
    frame_identifier: number;
    lap_distance_m: number;
    session_time_s: number;
    world_position_m: { x: number; y: number; z: number };
  }>;
  source: {
    attempt_key: string | null;
    run_id: string | null;
    session_uid: string | null;
    car_index: number | null;
    trace_sha256: string | null;
    trace_schema_version: number | null;
  } | null;
  reason_code: string | null;
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
    lifecycle_evidence: {
      analysis_version: string | null;
      event_packets_decoded: number | null;
      event_decode_errors: number | null;
      lifecycle_event_count: number | null;
      lifecycle_events_dropped: number | null;
      lifecycle_reconciliation_work: number | null;
      lifecycle_reconciliation_truncated_session_count: number | null;
      event_code_counts: Record<string, number>;
    } | null;
  };
  totals: {
    session_count: number;
    attempt_count: number;
    disposition_counts: Record<string, number>;
    game_validity_counts: { valid: number; invalid: number; unknown: number };
    stored_reference_eligible_count: number;
    exclusion_reason_counts: Array<{ reason: string; attempt_count: number }>;
    exclusion_reasons_complete: boolean;
    lifecycle_event_count: number;
    flashback_event_count: number;
    session_time_regression_count: number;
    uncertain_lifecycle_event_count: number;
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
  lifecycle_event_count: number;
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
  start_frame_ordinal: number | null;
  end_frame_ordinal: number | null;
  superseded: boolean | null;
  lifecycle_assessed: boolean;
  timing_evidence: AttemptTimingEvidence;
}

export interface ProcessingRunLifecycleEvent {
  event_ordinal: number;
  session_uid: string;
  frame_ordinal: number;
  current_frame_identifier: number;
  current_overall_frame_identifier: number;
  packet_format: number;
  packet_version: number | null;
  event_code: string | null;
  event_kind: string;
  session_time_s: number;
  target_game_frame_identifier: number | null;
  target_session_time_s: number | null;
  prior_session_time_s: number | null;
  cause: string;
  evidence_status: string;
  details_hex: string;
  details_length_bytes: number;
  details_truncated: boolean;
  duplicate_count: number;
}

export interface ProcessingRunDetail {
  summary: ProcessingRunSummary;
  sessions: ProcessingRunPage<ProcessingRunSession>;
  attempts: ProcessingRunPage<ProcessingRunAttempt>;
  lifecycle_events: ProcessingRunPage<ProcessingRunLifecycleEvent>;
}

export interface CarObservationSlot {
  car_index: number;
  observation_count: number;
  car_telemetry_count: number;
  motion_count: number;
  nonzero_speed_count: number;
  header_player_count: number;
  participant_snapshot_count: number;
  first_frame_ordinal: number;
  last_frame_ordinal: number;
  activity_evidence: "nonzero_speed_observed" | "no_nonzero_speed_observed";
}

export interface CarObservationInventory {
  run_id: string;
  session_uid: string;
  status: "available";
  archive_status: "available" | "empty" | "not_archived" | "unavailable";
  verification_scope: "session_car_observations";
  opponent_eligibility: "not_assessed";
  capture: { complete: boolean; footer_status: string | null };
  replay_quality: {
    late_packets_ignored: number | null;
    frame_overflow_packets_dropped: number | null;
    conflicting_observation_frames: number | null;
  };
  slots: ProcessingRunPage<CarObservationSlot>;
}

export interface CarObservationPreviewItem {
  frame_identifier: number;
  frame_ordinal: number;
  session_time_s: number;
  car_index: number;
  header_player_car_index: number;
  packet_format: number;
  lifecycle_epoch: number;
  lap_number: number;
  lap_distance_m: number | null;
  total_distance_m: number | null;
  current_lap_time_ms: number;
  speed_mps: number | null;
  throttle: number | null;
  brake: number | null;
  steering: number | null;
  gear: number | null;
  engine_rpm: number | null;
  drs_active: boolean | null;
  car_telemetry_available: boolean;
  car_telemetry_unavailable_reason: string | null;
  motion_available: boolean;
  motion_unavailable_reason: string | null;
  world_position_x_m: number | null;
  world_position_y_m: number | null;
  world_position_z_m: number | null;
  world_velocity_x_mps: number | null;
  world_velocity_y_mps: number | null;
  world_velocity_z_mps: number | null;
  g_force_lateral: number | null;
  g_force_longitudinal: number | null;
  g_force_vertical: number | null;
  context: Record<string, unknown> | null;
  validation_flags: string[];
}

export interface CarObservationPreview {
  run_id: string;
  session_uid: string;
  car_index: number;
  status: "available";
  archive_status: "available" | "empty" | "not_archived" | "unavailable";
  verification_scope: "session_car_observations";
  opponent_eligibility: "not_assessed";
  capture: { complete: boolean; footer_status: string | null };
  observations: {
    limit: number;
    offset: number;
    total: number;
    returned: number;
    source_chunks_read: number;
    items: CarObservationPreviewItem[];
  };
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
  car_damage_observations?: CarDamageObservationSummary;
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
    source_anchor?: {
      frame_identifier: number | null;
      session_time_s: number | null;
      lap_distance_m: number | null;
    } | null;
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
    target_time_coverage: number;
    reference_time_coverage: number;
    shared_time_coverage: number;
    target_resampled_time_connected: boolean;
    reference_resampled_time_connected: boolean;
    shared_delta_time_connected: boolean;
    target_source_session_time_connected: boolean;
    reference_source_session_time_connected: boolean;
    interval_connected_supported_time: boolean;
    unavailable_reason: string | null;
    unavailable_reasons: string[];
  };
}

export interface CornerAnalysis {
  analysis_version: string;
  model: TrackModelRecord;
  layout_validation_status: string;
  attempts_reference_eligible: { target: boolean; reference: boolean };
  diagnostic_only: boolean;
  source: {
    target: { attempt_key: string; run_id: string; trace_sha256: string };
    reference: { attempt_key: string; run_id: string; trace_sha256: string };
  };
  regions: CornerRegion[];
}

export interface PairedRegionDifference {
  status: "supported" | "unavailable";
  schema_version?: 1;
  analysis_version?: string;
  threshold?: number;
  value?: number | null;
  unit?: string;
  direction?: string;
  target_value?: number;
  reference_value?: number;
  distance_m?: number;
  target_anchor?: Record<string, number>;
  reference_anchor?: Record<string, number>;
  target_start_bracket_m?: [number, number];
  reference_start_bracket_m?: [number, number];
  target_minus_reference_start_bracket_m?: [number, number];
  target_end_bracket_m?: [number, number];
  reference_end_bracket_m?: [number, number];
  target_minus_reference_end_bracket_m?: [number, number];
  left_censored?: { target: boolean; reference: boolean };
  right_censored?: { target: boolean; reference: boolean };
  entry_delta_s?: number | null;
  exit_delta_s?: number | null;
  unavailable_reason?: string | null;
  [key: string]: unknown;
}

export interface BrakeThresholdReleaseDifference extends PairedRegionDifference {
  schema_version?: 1;
  analysis_version?: "brake-threshold-release-v1";
  threshold?: 0.1;
  target_end_bracket_m?: [number, number];
  reference_end_bracket_m?: [number, number];
  target_minus_reference_end_bracket_m?: [number, number];
  left_censored?: { target: boolean; reference: boolean };
  right_censored?: { target: boolean; reference: boolean };
}

export interface PairedRegionEntry {
  identifier: string;
  label: string;
  analysis_window_m: [number, number];
  configured_windows_m: {
    analysis: [number, number];
    braking_search: [number, number] | null;
    turn_in_search: [number, number] | null;
    throttle_pickup_search: [number, number] | null;
    exit_distance_m: number | null;
  };
  diagnostic_only: true;
  coaching_eligible: false;
  ranking_eligible: false;
  target: RegionAttempt;
  reference: RegionAttempt;
  delta_change: CornerRegion["delta_change"];
  supported_differences: Record<string, PairedRegionDifference> & {
    brake_10_percent_release?: BrakeThresholdReleaseDifference;
  };
  debrief?: {
    schema_version: 1;
    analysis_version: "diagnostic-region-debrief-v1";
    diagnostic_only: true;
    coaching_eligible: false;
    ranking_eligible: false;
    facts: Array<{ kind: string; text: string; source_fields: string[] }>;
    omissions: Array<{ kind: string; reason_code: string; text: string }>;
  };
}

export interface PairedRegionReport {
  schema_version: 1;
  analysis_version: string;
  region_analysis_version: string;
  artifact_kind: "paired_distance_region_observations";
  status: "available";
  comparison_policy: "time_trial" | "practice_qualifying";
  diagnostic_only: true;
  coaching_eligible: false;
  ranking_eligible: false;
  config: { grid_step_m: number; max_bracket_time_s: number; max_bracket_distance_m: number };
  policy_limitations: string[];
  track: {
    packet_format: number;
    track_id: number;
    track_name: string;
    track_length_m: number;
    layout_identity_status: "caller_declared";
  };
  attempts: Record<"target" | "reference", {
    attempt_key: string;
    run_id: string;
    session_uid: string;
    car_index: number;
    game_valid: boolean | null;
    reference_eligible: boolean;
    superseded: boolean | null;
    lifecycle_assessed: boolean;
    trace_sha256: string;
    trace_schema_version: number;
    capture: Record<string, unknown> | null;
    replay_counters: Record<string, unknown> | null;
    [key: string]: unknown;
  }>;
  warnings: Record<"target" | "reference", Array<{ code: string; text: string }>>;
  model: TrackModelRecord & {
    distance_origin_m: number;
    layout_identity_status: "caller_declared";
  };
  regions: PairedRegionEntry[];
  resource_policy: Record<string, unknown>;
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
  schema_version: 3;
  artifact_kind: "single_attempt_distance_region_observations";
  diagnostic_only: true;
  context_mode: "time_trial" | "practice_qualifying";
  config: { grid_step_m: number; max_bracket_time_s: number; max_bracket_distance_m: number };
  resource_policy: {
    resampling: { version: string; estimated_work: number; limit: number; status: string };
    region_analysis: { version: string; estimated_work: number; limit: number; status: string };
  };
  warnings: Array<{ code: string; text: string }>;
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
    start_observed: boolean;
    pit_encountered: boolean;
    superseded: boolean | null;
    lifecycle_assessed: boolean;
    trace_sha256: string;
    trace_schema_version: number;
    source_sample_count: number;
    capture: {
      complete: boolean | null;
      footer_status: string | null;
      recording_counters: Record<string, number | null>;
    } | null;
    replay_counters: Record<string, number | null> | null;
    session_context_segments: Array<{
      from_frame_identifier: number;
      context: Record<string, unknown> | null;
    }>;
  };
  analysis_version: string;
  model: TrackModelRecord & {
    distance_origin_m: number;
    layout_identity_status: "caller_declared";
  };
  layout_validation_status: string;
  event_thresholds: Record<string, number>;
  event_example_limit_per_channel_region: number;
  regions: AttemptRegionWindow[];
}

export interface ComparisonBriefFact {
  kind: string;
  text: string;
  unit?: string;
  source_fields: Record<string, string>;
  provenance: {
    target: {
      attempt_key: string | null;
      run_id: string | null;
      trace_sha256: string | null;
    };
    reference: {
      attempt_key: string | null;
      run_id: string | null;
      trace_sha256: string | null;
    };
  };
  [key: string]: unknown;
}

export interface ComparisonBrief {
  schema_version: number;
  analysis_version: string;
  status: "available" | "unavailable";
  diagnostic_only: boolean;
  text: string;
  facts: ComparisonBriefFact[];
  limitations: Array<{ code: string; text: string }>;
  limits: {
    fact_limit: number;
    omitted_fact_count: number;
    limitation_limit: number;
    omitted_limitation_count: number;
  };
}

export interface CornerLossCandidate {
  rank: number;
  region_id: string;
  region_label: string;
  analysis_window_m: [number, number];
  recorded_time_difference_s: number;
  measurement_direction: "target_minus_reference_interval_time_difference";
  connected_support: {
    target_resampled_time_connected: boolean;
    reference_resampled_time_connected: boolean;
    shared_delta_time_connected: boolean;
    target_source_session_time_connected: boolean;
    reference_source_session_time_connected: boolean;
    interval_connected_supported_time: boolean;
    target_time_coverage: number | null;
    reference_time_coverage: number | null;
    shared_time_coverage: number | null;
    entry_delta_s: number | null;
    exit_delta_s: number | null;
  };
}

export interface CornerLossCandidates {
  schema_version: number;
  analysis_version: string;
  policy_version: string;
  status: "abstained" | "ranked" | "no_positive_supported_differences";
  measurement_label: string;
  coaching_eligible: false;
  source: {
    target: Record<string, string | number | null>;
    reference: Record<string, string | number | null>;
    model: {
      model_id: string;
      revision: number;
      validation_status: string;
      registered: boolean;
      approved_for_candidate_ranking: boolean;
      model_content_sha256: string | null;
      approval_provenance: Record<string, unknown> | null;
    } | null;
    reference_selection: {
      reference_kind: string;
      status: string;
      policy_version: string;
      scope: Record<string, unknown> | null;
      selected_reference: Record<string, unknown> | null;
      reasons: string[];
    } | null;
    capture_evidence: "passed_session_best_policy" | "not_established";
  };
  gate_reasons: string[];
  gate_reasons_omitted_count: number;
  ranked_candidates: CornerLossCandidate[];
  region_assessment: {
    region_count: number;
    assessed_region_count: number;
    connected_interval_count: number;
    unsupported_region_count: number;
    diagnostic_region_count: number;
    non_positive_region_count: number;
    below_display_resolution_count: number;
    all_model_regions_assessed: boolean;
    excluded_regions: Array<{
      region_id: string | null;
      region_label: string | null;
      analysis_window_m: [number, number] | null;
      connected_interval_supported: boolean;
      recorded_time_difference_s: number | null;
      reasons: string[];
    }>;
    excluded_regions_omitted_count: number;
    omitted_region_count: number;
  };
  omitted_candidate_count: number;
  limits: Record<string, number>;
}

export interface CornerComparisonFact {
  kind: string;
  label: string;
  value: number;
  unit: string;
  direction: "target_minus_reference";
  text: string;
  source_fields: Record<string, string>;
  provenance: {
    target: { attempt_key?: string | null; run_id?: string | null; trace_sha256?: string | null };
    reference: { attempt_key?: string | null; run_id?: string | null; trace_sha256?: string | null };
    model: { model_id?: string; revision?: number; model_content_sha256?: string | null };
    reference_selection: Record<string, unknown>;
  };
  [key: string]: unknown;
}

export interface CornerComparisonBrief {
  schema_version: number;
  analysis_version: string;
  status: "available" | "abstained" | "no_ranked_candidates";
  coaching_eligible: false;
  text: string;
  regions: Array<{
    rank: number;
    region_id: string;
    region_label: string;
    analysis_window_m: [number, number];
    facts: CornerComparisonFact[];
    omitted_measurements: Array<{ metric: string; reason: string }>;
    omitted_measurement_count: number;
    connected_support: Record<string, boolean | number | null>;
    provenance: CornerComparisonFact["provenance"];
  }>;
  gate_reasons: string[];
  gate_reasons_omitted_count: number;
  omitted_region_count: number;
  limits: Record<string, number>;
}

export interface Comparison {
  analysis_version: string;
  comparison_brief: ComparisonBrief;
  corner_comparison_brief: CornerComparisonBrief;
  distance_window_brief?: DistanceWindowBrief;
  comparison_policy: "time_trial" | "practice_qualifying";
  comparison_policy_version: string;
  diagnostic_only: boolean;
  policy_limitations: string[];
  reported_timing_evidence: {
    target: AttemptTimingEvidence;
    reference: AttemptTimingEvidence;
  };
  sector_timing_difference_ms: {
    direction: "target_minus_reference";
    status: string;
    sectors: Record<
      string,
      {
        target_time_ms: number | null;
        reference_time_ms: number | null;
        target_minus_reference_ms: number | null;
        target_valid: boolean | null;
        reference_valid: boolean | null;
        status: string;
      }
    >;
    target_sector_sum_residual_ms: number | null;
    reference_sector_sum_residual_ms: number | null;
  };
  config: { max_bracket_time_s: number; [key: string]: number };
  target: {
    attempt_key: string;
    lap_time_ms: number | null;
    game_valid: boolean | null;
    reference_eligible?: boolean;
    superseded?: boolean | null;
    lifecycle_assessed?: boolean;
    exclusion_reasons?: string[];
    lifecycle_exclusions?: string[];
    [key: string]: unknown;
  };
  reference: {
    attempt_key: string;
    lap_time_ms: number | null;
    game_valid: boolean | null;
    reference_eligible?: boolean;
    superseded?: boolean | null;
    lifecycle_assessed?: boolean;
    exclusion_reasons?: string[];
    lifecycle_exclusions?: string[];
    [key: string]: unknown;
  };
  processing_run_evidence?: {
    target: ProcessingRunSummary | null;
    reference: ProcessingRunSummary | null;
  };
  observed_conditions: {
    target: ObservedConditionSummary;
    reference: ObservedConditionSummary;
  };
  comparison_window?: ComparisonWindow;
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
  corner_loss_candidates: CornerLossCandidates;
}

export interface ObservedConditionSummary {
  status: string;
  matched_sample_count: number | null;
  sample_count: number;
  missing_join_sample_count: number | null;
  unavailable_reason_counts: Record<string, number> | null;
  fields: Record<
    string,
    { valid_count: number; missing_count: number; invalid_count: number }
  > | null;
  first_last_observed: Record<
    string,
    {
      first: ObservedConditionAnchor | null;
      last: ObservedConditionAnchor | null;
    }
  > | null;
  discrete_changes: unknown[] | null;
  discrete_changes_truncated: boolean | null;
  distinct_compounds:
    | {
        actual: Array<{ raw_id: number; formula_id: number | null; label: string | null }>;
        visual: Array<{ raw_id: number; formula_id: number | null; label: string | null }>;
      }
    | null;
  fuel_quantity_unit_note: string;
  car_damage_observations?: CarDamageObservationSummary;
  environment_context: {
    status: string;
    segment_count: number;
    known_segment_count: number;
    unknown_segment_count: number;
    missing_value_counts: Record<string, number>;
    retained_segments: Array<{
      from_frame_identifier: number;
      weather_id: number | null;
      weather_name: string | null;
      track_temperature_c: number | null;
      air_temperature_c: number | null;
      formula_id: number | null;
    }>;
    omitted_segment_count: number;
    distinct_values: Record<
      string,
      { values: Array<string | number>; truncated: boolean }
    >;
  };
}

export interface ObservedConditionAnchor {
  value: number | boolean;
  frame_identifier: number | null;
  session_time_s: number | null;
  lap_distance_m: number | null;
}

export interface ComparisonWindow {
  schema_version: 1;
  artifact_kind: "comparison_distance_window";
  analysis_version: string;
  diagnostic_only: true;
  interval_convention: "[start_m, end_m)";
  window_m: { start_m: number; end_m: number };
  config: Record<string, number>;
  event_thresholds: Record<
    string,
    { channel: string; threshold: number; absolute: boolean }
  >;
  target: ComparisonWindowAttempt;
  reference: ComparisonWindowAttempt;
  delta: {
    status: string;
    start_boundary: {
      status: string;
      distance_m: number;
      target_minus_reference_s: number | null;
    };
    end_boundary: {
      status: string;
      distance_m: number;
      target_minus_reference_s: number | null;
    };
    target_time_coverage: number;
    reference_time_coverage: number;
    shared_time_coverage: number;
    target_source_session_time_connected: boolean;
    reference_source_session_time_connected: boolean;
    target_resampled_time_connected: boolean;
    reference_resampled_time_connected: boolean;
    shared_delta_time_connected: boolean;
    interval_connected_supported_time: boolean;
    delta_change_s: number | null;
    direction: string;
    unavailable_reason: string | null;
    unavailable_reasons: string[];
  };
}

export interface DistanceWindowBrief {
  schema_version: 1;
  artifact_kind: "distance_window_brief";
  analysis_version: string;
  status: "available" | "unavailable";
  diagnostic_only: true;
  coaching_eligible: false;
  text: string;
  window_m: { start_m: number; end_m: number } | null;
  provenance: {
    comparison_policy: string | null;
    window_m: { start_m: number; end_m: number } | null;
    target: {
      attempt_key?: string | null;
      run_id?: string | null;
      trace_sha256?: string | null;
    };
    reference: {
      attempt_key?: string | null;
      run_id?: string | null;
      trace_sha256?: string | null;
    };
  };
  facts: Array<{
    kind: string;
    text: string;
    source_fields: Record<string, string>;
    provenance: DistanceWindowBrief["provenance"];
    [key: string]: unknown;
  }>;
  limitations: Array<{ code: string; text: string }>;
  warnings: Array<{ code: string; text: string }>;
  limits: Record<string, number>;
}

export interface ObservationSetAttempt {
  attempt_key: string;
  attempt_number: number;
  run_id: string;
  session_uid: string;
  car_index: number;
  disposition: string;
  lap_time_ms: number | null;
  game_valid: boolean | null;
  superseded: boolean | null;
  lifecycle_assessed: boolean;
  attempt_exclusion_reasons: string[];
  trace_sha256: string | null;
  trace_schema_version: number | null;
  source_sample_count: number | null;
  observed_context?: {
    weather_id: number | null;
    weather_name: string | null;
    track_temperature_c: number | null;
    air_temperature_c: number | null;
    [key: string]: unknown;
  };
  analysis_status: "available" | "unavailable";
  analysis_reason: string | null;
  aggregate_exclusion_reasons: string[];
  warnings: Array<{ code: string; text: string }>;
  measurements?: {
    source_sample_count: number;
    speed_sample_count: number;
    brake_sample_count: number;
    minimum_speed: { status: string; speed_kph: number | null; anchor: Record<string, unknown> | null };
    peak_brake: { status: string; value: number | null; anchor: Record<string, unknown> | null };
    coverage: Record<string, number>;
    threshold_events: Record<string, ObservationThresholdEventSummary>;
  };
  [key: string]: unknown;
}

export interface ObservationThresholdEventSummary {
  status: string;
  events: Array<Record<string, unknown>>;
  event_count: number;
  events_truncated: boolean;
  left_censored_event_count: number;
  right_censored_event_count: number;
  rejected_short_event_count: number;
  unsupported_break_count: number;
}

export interface ObservationSetMetric {
  status: "supported" | "insufficient_contributors";
  required_contributor_count: number;
  contributor_count: number;
  unit: string;
  minimum: number | null;
  maximum: number | null;
  range: number | null;
  contributors: Array<{ attempt_key: string; value: number }>;
  exclusion_counts: Array<{ reason: string; attempt_count: number }>;
}

export interface ObservationSetReport {
  schema_version: 1;
  artifact_kind: "selected_window_observation_set";
  analysis_version: string;
  status: "available";
  comparison_policy: "time_trial" | "practice_qualifying";
  diagnostic_only: true;
  coaching_eligible: false;
  consistency_claim: false;
  interval_convention: "[start_m, end_m)";
  window_m: { start_m: number; end_m: number };
  track: { track_id: number | null; track_name: string | null; track_length_m: number };
  scope: { run_id: string; session_uid: string; car_index: number };
  attempts: ObservationSetAttempt[];
  aggregates: { minimum_speed: ObservationSetMetric; peak_brake: ObservationSetMetric };
  warnings: Array<{ code: string; text: string }>;
  limits: Record<string, number>;
}

export interface ComparisonWindowAttempt {
  source_sample_count: number;
  speed_sample_count: number;
  brake_sample_count: number;
  minimum_speed: {
    status: string;
    speed_kph: number | null;
    anchor: {
      frame_identifier: number;
      session_time_s: number | null;
      lap_distance_m: number;
    } | null;
  };
  peak_brake: {
    status: string;
    value: number | null;
    anchor: {
      frame_identifier: number;
      session_time_s: number | null;
      lap_distance_m: number;
    } | null;
  };
  coverage: Record<string, number>;
  threshold_events: Record<
    string,
    {
      status: string;
      threshold: number;
      events: Array<{
        channel: string;
        threshold: number;
        start_distance_m: number;
        start_distance_bracket_m: number[] | null;
        end_distance_m: number;
        end_distance_bracket_m: number[] | null;
        start_session_time_s: number;
        end_session_time_s: number;
        duration_s: number;
        peak_value: number;
        start_speed_mps: number | null;
        left_censored: boolean;
        right_censored: boolean;
      }>;
      event_count: number;
      events_truncated: boolean;
      left_censored_event_count: number;
      right_censored_event_count: number;
      rejected_short_event_count: number;
      unsupported_break_count: number;
    }
  >;
  excluded_spans: {
    count: number;
    examples: Array<{
      start_distance_m: number;
      end_distance_m: number;
      reason: string;
      channel: string | null;
    }>;
    truncated: boolean;
  };
}

export async function requestApi<T>(
  path: string,
): Promise<ApiResponse<T> | null> {
  const base = (
    process.env.F1_ENGINEER_API_URL ?? "http://127.0.0.1:8765"
  ).replace(/\/+$/, "");
  try {
    const response = await fetch(`${base}${path}`, { cache: "no-store" });
    const body: unknown = await response.json();
    return isApiResponseEnvelope(body) ? (body as ApiResponse<T>) : null;
  } catch {
    return null;
  }
}

function isApiResponseEnvelope(value: unknown): value is ApiResponse<unknown> {
  if (!value || typeof value !== "object") return false;
  const response = value as Partial<ApiResponse<unknown>>;
  return (
    response.api_version === "v1" &&
    (response.status === "ok" || response.status === "unavailable") &&
    "data" in response &&
    (response.reason === null || typeof response.reason === "string")
  );
}

export async function requestApiPost<T>(
  path: string,
  body: object,
): Promise<ApiResponse<T> | null> {
  const base = (
    process.env.F1_ENGINEER_API_URL ?? "http://127.0.0.1:8765"
  ).replace(/\/+$/, "");
  try {
    const response = await fetch(`${base}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    if (!response.ok) {
      return {
        api_version: "v1",
        status: "unavailable",
        data: null,
        reason: `http_status_${response.status}`,
      };
    }
    const result = (await response.json()) as ApiResponse<T>;
    if (
      result.api_version !== "v1" ||
      (result.status !== "ok" && result.status !== "unavailable")
    ) {
      return null;
    }
    return result;
  } catch {
    return null;
  }
}
