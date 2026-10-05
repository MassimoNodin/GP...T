import type {
  EngineerQueryReport,
  LapRecord,
  PairedRegionReport,
  TrackModelRecord,
} from "@/lib/api";

export type EngineerQueryRequestIdentity =
  | { intent: "attempt_summary"; targetAttemptKey: string }
  | {
      intent: "region_comparison";
      targetAttemptKey: string;
      referenceAttemptKey: string;
      comparisonPolicy: "time_trial" | "practice_qualifying";
      trackModelId: string;
      trackModelRevision: number;
      regionIdentifier: string;
    };

type AttemptIdentity = {
  attemptKey: string;
  runId: string;
  sessionUid: string;
  carIndex: number;
};

export function attemptIdentity(value: string): AttemptIdentity | null {
  const match = /^([a-f0-9]{64}):(\d{1,20}):(\d{1,2}):(\d{1,10})$/.exec(
    value,
  );
  if (!match) return null;
  const carIndex = Number(match[3]);
  const ordinal = Number(match[4]);
  if (
    !Number.isSafeInteger(carIndex) ||
    carIndex > 23 ||
    !Number.isSafeInteger(ordinal) ||
    ordinal <= 0
  ) {
    return null;
  }
  return {
    attemptKey: value,
    runId: match[1],
    sessionUid: match[2],
    carIndex,
  };
}

export function parseTrackModelKey(value: string | undefined) {
  if (!value || value.length > 512) return null;
  const separator = value.lastIndexOf("@");
  if (separator <= 0) return null;
  const modelId = value.slice(0, separator);
  const revisionText = value.slice(separator + 1);
  if (!modelId || modelId.length > 256 || !/^[1-9]\d{0,15}$/.test(revisionText)) {
    return null;
  }
  const revision = Number(revisionText);
  return Number.isSafeInteger(revision) ? { modelId, revision } : null;
}

export function engineerQueryReportMatchesRequest(
  report: EngineerQueryReport,
  request: EngineerQueryRequestIdentity,
): boolean {
  if (
    report.schema_version !== 1 ||
    report.analysis_version !== "engineer-query-v1" ||
    report.artifact_kind !== "engineer_query" ||
    report.intent !== request.intent ||
    !["available", "partial", "unavailable"].includes(report.status) ||
    report.diagnostic_only !== true ||
    report.coaching_eligible !== false ||
    report.ranking_eligible !== false ||
    !safeReportContent(report)
  ) {
    return false;
  }

  const selected = record(report.selected);
  const provenance = record(report.provenance);
  if (!selected) return false;
  if (!provenance) return false;
  if (selected.target_attempt_key !== request.targetAttemptKey) return false;
  if (request.intent === "attempt_summary") {
    const identity = attemptIdentity(request.targetAttemptKey);
    if (!identity) return false;
    if (report.status === "unavailable") return report.facts.length === 0;
    return Boolean(
      provenance?.verification_scope === "metadata_only" &&
        provenance.run_id === identity.runId &&
        provenance.session_uid === identity.sessionUid,
    );
  }

  if (
    selected.reference_attempt_key !== request.referenceAttemptKey ||
    selected.comparison_policy !== request.comparisonPolicy ||
    selected.track_model_id !== request.trackModelId ||
    selected.track_model_revision !== request.trackModelRevision ||
    selected.region_identifier !== request.regionIdentifier
  ) {
    return false;
  }
  if (report.status === "unavailable") return report.facts.length === 0;

  const target = attemptIdentity(request.targetAttemptKey);
  const reference = attemptIdentity(request.referenceAttemptKey);
  const attempts = record(provenance?.attempts);
  const sourceTarget = record(attempts?.target);
  const sourceReference = record(attempts?.reference);
  const model = record(provenance?.model);
  return Boolean(
    target &&
      reference &&
      provenance?.verification_scope === "checksummed_trace_analysis" &&
      provenance.run_id === target.runId &&
      sourceTarget?.attempt_key === target.attemptKey &&
      sourceReference?.attempt_key === reference.attemptKey &&
      isSha256(sourceTarget?.trace_sha256) &&
      isSha256(sourceReference?.trace_sha256) &&
      positiveInteger(sourceTarget?.trace_schema_version) &&
      positiveInteger(sourceReference?.trace_schema_version) &&
      model?.model_id === request.trackModelId &&
      model.revision === request.trackModelRevision &&
      isSha256(model.model_content_sha256) &&
      (model.origin === "packaged" || model.origin === "reviewed" || model.origin === "local_draft"),
  );
}

