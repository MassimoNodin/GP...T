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

export interface Comparison {
  analysis_version: string;
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
