// One table for every machine reason code the UI can show. The codes come from
// the local API (`reason` fields, `sectors_reason`, `tyre_reason`,
// `time_reason`, scan stop reasons, import failures) and from the web proxies.
// Unknown codes fall back to the code itself in sentence case.

const REASONS: Record<string, string> = {
  /* ---- telemetry traces (absorbed from lap-analysis/lap-telemetry.ts) ---- */
  no_samples: "The backend returned no telemetry samples for this lap.",
  too_few_samples: "Too few telemetry samples were recorded for this lap.",
  not_processed: "Detailed telemetry has not been processed for this lap.",
  trace_not_selected: "Detailed telemetry was not selected for processing for this lap.",
  comparison_read_budget_busy: "The telemetry reader is busy. Try again in a moment.",
  analysis_read_budget_exceeded: "The telemetry for this lap is too large to read.",
  attempt_trace_unavailable: "Telemetry is not available for this lap.",
  materialization_busy: "The telemetry reader is busy. Try again in a moment.",
  unreachable: "The local engine could not be reached.",

  /* ---- /laps enrichment: sectors and tyres ---- */
  lap_not_completed: "The lap was not completed.",
  journal_processor_version_unsupported:
    "This recording was made with a processor version that cannot be read for sectors or tyres.",
  session_history_not_in_journal: "Sector times were not in the recorded packets.",
  lap_not_in_session_history: "The game's lap history did not include this lap.",
  session_history_lap_time_mismatch: "The game's lap history disagrees with the recorded lap time.",
  sectors_unavailable_in_session_history: "Sector times were not reported for this lap.",
  sector_sum_mismatch: "Sector times do not add up to the lap time.",
  sectors_unavailable_in_lap_data: "Sector times were not reported for this lap.",
  lap_data_not_in_journal: "Sector times were not in the recorded packets.",
  lap_source_range_unavailable: "The recorded packets for this lap could not be located.",
  journal_scan_budget_exceeded: "The recording is too large to read in full, so some laps were skipped.",
  car_status_not_in_journal: "Tyre data was not in the recorded packets.",
  car_status_undecodable: "Tyre data in the recording could not be read.",
  car_status_not_in_lap: "Tyre data was not recorded during this lap.",
  attempt_row_budget_exceeded: "The session has more laps than can be listed.",
  time_budget_exceeded: "Reading the recording took too long, so the results are partial.",
  lap_enrichment_busy: "The engine is busy reading another recording. Try again in a moment.",

  /* ---- /summary ---- */
  session_active: "The session is still active.",
  captured_at_unavailable: "The recording does not include capture times.",
  captured_at_not_wall_clock: "The recording's capture times are not real-world clock times.",
  no_session_packets_in_committed_journal: "No packets were recorded for this session.",
  scan_budget_exceeded_before_session: "The recording is too large to read in full.",
  scan_incomplete_time_budget: "Reading the recording took too long, so the end time is unknown.",
  scan_incomplete_row_budget: "The recording is too large to read in full, so the end time is unknown.",
  scan_incomplete_byte_budget: "The recording is too large to read in full, so the end time is unknown.",
  scan_incomplete_session_run_ended: "The scan stopped before the session ended.",
  end_of_committed_journal: "All recorded packets were read.",
  time_budget: "Reading the recording took too long, so the results are partial.",
  row_budget: "The recording is too large to read in full, so the results are partial.",
  byte_budget: "The recording is too large to read in full, so the results are partial.",
  summary_busy: "The engine is busy reading another recording. Try again in a moment.",
  summary_processor_version_unavailable:
    "This recording was made with a processor version that cannot be summarised.",
  session_not_found: "That session could not be found.",
  session_generation_unavailable: "The recording for this session is unavailable.",

  /* ---- track models ---- */
  compatible_track_model_unavailable: "No corner regions are available for this circuit.",
  session_track_context_unavailable: "The recording does not say which circuit it was made at.",
  unknown_track_model_revision: "That track model revision is not installed.",

  /* ---- engineer activity and preferences ---- */
  engineer_activity_unavailable: "The engineer activity log could not be read.",
  unsupported_engineer_activity_schema: "The engineer activity log was written by a newer version.",
  preferences_file_unreadable: "The saved settings file could not be read.",
  preferences_file_too_large: "The saved settings file is too large to read.",
  preferences_file_invalid: "The saved settings file is not valid.",
  preferences_store_unavailable: "The engine could not save settings.",
  preferences_revision_conflict: "The settings were changed somewhere else.",
  preferences_control_not_authorized: "The web app is not allowed to change engine settings.",
  preferences_if_match_invalid: "The settings revision was not understood.",
  preferences_request_too_large: "The settings were too large to send.",
  preferences_content_length_invalid: "The settings request was malformed.",
  preferences_body_malformed: "The settings request was malformed.",
  preferences_invalid_type: "A setting has the wrong type.",
  preferences_invalid_value: "A setting has an unsupported value.",
  preferences_unknown_key: "A setting is not recognised by the engine.",
  preferences_missing_key: "A setting is missing.",

  /* ---- engineer questions ---- */
  engineer_target_or_future_binding_required: "Choose either a lap or a future lap to ask about.",
  engineer_request_invalid: "The question could not be sent.",
  engineer_request_limit_exceeded: "The question is too long.",
  engineer_request_deadline_exceeded: "The engineer took too long to answer. Try again.",
  engineer_client_disconnected: "The question was cancelled.",
  engineer_control_not_authorized: "The web app is not allowed to ask the engineer.",
  engineer_ask_deadline_exceeded: "The engineer took too long to answer. Try again.",
  engineer_ask_request_limit_exceeded: "The question is too long.",
  engineer_ask_response_limit_exceeded: "The engineer's answer was too large to show.",
  engineer_ask_service_unavailable: "The engineer is unavailable.",
  engineer_ask_unavailable: "The engineer is unavailable.",
  engineer_ask_busy: "The engineer is busy. Try again in a moment.",

  /* ---- imports and recordings ---- */
  recording_upload_content_type_invalid: "Only .f1ecap capture files can be imported.",
  recording_upload_filename_invalid: "The file name could not be used. Rename the file and try again.",
  recording_upload_service_unavailable: "The local engine is unavailable.",
  capture_unavailable: "The uploaded capture could not be found.",
  capture_id_unavailable: "The uploaded capture could not be found.",
  busy: "Another import or recording is running. The capture was queued.",
  another_import_is_in_progress: "Another import or recording is running. The capture was queued.",
  another_local_operation_is_in_progress: "Another import or recording is running. The capture was queued.",
  job_not_waiting: "The import has already started and cannot be cancelled.",
  import_job_not_waiting: "The import has already started and cannot be cancelled.",
  job_unavailable: "The import could not be found.",
  import_job_unavailable: "The import could not be found.",
  import_control_not_authorized: "The web app is not allowed to start imports.",
  import_queue_unavailable: "The import queue could not be read.",
  recording_catalog_unavailable: "The recordings folder could not be read.",
  unavailable: "The import could not be started.",

  /* ---- web proxy and transport ---- */
  same_origin_request_required: "The request was blocked because it did not come from this app.",
  local_engine_unavailable: "The local engine is unavailable.",
  upstream_deadline_exceeded: "The local engine took too long to respond.",
  upstream_response_invalid: "The local engine sent a response that could not be read.",
  upstream_response_limit_exceeded: "The local engine's response was too large to read.",
  request_body_invalid: "The request could not be read.",
  request_body_limit_exceeded: "The request was too large.",
  request_content_type_invalid: "The request must be JSON.",
  request_invalid: "The request was not valid.",
  api_not_authorized: "The web app is not allowed to use the local engine.",
  configured_database_unavailable: "The session database is unavailable.",
};

function sentenceCase(code: string): string {
  const words = code.replace(/[_\s]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1).toLowerCase() : "";
}

/** Plain-language text for a backend or proxy reason code. */
export function humanReason(code: string | null | undefined): string {
  if (typeof code !== "string" || code.trim() === "") return "";
  const known = Object.hasOwn(REASONS, code) ? REASONS[code] : undefined;
  return known ?? sentenceCase(code.slice(0, 120));
}

/** True when the table has explicit copy for the code. */
export function isKnownReason(code: string | null | undefined): boolean {
  return typeof code === "string" && Object.hasOwn(REASONS, code);
}
