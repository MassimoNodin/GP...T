import type {
  AttemptRegionReport,
  AttemptQualityReport,
  AttemptTraceChartReport,
  AttemptTrajectoryPreview,
  Comparison,
  PairedRegionReport,
  LapAttemptPage,
  LapRecord,
  TrackModelRecord,
} from "@/lib/api";
import { requestApi } from "@/lib/api";
import { attemptTraceChartReportMatchesSelection } from "@/lib/attempt-trace-chart-match";
import { attemptQualityReportMatchesSelection } from "@/lib/attempt-quality-match";
import {
  ATTEMPT_INVENTORY_PAGE_SIZE,
  attemptInventoryUrl,
  exactLapAttempt,
  lapAttemptPageMatchesScope,
} from "@/lib/attempt-inventory";
import AppHeader from "../AppHeader";
import TrackAnalysisScreen from "../TrackAnalysisScreen";
import {
  regionsFromAttempt,
  regionsFromComparison,
  shapeFromTrajectory,
  tracesFromComparison,
} from "@/lib/analysis-display";
import { comparisonReportMatchesAttempts } from "@/lib/comparison-report-match";
import { pairedRegionReportMatchesSelection } from "@/lib/engineer-query-match";
import TrackDiagnosticEvidencePanels from "../TrackDiagnosticEvidencePanels";
import DraftTrackModelPanel from "../DraftTrackModelPanel";
import AttemptTraceCharts from "../AttemptTraceCharts";
import AttemptQualityPanel from "../AttemptQualityPanel";
import {
  appScreenHref,
  isSelectionTransferBlocked,
  preservedAppStateQuery,
  type AppSearchParams,
} from "@/lib/navigation";

const selectionKeys = [
  "run_id",
  "session_key",
  "target_attempt_key",
  "attempt_offset",
  "track_model_key",
  "engineer_region_identifier",
  "reference_choice",
  "comparison_policy",
];

