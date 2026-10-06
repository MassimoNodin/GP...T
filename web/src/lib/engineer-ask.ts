import type {
  EngineerAskResult,
  EngineerAskSelection,
  EngineerQueryReport,
  LapDebrief,
  TrackModelRecord,
  EngineerRuntimeStatus,
} from "@/lib/api";
import {
  engineerQueryReportMatchesRequest,
  type EngineerQueryRequestIdentity,
} from "@/lib/engineer-query-match";
import { comparisonReportMatchesAttempts } from "@/lib/comparison-report-match";
import {
  lapDebriefMatchesComparison,
  type LapDebriefAttemptIdentity,
} from "@/lib/lap-debrief-match";

export type EngineerAskDebriefValidation = {
  report: EngineerQueryReport | null;
  target: LapDebriefAttemptIdentity | null;
  reference: LapDebriefAttemptIdentity | null;
  model: TrackModelRecord | null;
};

export function parseEngineerAskResult(
  value: unknown,
  expected: EngineerAskSelection,
  validation: EngineerAskDebriefValidation,
): EngineerAskResult | null {
  const result = record(value);
  if (
    !result ||
    result.schema_version !== 1 ||
    result.analysis_version !== "engineer-ask-v1" ||
    typeof result.status !== "string" ||
    !["answered", "partial", "unavailable", "unsupported"].includes(
      result.status,
    ) ||
    typeof result.message !== "string" ||
    result.message.length > 500 ||
    result.diagnostic_only !== true ||
    result.coaching_eligible !== false ||
    result.ranking_eligible !== false ||
    !("debrief" in result) ||
    !("debrief_evidence" in result) ||
    !sameSelection(result.selection, expected)
  ) {
    return null;
  }

  const route = result.route;
  const focus = result.focus;
  if (
    route !== null &&
    route !== "attempt_summary" &&
    route !== "region_comparison" &&
    route !== "lap_debrief" &&
    route !== "unsupported"
  ) {
    return null;
  }
  if (focus !== null && (typeof focus !== "string" || focus.length > 40))
    return null;
  if (
    result.reason !== undefined &&
    (typeof result.reason !== "string" || result.reason.length > 96)
  ) {
    return null;
  }

  let report: EngineerQueryReport | null = null;
  if (result.report !== null) {
    if (
      !result.report ||
      typeof result.report !== "object" ||
      (route !== "attempt_summary" && route !== "region_comparison")
    ) {
      return null;
    }
    const requested = queryIdentity(expected);
    if (
      !requested ||
      !engineerQueryReportMatchesRequest(
        result.report as EngineerQueryReport,
        requested,
      )
    ) {
      return null;
    }
    report = result.report as EngineerQueryReport;
  }

  let debrief: LapDebrief | null = null;
  let debriefEvidence: Record<string, unknown> | null = null;
  if (result.debrief !== null || result.debrief_evidence !== null) {
    if (
      route !== "lap_debrief" ||
      expected.intent !== "region_comparison" ||
      expected.comparison_policy !== "time_trial" ||
      !record(result.debrief) ||
      !record(result.debrief_evidence) ||
      !validDebriefEvidence(
        result.debrief_evidence,
        result.debrief,
        expected,
        validation,
      )
    ) {
      return null;
    }
    debrief = result.debrief as LapDebrief;
    debriefEvidence = result.debrief_evidence as Record<string, unknown>;
  }

  if (
    (result.status === "answered" || result.status === "partial") &&
    !report &&
    !debrief
  ) {
    return null;
  }
  if (route === "lap_debrief" && !debrief) return null;
  if (result.status === "unsupported" && route !== "unsupported") return null;
  if (route === "attempt_summary" && expected.intent !== "attempt_summary")
    return null;
  if (
    (route === "region_comparison" || route === "lap_debrief") &&
    expected.intent !== "region_comparison"
  ) {
    return null;
  }

  const model = result.model === null ? null : parseModel(result.model);
  if (result.model !== null && !model) return null;
  return {
    schema_version: 1,
    analysis_version: "engineer-ask-v1",
    status: result.status as EngineerAskResult["status"],
    route,
    focus,
    message: result.message,
    ...(typeof result.reason === "string" ? { reason: result.reason } : {}),
    selection: result.selection as Record<string, unknown>,
    report,
    debrief,
    debrief_evidence: debriefEvidence,
    model,
    diagnostic_only: true,
    coaching_eligible: false,
    ranking_eligible: false,
  };
}

