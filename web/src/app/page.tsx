import {
  AttemptRegionReport,
  Comparison,
  CornerComparisonBrief,
  CornerAnalysis,
  CornerLossCandidates,
  CornerRegion,
  EngineerQueryReport,
  AttemptTrajectoryPreview,
  AttemptQualityReport,
  AttemptTraceChartReport,
  LapRecord,
  ObservationSetReport,
  ObservedTrajectoryComparisonPreview,
  PairedRegionReport,
  ProcessingRunSummary,
  RegionEvent,
  ReferenceSelection,
  SessionBestOverview,
  SessionRecord,
  TrackModelRecord,
  requestApi,
  requestApiPost,
} from "@/lib/api";
import AppHeader from "./AppHeader";
import AttemptQualityPanel from "./AttemptQualityPanel";
import PlayerParticipantContextPanel from "./PlayerParticipantContextPanel";
import PlayerCarSetupContextPanel from "./PlayerCarSetupContextPanel";
import SessionBestOverviewPanel from "./SessionBestOverviewPanel";
import DiagnosticEvidencePanels from "./DiagnosticEvidencePanels";
import AttemptTraceCharts from "./AttemptTraceCharts";
import DraftTrackModelPanel from "./DraftTrackModelPanel";
import SessionsRunEvidence from "./SessionsRunEvidence";
import TrajectoryComparisonPanel from "./TrajectoryComparisonPanel";
import LinkedComparisonCharts from "./LinkedComparisonCharts";
import DrivingPatternAssessmentPanel from "./DrivingPatternAssessmentPanel";
import ThrottlePatternAssessmentPanel from "./ThrottlePatternAssessmentPanel";
import ObservationSetPanel from "./ObservationSetPanel";
import PairedRegionPanel from "./PairedRegionPanel";
import EngineerQueryPanel from "./EngineerQueryPanel";
import {
  engineerQueryReportMatchesRequest,
  pairedRegionReportMatchesSelection,
  type EngineerQueryRequestIdentity,
} from "@/lib/engineer-query-match";
import RecordedEvidenceSpeech from "./RecordedEvidenceSpeech";
import { buildComparisonCharts } from "./comparison-charts";
import ComparisonWindowPanel from "./ComparisonWindowPanel";
import ComparisonConditionsPanel from "./ComparisonConditionsPanel";
import { comparisonReportMatchesAttempts } from "@/lib/comparison-report-match";
import { attemptTraceChartReportMatchesSelection } from "@/lib/attempt-trace-chart-match";
import { buildRecordedSpeechPlan } from "@/lib/recorded-speech-plan";
import {
  appScreenHref,
  isSelectionTransferBlocked,
  preservedAppStateQuery,
  type AppSearchParams,
} from "@/lib/navigation";
type SearchParams = AppSearchParams;