export default async function TrackPage({
  searchParams,
}: {
  searchParams: Promise<AppSearchParams>;
}) {
  const params = await searchParams;
  const preservedQuery = preservedAppStateQuery(params);
  const blocked = isSelectionTransferBlocked(preservedQuery);
  const raw = {
    runId: one(params.run_id),
    sessionKey: one(params.session_key),
    attemptKey: one(params.target_attempt_key),
    attemptOffset: one(params.attempt_offset),
    modelKey: one(params.track_model_key),
    regionId: one(params.engineer_region_identifier),
    referenceKey: one(params.reference_choice),
    policy: one(params.comparison_policy),
  };
  const duplicateSelection = selectionKeys.some((key) => hasDuplicate(params[key]));
  const sessionMatch = raw.sessionKey
    ? /^([a-f0-9]{64}):(\d{1,20})$/.exec(raw.sessionKey)
    : null;
  const sessionRunId = sessionMatch?.[1] ?? null;
  const sessionUid = sessionMatch?.[2] ?? null;
  const runId = raw.runId ?? sessionRunId;
  const validRunId = Boolean(runId && /^[a-f0-9]{64}$/.test(runId));
  const runIdentityMismatch = Boolean(
    raw.runId && sessionRunId && raw.runId !== sessionRunId,
  );
  const invalidSession = Boolean(raw.sessionKey && !sessionMatch);
  const attemptKeyValid = validAttemptKey(raw.attemptKey);
  const attemptOffset = parseAttemptOffset(raw.attemptOffset);
  const invalidAttemptOffset = raw.attemptOffset !== undefined && attemptOffset === null;
  const invalidAttempt = Boolean(raw.attemptKey && !attemptKeyValid);
  const selectionInvalid =
    blocked || duplicateSelection || !validRunId || invalidSession || runIdentityMismatch || invalidAttemptOffset;

  const [modelsResponse, attemptPageResponse] = await Promise.all([
    blocked || duplicateSelection
      ? Promise.resolve(null)
      : requestApi<TrackModelRecord[]>("/api/v1/track-models"),
    !selectionInvalid && runId && sessionUid
      ? requestApi<LapAttemptPage>(
          attemptInventoryUrl({
            runId,
            sessionUid,
            offset: attemptOffset ?? 0,
            targetAttemptKey: attemptKeyValid ? raw.attemptKey : undefined,
            referenceAttemptKey: validAttemptKey(raw.referenceKey) ? raw.referenceKey : undefined,
          }),
        )
      : Promise.resolve(null),
  ]);
  const models = modelsResponse?.status === "ok" && Array.isArray(modelsResponse.data)
    ? modelsResponse.data
    : null;
  const pageCandidate = attemptPageResponse?.status === "ok"
    ? attemptPageResponse.data
    : null;
  const attemptPage = runId && sessionUid &&
    lapAttemptPageMatchesScope(pageCandidate, runId, sessionUid)
    ? pageCandidate
    : null;
  const selectedAttempt = runId && sessionUid && attemptKeyValid
    ? exactLapAttempt(attemptPage, raw.attemptKey, runId, sessionUid)
    : null;
  const selectedAttemptIdentityMatches = Boolean(
    selectedAttempt &&
      selectedAttempt.attempt_key === raw.attemptKey &&
      selectedAttempt.run_id === runId &&
      selectedAttempt.session_uid === sessionUid,
  );
  const attempt = selectedAttemptIdentityMatches ? selectedAttempt : null;
  const visibleAttempts = [...(attemptPage?.items ?? [])];
  if (attempt && !visibleAttempts.some((item) => item.attempt_key === attempt.attempt_key)) {
    visibleAttempts.push(attempt);
  }

  const matchingModels = models?.filter((model) => modelKey(model) === raw.modelKey) ?? [];
  const selectedModel = matchingModels.length === 1 ? matchingModels[0] : null;
  const duplicateModelIdentity = matchingModels.length > 1;
  const selectableModels = models?.filter(
    (model) => models.filter((candidate) => modelKey(candidate) === modelKey(model)).length === 1,
  ) ?? [];
  const staleModel = Boolean(raw.modelKey && models && !selectedModel && !duplicateModelIdentity);
  const modelCatalogUnavailable = modelsResponse?.status !== "ok" || !models;
  const traceChartRequest = attempt
    ? requestApi<AttemptTraceChartReport>(
        `/api/v1/attempts/${encodeURIComponent(attempt.attempt_key)}/traces`,
      )
    : Promise.resolve(null);
  const qualityRequest = attempt
    ? requestApi<AttemptQualityReport>(
        `/api/v1/attempts/${encodeURIComponent(attempt.attempt_key)}/quality`,
      )
    : Promise.resolve(null);
  const trajectoryRequest = attempt
    ? requestApi<AttemptTrajectoryPreview>(
        `/api/v1/attempts/${encodeURIComponent(attempt.attempt_key)}/trajectory`,
      )
    : Promise.resolve(null);
  const regionsRequest = attempt && selectedModel
    ? requestApi<AttemptRegionReport>(
        `/api/v1/attempts/${encodeURIComponent(attempt.attempt_key)}/regions?${new URLSearchParams({
          track_model_id: selectedModel.model_id,
          track_model_revision: String(selectedModel.revision),
        })}`,
      )
    : Promise.resolve(null);
  const [qualityResponse, traceChartResponse, trajectoryResponse, regionResponse] = await Promise.all([
    qualityRequest,
    traceChartRequest,
    trajectoryRequest,
    regionsRequest,
  ]);

  const qualityCandidate = qualityResponse?.status === "ok"
    ? qualityResponse.data
    : null;
  const qualityIdentityMismatch = Boolean(
    qualityCandidate && attempt &&
      !attemptQualityReportMatchesSelection(qualityCandidate, attempt),
  );
  const qualityReport = qualityCandidate && !qualityIdentityMismatch
    ? qualityCandidate
    : null;
  const qualityUnavailableReason = qualityIdentityMismatch
    ? "attempt_quality_provenance_or_shape_mismatch"
    : qualityResponse?.status === "unavailable"
      ? qualityResponse.reason
      : qualityResponse
        ? "attempt_quality_unavailable"
        : "local_api_request_failed";

  const traceChartCandidate = traceChartResponse?.status === "ok"
    ? traceChartResponse.data
    : null;
  const traceChartIdentityMismatch = Boolean(
    traceChartCandidate && attempt &&
      !attemptTraceChartReportMatchesSelection(traceChartCandidate, attempt),
  );
  const traceChartReport = traceChartCandidate && !traceChartIdentityMismatch
    ? traceChartCandidate
    : null;
  const traceChartUnavailableReason = traceChartIdentityMismatch
    ? "trace_chart_attempt_provenance_or_shape_mismatch"
    : traceChartResponse?.reason ?? (traceChartResponse ? "trace_chart_unavailable" : "local_api_request_failed");

  const trajectoryCandidate = trajectoryResponse?.status === "ok"
    ? trajectoryResponse.data
    : null;
  const trajectoryIdentityMismatch = Boolean(
    trajectoryCandidate && attempt && !trajectoryMatchesAttempt(trajectoryCandidate, attempt),
  );
  const trajectoryReport = trajectoryCandidate && !trajectoryIdentityMismatch
    ? trajectoryCandidate
    : null;
  const trajectoryUnavailableReason = trajectoryIdentityMismatch
    ? "trajectory_attempt_provenance_mismatch"
    : trajectoryResponse?.reason ?? (trajectoryResponse ? "trajectory_unavailable" : "local_api_request_failed");

  const regionCandidate = regionResponse?.status === "ok"
    ? regionResponse.data
    : null;
  const regionIdentityMismatch = Boolean(
    regionCandidate && attempt && selectedModel &&
      !regionMatchesSelection(regionCandidate, attempt, selectedModel),
  );
  const regionReport = regionCandidate && !regionIdentityMismatch
    ? regionCandidate
    : null;
  const regionUnavailableReason = regionIdentityMismatch
    ? "region_attempt_or_model_provenance_mismatch"
    : selectedModel
      ? regionResponse?.reason ?? (regionResponse ? "region_observations_unavailable" : "local_api_request_failed")
      : duplicateModelIdentity
        ? "duplicate_model_revision_identity_in_catalog"
        : staleModel
        ? "selected_model_revision_not_in_catalog"
        : modelCatalogUnavailable && raw.modelKey
          ? "track_model_catalog_unavailable"
          : "choose_a_catalog_model_revision_to_request_region_observations";

  const reference =
    runId && sessionUid && validAttemptKey(raw.referenceKey)
      ? exactLapAttempt(attemptPage, raw.referenceKey, runId, sessionUid)
      : null;
  const policy =
    raw.policy === "time_trial" || raw.policy === "practice_qualifying"
      ? raw.policy
      : null;
  const pairReady = Boolean(
    !selectionInvalid && attempt && reference && policy,
  );
  const pairQuery = new URLSearchParams({
    target_attempt_key: attempt?.attempt_key ?? "",
    reference_attempt_key: reference?.attempt_key ?? "",
    comparison_policy: policy ?? "",
  });
  const [pairResponse, pairedRegionsResponse] = await Promise.all([
    pairReady
      ? requestApi<Comparison>(`/api/v1/compare/laps?${pairQuery}`)
      : Promise.resolve(null),
    pairReady && selectedModel
      ? requestApi<PairedRegionReport>(
          `/api/v1/compare/regions?${pairQuery}&${new URLSearchParams({ track_model_id: selectedModel.model_id, track_model_revision: String(selectedModel.revision) })}`,
        )
      : Promise.resolve(null),
  ]);
  const pairCandidate =
    pairResponse?.status === "ok" ? pairResponse.data : null;
  const comparison =
    pairCandidate &&
    attempt &&
    reference &&
    pairCandidate.comparison_policy === policy &&
    comparisonReportMatchesAttempts(pairCandidate, attempt, reference)
      ? pairCandidate
      : null;
  const pairedCandidate =
    pairedRegionsResponse?.status === "ok" ? pairedRegionsResponse.data : null;
  const pairedRegions =
    comparison &&
    pairedCandidate &&
    attempt &&
    reference &&
    selectedModel &&
    policy &&
    pairedRegionReportMatchesSelection(pairedCandidate, {
      target: attempt,
      reference,
      model: selectedModel,
      comparisonPolicy: policy,
    })
      ? pairedCandidate
      : null;

  const requestedRegionExists = Boolean(
    raw.regionId && regionReport?.regions.some((region) => region.identifier === raw.regionId),
  );
  const selectedRegionId = requestedRegionExists ? raw.regionId! : null;
  const invalidRegion = Boolean(
    raw.regionId && (!attempt || !selectedModel || !requestedRegionExists),
  );
  const invalidSelectionMessage = blocked
    ? "The selected state exceeds the safe URL limit. Return to Sessions and open a single attempt."
    : duplicateSelection
      ? "Repeated run, session, attempt, model, region, or page values were rejected."
      : runIdentityMismatch
        ? "The run ID does not match the selected session key. No attempt was loaded."
        : !validRunId && raw.runId
          ? "The selected run ID is malformed. No attempt was loaded."
          : invalidSession
            ? "The selected session key is malformed. No attempt was loaded."
            : invalidAttemptOffset
              ? "The attempt page offset is malformed or outside the supported range."
              : null;
  const sessionsHref = appScreenHref("sessions", preservedQuery);
  const attemptStart = (attemptPage?.offset ?? 0) + 1;
  const attemptEnd = (attemptPage?.offset ?? 0) + (attemptPage?.items.length ?? 0);
  const previousAttemptsHref = attemptPage && attemptPage.offset > 0
    ? appScreenHref("track", preservedQuery, {
        attempt_offset: String(Math.max(0, attemptPage.offset - attemptPage.limit)),
      })
    : null;
  const nextAttemptsHref = attemptPage && attemptEnd < attemptPage.total
    ? appScreenHref("track", preservedQuery, {
        attempt_offset: String(attemptPage.offset + attemptPage.limit),
      })
    : null;

  return (
    <div className="app-shell">
      <AppHeader active="track" preservedQuery={preservedQuery} />
      <main className="page-content reference-page-content" id="main-content" tabIndex={-1}>
        <TrackAnalysisScreen
          key={JSON.stringify([
            attempt?.attempt_key,
            attempt?.trace_sha256,
            reference?.attempt_key,
            reference?.trace_sha256,
            policy,
            selectedModel?.model_id,
            selectedModel?.revision,
            selectedModel?.content_sha256,
            selectedRegionId,
          ])}
          initialInput={{
            version: 1,
            track: shapeFromTrajectory(
              trajectoryReport,
              attempt?.context?.track_name ??
                selectedModel?.track_name ??
                "No track selected",
              selectedModel?.track_length_m ??
                attempt?.context?.track_length_m ??
                0,
              comparison
                ? regionsFromComparison(pairedRegions, comparison)
                : regionsFromAttempt(regionReport),
            ),
            comparison: comparison
              ? tracesFromComparison(
                  comparison,
                  `Target · attempt ${attempt?.attempt_number}`,
                  `Reference · attempt ${reference?.attempt_number}`,
                )
              : undefined,
          }}
          initialRegionId={selectedRegionId}
          selectionHref={
            blocked ? null : appScreenHref("compare", preservedQuery)
          }
        />
        <details className="ref-workflow-details" id="track-evidence">
          <summary id="track-evidence-trigger">Open observed path and region evidence</summary>
          <div className="ref-workflow-content">
        <section className="compare-page-intro">
          <div className="eyebrow">TRACK / ONE RECORDED ATTEMPT</div>
          <h1>
            Inspect the
            <br />
            <span>observed path.</span>
          </h1>
          <p>
            Read trajectory and distance region evidence for one exact attempt. Paths are recorded traces; region windows are diagnostic drafts.
          </p>
        </section>

        {invalidSelectionMessage ? (
          <TrackStatePanel title="Selection needs attention" text={invalidSelectionMessage} alert />
        ) : null}
        {raw.regionId && invalidRegion ? (
          <TrackStatePanel
            title="The selected region is stale or unavailable"
            text="That region identifier was not returned for this exact attempt and model revision. Choose a region below; no replacement was selected."
          />
        ) : null}
        {attempt && trajectoryIdentityMismatch ? (
          <TrackStatePanel
            title="Trajectory provenance does not match"
            text="The returned path did not match the selected attempt, run, session, player, trace checksum, and schema. It was not displayed."
            alert
          />
        ) : null}
        {attempt && traceChartIdentityMismatch ? (
          <TrackStatePanel
            title="Trace chart provenance or shape does not match"
            text="The returned channels did not match this attempt's identity, verified checksum, units, and bounded chart structure. They were not displayed; the other diagnostic panels remain available."
            alert
          />
        ) : null}
        {attempt && regionIdentityMismatch ? (
          <TrackStatePanel
            title="Region provenance does not match"
            text="The returned observations did not match the selected attempt or model revision. They were not displayed or linked to the path."
            alert
          />
        ) : null}

        <section className="panel track-selection-panel">
          <div className="compare-selector-heading">
            <div>
              <span className="eyebrow">SELECTED EVIDENCE</span>
              <h2>Choose one attempt</h2>
            </div>
            {attemptPage ? <span className="count-pill">{attemptPage.total.toLocaleString()}</span> : null}
          </div>
          {!raw.sessionKey ? (
            <p className="track-selection-note">
              Open Track from an attempt in Sessions to carry its exact run and session identity.
            </p>
          ) : attemptPageResponse?.status !== "ok" || !attemptPage ? (
            <p className="compare-selection-warning" role="status">
              {attemptPageResponse?.reason ?? "The selected session attempt inventory is unavailable."}
            </p>
          ) : (
            <form className="compare-session-form" action="/track" method="get">
              {hiddenStateEntries(preservedQuery, [
                "run_id",
                "session_key",
                "target_attempt_key",
                "attempt_offset",
                "track_model_key",
                "engineer_region_identifier",
              ]).map(([key, value], index) => (
                <input key={`${key}-${index}`} type="hidden" name={key} value={value} />
              ))}
              <input type="hidden" name="run_id" value={runId ?? ""} />
              <input type="hidden" name="session_key" value={raw.sessionKey} />
              {raw.modelKey ? <input type="hidden" name="track_model_key" value={raw.modelKey} /> : null}
              <label className="compare-wide-field">
                <span>ATTEMPTS {attemptPage.total ? `${attemptStart}–${attemptEnd} OF ${attemptPage.total}` : "NONE STORED"}</span>
                <select name="target_attempt_key" defaultValue={attempt?.attempt_key ?? ""}>
                  <option value="">Choose an attempt</option>
                  {visibleAttempts.map((item) => (
                    <option value={item.attempt_key} key={item.attempt_key}>
                      {attemptLabel(item)}
                    </option>
                  ))}
                </select>
              </label>
              <button className="import-button" type="submit">Load selected attempt</button>
            </form>
          )}
          {attemptPage ? (
            <div className="compare-page-links">
              <span>Attempt list is bounded to {ATTEMPT_INVENTORY_PAGE_SIZE} rows per page.</span>
              {previousAttemptsHref ? <a href={previousAttemptsHref}>Previous attempts</a> : null}
              {nextAttemptsHref ? <a href={nextAttemptsHref}>More attempts</a> : null}
            </div>
          ) : null}
          {sessionsHref ? <div className="compare-page-links"><a href={sessionsHref}>Back to Sessions ↗</a></div> : null}
        </section>

        {raw.attemptKey && !invalidAttempt && attemptPage && !attempt ? (
          <TrackStatePanel
            title="The selected attempt is unavailable"
            text="The exact attempt key was not returned for this run and session. No substitute attempt was selected."
          />
        ) : null}
        {invalidAttempt ? (
          <TrackStatePanel
            title="The attempt key is malformed"
            text="Choose an attempt from the bounded session inventory."
            alert
          />
        ) : null}
        {!attempt && !raw.attemptKey && raw.sessionKey ? (
          <TrackStatePanel title="Select an attempt to continue" text="No attempt is selected automatically." />
        ) : null}

        {attempt ? (
          <>
            <section className="panel track-attempt-summary">
              <div className="track-attempt-heading">
                <div>
                  <span className="eyebrow">ATTEMPT {attempt.attempt_number} · {humanize(attempt.disposition)}</span>
                  <h2>{attempt.context?.track_name ?? "Unknown track"}</h2>
                  <p>{humanize(attempt.context?.session_type ?? "unknown mode")} · {humanize(attempt.context?.game_mode ?? "game mode unknown")} · session {attempt.session_uid} · player index {attempt.car_index}</p>
                </div>
                <span className="mode-badge">{attempt.game_valid === true ? "GAME VALID" : attempt.game_valid === false ? "GAME INVALID" : "VALIDITY UNKNOWN"}</span>
              </div>
              <dl className="track-attempt-facts">
                <div><dt>LAP TIME</dt><dd>{formatLapTime(attempt.lap_time_ms)}</dd></div>
                <div><dt>SAMPLES</dt><dd>{attempt.sample_count.toLocaleString()}</dd></div>
                <div><dt>TRACE CHECKSUM</dt><dd title={attempt.trace_sha256}>{attempt.trace_sha256.slice(0, 12)}…</dd></div>
                <div><dt>TRACE SCHEMA</dt><dd>v{attempt.trace_schema_version}</dd></div>
                <div><dt>REFERENCE ELIGIBILITY</dt><dd>{attempt.reference_eligible ? "eligible" : "not eligible"}</dd></div>
                <div><dt>LIFECYCLE</dt><dd>{attempt.lifecycle_assessed ? attempt.superseded ? "superseded" : "assessed" : "unassessed"}</dd></div>
              </dl>
              {attempt.exclusion_reasons.length ? (
                <p className="track-attempt-exclusions">Recorded exclusions: {attempt.exclusion_reasons.map(humanize).join(" · ")}</p>
              ) : null}
            </section>

            <section className="panel track-model-panel">
              <div className="compare-selector-heading">
                <div>
                  <span className="eyebrow">OPTIONAL DISTANCE WINDOWS</span>
                  <h2>Select a catalog model revision</h2>
                </div>
                {selectedModel ? <span className="mode-badge">{selectedModel.validation_status.toUpperCase()}</span> : null}
              </div>
              <p>Region observations are requested only after you select a model revision. Race and unknown modes can still show the observed path; the API will state when regions are unavailable.</p>
              <form className="compare-session-form" action="/track" method="get">
                {hiddenStateEntries(preservedQuery, [
                  "run_id",
                  "session_key",
                  "target_attempt_key",
                  "attempt_offset",
                  "track_model_key",
                  "engineer_region_identifier",
                ]).map(([key, value], index) => (
                  <input key={`${key}-${index}`} type="hidden" name={key} value={value} />
                ))}
                <input type="hidden" name="run_id" value={runId ?? ""} />
                <input type="hidden" name="session_key" value={raw.sessionKey ?? ""} />
                <input type="hidden" name="target_attempt_key" value={attempt.attempt_key} />
                {raw.attemptOffset !== undefined ? <input type="hidden" name="attempt_offset" value={raw.attemptOffset} /> : null}
                <label className="compare-wide-field">
                  <span>TRACK MODEL AND REVISION</span>
                  <select name="track_model_key" defaultValue={selectedModel ? modelKey(selectedModel) : ""}>
                    <option value="">Choose a model revision</option>
                    {selectableModels.map((model) => (
                      <option value={modelKey(model)} key={modelKey(model)}>
                        {model.track_name} · {model.layout_id} · rev {model.revision} · {model.origin ?? "unattributed"} · {model.validation_status}
                      </option>
                    ))}
                  </select>
                </label>
                <button className="import-button" type="submit">Load model evidence</button>
              </form>
              {staleModel ? <p className="track-selection-warning" role="status">The requested model revision is no longer in the catalog. It was not substituted.</p> : null}
              {duplicateModelIdentity ? <p className="track-selection-warning" role="status">The catalog contains multiple records for this model revision. It was not selected.</p> : null}
              {raw.modelKey && modelCatalogUnavailable ? <p className="track-selection-warning" role="status">The model catalog is unavailable, so the requested revision cannot be verified.</p> : null}
              {!raw.modelKey && modelCatalogUnavailable ? <p className="track-selection-warning" role="status">The local model catalog could not be loaded.</p> : null}
            </section>

            <AttemptTraceCharts
              report={traceChartReport}
              unavailableReason={traceChartUnavailableReason}
            />

            <AttemptQualityPanel
              report={qualityReport}
              unavailableReason={qualityUnavailableReason}
            />

            {selectedModel && !regionIdentityMismatch && regionReport?.model ? (
              <TrackDiagnosticEvidencePanels
                key={`${attempt.attempt_key}:${attempt.trace_sha256}:${attempt.trace_schema_version}:${modelKey(selectedModel)}:${selectedModel.content_sha256 ?? "unknown-content"}:${selectedRegionId ?? "no-region"}`}
                trajectoryReport={trajectoryReport}
                trajectoryUnavailableReason={trajectoryUnavailableReason}
                regionReport={regionReport}
                regionUnavailableReason={regionUnavailableReason}
                showRegionPanel
                initialSelectedRegionId={selectedRegionId}
                preservedQuery={preservedQuery}
              />
            ) : (
              <TrackDiagnosticEvidencePanels
                key={`${attempt.attempt_key}:${attempt.trace_sha256}:${attempt.trace_schema_version}:${raw.modelKey ?? "no-model"}:no-region`}
                trajectoryReport={trajectoryReport}
                trajectoryUnavailableReason={trajectoryUnavailableReason}
                regionReport={null}
                regionUnavailableReason={regionUnavailableReason}
                showRegionPanel={Boolean(raw.modelKey || modelsResponse)}
                initialSelectedRegionId={null}
                preservedQuery={preservedQuery}
              />
            )}
            <DraftTrackModelPanel
              key={
                `${attempt.run_id}:${attempt.session_uid}:${attempt.attempt_key}:${attempt.trace_sha256}:${attempt.trace_schema_version}`
              }
              attempt={attempt}
              explicitlySelected={Boolean(
                raw.attemptKey && raw.attemptKey === attempt.attempt_key,
              )}
            />
          </>
        ) : null}
          </div>
        </details>
      </main>
      <footer className="footer-bar">
        <span>GP...T <b>·</b> LOCAL FIRST</span>
        <span>Observed driving traces · Diagnostic only</span>
      </footer>
    </div>
  );
}