export function parseEngineerApiFailure(value: unknown): string | null {
  const response = record(value);
  if (
    !response ||
    response.api_version !== "v1" ||
    response.status !== "unavailable" ||
    typeof response.reason !== "string" ||
    response.reason.length > 96
  ) {
    return null;
  }
  const reasons: Record<string, string> = {
    engineer_ask_busy:
      "GP...T is already answering a question. Try again in a moment.",
    engineer_ask_deadline_exceeded:
      "The local question timed out. Shorten it and try again.",
    engineer_ask_unavailable:
      "The pinned local model is unavailable. Check Settings, then retry.",
    engineer_ask_source_unavailable:
      "The selected report could not be read. Reopen the exact selection and retry.",
    engineer_ask_selection_unavailable:
      "That selection is not available under its recorded mode policy.",
    engineer_ask_request_limit_exceeded:
      "This request is too large. Shorten the question and try again.",
    engineer_ask_question_limit_exceeded:
      "Keep the question under 1 KiB of text.",
  };
  return (
    reasons[response.reason] ??
    "The local question could not be completed. Check the service and retry."
  );
}

export function parseEngineerRuntimeResponse(
  value: unknown,
): EngineerRuntimeStatus | null {
  const envelope = record(value);
  const statusData = record(envelope?.data);
  if (
    !envelope ||
    envelope.api_version !== "v1" ||
    typeof envelope.status !== "string" ||
    !["ok", "unavailable"].includes(envelope.status) ||
    !statusData ||
    statusData.runtime !== "Ollama" ||
    statusData.endpoint !== "127.0.0.1:11435" ||
    statusData.model_name !== "qwen3:4b" ||
    statusData.cloud_routing !== "unverified" ||
    statusData.requested_placement !== "CPU" ||
    typeof statusData.status !== "string" ||
    ![
      "ready",
      "not_configured",
      "model_unavailable",
      "model_changed",
      "model_rejected",
      "unavailable",
    ].includes(statusData.status) ||
    !(statusData.reason === null || text(statusData.reason, 96)) ||
    !(
      statusData.runtime_version === null ||
      text(statusData.runtime_version, 64)
    ) ||
    !(
      statusData.model_digest === null ||
      validModelDigest(statusData.model_digest)
    ) ||
    !(
      statusData.model_quantization === null ||
      text(statusData.model_quantization, 32)
    ) ||
    !(
      statusData.model_parameter_size === null ||
      text(statusData.model_parameter_size, 32)
    ) ||
    !(
      statusData.last_inference_placement === null ||
      (text(statusData.last_inference_placement, 32) &&
        statusData.last_inference_placement.toUpperCase().includes("CPU") &&
        !statusData.last_inference_placement.toUpperCase().includes("GPU"))
    ) ||
    !(
      statusData.last_inference_at_utc === null ||
      (text(statusData.last_inference_at_utc, 40) &&
        Number.isFinite(Date.parse(statusData.last_inference_at_utc)))
    )
  ) {
    return null;
  }
  if (
    statusData.status === "ready" &&
    (!text(statusData.runtime_version, 64) ||
      !validModelDigest(statusData.model_digest))
  ) {
    return null;
  }
  return statusData as unknown as EngineerRuntimeStatus;
}

function queryIdentity(
  selection: EngineerAskSelection,
): EngineerQueryRequestIdentity | null {
  if (selection.intent === "attempt_summary") {
    return {
      intent: "attempt_summary",
      targetAttemptKey: selection.target_attempt_key,
    };
  }
  return {
    intent: "region_comparison",
    targetAttemptKey: selection.target_attempt_key,
    referenceAttemptKey: selection.reference_attempt_key,
    comparisonPolicy: selection.comparison_policy,
    trackModelId: selection.track_model_id,
    trackModelRevision: selection.track_model_revision,
    regionIdentifier: selection.region_identifier,
  };
}