type Series = {
  label: string;
  color: string;
  values: Array<number | null>;
  mask?: boolean[];
};

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const rawParams = await searchParams;
  const preservedQuery = preservedAppStateQuery(rawParams);
  const selectionTransferBlocked =
    isSelectionTransferBlocked(preservedQuery);
  const legacyRunEvidenceRequested = [
    "run_id",
    "run_offset",
    "session_offset",
    "attempt_offset",
    "lifecycle_event_offset",
    "observation_session_uid",
    "observation_car_index",
    "observation_offset",
  ].some((key) => rawParams[key] !== undefined);
  const params = {
    session_key: firstParam(rawParams.session_key),
    target_attempt_key: firstParam(rawParams.target_attempt_key),
    reference_choice: firstParam(rawParams.reference_choice),
    comparison_policy: firstParam(rawParams.comparison_policy),
    window_start_m: firstParam(rawParams.window_start_m),
    window_end_m: firstParam(rawParams.window_end_m),
    observation_attempt_keys: allParams(rawParams.observation_attempt_key),
    track_model_key: firstParam(rawParams.track_model_key),
    position_probe_m: firstParam(rawParams.position_probe_m),
    run_id: firstParam(rawParams.run_id),
    engineer_intent: firstParam(rawParams.engineer_intent),
    engineer_region_identifier: firstParam(rawParams.engineer_region_identifier),
  };
  const selectedRunId = !selectionTransferBlocked && /^[a-f0-9]{64}$/.test(params.run_id ?? "")
    ? params.run_id!
    : null;
  const [sessionResponse, trackModelsResponse] = await Promise.all([
    selectionTransferBlocked
      ? Promise.resolve(null)
      : requestApi<SessionRecord[]>("/api/v1/sessions"),
    selectionTransferBlocked
      ? Promise.resolve(null)
      : requestApi<TrackModelRecord[]>("/api/v1/track-models"),
  ]);
  const trackModels = trackModelsResponse?.data ?? [];
  const selectedModel =
    trackModels.find((item) => modelKey(item) === params.track_model_key) ??
    null;
  const selectedComparisonModel =
    selectedModel?.origin === "packaged" || selectedModel?.origin === "reviewed"
      ? selectedModel
      : null;
  const staleModelChoice = Boolean(params.track_model_key) && !selectedModel;
  const allSessions = sessionResponse?.data ?? [];
  const importedSessions = allSessions.filter(
    (item) => item.run_status === "complete",
  );
  const latestRunByCapture = new Map<string, SessionRecord>();
  for (const item of importedSessions) {
    const key = `${item.capture_sha256}:${item.session_uid}`;
    const current = latestRunByCapture.get(key);
    const itemFinishedAt = item.finished_at_utc ?? item.started_at_utc;
    const currentFinishedAt =
      current?.finished_at_utc ?? current?.started_at_utc;
    if (
      !current ||
      itemFinishedAt > currentFinishedAt! ||
      (itemFinishedAt === currentFinishedAt && item.run_id > current.run_id)
    )
      latestRunByCapture.set(key, item);
  }
  const sessions = [...latestRunByCapture.values()].sort((a, b) => {
    const aFinishedAt = a.finished_at_utc ?? a.started_at_utc;
    const bFinishedAt = b.finished_at_utc ?? b.started_at_utc;
    return (
      aFinishedAt.localeCompare(bFinishedAt) || a.run_id.localeCompare(b.run_id)
    );
  });
  const requestedSession =
    selectionTransferBlocked || params.session_key === undefined
      ? null
      : importedSessions.find((item) => item.session_key === params.session_key) ?? null;
  const sessionUnavailable =
    !selectionTransferBlocked &&
    params.session_key !== undefined &&
    requestedSession === null;
  const session =
    selectionTransferBlocked
      ? null
      : params.session_key === undefined
      ? sessions.at(-1) ?? null
      : requestedSession;
  const selectableSessions =
    session && !sessions.some((item) => item.session_key === session.session_key)
      ? [...sessions, session]
      : sessions;
  const isTimeTrial = session?.context?.session_type === "time_trial";
  const sessionType = session?.context?.session_type;
  const isPracticeQualifying =
    typeof sessionType === "string" &&
    /^(practice_|qualifying_|sprint_shootout_)/.test(sessionType);
  const standaloneRegionModeSupported = isTimeTrial || isPracticeQualifying;
  const comparisonPolicy =
    params.comparison_policy === "practice_qualifying"
      ? "practice_qualifying"
      : "time_trial";
  const trackModelCatalogUnavailable =
    !trackModelsResponse || trackModelsResponse.status === "unavailable";
  const lapResponse = session
    ? await requestApi<LapRecord[]>(
        `/api/v1/laps?${new URLSearchParams({ run_id: session.run_id, session_uid: session.session_uid })}`,
      )
    : null;
  const laps = lapResponse?.data ?? [];
  const completed = laps.filter((lap) => lap.disposition === "completed");
  const requestedTarget =
    params.target_attempt_key === undefined
      ? null
      : laps.find((lap) => lap.attempt_key === params.target_attempt_key) ?? null;
  const targetAttemptUnavailable =
    params.target_attempt_key !== undefined && requestedTarget === null;
  const target =
    params.target_attempt_key === undefined
      ? laps.at(-1) ?? null
      : requestedTarget;
  const observationSetQuery = new URLSearchParams();
  for (const attemptKey of params.observation_attempt_keys) {
    observationSetQuery.append("attempt_key", attemptKey);
  }
  observationSetQuery.set("comparison_policy", comparisonPolicy);
  if (params.window_start_m?.trim()) {
    observationSetQuery.set("window_start_m", params.window_start_m);
  }
  if (params.window_end_m?.trim()) {
    observationSetQuery.set("window_end_m", params.window_end_m);
  }
  const observationSetRequest =
    !selectionTransferBlocked &&
    params.observation_attempt_keys.length >= 2 &&
    params.observation_attempt_keys.length <= 8 &&
    Boolean(params.window_start_m?.trim() && params.window_end_m?.trim())
      ? requestApi<ObservationSetReport>(
          `/api/v1/analysis/observation-set?${observationSetQuery.toString()}`,
        )
      : Promise.resolve(null);
  const defaultReference =
    completed.find((lap) => lap.attempt_key !== target?.attempt_key) ?? null;
  const requestedReferenceChoice = params.reference_choice;
  const referenceChoice =
    requestedReferenceChoice === "session_best" &&
    comparisonPolicy === "practice_qualifying"
      ? defaultReference?.attempt_key ?? ""
      : (requestedReferenceChoice ?? defaultReference?.attempt_key ?? "session_best");
  const autoReference =
    comparisonPolicy === "time_trial" && referenceChoice === "session_best";

  const manualReference = autoReference
    ? null
    : (completed.find((lap) => lap.attempt_key === referenceChoice) ?? null);
  const selectionRequest = target && comparisonPolicy === "time_trial"
    ? requestApi<ReferenceSelection>(
        `/api/v1/references/session-best?${new URLSearchParams({ target_attempt_key: target.attempt_key })}`,
      )
    : Promise.resolve(null);
  const sessionBestAnchorSelected = Boolean(
    target &&
      params.target_attempt_key &&
      target.attempt_key === params.target_attempt_key,
  );
  const sessionBestRequest = sessionBestAnchorSelected && target
    ? requestApi<SessionBestOverview>(
        `/api/v1/analysis/session-best?${new URLSearchParams({ anchor_attempt_key: target.attempt_key })}`,
      )
    : Promise.resolve(null);
  const attemptQualityRequest = target
    ? requestApi<AttemptQualityReport>(
        `/api/v1/attempts/${encodeURIComponent(target.attempt_key)}/quality`,
      )
    : Promise.resolve(null);
  const attemptTraceChartRequest = target
    ? requestApi<AttemptTraceChartReport>(
        `/api/v1/attempts/${encodeURIComponent(target.attempt_key)}/traces`,
      )
    : Promise.resolve(null);
  const trajectoryRequest = target
    ? requestApi<AttemptTrajectoryPreview>(
        `/api/v1/attempts/${encodeURIComponent(target.attempt_key)}/trajectory`,
      )
    : Promise.resolve(null);
  const regionRequest = target && selectedModel
    ? requestApi<AttemptRegionReport>(
        `/api/v1/attempts/${encodeURIComponent(target.attempt_key)}/regions?${new URLSearchParams({
          track_model_id: selectedModel.model_id,
          track_model_revision: String(selectedModel.revision),
        })}`,
      )
    : Promise.resolve(null);
  const manualComparisonRequest =
    target && manualReference
      ? requestApi<Comparison>(
          `/api/v1/compare/laps?${comparisonQuery(target.attempt_key, manualReference.attempt_key, selectedComparisonModel, comparisonPolicy, params.window_start_m, params.window_end_m)}`,
        )
      : Promise.resolve(null);
  const pairedRegionSelectionReady = Boolean(
    target &&
      manualReference &&
      params.reference_choice &&
      params.reference_choice !== "session_best" &&
      selectedModel,
  );
  const engineerIntent = params.engineer_intent;
  const pairedRegionRequest =
    pairedRegionSelectionReady &&
    target &&
    manualReference &&
    selectedModel &&
    engineerIntent !== "region_comparison"
    ? requestApi<PairedRegionReport>(
        `/api/v1/compare/regions?${new URLSearchParams({
          target_attempt_key: target.attempt_key,
          reference_attempt_key: manualReference.attempt_key,
          comparison_policy: comparisonPolicy,
          track_model_id: selectedModel.model_id,
          track_model_revision: String(selectedModel.revision),
        })}`,
      )
    : Promise.resolve(null);
  const engineerQueryBody =
    engineerIntent === "attempt_summary" &&
    params.target_attempt_key &&
    target?.attempt_key === params.target_attempt_key
      ? {
          intent: "attempt_summary",
          target_attempt_key: params.target_attempt_key,
        }
      : engineerIntent === "region_comparison" &&
          params.target_attempt_key &&
          target?.attempt_key === params.target_attempt_key &&
          manualReference &&
          params.reference_choice === manualReference.attempt_key &&
          selectedModel &&
          params.engineer_region_identifier
        ? {
            intent: "region_comparison",
            target_attempt_key: target.attempt_key,
            reference_attempt_key: manualReference.attempt_key,
            comparison_policy: comparisonPolicy,
            track_model_id: selectedModel.model_id,
            track_model_revision: selectedModel.revision,
            region_identifier: params.engineer_region_identifier,
          }
        : null;
  const engineerQueryRequest = engineerQueryBody
    ? requestApiPost<EngineerQueryReport>(
        "/api/v1/engineer/query",
        engineerQueryBody,
      )
    : Promise.resolve(null);
  const [selectionResponse, sessionBestResponse, manualComparisonResponse, qualityResponse, traceChartResponse, trajectoryResponse, regionResponse, observationSetResponse, pairedRegionResponse, engineerQueryResponse] = await Promise.all([
    selectionRequest,
    sessionBestRequest,
    manualComparisonRequest,
    attemptQualityRequest,
    attemptTraceChartRequest,
    trajectoryRequest,
    regionRequest,
    observationSetRequest,
    pairedRegionRequest,
    engineerQueryRequest,
  ]);
  const attemptQuality =
    qualityResponse?.status === "ok" ? qualityResponse.data : null;
  const traceChartCandidate =
    traceChartResponse?.status === "ok" ? traceChartResponse.data : null;
  const traceChartIdentityMismatch = Boolean(
    traceChartCandidate && target &&
      !attemptTraceChartReportMatchesSelection(traceChartCandidate, target),
  );
  const traceChartReport = traceChartCandidate && target && !traceChartIdentityMismatch
    ? traceChartCandidate
    : null;
  const traceChartUnavailableReason = traceChartIdentityMismatch
    ? "trace_chart_attempt_provenance_or_shape_mismatch"
    : traceChartResponse?.status === "unavailable"
      ? traceChartResponse.reason
      : null;
  const trajectoryPreview =
    trajectoryResponse?.status === "ok" ? trajectoryResponse.data : null;
  const selection = selectionResponse?.data ?? null;
  const referenceKey = autoReference
    ? String(selection?.selected_reference?.attempt_key ?? "") || null
    : (manualReference?.attempt_key ?? null);
  const comparisonReference =
    completed.find((lap) => lap.attempt_key === referenceKey) ?? null;
  const comparisonResponse =
    autoReference && target && referenceKey
      ? await requestApi<Comparison>(
          `/api/v1/compare/laps?${comparisonQuery(target.attempt_key, referenceKey, selectedComparisonModel, "time_trial", params.window_start_m, params.window_end_m)}`,
        )
      : manualComparisonResponse;
  const comparisonCandidate =
    comparisonResponse?.status === "ok" ? comparisonResponse.data : null;
  const comparison =
    comparisonCandidate &&
    target &&
    comparisonReference &&
    comparisonCandidate.comparison_policy === comparisonPolicy &&
    comparisonReportMatchesAttempts(
      comparisonCandidate,
      target,
      comparisonReference,
    )
      ? comparisonCandidate
      : null;
  const observationSet =
    observationSetResponse?.status === "ok" ? observationSetResponse.data : null;
  const pairedRegionCandidate =
    pairedRegionResponse?.status === "ok" ? pairedRegionResponse.data : null;
  const pairedRegionReport =
    pairedRegionCandidate &&
    target &&
    manualReference &&
    selectedModel &&
    pairedRegionReportMatchesSelection(pairedRegionCandidate, {
      target,
      reference: manualReference,
      comparisonPolicy,
      model: selectedModel,
    })
      ? pairedRegionCandidate
      : null;
  const engineerQueryCandidate =
    engineerQueryResponse?.status === "ok" ? engineerQueryResponse.data : null;
  const engineerQueryIdentity: EngineerQueryRequestIdentity | null =
    engineerQueryBody?.intent === "attempt_summary"
      ? {
          intent: "attempt_summary",
          targetAttemptKey: engineerQueryBody.target_attempt_key,
        }
      : engineerQueryBody?.intent === "region_comparison" &&
          typeof engineerQueryBody.reference_attempt_key === "string" &&
          (engineerQueryBody.comparison_policy === "time_trial" ||
            engineerQueryBody.comparison_policy === "practice_qualifying") &&
          typeof engineerQueryBody.track_model_id === "string" &&
          typeof engineerQueryBody.track_model_revision === "number" &&
          typeof engineerQueryBody.region_identifier === "string"
        ? {
            intent: "region_comparison",
            targetAttemptKey: engineerQueryBody.target_attempt_key,
            referenceAttemptKey: engineerQueryBody.reference_attempt_key,
            comparisonPolicy: engineerQueryBody.comparison_policy,
            trackModelId: engineerQueryBody.track_model_id,
            trackModelRevision: engineerQueryBody.track_model_revision,
            regionIdentifier: engineerQueryBody.region_identifier,
          }
        : null;
  const engineerQueryReport =
    engineerQueryCandidate &&
    engineerQueryIdentity &&
    engineerQueryReportMatchesRequest(
      engineerQueryCandidate,
      engineerQueryIdentity,
    )
      ? engineerQueryCandidate
      : null;
  const engineerQueryRequestState =
    engineerIntent !== "attempt_summary" && engineerIntent !== "region_comparison"
      ? "not_requested"
      : !engineerQueryBody
        ? "not_ready"
        : engineerQueryResponse?.status === "ok" && engineerQueryReport
          ? "ok"
          : "failed";
  const engineerQueryRequestReason =
    engineerQueryRequestState !== "not_ready"
      ? engineerQueryResponse?.reason ??
        (engineerQueryCandidate && !engineerQueryReport
          ? "The local API response did not match the exact request and source provenance. No substitute evidence was shown."
          : null)
      : engineerIntent === "attempt_summary"
        ? params.target_attempt_key
          ? "The requested attempt is not available in the selected recording. Choose an attempt from this session and retry."
          : "Select an attempt from this session before requesting a summary."
        : "Choose an available target attempt, explicit reference, registered model, and region before requesting a comparison.";
  const trajectoryComparisonQuery = new URLSearchParams();
  if (target) trajectoryComparisonQuery.set("target_attempt_key", target.attempt_key);
  if (referenceKey) trajectoryComparisonQuery.set("reference_attempt_key", referenceKey);
  trajectoryComparisonQuery.set(
    "comparison_policy",
    comparison?.comparison_policy ?? comparisonPolicy,
  );
  if (params.position_probe_m?.trim()) {
    trajectoryComparisonQuery.set("position_probe_m", params.position_probe_m);
  }
  const trajectoryComparisonResponse =
    comparison && target && referenceKey
      ? await requestApi<ObservedTrajectoryComparisonPreview>(
          `/api/v1/compare/trajectories?${trajectoryComparisonQuery}`,
        )
      : null;
  const trajectoryComparisonFormParams: Record<string, string | string[]> = {};
  for (const [name, value] of Object.entries(params)) {
    if (
      name !== "position_probe_m" &&
      name !== "observation_attempt_keys" &&
      typeof value === "string" &&
      value
    ) {
      trajectoryComparisonFormParams[name] = value;
    }
  }
  if (params.observation_attempt_keys.length) {
    trajectoryComparisonFormParams.observation_attempt_key =
      params.observation_attempt_keys;
  }
  if (session) trajectoryComparisonFormParams.session_key = session.session_key;
  if (target) trajectoryComparisonFormParams.target_attempt_key = target.attempt_key;
  if (referenceChoice) trajectoryComparisonFormParams.reference_choice = referenceChoice;
  if (comparison?.comparison_policy ?? comparisonPolicy) {
    trajectoryComparisonFormParams.comparison_policy =
      comparison?.comparison_policy ?? comparisonPolicy;
  }
  if (params.window_start_m) trajectoryComparisonFormParams.window_start_m = params.window_start_m;
  if (params.window_end_m) trajectoryComparisonFormParams.window_end_m = params.window_end_m;
  if (selectedModel) trajectoryComparisonFormParams.track_model_key = modelKey(selectedModel);
  const reference = comparisonReference;
  const lifecycleReasonsFor = (
    ...sources: Array<{
      exclusion_reasons?: string[];
      lifecycle_exclusions?: string[];
      superseded?: boolean | null;
      lifecycle_assessed?: boolean;
    } | null | undefined>
  ) => {
    const reasons: string[] = [];
    for (const source of sources) {
      reasons.push(
        ...(source?.exclusion_reasons ?? []).filter((value) =>
          value === "superseded_by_flashback" || value === "lifecycle_evidence_unassessed",
        ),
        ...(source?.lifecycle_exclusions ?? []),
      );
      if (source?.superseded === true) reasons.push("superseded_by_flashback");
      if (source?.lifecycle_assessed === false || source?.superseded === null) {
        reasons.push("lifecycle_evidence_unassessed");
      }
    }
    return Array.from(new Set(reasons));
  };
  const lifecycleComparisonStatuses = [
    {
      side: "target",
      reasons: lifecycleReasonsFor(target, comparison?.target),
    },
    {
      side: "reference",
      reasons: lifecycleReasonsFor(reference, comparison?.reference),
    },
  ].filter((item) => item.reasons.length > 0);
  const staleManualReference =
    Boolean(params.reference_choice) &&
    params.reference_choice !== "session_best" &&
    !manualReference;
  const apiUnavailable =
    !sessionResponse || sessionResponse.status === "unavailable";
  const lapApiUnavailable =
    Boolean(session) && (!lapResponse || lapResponse.status === "unavailable");
  const selectionUnavailable =
    !selectionResponse || selectionResponse.status === "unavailable";
  const selectionState = selection?.status;
  const policyBadge = comparisonPolicy === "practice_qualifying"
    ? "MANUAL ONLY"
    : selectionState === "selected"
      ? "READY"
      : selectionState === "no_eligible_reference"
        ? "ABSTAINED"
        : selectionState
          ? label(selectionState)
          : "UNAVAILABLE";
  const policyDescription = comparisonPolicy === "practice_qualifying"
    ? "Choose an explicit same-session reference. Automatic reference selection is only available for Time Trial."
    : selection?.status === "selected" && selection.selected_reference
      ? `Attempt ${selection.selected_reference.attempt_number} · ${lapTime(Number(selection.selected_reference.lap_time_ms))} · same run, session and driver`
      : selection?.reasons.length
        ? selection.reasons.map(reason).join(" · ")
        : selection?.status === "no_eligible_reference"
          ? "No prior lap meets the session-best policy."
          : selection?.status === "target_not_completed"
            ? "Automatic reference selection requires a completed target lap."
            : selection?.status === "target_unavailable"
              ? "The selected target attempt is unavailable in the local archive."
              : selection?.status === "unsupported_policy"
                ? "Automatic session-best selection is not supported for this mode."
                : !target
                  ? "Select a recorded attempt to inspect reference eligibility."
                  : (selectionResponse?.reason ??
                    (selectionUnavailable
                      ? "Could not load reference policy evidence from the local API."
                      : "The API returned no reference policy evidence."));
  const noComparison = !target
    ? {
        eyebrow: "NO TARGET SELECTED",
        title: "Choose an attempt to inspect.",
        body: "Select a recorded lap or partial attempt from the inventory.",
      }
    : staleManualReference
      ? {
          eyebrow: "REFERENCE UNAVAILABLE",
          title: "The selected reference is not in this recording.",
          body:
            comparisonPolicy === "practice_qualifying"
              ? "Choose an available completed attempt from the same session."
              : "Choose an available completed lap or use automatic session best.",
        }
      : autoReference && !selectionResponse
        ? {
            eyebrow: "REFERENCE REQUEST FAILED",
            title: "Reference evidence could not be loaded.",
            body: "The local API did not return a response. Check that it is running, then reload.",
          }
        : autoReference && selectionResponse?.status === "unavailable"
          ? {
              eyebrow: "REFERENCE API UNAVAILABLE",
              title: "Automatic reference evidence is unavailable.",
              body:
                selectionResponse.reason ??
                "The local API could not provide reference evidence.",
            }
          : autoReference && !selection
            ? {
                eyebrow: "REFERENCE DATA MISSING",
                title: "The API returned no reference selection.",
                body: "Reload the recording to request current policy evidence.",
              }
            : autoReference && selection?.status === "no_eligible_reference"
              ? {
                  eyebrow: "NO ELIGIBLE REFERENCE",
                  title: "The automatic policy abstained.",
                  body:
                    selection.reasons.map(reason).join(" · ") ||
                    "No prior lap meets the current policy.",
                }
              : autoReference && selection?.status === "target_not_completed"
                ? {
                    eyebrow: "TARGET INCOMPLETE",
                    title: "A partial attempt cannot be compared yet.",
                    body:
                      selection.reasons.map(reason).join(" · ") ||
                      "Comparison requires a completed lap.",
                  }
                : autoReference && selection?.status === "unsupported_policy"
                  ? {
                      eyebrow: "POLICY UNSUPPORTED",
                      title:
                        "Automatic reference selection is unavailable for this mode.",
                      body:
                        selection.reasons.map(reason).join(" · ") ||
                        "This mode has no validated reference policy.",
                    }
                  : autoReference && selection?.status === "target_unavailable"
                    ? {
                        eyebrow: "TARGET UNAVAILABLE",
                        title:
                          "The selected attempt is not available for analysis.",
                        body:
                          selection.reasons.map(reason).join(" · ") ||
                          "Reload the recording to refresh its attempts.",
                      }
                    : !autoReference && !manualReference
                      ? {
                          eyebrow: "NO COMPLETED REFERENCE",
                          title: "Choose another completed attempt.",
                          body: "A reference lap is required for distance comparison.",
                        }
                      : {
                          eyebrow: "COMPARISON REQUEST FAILED",
                          title: "Comparison evidence could not be loaded.",
                          body: "The local API did not return a comparison. Check that it is running, then reload.",
                        };
  const urlFor = (values: Record<string, string>) => {
    const query = new URLSearchParams(values);
    if (selectedModel) query.set("track_model_key", modelKey(selectedModel));
    if (comparisonPolicy !== "time_trial")
      query.set("comparison_policy", comparisonPolicy);
    return `/?${query.toString()}`;
  };
  const engineerSourceHref = (attemptKey: string | null) => {
    if (!attemptKey || !session) return null;
    const query = new URLSearchParams({
      session_key: session.session_key,
      target_attempt_key: attemptKey,
    });
    return `/?${query.toString()}`;
  };
  const engineerComparisonHref =
    params.target_attempt_key &&
    target?.attempt_key === params.target_attempt_key &&
    manualReference &&
    params.reference_choice === manualReference.attempt_key &&
    selectedModel
      ? (() => {
          const query = new URLSearchParams({
            session_key: session?.session_key ?? "",
            target_attempt_key: target.attempt_key,
            reference_choice: manualReference.attempt_key,
            track_model_key: modelKey(selectedModel),
          });
          if (comparisonPolicy !== "time_trial")
            query.set("comparison_policy", comparisonPolicy);
          return `/?${query.toString()}`;
        })()
      : null;
  const engineerRegions = (pairedRegionReport?.regions ?? []).map((region) => ({
    identifier: region.identifier,
    label: region.label,
  }));
  const engineerRegionLinks = Object.fromEntries(
    params.target_attempt_key &&
      target?.attempt_key === params.target_attempt_key &&
      manualReference &&
      params.reference_choice === manualReference.attempt_key &&
      selectedModel &&
      session &&
      pairedRegionReport
      ? engineerRegions.flatMap((region) => {
          const href = appScreenHref("engineer", preservedQuery, {
            session_key: session.session_key,
            target_attempt_key: target.attempt_key,
            reference_choice: manualReference.attempt_key,
            comparison_policy: comparisonPolicy,
            track_model_key: modelKey(selectedModel),
            engineer_intent: "region_comparison",
            engineer_region_identifier: region.identifier,
          });
          return href ? [[region.identifier, href] as const] : [];
        })
      : [],
  );

  return (
    <div className="app-shell">
      <AppHeader active="dashboard" preservedQuery={preservedQuery} />
      <main className="page-content" id="main-content" tabIndex={-1}>
        <section className="intro-row">
          <div>
            <div className="eyebrow">PERSONAL AI RACE ENGINEER / SESSION REVIEW</div>
            <h1>
              Know the lap.
              <br />
              <span>Find the time.</span>
            </h1>
            <p className="intro-copy">
              GP...T is a personal nod to the GP race-engineer call on Max
              Verstappen’s radio, with T completing the GPT wordplay. Today it
              compares recorded laps and shows the evidence behind each finding.
            </p>
          </div>
          <div className="lap-stamp">
            <span className="stamp-ring">
              F1
              <br />
              25
            </span>
            <span>
              DATA-LED
              <br />
              BY DESIGN
            </span>
          </div>
        </section>

        {legacyRunEvidenceRequested && !selectionTransferBlocked ? (
          <SessionsRunEvidence
            searchParams={Promise.resolve(rawParams)}
            screen="dashboard"
          />
        ) : null}

        {selectionTransferBlocked ? (
          <section className="connection-state panel" role="status">
            <span className="state-icon">!</span>
            <div>
              <h2>Selection transfer is paused</h2>
              <p>
                This selection was too large to carry safely between screens.
                GP...T has not selected a recording or attempt automatically.
                Return to the previous screen or choose a recording explicitly.
              </p>
              <a className="recording-evidence-link" href="/recordings">
                Open recordings
              </a>
            </div>
          </section>
        ) : apiUnavailable ? (
          <section className="connection-state panel">
            <span className="state-icon">!</span>
            <div>
              <h2>Analysis API is offline</h2>
              <p>Start the local API, then reload this page:</p>
              <code>
                uv run --extra app f1-engineer api --database data/dev.sqlite3
              </code>
            </div>
          </section>
        ) : sessionUnavailable ? (
          <section className="connection-state panel">
            <span className="state-icon">!</span>
            <div>
              <h2>Session unavailable</h2>
              <p>
                The requested session is missing or its processing run is not
                complete. Choose a completed session from the archive.
              </p>
            </div>
          </section>
        ) : sessions.length === 0 ? (
          <section className="empty-state panel">
            <div className="eyebrow">NO COMPLETED RECORDINGS</div>
            <h2>Record and import a session to begin.</h2>
            <p>
              GP...T keeps recording local and builds a replayable lap archive.
            </p>
          </section>
        ) : lapApiUnavailable ? (
          <section className="connection-state panel">
            <span className="state-icon">!</span>
            <div>
              <h2>Lap inventory is unavailable</h2>
              <p>
                The local API could not load this recording's attempts. Check
                the API, then reload.
              </p>
            </div>
          </section>
        ) : (
          <>
            {targetAttemptUnavailable ? (
              <section className="connection-state panel">
                <span className="state-icon">!</span>
                <div>
                  <h2>Attempt unavailable</h2>
                  <p>
                    The requested attempt is missing from this completed
                    session. Choose an available attempt from its inventory.
                  </p>
                </div>
              </section>
            ) : null}
            <section className="session-strip panel">
              <div className="session-main">
                <span className="strip-label">SELECTED RECORDING</span>
                <div className="session-title-row">
                  <h2>{session?.context?.track_name ?? "Unknown circuit"}</h2>
                  <span className="mode-badge">
                    {label(session?.context?.session_type)}
                  </span>
                </div>
                <div className="session-meta">
                  <span>
                    {session?.context?.weather_name ?? "Conditions unknown"}
                  </span>
                  <span className="meta-dot">·</span>
                  <span>{session?.lap_attempts ?? 0} attempts</span>
                  <span className="meta-dot">·</span>
                  <span>Run {session?.run_id.slice(0, 8)}</span>
                </div>
              </div>
              <div className="session-stats">
                <div>
                  <strong>{String(laps.length).padStart(2, "0")}</strong>
                  <span>RECORDED</span>
                </div>
                <div>
                  <strong>{String(completed.length).padStart(2, "0")}</strong>
                  <span>COMPLETED</span>
                </div>
                <div className="stat-mode">
                  <strong>{label(session?.context?.game_mode)}</strong>
                  <span>GAME MODE</span>
                </div>
              </div>
            </section>

            <div className="workspace-grid">
              <aside className="sidebar">
                <section className="panel sidebar-panel">
                  <div className="section-heading">
                    <span className="eyebrow">RECORDINGS</span>
                    <span className="count-pill">{sessions.length}</span>
                  </div>
                  <div className="recording-list">
                    {[...sessions].reverse().map((item, index) => (
                      <div className="recording-entry" key={item.session_key}>
                        <a
                          className={`recording-item ${item.session_key === session?.session_key ? "selected" : ""}`}
                          href={urlFor({ session_key: item.session_key })}
                        >
                          <span className="recording-index">
                            {String(index + 1).padStart(2, "0")}
                          </span>
                          <span className="recording-copy">
                            <strong>
                              {item.context?.track_name ?? "Unknown circuit"}
                            </strong>
                            <small>
                              {label(item.context?.session_type)} ·{" "}
                              {item.run_id.slice(0, 8)}
                            </small>
                          </span>
                          <span className="recording-chevron">↗</span>
                        </a>
                        <a
                          className="recording-evidence-link"
                          href={appScreenHref("sessions", preservedQuery, {
                            run_id: item.run_id,
                            session_key: item.session_key,
                          }) ?? undefined}
                          aria-label={`Open run evidence for ${item.context?.track_name ?? "unknown circuit"}`}
                        >
                          Evidence
                        </a>
                      </div>
                    ))}
                  </div>
                  <div className="archive-note">
                    <i /> All recordings are stored locally.
                  </div>
                </section>
                <section className="panel sidebar-panel">
                  <div className="section-heading">
                    <span className="eyebrow">LAP INVENTORY</span>
                    <span className="count-pill">{laps.length}</span>
                  </div>
                  <div className="attempt-list">
                    {laps.map((lap) => (
                      <a
                        className={`attempt-item ${lap.attempt_key === target?.attempt_key ? "active" : ""}`}
                        href={urlFor({
                          session_key: session!.session_key,
                          target_attempt_key: lap.attempt_key,
                        })}
                        key={lap.attempt_key}
                      >
                        <span className="attempt-number">
                          {String(lap.attempt_number).padStart(2, "0")}
                        </span>
                        <span className="attempt-copy">
                          <strong>Attempt {lap.attempt_number}</strong>
                          <small>
                            {label(lap.disposition)} ·{" "}
                            {lapTime(lap.lap_time_ms)}
                          </small>
                        </span>
                        <span
                          className={`validity ${lap.game_valid === true ? "valid" : lap.game_valid === false ? "invalid" : "partial"}`}
                        >
                          {lap.game_valid === true
                            ? "GAME VALID"
                            : lap.game_valid === false
                              ? "GAME INVALID"
                              : "UNKNOWN"}
                        </span>
                      </a>
                    ))}
                  </div>
                </section>
              </aside>

              <section className="analysis-column">
                <SessionBestOverviewPanel
                  report={sessionBestResponse?.data ?? null}
                  anchorSelected={sessionBestAnchorSelected}
                  unavailableReason={
                    sessionBestResponse?.status === "unavailable"
                      ? sessionBestResponse.reason
                      : null
                  }
                />
                <DraftTrackModelPanel
                  key={
                    target
                      ? `${target.run_id}:${target.session_uid}:${target.attempt_key}:${target.trace_sha256}:${target.trace_schema_version}`
                      : "no-selected-attempt"
                  }
                  attempt={target}
                  explicitlySelected={Boolean(
                    target &&
                      params.target_attempt_key &&
                      target.attempt_key === params.target_attempt_key,
                  )}
                />
                {target ? (
                  <AttemptQualityPanel
                    report={attemptQuality}
                    unavailableReason={
                      qualityResponse?.status === "unavailable"
                        ? qualityResponse.reason
                        : null
                    }
                  />
                ) : null}
                {target && !comparison ? (
                  <PlayerParticipantContextPanel
                    target={target.player_participant_context}
                  />
                ) : null}
                {target && !comparison ? (
                  <PlayerCarSetupContextPanel
                    target={target.player_car_setup_context}
                  />
                ) : null}
                {target ? (
                  <AttemptTraceCharts
                    report={traceChartReport}
                    unavailableReason={traceChartUnavailableReason}
                  />
                ) : null}
                {target ? (
                  <DiagnosticEvidencePanels
                    key={`${target.attempt_key}:${selectedModel ? modelKey(selectedModel) : "no-model"}`}
                    trajectoryReport={trajectoryPreview}
                    trajectoryUnavailableReason={
                      trajectoryResponse?.status === "unavailable"
                        ? trajectoryResponse.reason
                        : null
                    }
                    regionReport={
                      regionResponse?.status === "ok"
                        ? regionResponse.data
                        : null
                    }
                    regionUnavailableReason={
                      regionResponse?.status === "unavailable"
                        ? regionResponse.reason
                        : regionResponse
                          ? null
                          : "region_analysis_api_unavailable"
                    }
                    showRegionPanel={selectedModel !== null}
                  />
                ) : null}
                <section className="panel compare-panel">
                  <div className="compare-header">
                    <div>
                      <div className="eyebrow">DISTANCE COMPARISON</div>
                      <h2>Choose your reference</h2>
                    </div>
                    <span className="protocol-tag">1 M RESAMPLE GRID</span>
                  </div>
                  <form className="compare-form" method="get">
                    <label>
                      <span>SESSION</span>
                      <select
                        name="session_key"
                        defaultValue={session?.session_key}
                      >
                        {selectableSessions
                          .slice()
                          .reverse()
                          .map((item) => (
                            <option
                              value={item.session_key}
                              key={item.session_key}
                            >
                              {item.context?.track_name ?? "Unknown"} ·{" "}
                              {label(item.context?.session_type)} ·{" "}
                              {item.run_id.slice(0, 8)}
                            </option>
                          ))}
                      </select>
                    </label>
                    <label>
                      <span>TARGET LAP</span>
                      <select
                        name="target_attempt_key"
                        defaultValue={target?.attempt_key}
                      >
                        {laps.map((lap) => (
                          <option value={lap.attempt_key} key={lap.attempt_key}>
                            Attempt {lap.attempt_number} ·{" "}
                            {label(lap.disposition)} ·{" "}
                            {lapTime(lap.lap_time_ms)}
                            {lap.game_valid === false
                              ? " · game invalid"
                              : lap.game_valid === true
                                ? " · game valid"
                                : " · validity unknown"}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      <span>COMPARISON POLICY</span>
                      <select
                        name="comparison_policy"
                        defaultValue={comparisonPolicy}
                      >
                        <option value="time_trial">
                          Time Trial compatible
                        </option>
                        <option value="practice_qualifying">
                          Practice / qualifying diagnostic
                        </option>
                      </select>
                    </label>
                    <label>
                      <span>REFERENCE</span>
                      <select
                        name="reference_choice"
                        defaultValue={referenceChoice}
                      >
                        {comparisonPolicy === "time_trial" ? (
                          <option value="session_best">
                            Automatic session best
                          </option>
                        ) : null}
                        {completed
                          .filter(
                            (lap) => lap.attempt_key !== target?.attempt_key,
                          )
                          .map((lap) => (
                            <option
                              value={lap.attempt_key}
                              key={lap.attempt_key}
                            >
                              Attempt {lap.attempt_number} ·{" "}
                              {lapTime(lap.lap_time_ms)}
                              {lap.game_valid === false
                                ? " · diagnostic only"
                                : ""}
                            </option>
                          ))}
                      </select>
                    </label>
                    <label>
                      <span>DIAGNOSTIC REGION MODEL</span>
                      <select
                        name="track_model_key"
                        defaultValue={
                          selectedModel ? modelKey(selectedModel) : ""
                        }
                        disabled={trackModelCatalogUnavailable}
                      >
                        <option value="">No region analysis</option>
                        {trackModels.map((model) => (
                          <option value={modelKey(model)} key={modelKey(model)}>
                            {model.track_name} ·{" "}
                            {model.origin === "local_draft"
                              ? "local draft · diagnostic"
                              : model.origin === "reviewed"
                                ? "reviewed distance regions"
                              : model.validation_status}{" "}
                            · rev{" "}
                            {model.revision} · {model.region_count ?? 0} regions
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      <span>INTERVAL START · M</span>
                      <input
                        name="window_start_m"
                        type="number"
                        min="0"
                        step="0.1"
                        placeholder="Optional"
                        defaultValue={params.window_start_m ?? ""}
                      />
                    </label>
                    <label>
                      <span>INTERVAL END · M</span>
                      <input
                        name="window_end_m"
                        type="number"
                        min="0"
                        step="0.1"
                        placeholder="Optional"
                        defaultValue={params.window_end_m ?? ""}
                      />
                    </label>
                    <button className="compare-button" type="submit">
                      Load analysis <span>↗</span>
                    </button>
                  </form>
                  {trackModelCatalogUnavailable ? (
                    <p className="model-note model-warning">
                      Track model metadata could not be loaded from the local
                      API.
                    </p>
                  ) : staleModelChoice ? (
                    <p className="model-note model-warning">
                      The selected model revision is no longer available. The
                      regular lap comparison remains available.
                    </p>
                  ) : !standaloneRegionModeSupported ? (
                    <p className="model-note">
                      Standalone region observations require a stable known
                      Time Trial or Practice/Qualifying context. Race and
                      unknown modes return an explicit unavailable result.
                    </p>
                  ) : selectedModel ? (
                    <p className="model-note">
                      {selectedModel.origin === "local_draft"
                        ? "Local draft selected for diagnostic inspection on this attempt and in the explicitly selected pair. Results do not authorize ranking or coaching."
                        : selectedModel.origin === "reviewed"
                          ? "Reviewed distance regions may support recorded measurements and ranking when every independent reference, capture, lifecycle, and telemetry gate passes. Review does not verify physical geometry or authorize coaching."
                          : "Explicit revision selected. Draft windows remain diagnostic and do not represent validated circuit corners."}
                    </p>
                  ) : (
                    <p className="model-note">
                      Select a packaged, reviewed, or configured local draft revision to
                      inspect this attempt's distance regions.
                    </p>
                  )}
                  {comparisonPolicy === "practice_qualifying" ? (
                    <p className="model-note">
                      Manual practice and qualifying comparisons are diagnostic
                      only. Fuel, tyres, traffic, and cooldown intent are not
                      controlled; Race comparisons remain unsupported.
                    </p>
                  ) : null}
                  {target?.disposition !== "completed" && target ? (
                    <div className="diagnostic-banner">
                      <b>i</b>
                      <span>
                        {label(target.disposition)} attempt · comparison
                        requires a completed lap. The selected attempt is
                        preserved.
                      </span>
                    </div>
                  ) : comparisonPolicy === "practice_qualifying" ? (
                    <div className="diagnostic-banner">
                      <b>i</b>
                      <span>
                        Diagnostic comparison only · attempts must come from
                        the same run, session, and player, with a clean start
                        and no pit encounter.
                      </span>
                    </div>
                  ) : target?.game_valid === false ||
                    reference?.game_valid === false ? (
                    <div className="diagnostic-banner">
                      <b>i</b>
                      <span>
                        Diagnostic comparison · one or both laps are
                        game-invalid. This describes recorded telemetry and is
                        not coaching.
                      </span>
                    </div>
                  ) : null}
                  {lifecycleComparisonStatuses.length ? (
                    <div className="diagnostic-banner">
                      <b>i</b>
                      <span>
                        Diagnostic comparison only · lifecycle evidence excludes this pair from automatic reference selection: {lifecycleComparisonStatuses.map((item) => `${item.side}: ${item.reasons.map(reason).join(" · ")}`).join("; ")}.
                      </span>
                    </div>
                  ) : null}
                </section>

                <ObservationSetPanel
                  laps={laps}
                  report={observationSet}
                  unavailableReason={
                    observationSetResponse?.status === "unavailable"
                      ? observationSetResponse.reason
                      : null
                  }
                  selectedAttemptKeys={params.observation_attempt_keys}
                  sessionKey={session?.session_key ?? null}
                  targetAttemptKey={target?.attempt_key ?? null}
                  referenceChoice={referenceChoice || null}
                  comparisonPolicy={comparisonPolicy}
                  windowStartM={params.window_start_m ?? null}
                  windowEndM={params.window_end_m ?? null}
                  trackModelKey={selectedModel ? modelKey(selectedModel) : null}
                  positionProbeM={params.position_probe_m ?? null}
                />

                <section className="panel reference-panel">
                  <div className="reference-icon">PB</div>
                  <div className="reference-copy">
                    <div className="eyebrow">
                      {comparisonPolicy === "practice_qualifying"
                        ? "REFERENCE POLICY"
                        : "AUTOMATIC REFERENCE POLICY"}
                    </div>
                    <h3>
                      {comparisonPolicy === "practice_qualifying"
                        ? "Manual reference required"
                        : selection?.status === "selected"
                        ? "Session best identified"
                        : selection
                          ? label(selection.status)
                          : target
                            ? "Reference policy unavailable"
                            : "No target selected"}
                    </h3>
                    <p>{policyDescription}</p>
                  </div>
                  <span
                    className={`policy-state ${comparisonPolicy === "practice_qualifying" ? "policy-manual" : selection?.status === "selected" ? "policy-ready" : selection?.status === "no_eligible_reference" ? "policy-abstain" : "policy-unavailable"}`}
                  >
                    {policyBadge}
                  </span>
                </section>
                {selection?.candidates.length ? (
                  <details className="candidate-details panel">
                    <summary>
                      Reference candidate evidence{" "}
                      <span>{selection.candidates.length} attempts</span>
                    </summary>
                    <div className="candidate-list">
                      {selection.candidates.map((candidate) => (
                        <div
                          className="candidate-row"
                          key={candidate.attempt_key}
                        >
                          <span>Attempt {candidate.attempt_number}</span>
                          <span>
                            {candidate.eligible
                              ? "ELIGIBLE"
                              : candidate.exclusion_reasons
                                  .map(reason)
                                  .join(" · ")}
                          </span>
                        </div>
                      ))}
                    </div>
                  </details>
                ) : null}

                {comparisonResponse?.status === "ok" && !comparison ? (
                  <section className="unavailable-panel panel" role="alert">
                    <span className="state-icon">!</span>
                    <div>
                      <div className="eyebrow">COMPARISON SOURCE MISMATCH</div>
                      <h3>The returned pair did not match the selected traces.</h3>
                      <p>Attempt, run, session, car, trace hash, trace schema, or comparison policy differed. No substitute evidence was shown.</p>
                    </div>
                  </section>
                ) : null}
                {comparisonResponse?.status === "unavailable" ? (
                  <section className="unavailable-panel panel">
                    <span className="state-icon">!</span>
                    <div>
                      <div className="eyebrow">COMPARISON UNAVAILABLE</div>
                      <h3>This pair does not support a distance comparison.</h3>
                      <p>{comparisonResponse.reason}</p>
                    </div>
                  </section>
                ) : comparison ? (
                  <>
                    <section className="result-heading">
                      <div>
                        <div className="eyebrow">
                          {comparison.track.track_name} ·{" "}
                          {label(target?.context?.session_type)}
                        </div>
                        <h2>
                          Lap delta <span>across distance.</span>
                        </h2>
                      </div>
                      <div className="result-distance">
                        0 <i>—</i>{" "}
                        {Math.round(
                          comparison.track.track_length_m,
                        ).toLocaleString()}
                        <small>METRES</small>
                      </div>
                    </section>
                    <TrajectoryComparisonPanel
                      report={
                        trajectoryComparisonResponse?.status === "ok"
                          ? trajectoryComparisonResponse.data
                          : null
                      }
                      unavailableReason={
                        trajectoryComparisonResponse?.status === "unavailable"
                          ? trajectoryComparisonResponse.reason
                          : null
                      }
                      probeDistanceM={params.position_probe_m ?? ""}
                      formParams={trajectoryComparisonFormParams}
                    />
                    <section className="metric-row">
                      <Metric
                        label="OFFICIAL LAP TIME"
                        value={seconds(
                          comparison.official_lap_time_difference_s,
                        )}
                        detail="Target minus reference"
                        tone={
                          comparison.official_lap_time_difference_s != null &&
                          comparison.official_lap_time_difference_s > 0
                            ? "warm"
                            : "cool"
                        }
                      />
                      <Metric
                        label="OBSERVED-RANGE DELTA"
                        value={seconds(
                          comparison.observed_range_delta
                            .observed_range_change_s,
                        )}
                        detail={
                          comparison.observed_range_delta
                            .first_supported_distance_m == null
                            ? "No shared supported span"
                            : `${Math.round(comparison.observed_range_delta.first_supported_distance_m)}–${Math.round(comparison.observed_range_delta.last_supported_distance_m ?? 0)} m supported`
                        }
                        tone="neutral"
                      />
                      <Metric
                        label="DELTA COVERAGE"
                        value={percent(comparison.quality.delta_time_coverage)}
                        detail={`${comparison.analysis_version} · ${comparison.config.max_bracket_time_s} s max bracket`}
                        tone="neutral"
                      />
                    </section>
                    {comparison.lap_debrief ? (
                      <section
                        className="lap-debrief panel"
                        aria-label="Recorded lap debrief"
                      >
                        <div className="comparison-brief-heading">
                          <div>
                            <span className="eyebrow">RECORDED LAP DEBRIEF</span>
                            <h3>Lap result at a glance</h3>
                          </div>
                          <span className="brief-version">
                            {comparison.lap_debrief.status} · {comparison.lap_debrief.analysis_version}
                          </span>
                        </div>
                        <p className="comparison-brief-text">
                          {comparison.lap_debrief.text}
                        </p>
                        {comparison.lap_debrief.ranked_regions.length ? (
                          <ol className="lap-debrief-regions">
                            {comparison.lap_debrief.ranked_regions.map((region) => (
                              <li key={`${region.rank}-${region.region_id}`}>
                                <strong>{region.text}</strong>
                                <small>
                                  Rank {region.rank} · {region.provenance?.target?.attempt_key ?? "target attempt"} vs {region.provenance?.reference?.attempt_key ?? "reference"}
                                  {region.provenance?.model?.model_id
                                    ? ` · ${region.provenance.model.model_id} revision ${region.provenance.model.revision ?? "?"}`
                                    : ""}
                                </small>
                              </li>
                            ))}
                          </ol>
                        ) : null}
                        {comparison.lap_debrief.limitations.length ? (
                          <ul className="brief-limitations lap-debrief-limitations">
                            {comparison.lap_debrief.limitations.map((item) => (
                              <li key={item.code}>{item.text}</li>
                            ))}
                          </ul>
                        ) : null}
                        {target?.attempt_key && reference?.attempt_key ? (
                          <RecordedEvidenceSpeech
                            planResult={buildRecordedSpeechPlan({
                              kind: "lap_debrief",
                              report: comparison.lap_debrief,
                              targetAttemptKey: target.attempt_key,
                              referenceAttemptKey: reference.attempt_key,
                            })}
                          />
                        ) : null}
                      </section>
                    ) : null}
                    <section
                      className="comparison-brief panel"
                      aria-label="Deterministic comparison brief"
                    >
                      <div className="comparison-brief-heading">
                        <div>
                          <span className="eyebrow">DETERMINISTIC BRIEF</span>
                          <h3>What the selected evidence says</h3>
                        </div>
                        <span className="brief-version">
                          {comparison.comparison_brief.analysis_version}
                        </span>
                      </div>
                      <p className="comparison-brief-text">
                        {comparison.comparison_brief.text}
                      </p>
                      <details className="comparison-brief-details">
                        <summary>
                          {comparison.comparison_brief.facts.length} supported facts · source fields
                        </summary>
                        {comparison.comparison_brief.facts.length ? (
                          <ul>
                            {comparison.comparison_brief.facts.map((fact, index) => (
                              <li key={`${fact.kind}-${index}`}>
                                <strong>{fact.text}</strong>
                                <small>
                                  Target {fact.provenance.target.attempt_key ?? "unknown"} ·
                                  Reference {fact.provenance.reference.attempt_key ?? "unknown"}
                                  {Object.values(fact.source_fields).length
                                    ? ` · ${Object.values(fact.source_fields).join(" / ")}`
                                    : ""}
                                </small>
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <p>No facts meet the brief&apos;s support requirements.</p>
                        )}
                        {comparison.comparison_brief.limitations.length ? (
                          <ul className="brief-limitations">
                            {comparison.comparison_brief.limitations.map((item, index) => (
                              <li key={`${item.code}-${index}`}>{item.text}</li>
                            ))}
                          </ul>
                        ) : null}
                      </details>
                    </section>
                    <section className="quality-row panel" aria-label="Reported lap and sector timing">
                      <div className="quality-title">
                        <span className="eyebrow">SESSION HISTORY</span>
                        <strong>Reported timing</strong>
                      </div>
                      {(["sector1", "sector2", "sector3"] as const).map((sector, index) => {
                        const evidence = comparison.sector_timing_difference_ms;
                        const item = evidence.sectors[sector];
                        return (
                          <div key={sector}>
                            <span>SECTOR {index + 1} · TARGET − REFERENCE</span>
                            <strong>{seconds(item?.target_minus_reference_ms == null ? null : item.target_minus_reference_ms / 1000)}</strong>
                            <small>{item?.target_valid === false || item?.reference_valid === false ? "At least one reported sector is invalid" : item?.status === "matched_values" ? "Reported sector values" : "Timing evidence unavailable"}</small>
                          </div>
                        );
                      })}
                    </section>
                    <ComparisonRunEvidence
                      target={comparison.processing_run_evidence?.target ?? null}
                      reference={comparison.processing_run_evidence?.reference ?? null}
                      hrefForRun={(runId) =>
                        appScreenHref("sessions", preservedQuery, {
                          run_id: runId,
                        })
                      }
                    />
                    <ComparisonConditionsPanel
                      target={comparison.observed_conditions?.target}
                      reference={comparison.observed_conditions?.reference}
                    />
                    <PlayerParticipantContextPanel
                      target={target?.player_participant_context}
                      reference={reference?.player_participant_context}
                    />
                    <PlayerCarSetupContextPanel
                      target={target?.player_car_setup_context}
                      reference={reference?.player_car_setup_context}
                    />
                    {comparison.comparison_window ? (
                      <ComparisonWindowPanel
                        comparison={comparison}
                        report={comparison.comparison_window}
                        brief={comparison.distance_window_brief ?? null}
                        target={target}
                        reference={reference}
                        window={distanceWindow(
                          params.window_start_m,
                          params.window_end_m,
                        )}
                      />
                    ) : null}
                    <LinkedComparisonCharts
                      key={JSON.stringify([
                        target?.attempt_key ?? "",
                        reference?.attempt_key ?? "",
                        params.window_start_m ?? "",
                        params.window_end_m ?? "",
                      ])}
                      distance={comparison.distance_m}
                      initialWindowM={distanceWindow(
                        params.window_start_m,
                        params.window_end_m,
                      )}
                      charts={buildComparisonCharts(comparison, target, reference)}
                    />
                    <section className="quality-row panel">
                      <div className="quality-title">
                        <span className="eyebrow">DATA QUALITY</span>
                        <strong>Coverage and gaps</strong>
                      </div>
                      <div>
                        <span>TARGET SAMPLES</span>
                        <strong>
                          {comparison.target_trace.source_sample_count.toLocaleString()}
                        </strong>
                      </div>
                      <div>
                        <span>REFERENCE SAMPLES</span>
                        <strong>
                          {comparison.reference_trace.source_sample_count.toLocaleString()}
                        </strong>
                      </div>
                      <div>
                        <span>DELTA SUPPORTED</span>
                        <strong>
                          {percent(comparison.quality.delta_time_coverage)}
                        </strong>
                      </div>
                      <div>
                        <span>UNSUPPORTED SPANS</span>
                        <strong>
                          {comparison.quality.target_excluded_spans.length +
                            comparison.quality.reference_excluded_spans.length}
                        </strong>
                      </div>
                    </section>
                    <p className="chart-footnote">
                      Lines stop where a channel is unsupported. Missing
                      telemetry is not interpolated across.
                    </p>
                    <CornerLossCandidatesPanel
                      analysis={comparison.corner_loss_candidates}
                    />
                    {comparison.corner_comparison_brief.status === "available" ? (
                      <CornerComparisonBriefPanel
                        report={comparison.corner_comparison_brief}
                      />
                    ) : null}
                    <DrivingPatternAssessmentPanel
                      report={comparison.driving_pattern_assessment}
                    />
                    <ThrottlePatternAssessmentPanel
                      report={comparison.throttle_pattern_assessment}
                    />
                    {comparison.corner_analysis ? (
                      <RegionAnalysisPanel
                        analysis={comparison.corner_analysis}
                        comparison={comparison}
                      />
                    ) : (
                      <section className="region-prompt panel">
                        <div className="eyebrow">
                          {selectedModel
                            ? "REGION ANALYSIS UNAVAILABLE"
                            : trackModelCatalogUnavailable
                              ? "REGION MODEL CATALOG UNAVAILABLE"
                              : standaloneRegionModeSupported
                                ? "NO REGION MODEL SELECTED"
                                : "REGION POLICY UNSUPPORTED"}
                        </div>
                        <h3>
                          {selectedModel
                            ? "The selected model returned no region analysis."
                            : trackModelCatalogUnavailable
                              ? "Track model metadata could not be loaded."
                            : standaloneRegionModeSupported
                                ? "Choose an explicit model to inspect distance regions."
                              : "Standalone region inspection requires stable Time Trial or Practice/Qualifying context."}
                        </h3>
                        <p>
                          {selectedModel
                            ? "The API returned the lap comparison without region evidence. Reload or choose another registered revision."
                            : trackModelCatalogUnavailable
                              ? "The local API did not provide the model catalog. Reload after the API is available to select a registered revision."
                              : standaloneRegionModeSupported
                                ? "No circuit geometry is inferred from session telemetry. Models are versioned and selected explicitly."
                                : "Race and unknown contexts stay unavailable; no region results are inferred for this session."}
                        </p>
                      </section>
                    )}
                  </>
                ) : (
                  <section className="empty-state panel">
                    <div className="eyebrow">{noComparison.eyebrow}</div>
                    <h2>{noComparison.title}</h2>
                    <p>{noComparison.body}</p>
                  </section>
                )}
                {engineerIntent !== "region_comparison" ? (
                  <PairedRegionPanel
                    report={pairedRegionReport}
                    engineerLinks={engineerRegionLinks}
                    unavailableReason={
                      pairedRegionResponse?.status === "unavailable"
                        ? pairedRegionResponse.reason
                        : null
                    }
                    selectionReady={pairedRegionSelectionReady}
                    navigation={{
                      sessionKey: session?.session_key ?? null,
                      runId: selectedRunId,
                      runOffset: firstParam(rawParams.run_offset) ?? null,
                      sessionOffset: firstParam(rawParams.session_offset) ?? null,
                      attemptOffset: firstParam(rawParams.attempt_offset) ?? null,
                      lifecycleEventOffset: firstParam(rawParams.lifecycle_event_offset) ?? null,
                      observationAttemptKeys: params.observation_attempt_keys,
                      positionProbeM: params.position_probe_m ?? null,
                    }}
                  />
                ) : null}
              </section>
            </div>
          </>
        )}
      </main>
      <EngineerQueryPanel
        report={engineerQueryReport}
        requestState={engineerQueryRequestState}
        intent={
          engineerIntent === "attempt_summary" || engineerIntent === "region_comparison"
            ? engineerIntent
            : null
        }
        sessionKey={session?.session_key ?? null}
        targetAttemptKey={
          params.target_attempt_key && target?.attempt_key === params.target_attempt_key
            ? params.target_attempt_key
            : null
        }
        referenceAttemptKey={manualReference?.attempt_key ?? null}
        referenceChoice={params.reference_choice ?? null}
        comparisonPolicy={comparisonPolicy}
        trackModelKey={selectedModel ? modelKey(selectedModel) : null}
        regions={engineerRegions}
        targetHref={engineerSourceHref(target?.attempt_key ?? null)}
        referenceHref={engineerSourceHref(manualReference?.attempt_key ?? null)}
        comparisonHref={engineerComparisonHref}
        requestReason={engineerQueryRequestReason}
        summaryScreenHref={
          target && session && params.target_attempt_key === target.attempt_key
            ? appScreenHref("engineer", preservedQuery, {
                session_key: session.session_key,
                target_attempt_key: target.attempt_key,
                engineer_intent: "attempt_summary",
              })
            : null
        }
      />
      <footer className="footer-bar">
        <span>
          GP...T <b>·</b> LOCAL FIRST
        </span>
        <span>Recorded telemetry only · No generated coaching</span>
      </footer>
    </div>
  );
}

function ComparisonRunEvidence({
  target,
  reference,
  hrefForRun,
}: {
  target: ProcessingRunSummary | null;
  reference: ProcessingRunSummary | null;
  hrefForRun: (runId: string) => string | null;
}) {
  const entries =
    target && reference && target.run_id === reference.run_id
      ? [{ label: "TARGET + REFERENCE", summary: target }]
      : [
          ...(target ? [{ label: "TARGET CAPTURE", summary: target }] : []),
          ...(reference
            ? [{ label: "REFERENCE CAPTURE", summary: reference }]
            : []),
        ];

  if (entries.length === 0) {
    return (
      <section className="quality-row panel">
        <div className="quality-title">
          <span className="eyebrow">CAPTURE EVIDENCE</span>
          <strong>Source run evidence unavailable</strong>
        </div>
        <div>
          <span>STATUS</span>
          <strong>Unknown</strong>
        </div>
      </section>
    );
  }

  return (
    <>
      {entries.map(({ label: sourceLabel, summary }) => (
        <section
          className="quality-row capture-evidence-row panel"
          key={summary.run_id}
        >
          <div className="quality-title">
            <span className="eyebrow">{sourceLabel} · CAPTURE EVIDENCE</span>
            <strong>Run {summary.run_id.slice(0, 8)}</strong>
            <a className="capture-evidence-link" href={hrefForRun(summary.run_id) ?? undefined}>
              Open run summary ↗
            </a>
          </div>
          <div>
            <span>CAPTURE FOOTER</span>
            <strong>
              {summary.capture.complete === true
                ? "COMPLETE"
                : summary.capture.complete === false
                  ? "INCOMPLETE"
                  : "UNKNOWN"}
            </strong>
            <small>{summary.capture.footer_status ?? "status unknown"}</small>
          </div>
          <div>
            <span>CAPTURE PACKETS</span>
            <strong>
              {formatEvidenceCount(
                summary.capture.recording_counters?.recovered_datagrams ??
                  summary.capture.recording_counters?.recorded ??
                  summary.processing.import_counters?.packet_count,
              )}
            </strong>
          </div>
          <div>
            <span>RECORDING QUEUE DROPS</span>
            <strong>
              {formatEvidenceCount(
                summary.capture.recording_counters?.queue_dropped,
              )}
            </strong>
          </div>
          <div>
            <span>REPLAY LATE PACKETS</span>
            <strong>
              {formatEvidenceCount(
                summary.processing.replay_counters
                  ?.import_late_packets_ignored,
              )}
            </strong>
          </div>
          <div>
            <span>REPLAY FRAME DROPS</span>
            <strong>
              {formatEvidenceCount(
                summary.processing.replay_counters
                  ?.import_frame_overflow_packets_dropped,
              )}
            </strong>
          </div>
        </section>
      ))}
    </>
  );
}

function Metric({
  label: title,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone: "warm" | "cool" | "neutral";
}) {
  return (
    <div className={`metric-card panel metric-${tone}`}>
      <span>{title}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function RegionAnalysisPanel({
  analysis,
  comparison,
}: {
  analysis: CornerAnalysis;
  comparison: Comparison;
}) {
  return (
    <section className="region-analysis">
      <header className="region-overview panel">
        <div>
          <div className="eyebrow">
            {label(analysis.model.validation_status)} DISTANCE REGIONS ·{" "}
            {analysis.regions.length} WINDOWS
          </div>
          <h2>{analysis.model.track_name} region inspection</h2>
          <p>{analysis.model.provenance}</p>
        </div>
        <span className="region-state">
          {analysis.diagnostic_only ? "DIAGNOSTIC ONLY" : "MEASURED"}
        </span>
      </header>
      <p className="region-caveat">
        {label(analysis.layout_validation_status)}. Regions are not verified
        corner numbers. Steering turn-in is a proxy; driver apex and
        track-relative geometry are unavailable. No coaching is generated.
      </p>
      <div className="region-grid">
        {analysis.regions.map((region, index) => (
          <RegionCard
            key={region.identifier}
            region={region}
            index={index}
            comparison={comparison}
          />
        ))}
      </div>
    </section>
  );
}

function CornerLossCandidatesPanel({
  analysis,
}: {
  analysis: CornerLossCandidates;
}) {
  const model = analysis.source.model;
  const selection = analysis.source.reference_selection;
  const selectedReference = selection?.selected_reference?.attempt_key;
  const assessments = analysis.region_assessment;

  return (
    <section className="corner-candidates panel" aria-label="Recorded corner time differences">
      <header className="region-card-header">
        <div>
          <div className="region-index">RECORDED CORNER-TIME DIFFERENCES</div>
          <h3>
            {analysis.status === "ranked"
              ? "Largest supported measurements"
              : analysis.status === "no_positive_supported_differences"
                ? "No positive supported differences"
                : "Candidate ranking abstained"}
          </h3>
          <p>
            {analysis.measurement_label}. These measurements do not identify a
            cause or give driving advice.
          </p>
        </div>
        <span className={`region-state ${analysis.status === "ranked" ? "" : "region-state-draft"}`}>
          {label(analysis.status)}
        </span>
      </header>
      <div className="corner-candidate-provenance">
        <span>
          MODEL {model ? `${model.model_id} r${model.revision}` : "NOT SELECTED"}
        </span>
        <span>
          SESSION BEST {typeof selectedReference === "string"
            ? selectedReference
            : selection?.status
              ? label(selection.status)
              : "NOT ASSESSED"}
        </span>
        <span>
          CONNECTED REGIONS {assessments.connected_interval_count}/
          {assessments.region_count}
        </span>
      </div>
      {analysis.ranked_candidates.length ? (
        <ol className="corner-candidate-list">
          {analysis.ranked_candidates.map((candidate) => (
            <li key={candidate.region_id}>
              <strong>#{candidate.rank} {candidate.region_label}</strong>
              <span>
                {seconds(candidate.recorded_time_difference_s)} ·{" "}
                [{candidate.analysis_window_m[0].toFixed(1)}, {candidate.analysis_window_m[1].toFixed(1)}) m
              </span>
              <small>
                Shared coverage {percent(candidate.connected_support.shared_time_coverage)} ·
                target {percent(candidate.connected_support.target_time_coverage)} ·
                reference {percent(candidate.connected_support.reference_time_coverage)}
              </small>
            </li>
          ))}
        </ol>
      ) : (
        <div className="corner-candidate-exclusions">
          <strong>No ranked measurements.</strong>
          {analysis.gate_reasons.length ? (
            <ul>
              {analysis.gate_reasons.map((reason) => (
                <li key={reason}>{label(reason)}</li>
              ))}
            </ul>
          ) : (
            <p>No positive supported difference remained at one-millisecond display precision.</p>
          )}
          {analysis.gate_reasons_omitted_count > 0 ? (
            <p>
              {analysis.gate_reasons_omitted_count} additional gate reasons
              omitted.
            </p>
          ) : null}
        </div>
      )}
      <p className="corner-candidate-note">
        Half-open distance bounds [start, end). Coaching is disabled.
      </p>
    </section>
  );
}

function CornerComparisonBriefPanel({
  report,
}: {
  report: CornerComparisonBrief;
}) {
  return (
    <section className="corner-comparison-brief panel" aria-label="Recorded corner comparison brief">
      <header className="region-card-header">
        <div>
          <div className="region-index">RECORDED CONTROL OBSERVATIONS</div>
          <h3>What the ranked regions show</h3>
        </div>
        <span className="brief-version">{report.analysis_version}</span>
      </header>
      <p className="corner-comparison-brief-text">{report.text}</p>
      <div className="corner-comparison-brief-regions">
        {report.regions.map((region) => (
          <article key={region.region_id}>
            <div className="corner-comparison-brief-region-heading">
              <strong>#{region.rank} {region.region_label}</strong>
              <span>[{region.analysis_window_m[0].toFixed(1)}, {region.analysis_window_m[1].toFixed(1)}) m</span>
            </div>
            <ul>
              {region.facts.map((fact, index) => (
                <li key={`${fact.kind}-${index}`}>
                  <strong>{fact.text}</strong>
                  <small>
                    Target {fact.provenance.target.attempt_key ?? "unknown"} ·
                    Reference {fact.provenance.reference.attempt_key ?? "unknown"} ·
                    {Object.values(fact.source_fields).join(" / ")}
                  </small>
                </li>
              ))}
            </ul>
            {region.omitted_measurement_count > 0 ? (
              <p className="corner-comparison-brief-omissions">
                {region.omitted_measurement_count} control observation(s) omitted: {region.omitted_measurements.map((item) => `${label(item.metric)} (${label(item.reason)})`).join(" · ")}
              </p>
            ) : null}
            <small className="corner-comparison-brief-provenance">
              Model {region.provenance.model.model_id ?? "unknown"} r{region.provenance.model.revision ?? "?"} ·
              SHA-256 {region.provenance.model.model_content_sha256 ?? "unavailable"}
            </small>
          </article>
        ))}
      </div>
      <p className="corner-candidate-note">
        Recorded measurements only. They do not establish a cause or driving advice.
      </p>
    </section>
  );
}

function RegionCard({
  region,
  index,
  comparison,
}: {
  region: CornerRegion;
  index: number;
  comparison: Comparison;
}) {
  const [start, end] = region.analysis_window_m;
  const plotIndices = comparison.distance_m.flatMap((distance, sampleIndex) =>
    distance >= start && distance <= end ? [sampleIndex] : [],
  );
  const plotDistance = plotIndices.map(
    (sampleIndex) => comparison.distance_m[sampleIndex],
  );
  const valueSlice = <T extends number | boolean | null>(values: T[]) =>
    plotIndices.map((sampleIndex) => values[sampleIndex] ?? null);
  const maskSlice = (values: boolean[]) =>
    plotIndices.map((sampleIndex) => values[sampleIndex] ?? false);
  const targetSpeed = region.target.minimum_speed;
  const referenceSpeed = region.reference.minimum_speed;
  const speedDifference = region.differences.minimum_speed_kph;
  const intervalSupport = region.delta_change;
  const intervalDetail = [
    `${percent(intervalSupport.shared_time_coverage)} shared time coverage`,
    `target ${percent(intervalSupport.target_time_coverage)}`,
    `reference ${percent(intervalSupport.reference_time_coverage)}`,
    ...intervalSupport.unavailable_reasons.map(label),
  ].join(" · ");

  return (
    <article className="region-card panel">
      <header className="region-card-header">
        <div>
          <div className="region-index">
            WINDOW {String(index + 1).padStart(2, "0")} · {Math.round(start)}–
            {Math.round(end)} M
          </div>
          <h3>{region.label}</h3>
        </div>
        <span className="region-state region-state-draft">
          {region.diagnostic_only ? "DIAGNOSTIC" : "SUPPORTED"}
        </span>
      </header>

      <div className="region-stat-grid">
        <RegionStat
          title="REGION DELTA CHANGE"
          value={seconds(intervalSupport.delta_change_s)}
          detail={`${label(intervalSupport.status)} · target minus reference from entry to exit · ${intervalDetail}`}
        />
        <RegionStat
          title="TARGET OBSERVED MINIMUM"
          value={speedValue(targetSpeed.speed_kph)}
          detail={`${label(targetSpeed.status)} · ${percent(targetSpeed.supported_grid_coverage)} speed coverage`}
        />
        <RegionStat
          title="REFERENCE OBSERVED MINIMUM"
          value={speedValue(referenceSpeed.speed_kph)}
          detail={`${label(referenceSpeed.status)} · ${percent(referenceSpeed.supported_grid_coverage)} speed coverage`}
        />
        <RegionStat
          title="MINIMUM SPEED DIFFERENCE"
          value={signedValue(speedDifference, "km/h")}
          detail="Target minus reference"
        />
        <RegionStat
          title="BRAKING ONSET SHIFT"
          value={signedValue(region.differences.braking_onset_distance_m, "m")}
          detail="Target minus reference · event evidence below"
        />
        <RegionStat
          title="50% THROTTLE SHIFT"
          value={signedValue(region.differences.throttle_50_distance_m, "m")}
          detail="Target minus reference · event evidence below"
        />
      </div>

      <div className="region-event-grid">
        <RegionEventEvidence
          title="Braking onset"
          target={region.target.braking}
          reference={region.reference.braking}
          targetCoverage={region.target.event_channel_coverage.brake}
          referenceCoverage={region.reference.event_channel_coverage.brake}
        />
        <RegionEventEvidence
          title="Turn-in proxy"
          target={region.target.turn_in_proxy}
          reference={region.reference.turn_in_proxy}
          targetCoverage={region.target.event_channel_coverage.steering}
          referenceCoverage={region.reference.event_channel_coverage.steering}
          note="Absolute steering threshold; not a geometric turn-in point."
        />
        <RegionEventEvidence
          title="50% throttle pickup"
          target={region.target.throttle_pickup["0.5"]}
          reference={region.reference.throttle_pickup["0.5"]}
          targetCoverage={region.target.event_channel_coverage.throttle}
          referenceCoverage={region.reference.event_channel_coverage.throttle}
        />
      </div>

      <div className="region-exits">
        <div className="region-subhead">
          <strong>Exit speed observations</strong>
          <span>Measured at configured window offsets</span>
        </div>
        <div className="region-table-wrap">
          <table>
            <thead>
              <tr>
                <th>OFFSET</th>
                <th>TARGET</th>
                <th>REFERENCE</th>
                <th>SUPPORT</th>
              </tr>
            </thead>
            <tbody>
              {region.target.exit_speeds.map((targetExit, exitIndex) => {
                const referenceExit = region.reference.exit_speeds[exitIndex];
                return (
                  <tr key={targetExit.offset_m}>
                    <td>+{targetExit.offset_m} m</td>
                    <td>{speedValue(targetExit.speed_kph)}</td>
                    <td>{speedValue(referenceExit?.speed_kph ?? null)}</td>
                    <td>
                      {label(targetExit.status)} /{" "}
                      {label(referenceExit?.status)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <p className="region-apex-note">
        Driver apex: {label(region.target.driver_apex.status)} ·{" "}
        {label(region.target.driver_apex.reason)}. The saved traces do not
        include calibrated track-relative geometry; stored world positions
        remain in world coordinates.
      </p>

      <details className="region-traces">
        <summary>Open local speed, delta and input traces</summary>
        {plotDistance.length ? (
          <div className="chart-stack">
            <Chart
              title={`${region.label} speed`}
              subtitle={`${Math.round(start)}–${Math.round(end)} m · km/h`}
              unit="KM/H"
              distance={plotDistance}
              series={[
                {
                  label: "Target",
                  color: "#f06a4f",
                  values: speed(
                    valueSlice(comparison.target_trace.values.speed_mps),
                  ),
                  mask: maskSlice(comparison.target_trace.masks.speed_mps),
                },
                {
                  label: "Reference",
                  color: "#71c7b5",
                  values: speed(
                    valueSlice(comparison.reference_trace.values.speed_mps),
                  ),
                  mask: maskSlice(comparison.reference_trace.masks.speed_mps),
                },
              ]}
            />
            <Chart
              title={`${region.label} delta`}
              subtitle="Target minus reference · seconds"
              unit="SECONDS"
              distance={plotDistance}
              zero
              series={[
                {
                  label: "Target − reference",
                  color: "#f0b45c",
                  values: valueSlice(comparison.delta_s),
                  mask: maskSlice(comparison.delta_mask),
                },
              ]}
            />
            <Chart
              title={`${region.label} driver inputs`}
              subtitle="Brake and throttle · percent"
              unit="%"
              distance={plotDistance}
              range={[0, 1]}
              percentAxis
              series={[
                {
                  label: "Target brake",
                  color: "#f06a4f",
                  values: numeric(
                    valueSlice(comparison.target_trace.values.brake),
                  ),
                  mask: maskSlice(comparison.target_trace.masks.brake),
                },
                {
                  label: "Target throttle",
                  color: "#71c7b5",
                  values: numeric(
                    valueSlice(comparison.target_trace.values.throttle),
                  ),
                  mask: maskSlice(comparison.target_trace.masks.throttle),
                },
                {
                  label: "Reference brake",
                  color: "#d89079",
                  values: numeric(
                    valueSlice(comparison.reference_trace.values.brake),
                  ),
                  mask: maskSlice(comparison.reference_trace.masks.brake),
                },
                {
                  label: "Reference throttle",
                  color: "#97b2a9",
                  values: numeric(
                    valueSlice(comparison.reference_trace.values.throttle),
                  ),
                  mask: maskSlice(comparison.reference_trace.masks.throttle),
                },
              ]}
            />
          </div>
        ) : (
          <p className="region-empty-trace">
            No shared distance-grid samples fall inside this analysis window.
          </p>
        )}
      </details>
    </article>
  );
}

function RegionStat({
  title,
  value,
  detail,
}: {
  title: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="region-stat">
      <span>{title}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function RegionEventEvidence({
  title,
  target,
  reference,
  targetCoverage,
  referenceCoverage,
  note,
}: {
  title: string;
  target: RegionEvent;
  reference: RegionEvent;
  targetCoverage: number | null;
  referenceCoverage: number | null;
  note?: string;
}) {
  return (
    <section className="region-event">
      <div className="region-subhead">
        <strong>{title}</strong>
        <span>
          Coverage {percent(targetCoverage)} / {percent(referenceCoverage)}
        </span>
      </div>
      <RegionEventLine label="TARGET" event={target} />
      <RegionEventLine label="REFERENCE" event={reference} />
      {note ? <p>{note}</p> : null}
    </section>
  );
}

function RegionEventLine({
  label: title,
  event,
}: {
  label: string;
  event: RegionEvent;
}) {
  return (
    <div className="region-event-line">
      <span>
        {title} · {label(event.status)}
      </span>
      {event.events.length ? (
        <ul>
          {event.events.map((item, index) => (
            <li key={`${item.start_distance_m}-${index}`}>
              {item.start_distance_bracket_m
                ? `${formatDistance(item.start_distance_bracket_m[0])}–${formatDistance(item.start_distance_bracket_m[1])} m onset bracket`
                : `${formatDistance(item.start_distance_m)} m onset; unbracketed`}
              {` · ${item.duration_s.toFixed(2)} s`}
              {item.left_censored ? " · left-censored" : ""}
              {item.right_censored ? " · right-censored" : ""}
            </li>
          ))}
        </ul>
      ) : (
        <small>
          {event.reason ? label(event.reason) : "No supported event"}
        </small>
      )}
    </div>
  );
}

function formatDistance(value: number) {
  return value.toFixed(1);
}

function speedValue(value: number | null) {
  return value == null ? "—" : `${value.toFixed(1)} km/h`;
}

function signedValue(value: number | null, unit: string) {
  if (value == null) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toFixed(1)} ${unit}`;
}

function Chart({
  title,
  subtitle,
  unit,
  distance,
  series,
  range,
  zero = false,
  percentAxis = false,
}: {
  title: string;
  subtitle: string;
  unit: string;
  distance: number[];
  series: Series[];
  range?: [number, number];
  zero?: boolean;
  percentAxis?: boolean;
}) {
  const w = 1000,
    h = 220,
    left = 58,
    right = 18,
    top = 17,
    bottom = 30;
  const plotH = h - top - bottom,
    plotW = w - left - right;
  const numbers = series.flatMap((line) =>
    line.values.filter(
      (value, index): value is number =>
        value !== null &&
        Number.isFinite(value) &&
        (line.mask?.[index] ?? true),
    ),
  );
  let min = range?.[0] ?? Math.min(...numbers, zero ? 0 : Infinity);
  let max = range?.[1] ?? Math.max(...numbers, zero ? 0 : -Infinity);
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    min = 0;
    max = 1;
  }
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const pad = range ? 0 : (max - min) * 0.08;
  min -= pad;
  max += pad;
  const y = (value: number) => top + ((max - value) / (max - min)) * plotH;
  const x = (index: number) =>
    left + (index / Math.max(1, distance.length - 1)) * plotW;
  const ticks = Array.from(
    { length: 4 },
    (_, index) => min + ((max - min) * index) / 3,
  );
  return (
    <section className="chart-card panel">
      <div className="chart-header">
        <div>
          <h3>{title}</h3>
          <p>{subtitle}</p>
        </div>
        <span className="chart-unit">{unit}</span>
      </div>
      <div className="chart-wrap">
        <svg
          viewBox={`0 0 ${w} ${h}`}
          role="img"
          aria-label={`${title} over lap distance`}
        >
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                className="grid-line"
                x1={left}
                x2={w - right}
                y1={y(tick)}
                y2={y(tick)}
              />
              <text
                className="axis-label"
                x={left - 9}
                y={y(tick) + 4}
                textAnchor="end"
              >
                {percentAxis ? Math.round(tick * 100) : tick.toFixed(1)}
              </text>
            </g>
          ))}
          {zero && min < 0 && max > 0 ? (
            <line
              className="zero-line"
              x1={left}
              x2={w - right}
              y1={y(0)}
              y2={y(0)}
            />
          ) : null}
          {series.map((line) => (
            <path
              key={line.label}
              d={makePath(line, x, y, displayIndices(line, distance.length))}
              fill="none"
              stroke={line.color}
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {distance.length ? (
            <>
              <text className="axis-label" x={left} y={h - 6}>
                {Math.round(distance[0]).toLocaleString()} m
              </text>
              <text
                className="axis-label"
                x={w - right}
                y={h - 6}
                textAnchor="end"
              >
                {Math.round(distance.at(-1) ?? 0).toLocaleString()} m
              </text>
            </>
          ) : null}
        </svg>
      </div>
      <div className="chart-legend">
        {series.map((line) => (
          <span key={line.label}>
            <i style={{ background: line.color }} />
            {line.label}
          </span>
        ))}
        <span className="legend-gap">Gaps break the line</span>
      </div>
    </section>
  );
}

function makePath(
  line: Series,
  x: (index: number) => number,
  y: (value: number) => number,
  indices: number[],
) {
  const segments: string[] = [];
  let current: string[] = [];
  indices.forEach((index) => {
    const value = line.values[index];
    if (
      value === null ||
      value === undefined ||
      !Number.isFinite(value) ||
      !(line.mask?.[index] ?? true)
    ) {
      if (current.length > 1) segments.push(current.join(" "));
      current = [];
      return;
    }
    current.push(
      `${current.length ? "L" : "M"}${x(index).toFixed(2)},${y(value).toFixed(2)}`,
    );
  });
  if (current.length > 1) segments.push(current.join(" "));
  return segments.join(" ");
}

function displayIndices(line: Series, length: number) {
  if (length <= 1200) return Array.from({ length }, (_, index) => index);

  const stride = Math.ceil(length / 1200);
  const indices = new Set<number>();
  const supported = (index: number) => {
    const value = line.values[index];
    return (
      value !== null &&
      value !== undefined &&
      Number.isFinite(value) &&
      (line.mask?.[index] ?? true)
    );
  };

  for (let index = 0; index < length; index += stride) indices.add(index);
  indices.add(length - 1);

  for (let index = 1; index < length; index++) {
    if (supported(index) !== supported(index - 1)) {
      indices.add(index - 1);
      indices.add(index);
    }
  }

  return [...indices].sort((a, b) => a - b);
}

const speed = (values: Array<number | boolean | null>) =>
  values.map((value) => (typeof value === "number" ? value * 3.6 : null));
const firstParam = (value: string | string[] | undefined) =>
  Array.isArray(value) ? value[0] : value;
const allParams = (value: string | string[] | undefined) =>
  Array.isArray(value) ? value : value === undefined ? [] : [value];
const modelKey = (model: TrackModelRecord) =>
  `${model.model_id}@${model.revision}`;
function distanceWindow(start?: string, end?: string): [number, number] | null {
  if (!start?.trim() || !end?.trim()) return null;
  const first = Number(start);
  const last = Number(end);
  const valid =
    Number.isFinite(first) && Number.isFinite(last) && first >= 0 && last > first;
  return valid
    ? [first, last]
    : null;
}
function comparisonQuery(
  targetAttemptKey: string,
  referenceAttemptKey: string,
  model: TrackModelRecord | null,
  policy: "time_trial" | "practice_qualifying" = "time_trial",
  windowStartM?: string,
  windowEndM?: string,
) {
  const query = new URLSearchParams({
    target_attempt_key: targetAttemptKey,
    reference_attempt_key: referenceAttemptKey,
    comparison_policy: policy,
  });
  if (model && policy === "time_trial") {
    query.set("track_model_id", model.model_id);
    query.set("track_model_revision", String(model.revision));
  }
  if (windowStartM?.trim()) query.set("window_start_m", windowStartM);
  if (windowEndM?.trim()) query.set("window_end_m", windowEndM);
  return query.toString();
}
const numeric = (values: Array<number | boolean | null>) =>
  values.map((value) => (typeof value === "number" ? value : null));
const label = (value: unknown) =>
  typeof value === "string" && value
    ? value.replaceAll("_", " ").toUpperCase()
    : "MODE UNKNOWN";
const lapTime = (ms: number | null | undefined) =>
  ms == null
    ? "No official time"
    : `${Math.floor(ms / 60_000)}:${((ms % 60_000) / 1000).toFixed(3).padStart(6, "0")}`;
const seconds = (value: number | null | undefined) =>
  value == null
    ? "—"
    : `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(3)} s`;
const percent = (value: number | null | undefined) =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const formatEvidenceCount = (value: number | null | undefined) =>
  typeof value === "number" ? value.toLocaleString() : "Unknown";
const reason = (value: string) =>
  ({
    no_prior_candidate_passed_policy: "No prior lap meets policy",
    game_invalid: "Game-invalid",
    game_marked_invalid: "Marked invalid by the game",
    not_reference_eligible: "Not reference-eligible",
    target_attempt: "Target lap",
    recorded_after_target: "Recorded later",
    import_frame_overflow_drops: "Replay assembler dropped packets",
    import_late_packet_drops: "Replay discarded late packets",
    practice_qualifying_unsupported_session_type:
      "This is not a supported practice or qualifying session",
    practice_qualifying_unknown_context:
      "Practice or qualifying context is missing or unknown",
    practice_qualifying_unknown_mode:
      "Gameplay mode is missing or unknown",
    practice_qualifying_unsupported_mode_or_ruleset:
      "This gameplay mode or ruleset is not supported",
    practice_qualifying_incomplete_context:
      "Track or assist context is incomplete",
    practice_qualifying_attempts_have_incompatible_context:
      "The attempts have different track, mode, session, or assist settings",
    practice_qualifying_attempts_must_share_processing_run:
      "The attempts must come from the same imported run",
    practice_qualifying_attempts_must_share_session:
      "The attempts must come from the same game session",
    practice_qualifying_attempts_must_share_player:
      "The attempts must belong to the same player car",
    practice_qualifying_attempt_start_unobserved:
      "The lap start was not observed",
    practice_qualifying_attempt_encountered_pit:
      "The lap includes a pit encounter",
    practice_qualifying_attempt_requires_positive_lap_time:
      "The lap has no positive official time",
    practice_qualifying_resampling_grid_limit_exceeded:
      "The requested distance grid is too large",
    practice_qualifying_region_analysis_unsupported_for_practice_qualifying:
      "Track-region analysis is currently limited to Time Trial",
  })[value] ?? value.replaceAll("_", " ");