function TrackStatePanel({
  title,
  text,
  alert = false,
}: {
  title: string;
  text: string;
  alert?: boolean;
}) {
  return (
    <section className="compare-state-panel panel" role={alert ? "alert" : "status"}>
      <h2>{title}</h2>
      <p>{text}</p>
    </section>
  );
}

function hasDuplicate(value: string | string[] | undefined) {
  return Array.isArray(value) && value.length !== 1;
}

function one(value: string | string[] | undefined) {
  return typeof value === "string" ? value : undefined;
}

function hiddenStateEntries(preservedQuery: string, excluded: string[]) {
  const excludedKeys = new Set(excluded);
  return Array.from(new URLSearchParams(preservedQuery).entries()).filter(
    ([key]) => !excludedKeys.has(key),
  );
}

function parseAttemptOffset(value: string | undefined): number | null {
  if (value === undefined) return 0;
  if (!/^(0|[1-9]\d{0,5})$/.test(value)) return null;
  const offset = Number(value);
  return Number.isSafeInteger(offset) && offset <= 100_000 ? offset : null;
}

function validAttemptKey(value: string | undefined): value is string {
  if (!value || value.length > 256) return false;
  const match = /^([a-f0-9]{64}):(\d{1,20}):(\d{1,2}):(\d{1,10})$/.exec(value);
  if (!match) return false;
  const carIndex = Number(match[3]);
  const ordinal = Number(match[4]);
  return Number.isSafeInteger(carIndex) && carIndex <= 23 && Number.isSafeInteger(ordinal) && ordinal > 0;
}