function validDebriefEvidence(
  evidenceValue: unknown,
  value: unknown,
  expected: Extract<EngineerAskSelection, { intent: "region_comparison" }>,
  validation: EngineerAskDebriefValidation,
): boolean {
  const debrief = record(value);
  const evidence = record(evidenceValue);
  const selectedModel = validation.model;
  const report = validation.report;
  const request = queryIdentity(expected);
  const reportModel = record(report?.provenance.model);
  const selectedTarget = validation.target;
  const selectedReference = validation.reference;
  const analysis = record(evidence?.corner_analysis);
  const analysisModel = record(analysis?.model);
  const ranking = record(evidence?.corner_loss_candidates);
  const rankingSource = record(ranking?.source);
  const rankingModel = record(rankingSource?.model);
  const track = record(evidence?.track);
  if (
    !debrief ||
    !evidence ||
    evidence.schema_version !== 1 ||
    evidence.artifact_kind !== "engineer_ask_lap_debrief_evidence" ||
    evidence.comparison_policy !== "time_trial" ||
    expected.comparison_policy !== "time_trial" ||
    !selectedTarget ||
    !selectedReference ||
    !selectedModel ||
    !report ||
    !request ||
    !engineerQueryReportMatchesRequest(report, request) ||
    !reportModel ||
    reportModel.model_id !== selectedModel.model_id ||
    reportModel.revision !== selectedModel.revision ||
    reportModel.content_sha256 !== selectedModel.content_sha256 ||
    reportModel.model_content_sha256 !== selectedModel.model_content_sha256 ||
    reportModel.origin !== selectedModel.origin ||
    typeof analysis?.diagnostic_only !== "boolean" ||
    !analysisModel ||
    analysisModel.model_id !== selectedModel.model_id ||
    analysisModel.revision !== selectedModel.revision ||
    analysisModel.validation_status !== selectedModel.validation_status ||
    analysisModel.packet_format !== selectedModel.packet_format ||
    analysisModel.track_id !== selectedModel.track_id ||
    analysisModel.track_name !== selectedModel.track_name ||
    analysisModel.layout_id !== selectedModel.layout_id ||
    analysisModel.track_length_m !== selectedModel.track_length_m ||
    analysisModel.provenance !== selectedModel.provenance ||
    !rankingModel ||
    rankingModel.model_id !== selectedModel.model_id ||
    rankingModel.revision !== selectedModel.revision ||
    rankingModel.model_content_sha256 !== selectedModel.model_content_sha256 ||
    rankingModel.origin !== selectedModel.origin ||
    rankingModel.content_sha256 !== selectedModel.content_sha256 ||
    track?.track_length_m !== selectedModel.track_length_m ||
    !comparisonReportMatchesAttempts(
      evidence,
      selectedTarget,
      selectedReference,
    ) ||
    !lapDebriefMatchesComparison(
      debrief,
      evidence,
      selectedTarget,
      selectedReference,
    )
  ) {
    return false;
  }
  return true;
}

function validModelDigest(value: unknown): value is string {
  return typeof value === "string" && /^sha256:[a-f0-9]{64}$/.test(value);
}

function parseModel(value: unknown): EngineerAskResult["model"] {
  const model = record(value);
  if (
    !model ||
    !text(model.model_name, 64) ||
    model.model_name !== "qwen3:4b" ||
    typeof model.model_digest !== "string" ||
    !/^sha256:[a-f0-9]{64}$/.test(model.model_digest) ||
    !(model.runtime_version === null || text(model.runtime_version, 64)) ||
    !text(model.inference_placement, 32) ||
    !model.inference_placement.toUpperCase().includes("CPU") ||
    model.inference_placement.toUpperCase().includes("GPU") ||
    !count(model.latency_ms) ||
    model.latency_ms > 60_000
  ) {
    return null;
  }
  return {
    model_name: model.model_name,
    model_digest: model.model_digest,
    runtime_version: model.runtime_version as string | null,
    inference_placement: model.inference_placement,
    latency_ms: model.latency_ms,
  };
}

function sameSelection(
  value: unknown,
  expected: EngineerAskSelection,
): boolean {
  const actual = record(value);
  if (!actual) return false;
  const expectedKeys = Object.keys(expected).sort();
  if (Object.keys(actual).sort().join("\0") !== expectedKeys.join("\0"))
    return false;
  return expectedKeys.every(
    (key) => actual[key] === expected[key as keyof typeof expected],
  );
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown, maximum: number): value is string {
  return (
    typeof value === "string" && value.length > 0 && value.length <= maximum
  );
}

function count(value: unknown): value is number {
  return Number.isInteger(value) && typeof value === "number" && value >= 0;
}
