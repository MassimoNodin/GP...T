import type {
  ApiResponse,
  Comparison,
  LapAttemptPage,
  LapRecord,
  ProcessingRunDetail,
  ProcessingRunPage,
  ProcessingRunSession,
  ProcessingRunSummary,
  ReferenceSelection,
  SessionContext,
} from "@/lib/api";
import { requestApi } from "@/lib/api";
import {
  attemptInventoryUrl,
  exactLapAttempt,
  lapAttemptPageMatchesScope,
} from "@/lib/attempt-inventory";
import AppHeader from "../AppHeader";
import CompareResultPanel from "../CompareResultPanel";
import {
  appScreenHref,
  isSelectionTransferBlocked,
  preservedAppStateQuery,
  type AppSearchParams,
} from "@/lib/navigation";

type Policy = "time_trial" | "practice_qualifying";
const selectionKeys = [
  "run_id",
  "run_offset",
  "session_offset",
  "attempt_offset",
  "session_key",
  "target_attempt_key",
  "reference_choice",
  "comparison_policy",
  "window_start_m",
  "window_end_m",
];

export default async function ComparePage({
  searchParams,
}: {
  searchParams: Promise<AppSearchParams>;
}) {
  const params = await searchParams;
  const preservedQuery = preservedAppStateQuery(params);
  const blocked = isSelectionTransferBlocked(preservedQuery);
  const raw = {
    runId: one(params.run_id),
    runOffset: one(params.run_offset),
    sessionOffset: one(params.session_offset),
    attemptOffset: one(params.attempt_offset),
    sessionKey: one(params.session_key),
    targetKey: one(params.target_attempt_key),
    referenceChoice: one(params.reference_choice),
    policy: one(params.comparison_policy),
    windowStart: one(params.window_start_m),
    windowEnd: one(params.window_end_m),
  };
  const duplicateSelection = selectionKeys.some((key) =>
    hasDuplicate(params[key]),
  );
  const runOffset = pageOffset(raw.runOffset, 10);
  const sessionOffset = pageOffset(raw.sessionOffset, 20);
  const attemptOffset = pageOffset(raw.attemptOffset, 50);
  const sessionRunPrefix = raw.sessionKey?.split(":", 1)[0];
  const requestedRunId = raw.runId ?? sessionRunPrefix;
  const runId =
    typeof requestedRunId === "string" && /^[a-f0-9]{64}$/.test(requestedRunId)
      ? requestedRunId
      : null;
  const invalidRunId = requestedRunId !== undefined && runId === null;

  const [runsResponse, detailResponse] = blocked
    ? [null, null]
    : await Promise.all([
        requestApi<ProcessingRunPage<ProcessingRunSummary>>(
          `/api/v1/processing-runs?limit=10&offset=${runOffset}`,
        ),
        runId && !duplicateSelection
          ? requestApi<ProcessingRunDetail>(
              `/api/v1/processing-runs/${encodeURIComponent(runId)}?${new URLSearchParams({
                session_limit: "20",
                session_offset: String(sessionOffset),
                attempt_limit: "1",
                attempt_offset: "0",
                lifecycle_event_limit: "1",
                lifecycle_event_offset: "0",
              })}`,
            )
          : Promise.resolve(null),
      ]);
  const runDetailCandidate =
    detailResponse?.status === "ok" ? detailResponse.data : null;
  const runDetail =
    runDetailCandidate?.summary.run_id === runId ? runDetailCandidate : null;
  const run = runDetail?.summary ?? null;
  const sessionPage = runDetail?.sessions ?? null;
  const session = raw.sessionKey
    ? sessionPage?.items.find((item) => item.session_key === raw.sessionKey) ?? null
    : null;
  const sessionContext = session
    ? asSessionContext(session.latest_context_snapshot)
    : null;
  const requestedSessionMissing = Boolean(
    raw.sessionKey && runDetail && sessionPage && !session,
  );
  const attemptPageResponse =
    !blocked &&
    !duplicateSelection &&
    run?.processing.status === "complete" &&
    session &&
    runId
      ? await requestApi<LapAttemptPage>(
          attemptInventoryUrl({
            runId,
            sessionUid: session.session_uid,
            offset: attemptOffset,
            targetAttemptKey: raw.targetKey,
            referenceAttemptKey:
              raw.referenceChoice && raw.referenceChoice !== "session_best"
                ? raw.referenceChoice
                : undefined,
          }),
        )
      : null;
  const attemptPageCandidate =
    attemptPageResponse?.status === "ok" ? attemptPageResponse.data : null;
  const attemptPage =
    runId && session &&
    lapAttemptPageMatchesScope(
      attemptPageCandidate,
      runId,
      session.session_uid,
    )
      ? attemptPageCandidate
      : null;
  const laps = attemptPage?.items ?? [];
  const selectedAttempt = (attemptKey: string | undefined) =>
    runId && session
      ? exactLapAttempt(attemptPage, attemptKey, runId, session.session_uid)
      : null;
  const target = selectedAttempt(raw.targetKey);
  const targetMatchesSelection = Boolean(
    target &&
      target.attempt_key === raw.targetKey &&
      target.run_id === runId &&
      target.session_uid === session?.session_uid,
  );
  const resolvedTarget = targetMatchesSelection ? target : null;
  const supportedPolicy = session ? modePolicy(sessionContext?.session_type) : null;
  const policy: Policy | null =
    raw.policy === "time_trial" || raw.policy === "practice_qualifying"
      ? raw.policy
      : null;
  const policyMatchesMode = Boolean(policy && policy === supportedPolicy);
  const selectedReference =
    raw.referenceChoice && raw.referenceChoice !== "session_best"
      ? selectedAttempt(raw.referenceChoice)
      : null;
  const selectedReferenceMatchesSelection = Boolean(
    selectedReference &&
      selectedReference.attempt_key === raw.referenceChoice &&
      selectedReference.run_id === runId &&
      selectedReference.session_uid === session?.session_uid &&
      selectedReference.disposition === "completed",
  );
  const resolvedManualReference = selectedReferenceMatchesSelection
    ? selectedReference
    : null;
  const visibleLaps = [...laps];
  for (const selected of [resolvedTarget, resolvedManualReference]) {
    if (selected && !visibleLaps.some((lap) => lap.attempt_key === selected.attempt_key)) {
      visibleLaps.push(selected);
    }
  }
  const completed = visibleLaps.filter((lap) => lap.disposition === "completed");
  const sameAttempt = Boolean(
    resolvedTarget &&
      resolvedManualReference &&
      resolvedTarget.attempt_key === resolvedManualReference.attempt_key,
  );
  const window = parseWindow(
    raw.windowStart,
    raw.windowEnd,
    sessionContext?.track_length_m,
  );
  const hasWindowValue = Boolean(
    raw.windowStart?.trim() || raw.windowEnd?.trim(),
  );
  const invalidWindow = hasWindowValue && window === null;
  const automaticReferenceRequested = raw.referenceChoice === "session_best";
  const invalidAutomaticReference =
    automaticReferenceRequested && supportedPolicy !== "time_trial";
  const pairRequested = Boolean(
    !blocked &&
      !duplicateSelection &&
      run?.processing.status === "complete" &&
      session &&
      supportedPolicy &&
      policyMatchesMode &&
      resolvedTarget &&
      raw.referenceChoice &&
      !invalidAutomaticReference &&
      !sameAttempt &&
      !invalidWindow,
  );
  const automaticSelectionResponse =
    pairRequested && automaticReferenceRequested && policy === "time_trial" && resolvedTarget
      ? await requestApi<ReferenceSelection>(
          `/api/v1/references/session-best?${new URLSearchParams({
            target_attempt_key: resolvedTarget.attempt_key,
          })}`,
        )
      : null;
  const automaticSelection =
    automaticSelectionResponse?.status === "ok"
      ? automaticSelectionResponse.data
      : null;
  const automaticSelectionTargetValid = Boolean(
    automaticSelection &&
      resolvedTarget &&
      automaticSelection.reference_kind === "session_best" &&
      automaticSelection.target?.attempt_key === resolvedTarget.attempt_key,
  );
  const automaticScopeValid = Boolean(
    automaticSelectionTargetValid &&
      resolvedTarget &&
      automaticSelection &&
      scopeMatchesTarget(
        automaticSelection.scope,
        runId,
        session?.session_uid,
        resolvedTarget.car_index,
        resolvedTarget.attempt_number,
      ),
  );
  const automaticReferenceKey =
    automaticSelectionTargetValid &&
    automaticSelection?.status === "selected" &&
    typeof automaticSelection.selected_reference?.attempt_key === "string"
      ? automaticSelection.selected_reference.attempt_key
      : null;
  const automaticReferenceMetadataResponse =
    automaticReferenceKey && runId && session
      ? await requestApi<LapAttemptPage>(
          attemptInventoryUrl({
            runId,
            sessionUid: session.session_uid,
            offset: 0,
            limit: 1,
            targetAttemptKey: resolvedTarget?.attempt_key,
            referenceAttemptKey: automaticReferenceKey,
          }),
        )
      : null;
  const automaticReference =
    automaticReferenceMetadataResponse?.status === "ok"
      ? automaticReferenceMetadataResponse.data?.selected_attempts.find(
          (item) => item.requested_attempt_key === automaticReferenceKey,
        )?.attempt ?? null
      : null;
  const automaticCandidate =
    automaticSelection?.candidates.find(
      (item) => item.attempt_key === automaticReferenceKey,
    ) ?? null;
  const automaticReferenceValid = Boolean(
    automaticSelectionTargetValid &&
      automaticScopeValid &&
      resolvedTarget &&
      automaticSelection?.status === "selected" &&
      automaticReferenceKey &&
      automaticReferenceMetadataResponse?.data?.run_id === runId &&
      automaticReferenceMetadataResponse?.data?.session_uid === session?.session_uid &&
      automaticReference &&
      automaticReference.attempt_key === automaticReferenceKey &&
      automaticReference.run_id === runId &&
      automaticReference.session_uid === session?.session_uid &&
      automaticReference.car_index === resolvedTarget.car_index &&
      automaticReference.disposition === "completed" &&
      automaticReference.attempt_number < resolvedTarget.attempt_number &&
      automaticSelection.selected_reference?.trace_sha256 ===
        automaticReference.trace_sha256 &&
      automaticCandidate?.eligible === true &&
      automaticCandidate.selected === true &&
      automaticCandidate.trace_sha256 === automaticReference.trace_sha256,
  );
  const resolvedReference = automaticReferenceRequested
    ? automaticReferenceValid
      ? automaticReference
      : null
    : resolvedManualReference;
  const automaticAbstentionValid = Boolean(
    automaticSelectionTargetValid &&
      automaticSelection &&
      [
        "no_eligible_reference",
        "target_not_completed",
        "target_unavailable",
        "unsupported_policy",
      ].includes(automaticSelection.status) &&
      automaticSelection.selected_reference === null &&
      (automaticSelection.scope === null || automaticScopeValid),
  );
  const referenceIdentityMismatch = Boolean(
    automaticReferenceRequested &&
      automaticSelectionResponse?.status === "ok" &&
      (!automaticSelectionTargetValid ||
        (automaticSelection?.status === "selected" && !automaticReferenceValid) ||
        (automaticSelection?.status !== "selected" &&
          !automaticAbstentionValid)),
  );
  const comparisonResponse =
    pairRequested &&
    !referenceIdentityMismatch &&
    resolvedTarget &&
    resolvedReference &&
    policy
      ? await requestApi<Comparison>(
          `/api/v1/compare/laps?${comparisonQuery(
            resolvedTarget.attempt_key,
            resolvedReference.attempt_key,
            policy,
            window,
          )}`,
        )
      : null;
  const rawComparison =
    comparisonResponse?.status === "ok" ? comparisonResponse.data : null;
  const comparisonMatchesSelection = Boolean(
    rawComparison &&
      resolvedTarget &&
      resolvedReference &&
      policy &&
      rawComparison.target.attempt_key === resolvedTarget.attempt_key &&
      rawComparison.reference.attempt_key === resolvedReference.attempt_key &&
      rawComparison.comparison_policy === policy &&
      comparisonMatchesWindow(rawComparison, window, resolvedTarget, resolvedReference),
  );
  const comparison = comparisonMatchesSelection ? rawComparison : null;
  const runPage = runsResponse?.status === "ok" ? runsResponse.data : null;
  const listedRuns = runPage?.items ?? [];
  const visibleRuns = run && !listedRuns.some((item) => item.run_id === run.run_id)
    ? [...listedRuns, run]
    : listedRuns;
  const canSelectSession = Boolean(
    run && run.processing.status === "complete" && sessionPage,
  );
  const runFormFields = safeFields(preservedQuery, [
    ...selectionKeys,
    "session_key",
  ]);
  const sessionFormFields = safeFields(preservedQuery, [
    ...selectionKeys,
    "session_key",
  ]);
  const pairFormFields = safeFields(preservedQuery, [
    "run_id",
    "session_key",
    "target_attempt_key",
    "reference_choice",
    "comparison_policy",
    "window_start_m",
    "window_end_m",
  ]);
  const previousAttemptsHref = attemptOffset > 0
    ? pageHref(preservedQuery, [], { attempt_offset: String(Math.max(0, attemptOffset - 50)) })
    : null;
  const nextAttemptsHref = attemptPage && attemptOffset + attemptPage.items.length < attemptPage.total
    ? pageHref(preservedQuery, [], { attempt_offset: String(attemptOffset + 50) })
    : null;
  const olderRunsHref = pageHref(preservedQuery, [
    ...selectionKeys,
  ], { run_offset: String(Math.max(0, runOffset - 10)) });
  const newerRunsHref = pageHref(preservedQuery, [
    ...selectionKeys,
  ], { run_offset: String(runOffset + 10) });
  const previousSessionsHref = pageHref(preservedQuery, [
    "session_key",
    "attempt_offset",
    "target_attempt_key",
    "reference_choice",
    "comparison_policy",
    "window_start_m",
    "window_end_m",
  ], {
    run_id: runId ?? "",
    session_offset: String(Math.max(0, sessionOffset - 20)),
  });
  const nextSessionsHref = pageHref(preservedQuery, [
    "session_key",
    "attempt_offset",
    "target_attempt_key",
    "reference_choice",
    "comparison_policy",
    "window_start_m",
    "window_end_m",
  ], {
    run_id: runId ?? "",
    session_offset: String(sessionOffset + 20),
  });
  const dashboardHref = blocked
    ? null
    : appScreenHref("dashboard", preservedQuery);
  const resetHref = blocked
    ? "/compare"
    : pageHref(preservedQuery, selectionKeys, {});

  return (
    <div className="app-shell">
      <AppHeader active="compare" preservedQuery={preservedQuery} />
      <main className="page-content compare-page-content" id="main-content" tabIndex={-1}>
        <section className="compare-page-intro">
          <div className="eyebrow">COMPARE / RECORDED ATTEMPTS</div>
          <h1>
            Compare the
            <br />
            <span>selected evidence.</span>
          </h1>
          <p>
            Choose one imported run, session, target attempt, reference, and
            supported policy. Charts retain unsupported spans and gaps.
          </p>
        </section>

        {blocked ? (
          <StatePanel
            title="Selection too large to carry safely"
            text="Return to the source screen and choose the run and pair again."
          />
        ) : duplicateSelection ? (
          <StatePanel
            title="Choose one value for each selection"
            text="Repeated run, session, attempt, policy, or window parameters were rejected."
            alert
          />
        ) : runsResponse?.status !== "ok" || !runPage ? (
          <section className="unavailable-panel panel" role="status">
            <span className="state-icon">!</span>
            <div>
              <div className="eyebrow">RUN INVENTORY UNAVAILABLE</div>
              <h2>Recorded runs cannot be loaded.</h2>
              <p>{runsResponse?.reason ?? "The local API did not return a bounded run page."}</p>
            </div>
          </section>
        ) : (
          <>
            {invalidRunId && (
              <StatePanel
                title="The selected run identity is malformed"
                text="Choose a run from the bounded inventory."
                alert
              />
            )}
            <section className="panel compare-selector-panel">
              <div className="compare-selector-heading">
                <div>
                  <span className="eyebrow">STEP 1</span>
                  <h2>Select an imported run</h2>
                </div>
                <span className="count-pill">{runPage.total.toLocaleString()}</span>
              </div>
              <form className="compare-session-form" action="/compare" method="get">
                {runFormFields.map(([key, value], index) => (
                  <input key={`${key}-${index}`} type="hidden" name={key} value={value} />
                ))}
                <label className="compare-wide-field">
                  <span>RECENT IMPORTED RUNS</span>
                  <select name="run_id" defaultValue={run?.run_id ?? ""}>
                    <option value="">Choose a run</option>
                    {visibleRuns.map((item) => (
                      <option value={item.run_id} key={item.run_id}>
                        {dateLabel(item.processing.started_at_utc)} · {item.totals.session_count} sessions · {item.totals.attempt_count} attempts · {item.capture.complete === true ? "capture complete" : item.capture.complete === false ? "capture incomplete" : "capture status unknown"} · {item.run_id.slice(0, 8)}
                      </option>
                    ))}
                  </select>
                </label>
                <button className="import-button" type="submit">Load selected run</button>
              </form>
              <div className="compare-page-links">
                {runOffset > 0 && olderRunsHref && <a href={olderRunsHref}>Newer runs</a>}
                {runOffset + runPage.items.length < runPage.total && newerRunsHref && <a href={newerRunsHref}>Older runs</a>}
              </div>
            </section>

            {runId && !runDetail && (
              <StatePanel
                title="The selected run is unavailable"
                text={detailResponse?.reason ?? "No matching run detail was returned; no other run was selected."}
              />
            )}
            {run && (
              <>
                <section className="compare-selected-session panel">
                  <div>
                    <span className="eyebrow">SELECTED RUN · {run.processing.status.toUpperCase()}</span>
                    <h2>Run {run.run_id.slice(0, 8)}</h2>
                    <p>
                      {run.totals.session_count} sessions · {run.totals.attempt_count} attempts · capture {run.capture.complete === true ? "complete" : run.capture.complete === false ? "incomplete" : "status unknown"}
                    </p>
                  </div>
                  <span className="mode-badge">{run.capture.footer_status ?? "FOOTER UNKNOWN"}</span>
                </section>

                {run.processing.status !== "complete" ? (
                  <StatePanel
                    title="This processing run is not ready for comparison"
                    text={`Run status: ${run.processing.status}.`}
                  />
                ) : (
                  <section className="panel compare-selector-panel">
                    <div className="compare-selector-heading">
                      <div>
                        <span className="eyebrow">STEP 2</span>
                        <h2>Select a game session</h2>
                      </div>
                      <span className="count-pill">{sessionPage?.total.toLocaleString() ?? "—"}</span>
                    </div>
                    {sessionPage ? (
                      <form className="compare-session-form" action="/compare" method="get">
                        {sessionFormFields.map(([key, value], index) => (
                          <input key={`${key}-${index}`} type="hidden" name={key} value={value} />
                        ))}
                        <input type="hidden" name="run_id" value={run.run_id} />
                        <label className="compare-wide-field">
                          <span>SESSION ON THIS RUN</span>
                          <select name="session_key" defaultValue={session?.session_key ?? ""}>
                            <option value="">Choose a session</option>
                            {sessionPage.items.map((item) => {
                              const context = asSessionContext(item.latest_context_snapshot);
                              return (
                                <option key={item.session_key} value={item.session_key}>
                                  {context?.track_name ?? "Unknown track"} · {label(context?.session_type)} · {label(context?.game_mode)} · {item.attempt_count} attempts · UID {item.session_uid}
                                </option>
                              );
                            })}
                          </select>
                        </label>
                        <button className="import-button" type="submit">Load selected session</button>
                      </form>
                    ) : (
                      <p className="compare-selection-warning" role="status">
                        {detailResponse?.reason ?? "The selected run session page is unavailable."}
                      </p>
                    )}
                    {sessionPage && (sessionOffset > 0 || sessionOffset + sessionPage.items.length < sessionPage.total) && (
                      <div className="compare-page-links">
                        {sessionOffset > 0 && previousSessionsHref && <a href={previousSessionsHref}>Previous sessions</a>}
                        {sessionOffset + sessionPage.items.length < sessionPage.total && nextSessionsHref && <a href={nextSessionsHref}>More sessions</a>}
                      </div>
                    )}
                  </section>
                )}

                {requestedSessionMissing && (
                  <StatePanel
                    title="The selected session is not on this run page"
                    text="The session identity was retained. Use the session-page controls to locate it; no substitute session was loaded."
                  />
                )}
                {canSelectSession && !raw.sessionKey && (
                  <StatePanel
                    title="Select a session to continue"
                    text="No session or attempt is selected automatically."
                  />
                )}
                {session && run?.processing.status === "complete" && (
                  <>
                    <section className="compare-selected-session panel">
                      <div>
                        <span className="eyebrow">SELECTED SESSION</span>
                        <h2>{sessionContext?.track_name ?? "Unknown track"}</h2>
                        <p>
                          {label(sessionContext?.session_type)} · {label(sessionContext?.game_mode)} · UID {session.session_uid} · {session.attempt_count} attempts
                        </p>
                      </div>
                      <span className="mode-badge">{sessionContext?.weather_name ?? "CONDITIONS UNKNOWN"}</span>
                    </section>
                    {!supportedPolicy ? (
                      <StatePanel
                        title="This session is not available for lap comparison"
                        text="Race, unknown, and unsupported session types remain diagnostic or unavailable under the current reference policy."
                      />
                    ) : (
                      <section className="panel compare-selector-panel">
                        <div className="compare-selector-heading">
                          <div>
                            <span className="eyebrow">STEP 3</span>
                            <h2>Choose target, reference, and policy</h2>
                          </div>
                          <span className="protocol-tag">{policyLabel(supportedPolicy)}</span>
                        </div>
                        {attemptPageResponse?.status === "ok" && attemptPage ? (
                          <form className="compare-form" action="/compare" method="get">
                            {pairFormFields.map(([key, value], index) => (
                              <input key={`${key}-${index}`} type="hidden" name={key} value={value} />
                            ))}
                            <input type="hidden" name="run_id" value={run.run_id} />
                            <input type="hidden" name="session_key" value={session.session_key} />
                            <label>
                              <span>TARGET ATTEMPT</span>
                            <select name="target_attempt_key" defaultValue={resolvedTarget?.attempt_key ?? ""}>
                                <option value="">Choose target</option>
                                {visibleLaps.map((lap) => (
                                  <option value={lap.attempt_key} key={lap.attempt_key}>
                                    Attempt {lap.attempt_number} · {label(lap.disposition)} · {lapTime(lap.lap_time_ms)} · {validity(lap.game_valid)}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label>
                              <span>POLICY</span>
                              <select name="comparison_policy" defaultValue={policy ?? ""}>
                                <option value="">Choose policy</option>
                                <option value={supportedPolicy}>{policyLabel(supportedPolicy)}</option>
                              </select>
                            </label>
                            <label>
                              <span>REFERENCE</span>
                              <select name="reference_choice" defaultValue={raw.referenceChoice ?? ""}>
                                <option value="">Choose reference</option>
                                {supportedPolicy === "time_trial" && (
                                  <option value="session_best">Automatic session best · eligibility policy</option>
                                )}
                                {completed
                                  .filter((lap) => lap.attempt_key !== resolvedTarget?.attempt_key)
                                  .map((lap) => (
                                    <option value={lap.attempt_key} key={lap.attempt_key}>
                                      Attempt {lap.attempt_number} · {lapTime(lap.lap_time_ms)} · {validity(lap.game_valid)}
                                    </option>
                                  ))}
                              </select>
                            </label>
                            <label>
                              <span>INTERVAL START · M</span>
                              <input name="window_start_m" type="number" min="0" step="0.1" placeholder="Optional" defaultValue={raw.windowStart ?? ""} />
                            </label>
                            <label>
                              <span>INTERVAL END · M</span>
                              <input name="window_end_m" type="number" min="0" step="0.1" placeholder="Optional" defaultValue={raw.windowEnd ?? ""} />
                            </label>
                            <button className="import-button compare-submit" type="submit">Compare selected attempts</button>
                          </form>
                        ) : (
                          <p className="compare-selection-warning" role="status">
                            {attemptPageResponse?.reason ?? "Attempt inventory is unavailable for this session."}
                          </p>
                        )}
                        {attemptPage && (
                          <div className="compare-page-links">
                            <span>Attempts {laps.length === 0 ? "no rows on this page" : `${attemptOffset + 1}–${attemptOffset + laps.length}`} of {attemptPage.total}</span>
                            {previousAttemptsHref && <a href={previousAttemptsHref}>Previous attempts</a>}
                            {nextAttemptsHref && <a href={nextAttemptsHref}>More attempts</a>}
                          </div>
                        )}
                        {attemptPage && attemptPage.total === 0 && (
                          <p className="compare-selection-warning" role="status">No comparison-ready attempts with stored telemetry were found for this selected session.</p>
                        )}
                      </section>
                    )}

                    {raw.targetKey && !resolvedTarget && attemptPageResponse?.status === "ok" && attemptPage && (
                      <StatePanel
                        title="The selected target is unavailable in this session"
                        text="No matching telemetry-ready attempt was returned for this exact identity; no page entry was substituted."
                      />
                    )}
                    {policy && supportedPolicy !== policy && (
                      <StatePanel
                        title="The selected policy does not support this session mode"
                        text="Choose the policy offered for this exact session."
                      />
                    )}
                    {invalidWindow && (
                      <StatePanel
                        title="The distance window is invalid"
                        text="Enter both finite bounds, start before end, and keep the interval within the selected track."
                        alert
                      />
                    )}
                    {sameAttempt && (
                      <StatePanel
                        title="Target and reference must be different attempts"
                        text="The selected pair was kept; no comparison request was made."
                        alert
                      />
                    )}
                    {raw.referenceChoice && raw.referenceChoice !== "session_best" && !resolvedManualReference && attemptPageResponse?.status === "ok" && attemptPage && (
                      <StatePanel
                        title="The selected reference is unavailable"
                        text="No replacement reference was selected."
                      />
                    )}
                    {invalidAutomaticReference && (
                      <StatePanel
                        title="Automatic session best is available only for Time Trial"
                        text="Choose an explicit practice or qualifying reference for this diagnostic comparison."
                      />
                    )}
                    {automaticReferenceRequested && !invalidAutomaticReference && (
                      <ReferenceSelectionState
                        response={automaticSelectionResponse}
                        selection={automaticSelectionTargetValid ? automaticSelection : null}
                        identityMismatch={referenceIdentityMismatch}
                      />
                    )}
                    {comparisonResponse?.status === "unavailable" && (
                      <section className="unavailable-panel panel" role="status">
                        <span className="state-icon">!</span>
                        <div>
                          <div className="eyebrow">COMPARISON UNAVAILABLE</div>
                          <h2>The selected pair does not support this comparison.</h2>
                          <p>{comparisonResponse.reason ?? "The comparison service did not return a reason."}</p>
                        </div>
                      </section>
                    )}
                    {comparisonResponse?.status === "ok" && !comparisonMatchesSelection && (
                      <StatePanel
                        title="The comparison response did not match the selected evidence"
                        text="Its attempt identities, policy, or requested distance window differed from the selection, so it was not shown."
                        alert
                      />
                    )}
                    {comparison && resolvedTarget && resolvedReference && (
                      <CompareResultPanel
                        comparison={comparison}
                        target={resolvedTarget}
                        reference={resolvedReference}
                        window={window}
                        preservedQuery={preservedQuery}
                      />
                    )}
                    {pairRequested && !automaticReferenceRequested && !comparisonResponse && (
                      <StatePanel
                        title="The comparison service did not respond"
                        text="The selected pair was retained. Refresh to request the same pair again."
                      />
                    )}
                    {pairRequested && automaticReferenceRequested && automaticReferenceValid && !comparisonResponse && (
                      <StatePanel
                        title="The comparison service did not respond"
                        text="The selected reference was retained. Refresh to request the same pair again."
                      />
                    )}
                  </>
                )}
              </>
            )}
          </>
        )}

        <div className="compare-page-links">
          {dashboardHref && <a href={dashboardHref}>Back to dashboard</a>}
          {resetHref && <a href={resetHref}>Clear comparison selections</a>}
        </div>
      </main>
      <footer className="footer-bar">
        <span>GP...T <b>·</b> LOCAL FIRST</span>
        <span>Recorded comparisons · Existing reference policies</span>
      </footer>
    </div>
  );
}

function ReferenceSelectionState({
  response,
  selection,
  identityMismatch,
}: {
  response: ApiResponse<ReferenceSelection> | null;
  selection: ReferenceSelection | null;
  identityMismatch: boolean;
}) {
  if (identityMismatch) {
    return (
      <StatePanel
        title="The automatic reference did not match the selected evidence"
        text="Its target, policy scope, or selected attempt identity did not match this session, so it was not used."
        alert
      />
    );
  }
  if (!response || response.status !== "ok" || !selection?.selected_reference) {
    return (
      <StatePanel
        title={selection?.status === "no_eligible_reference"
          ? "No eligible session best is available"
          : "Automatic reference selection is unavailable"}
        text={selection?.reasons.join(" · ") || response?.reason || "No substitute reference was selected."}
      />
    );
  }
  return (
    <section className="compare-reference-policy panel">
      <div>
        <span className="eyebrow">AUTOMATIC TIME TRIAL REFERENCE</span>
        <h2>Attempt {selection.selected_reference.attempt_number} selected by policy</h2>
        <p>Reference key {selection.selected_reference.attempt_key}</p>
      </div>
      <details>
        <summary>{selection.candidates.length} candidate attempts</summary>
        <ul>
          {selection.candidates.map((candidate) => (
            <li key={candidate.attempt_key}>
              Attempt {candidate.attempt_number} · {candidate.eligible ? "eligible" : candidate.exclusion_reasons.join(", ") || "excluded"}
              {candidate.selected ? " · selected" : ""}
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}

function StatePanel({
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

function modePolicy(sessionType: string | null | undefined): Policy | null {
  if (sessionType === "time_trial") return "time_trial";
  if (typeof sessionType === "string" && /^(practice_|qualifying_|sprint_shootout_)/.test(sessionType)) {
    return "practice_qualifying";
  }
  return null;
}

function asSessionContext(value: Record<string, unknown> | null): SessionContext | null {
  return value ? (value as SessionContext) : null;
}

function parseWindow(
  start: string | undefined,
  end: string | undefined,
  trackLength: number | null | undefined,
): [number, number] | null {
  if (start === undefined && end === undefined) return null;
  if (start === undefined || end === undefined || !start.trim() || !end.trim()) return null;
  const first = Number(start);
  const last = Number(end);
  if (
    !Number.isFinite(first) ||
    !Number.isFinite(last) ||
    first < 0 ||
    last <= first ||
    (trackLength != null && last > trackLength)
  ) {
    return null;
  }
  return [first, last];
}

function comparisonQuery(
  targetAttemptKey: string,
  referenceAttemptKey: string,
  policy: Policy,
  window: [number, number] | null,
) {
  const query = new URLSearchParams({
    target_attempt_key: targetAttemptKey,
    reference_attempt_key: referenceAttemptKey,
    comparison_policy: policy,
  });
  if (window) {
    query.set("window_start_m", String(window[0]));
    query.set("window_end_m", String(window[1]));
  }
  return query.toString();
}

function scopeMatchesTarget(
  scope: Record<string, unknown> | null,
  runId: string | null,
  sessionUid: string | undefined,
  carIndex: number,
  attemptNumber: number,
) {
  return Boolean(
    scope &&
      scope.type === "same_run_session_player_prior_attempts" &&
      scope.run_id === runId &&
      scope.session_uid === sessionUid &&
      scope.car_index === carIndex &&
      scope.before_attempt_number === attemptNumber,
  );
}

function comparisonMatchesWindow(
  comparison: Comparison,
  window: [number, number] | null,
  target: LapRecord,
  reference: LapRecord,
) {
  const evidence = comparison.comparison_window;
  if (!window) return evidence == null;
  return Boolean(
    evidence &&
      evidence.source?.target_attempt_key === target.attempt_key &&
      evidence.source?.reference_attempt_key === reference.attempt_key &&
      closeEnough(evidence.window_m?.start_m, window[0]) &&
      closeEnough(evidence.window_m?.end_m, window[1]),
  );
}

function closeEnough(value: number, expected: number) {
  return Number.isFinite(value) && Math.abs(value - expected) <= 1e-9;
}

function safeFields(query: string, exclude: string[]) {
  const excluded = new Set(exclude);
  return Array.from(new URLSearchParams(query).entries()).filter(
    ([key]) => !excluded.has(key),
  );
}

function pageHref(
  query: string,
  remove: string[],
  values: Record<string, string>,
) {
  const next = new URLSearchParams(query);
  remove.forEach((key) => next.delete(key));
  Object.entries(values).forEach(([key, value]) => next.set(key, value));
  const encoded = next.toString();
  if (encoded.length > 4096) return null;
  return encoded ? `/compare?${encoded}` : "/compare";
}

function one(value: string | string[] | undefined) {
  return Array.isArray(value) ? (value.length === 1 ? value[0] : undefined) : value;
}

function hasDuplicate(value: string | string[] | undefined) {
  return Array.isArray(value) && value.length !== 1;
}

function pageOffset(value: string | undefined, pageSize: number) {
  if (!value || !/^\d+$/.test(value)) return 0;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed > 100_000) return 0;
  return Math.floor(parsed / pageSize) * pageSize;
}

function label(value: string | null | undefined) {
  return value ? value.replaceAll("_", " ").toUpperCase() : "UNKNOWN";
}

function policyLabel(policy: Policy) {
  return policy === "time_trial" ? "Time Trial" : "Practice / qualifying diagnostic";
}

function validity(value: boolean | null) {
  return value === true ? "game valid" : value === false ? "game invalid" : "validity unknown";
}

function lapTime(ms: number | null) {
  return ms == null
    ? "No official time"
    : `${Math.floor(ms / 60_000)}:${((ms % 60_000) / 1000).toFixed(3).padStart(6, "0")}`;
}

function dateLabel(value: string) {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp)
    ? new Intl.DateTimeFormat("en-AU", {
        dateStyle: "medium",
        timeZone: "UTC",
      }).format(timestamp)
    : "Date unknown";
}