function modelKey(model: TrackModelRecord) {
  return `${model.model_id}@${model.revision}`;
}

function trajectoryMatchesAttempt(
  report: AttemptTrajectoryPreview,
  attempt: LapRecord,
) {
  const source = report?.source;
  return Boolean(
    source &&
      source.attempt_key === attempt.attempt_key &&
      source.run_id === attempt.run_id &&
      source.session_uid === attempt.session_uid &&
      source.car_index === attempt.car_index &&
      source.trace_sha256 === attempt.trace_sha256 &&
      source.trace_schema_version === attempt.trace_schema_version,
  );
}

function regionMatchesSelection(
  report: AttemptRegionReport,
  attempt: LapRecord,
  model: TrackModelRecord,
) {
  const source = report?.source;
  const reportModel = report?.model;
  return Boolean(
    source &&
      source.attempt_key === attempt.attempt_key &&
      source.run_id === attempt.run_id &&
      source.session_uid === attempt.session_uid &&
      source.car_index === attempt.car_index &&
      source.attempt_number === attempt.attempt_number &&
      source.trace_sha256 === attempt.trace_sha256 &&
      source.trace_schema_version === attempt.trace_schema_version &&
      report.context_mode === expectedRegionMode(attempt.context?.session_type) &&
      reportModel &&
      reportModel.model_id === model.model_id &&
      reportModel.revision === model.revision &&
      reportModel.packet_format === model.packet_format &&
      reportModel.track_id === model.track_id &&
      reportModel.layout_id === model.layout_id &&
      reportModel.track_name === model.track_name &&
      reportModel.track_length_m === model.track_length_m &&
      (model.content_sha256 == null || reportModel.content_sha256 === model.content_sha256) &&
      (attempt.context?.packet_format == null || reportModel.packet_format === attempt.context.packet_format) &&
      (attempt.context?.track_id == null || reportModel.track_id === attempt.context.track_id) &&
      Array.isArray(report.regions) &&
      new Set(report.regions.map((region) => region.identifier)).size === report.regions.length,
  );
}

function expectedRegionMode(sessionType: string | null | undefined) {
  if (sessionType === "time_trial") return "time_trial";
  if (typeof sessionType === "string" && /^(practice_|qualifying_|sprint_shootout_)/.test(sessionType)) {
    return "practice_qualifying";
  }
  return null;
}

function attemptLabel(attempt: LapRecord) {
  const lapTime = formatLapTime(attempt.lap_time_ms);
  const validity = attempt.game_valid === true
    ? "game valid"
    : attempt.game_valid === false
      ? "game invalid"
      : "validity unknown";
  return `Attempt ${attempt.attempt_number} · ${humanize(attempt.disposition)} · ${lapTime} · ${validity}`;
}

function formatLapTime(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "time unavailable";
  const minutes = Math.floor(value / 60_000);
  const seconds = ((value % 60_000) / 1000).toFixed(3).padStart(6, "0");
  return `${minutes}:${seconds}`;
}

function humanize(value: string) {
  return value.replaceAll("_", " ");
}