export function pairedRegionReportMatchesSelection(
  report: PairedRegionReport,
  selection: {
    target: LapRecord;
    reference: LapRecord;
    comparisonPolicy: "time_trial" | "practice_qualifying";
    model: TrackModelRecord;
  },
): boolean {
  const target = report?.attempts?.target;
  const reference = report?.attempts?.reference;
  const model = report?.model;
  const regions = report?.regions;
  return Boolean(
    report?.schema_version === 1 &&
      report.artifact_kind === "paired_distance_region_observations" &&
      report.status === "available" &&
      report.comparison_policy === selection.comparisonPolicy &&
      report.diagnostic_only === true &&
      report.coaching_eligible === false &&
      report.ranking_eligible === false &&
      target?.attempt_key === selection.target.attempt_key &&
      target.run_id === selection.target.run_id &&
      target.session_uid === selection.target.session_uid &&
      target.car_index === selection.target.car_index &&
      target.trace_sha256 === selection.target.trace_sha256 &&
      target.trace_schema_version === selection.target.trace_schema_version &&
      reference?.attempt_key === selection.reference.attempt_key &&
      reference.run_id === selection.reference.run_id &&
      reference.session_uid === selection.reference.session_uid &&
      reference.car_index === selection.reference.car_index &&
      reference.trace_sha256 === selection.reference.trace_sha256 &&
      reference.trace_schema_version ===
        selection.reference.trace_schema_version &&
      model?.model_id === selection.model.model_id &&
      model.revision === selection.model.revision &&
      model.packet_format === selection.model.packet_format &&
      model.track_id === selection.model.track_id &&
      model.track_name === selection.model.track_name &&
      model.layout_id === selection.model.layout_id &&
      model.track_length_m === selection.model.track_length_m &&
      (selection.model.content_sha256 == null ||
        model.content_sha256 === selection.model.content_sha256) &&
      (selection.model.model_content_sha256 == null ||
        model.model_content_sha256 === selection.model.model_content_sha256) &&
      (selection.model.origin == null || model.origin === selection.model.origin) &&
      report.track?.packet_format === selection.model.packet_format &&
      report.track.track_id === selection.model.track_id &&
      report.track.track_name === selection.model.track_name &&
      report.track.track_length_m === selection.model.track_length_m &&
      Array.isArray(regions) &&
      regions.length <= 64 &&
      safeRegionRows(regions),
  );
}

function safeReportContent(report: EngineerQueryReport) {
  return (
    Array.isArray(report.reason_codes) &&
    report.reason_codes.length <= 16 &&
    report.reason_codes.every(
      (reason) => typeof reason === "string" && reason.length <= 96,
    ) &&
    Array.isArray(report.facts) &&
    report.facts.length <= 6 &&
    report.facts.every((value: unknown) => {
      const fact = record(value);
      return Boolean(
        fact &&
          typeof fact.kind === "string" &&
          fact.kind.length > 0 &&
          fact.kind.length <= 128 &&
          typeof fact.text === "string" &&
          fact.text.length > 0 &&
          fact.text.length <= 240 &&
          Array.isArray(fact.source_fields) &&
          fact.source_fields.length <= 32 &&
          fact.source_fields.every(
            (field) => typeof field === "string" && field.length <= 256,
          ),
      );
    }) &&
    Array.isArray(report.warnings) &&
    report.warnings.length <= 8 &&
    report.warnings.every((value: unknown) => {
      const warning = record(value);
      return Boolean(
        warning &&
          typeof warning.code === "string" &&
          warning.code.length > 0 &&
          warning.code.length <= 96 &&
          typeof warning.text === "string" &&
          warning.text.length > 0 &&
          warning.text.length <= 240 &&
          Array.isArray(warning.source_fields) &&
          warning.source_fields.length <= 32 &&
          warning.source_fields.every(
            (field) => typeof field === "string" && field.length <= 256,
          ) &&
          (warning.sides === undefined ||
            (Array.isArray(warning.sides) &&
              warning.sides.length <= 2 &&
              warning.sides.every(
                (side) => side === "target" || side === "reference",
              )))
      );
    }) &&
    nonNegativeInteger(report.omitted_fact_count) &&
    nonNegativeInteger(report.omitted_warning_count)
  );
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
}

function safeRegionRows(rows: unknown[]): boolean {
  const identifiers: string[] = [];
  for (const value of rows) {
    const region = record(value);
    const window = region?.analysis_window_m;
    if (
      !region ||
      typeof region.identifier !== "string" ||
      region.identifier.length === 0 ||
      region.identifier.length > 256 ||
      typeof region.label !== "string" ||
      region.label.length === 0 ||
      region.label.length > 256 ||
      !Array.isArray(window) ||
      window.length !== 2 ||
      typeof window[0] !== "number" ||
      typeof window[1] !== "number" ||
      !Number.isFinite(window[0]) ||
      !Number.isFinite(window[1]) ||
      window[0] >= window[1] ||
      region.diagnostic_only !== true ||
      region.coaching_eligible !== false ||
      region.ranking_eligible !== false
    ) {
      return false;
    }
    identifiers.push(region.identifier);
  }
  return new Set(identifiers).size === identifiers.length;
}

function positiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && typeof value === "number" && value > 0;
}

function nonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && typeof value === "number" && value >= 0;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
