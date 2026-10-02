export interface ApiResponse<T> {
  api_version: "v1";
  status: "ok" | "unavailable";
  data: T | null;
  reason: string | null;
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
