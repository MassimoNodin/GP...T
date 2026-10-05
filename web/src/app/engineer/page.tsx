import type { EngineerQueryReport } from "@/lib/api";
import { requestApiPost } from "@/lib/api";
import {
  attemptIdentity,
  engineerQueryReportMatchesRequest,
  parseTrackModelKey,
  type EngineerQueryRequestIdentity,
} from "@/lib/engineer-query-match";
import AppHeader from "../AppHeader";
import EngineerQueryPanel from "../EngineerQueryPanel";
import {
  appScreenHref,
  isSelectionTransferBlocked,
  preservedAppStateQuery,
  type AppSearchParams,
} from "@/lib/navigation";

export default async function EngineerPage({
  searchParams,
}: {
  searchParams: Promise<AppSearchParams>;
}) {
  const params = await searchParams;
  const preservedQuery = preservedAppStateQuery(params);
  const selectionTransferBlocked = isSelectionTransferBlocked(preservedQuery);
  const requestedAttempt = singleParam(params.target_attempt_key);
  const requestedIntent = singleParam(params.engineer_intent);
  const requestedSessionKey = singleParam(params.session_key);
  const requestedReferenceKey = singleParam(params.reference_choice);
  const requestedPolicy = singleParam(params.comparison_policy);
  const requestedModelKey = singleParam(params.track_model_key);
  const requestedRegionId = singleParam(params.engineer_region_identifier);
  const ambiguousAttempt =
    hasMultiple(params.target_attempt_key);
  const ambiguousIntent =
    hasMultiple(params.engineer_intent);
  const attemptKey = validAttemptKey(requestedAttempt)
    ? requestedAttempt
    : null;
  const malformedAttempt =
    !selectionTransferBlocked &&
    (ambiguousAttempt ||
      (requestedAttempt !== undefined && attemptKey === null));
  const unsupportedIntent =
    !ambiguousIntent &&
    requestedIntent !== undefined &&
    requestedIntent !== "attempt_summary" &&
    requestedIntent !== "region_comparison";
  const regionIntent =
    !ambiguousIntent && requestedIntent === "region_comparison";
  const ambiguousRegionSelection =
    regionIntent &&
    [
      params.session_key,
      params.reference_choice,
      params.comparison_policy,
      params.track_model_key,
      params.engineer_region_identifier,
    ].some(hasMultiple);
  const parsedTargetIdentity = attemptKey ? attemptIdentity(attemptKey) : null;
  const parsedReferenceIdentity = validAttemptKey(requestedReferenceKey)
    ? attemptIdentity(requestedReferenceKey)
    : null;
  const parsedSessionIdentity = parseSessionKey(requestedSessionKey);
  const parsedModel = parseTrackModelKey(requestedModelKey);
  const comparisonPolicy =
    requestedPolicy === "time_trial" ||
    requestedPolicy === "practice_qualifying"
      ? requestedPolicy
      : null;
  const regionIdentifier =
    typeof requestedRegionId === "string" &&
    requestedRegionId.length > 0 &&
    requestedRegionId.length <= 256
      ? requestedRegionId
      : null;
  const regionScopeMatches = Boolean(
    parsedTargetIdentity &&
      parsedReferenceIdentity &&
      parsedSessionIdentity &&
      parsedSessionIdentity.runId === parsedTargetIdentity.runId &&
      parsedSessionIdentity.sessionUid === parsedTargetIdentity.sessionUid &&
      parsedSessionIdentity.runId === parsedReferenceIdentity.runId &&
      parsedSessionIdentity.sessionUid === parsedReferenceIdentity.sessionUid,
  );
  const canRequestRegion = Boolean(
    regionIntent &&
      !selectionTransferBlocked &&
      !malformedAttempt &&
      !ambiguousIntent &&
      !ambiguousRegionSelection &&
      attemptKey &&
      requestedReferenceKey !== "session_best" &&
      parsedReferenceIdentity &&
      comparisonPolicy &&
      parsedModel &&
      regionIdentifier &&
      regionScopeMatches,
  );
  const canRequestSummary = Boolean(
    !selectionTransferBlocked &&
    !malformedAttempt &&
    !ambiguousIntent &&
    attemptKey &&
    requestedIntent === "attempt_summary",
  );
  const requestIdentity: EngineerQueryRequestIdentity | null = canRequestSummary
    ? { intent: "attempt_summary", targetAttemptKey: attemptKey! }
    : canRequestRegion &&
        attemptKey &&
        requestedReferenceKey &&
        comparisonPolicy &&
        parsedModel &&
        regionIdentifier
      ? {
          intent: "region_comparison",
          targetAttemptKey: attemptKey,
          referenceAttemptKey: requestedReferenceKey,
          comparisonPolicy,
          trackModelId: parsedModel.modelId,
          trackModelRevision: parsedModel.revision,
          regionIdentifier,
        }
      : null;
  const requestBody =
    requestIdentity?.intent === "attempt_summary"
      ? {
          intent: "attempt_summary",
          target_attempt_key: requestIdentity.targetAttemptKey,
        }
      : requestIdentity?.intent === "region_comparison"
        ? {
            intent: "region_comparison",
            target_attempt_key: requestIdentity.targetAttemptKey,
            reference_attempt_key: requestIdentity.referenceAttemptKey,
            comparison_policy: requestIdentity.comparisonPolicy,
            track_model_id: requestIdentity.trackModelId,
            track_model_revision: requestIdentity.trackModelRevision,
            region_identifier: requestIdentity.regionIdentifier,
          }
        : null;
  const reportResponse = requestBody
    ? await requestApiPost<EngineerQueryReport>(
        "/api/v1/engineer/query",
        requestBody,
      )
    : null;
  const candidateReport =
    reportResponse?.status === "ok" ? reportResponse.data : null;
  const report = candidateReport && requestIdentity &&
    engineerQueryReportMatchesRequest(candidateReport, requestIdentity)
      ? candidateReport
      : null;
  const querySubmitted =
    requestedIntent === "attempt_summary" || regionIntent;
  const requestState = !querySubmitted
    ? "not_requested"
    : requestIdentity
      ? report
        ? "ok"
        : "failed"
      : "not_ready";
  const requestReason =
    requestState === "not_ready"
      ? selectionTransferBlocked
        ? "The selection was too large to carry safely. No attempt was requested. Choose the attempt again from its source."
        : ambiguousIntent
          ? "The request contains multiple engineer intents. Choose one action from the selected attempt again."
          : malformedAttempt
            ? "The requested attempt identity is malformed. No attempt was requested; choose it again from the inventory."
            : regionIntent
              ? ambiguousRegionSelection
                ? "The regional selection contains repeated values. No query was sent; reopen one verified region from Dashboard."
                : !attemptKey
                  ? "Select an exact target attempt before requesting a regional explanation."
                  : requestedReferenceKey === "session_best"
                    ? "A regional explanation requires an explicit manual reference attempt."
                    : !parsedReferenceIdentity
                      ? "The reference attempt selection is missing or malformed. No query was sent."
                      : !comparisonPolicy
                        ? "Choose an explicit comparison policy before requesting a regional explanation."
                        : !parsedModel
                          ? "Choose an exact registered model ID and revision before requesting a regional explanation."
                          : !regionIdentifier
                            ? "Select one available region before requesting an explanation."
                            : !regionScopeMatches
                              ? "The target and reference do not match the selected session. No query was sent."
                              : "The regional selection is incomplete. No query was sent."
              : "Select an explicit attempt before requesting a summary."
      : (reportResponse?.reason ??
        (candidateReport && !report
          ? "The API response did not match the exact request and source provenance. No substitute evidence was shown."
          : null));
  const returnedTargetHref = report ? sourceAttemptHref(report) : null;
  const returnedReferenceHref =
    report?.intent === "region_comparison"
      ? sourceAttemptHrefForKey(report, report.selected.reference_attempt_key)
      : null;
  const dashboardState = new URLSearchParams(preservedQuery);
  dashboardState.delete("engineer_intent");
  dashboardState.delete("engineer_region_identifier");
  const dashboardHref = selectionTransferBlocked
    ? null
    : appScreenHref("dashboard", dashboardState.toString(), {
        ...(requestedSessionKey ? { session_key: requestedSessionKey } : {}),
        ...(attemptKey ? { target_attempt_key: attemptKey } : {}),
        ...(requestedReferenceKey ? { reference_choice: requestedReferenceKey } : {}),
        ...(comparisonPolicy ? { comparison_policy: comparisonPolicy } : {}),
        ...(requestedModelKey ? { track_model_key: requestedModelKey } : {}),
      });
  const replacedFormKeys = regionIntent
    ? [
        "session_key",
        "target_attempt_key",
        "reference_choice",
        "comparison_policy",
        "track_model_key",
        "engineer_intent",
        "engineer_region_identifier",
      ]
    : ["target_attempt_key", "engineer_intent"];
  const preservedFormEntries = selectionTransferBlocked
    ? []
    : Array.from(new URLSearchParams(preservedQuery).entries()).filter(
        ([key]) => !replacedFormKeys.includes(key),
      );

  return (
    <div className="app-shell">
      <AppHeader active="engineer" preservedQuery={preservedQuery} />
      <main
        className="page-content engineer-page-content"
        id="main-content"
        tabIndex={-1}
      >
        <section className="engineer-page-intro">
          <div className="eyebrow">ENGINEER / RECORDED EVIDENCE</div>
          <h1>
            {regionIntent ? "One region." : "One attempt."}
            <br />
            <span>{regionIntent ? "Both laps, sourced." : "Evidence in context."}</span>
          </h1>
          <p>
            {regionIntent
              ? "GP...T shows deterministic facts for the exact selected target, reference, model revision, and distance region. It does not generate advice or decide eligibility for coaching."
              : "GP...T builds a short, deterministic summary from stored attempt metadata. It does not read trace samples, generate advice, or decide whether the attempt is eligible for comparison or coaching."}
          </p>
        </section>

        {selectionTransferBlocked ? (
          <section className="connection-state panel" role="status">
            <span className="state-icon">!</span>
            <div>
              <h2>Attempt selection is paused</h2>
              <p>
                The selected context could not be carried intact. No attempt
                summary was requested; return to an inventory and choose one
                attempt explicitly.
              </p>
            </div>
          </section>
        ) : regionIntent && ambiguousRegionSelection ? (
          <section className="connection-state panel" role="status">
            <span className="state-icon">i</span>
            <div>
              <h2>Regional selection is ambiguous</h2>
              <p>
                Multiple values were supplied for a required selection. No
                query was sent and the request was not converted into an
                attempt summary.
              </p>
              {dashboardHref ? (
                <a className="engineer-return-link" href={dashboardHref}>
                  Return to the selected comparison and region ↗
                </a>
              ) : null}
            </div>
          </section>
        ) : malformedAttempt ? (
          <section className="connection-state panel" role="status">
            <span className="state-icon">!</span>
            <div>
              <h2>Attempt identity unavailable</h2>
              <p>
                The requested attempt key is malformed or ambiguous. No query
                was sent and no replacement attempt was selected.
              </p>
            </div>
          </section>
        ) : unsupportedIntent || ambiguousIntent ? (
          <section className="connection-state panel" role="status">
            <span className="state-icon">!</span>
            <div>
              <h2>Engineer request not supported here</h2>
              <p>
                Choose a single attempt-summary action from an explicit attempt.
                No other engineer request was sent.
              </p>
              {dashboardHref ? (
                <a className="engineer-return-link" href={dashboardHref}>
                  Return to the dashboard selection ↗
                </a>
              ) : null}
            </div>
          </section>
        ) : (
          <EngineerQueryPanel
            report={report}
            requestState={requestState}
            intent={querySubmitted ? requestedIntent ?? "region_comparison" : null}
            sessionKey={regionIntent ? requestedSessionKey ?? null : null}
            targetAttemptKey={attemptKey}
            referenceAttemptKey={regionIntent ? requestedReferenceKey ?? null : null}
            referenceChoice={regionIntent ? requestedReferenceKey ?? null : null}
            comparisonPolicy={comparisonPolicy ?? "time_trial"}
            trackModelKey={regionIntent ? requestedModelKey ?? null : null}
            regions={regionIdentifier ? [{ identifier: regionIdentifier, label: regionIdentifier }] : []}
            targetHref={returnedTargetHref}
            referenceHref={returnedReferenceHref}
            comparisonHref={regionIntent ? dashboardHref : null}
            requestReason={requestReason}
            actionPath="/engineer"
            summaryOnly={!regionIntent}
            displayOnly={regionIntent}
            preservedFormEntries={preservedFormEntries}
          />
        )}
      </main>
      <footer className="footer-bar">
        <span>
          GP...T <b>·</b> LOCAL FIRST
        </span>
        <span>Recorded evidence · Diagnostic only</span>
      </footer>
    </div>
  );
}

function singleParam(value: string | string[] | undefined) {
  if (Array.isArray(value)) return value.length === 1 ? value[0] : undefined;
  return value;
}

function hasMultiple(value: string | string[] | undefined) {
  return Array.isArray(value) && value.length !== 1;
}

function parseSessionKey(value: string | undefined) {
  if (!value || value.length > 256) return null;
  const match = /^([a-f0-9]{64}):(\d{1,20})$/.exec(value);
  return match ? { runId: match[1], sessionUid: match[2] } : null;
}

function validAttemptKey(value: string | undefined): value is string {
  return Boolean(value && value.length <= 256 && attemptIdentity(value));
}

function sourceAttemptHref(report: EngineerQueryReport) {
  return sourceAttemptHrefForKey(report, report.selected.target_attempt_key);
}

function sourceAttemptHrefForKey(
  report: EngineerQueryReport,
  attemptKey: string | undefined,
) {
  if (!attemptKey) return null;
  const identity = attemptIdentity(attemptKey);
  if (!identity) return null;
  if (
    report.intent === "attempt_summary" &&
    (report.provenance.run_id !== identity.runId ||
      report.provenance.session_uid !== identity.sessionUid)
  ) {
    return null;
  }
  if (
    report.intent === "region_comparison" &&
    report.provenance.run_id !== identity.runId
  ) {
    return null;
  }
  const sessionKey = `${identity.runId}:${identity.sessionUid}`;
  return appScreenHref("dashboard", "", {
    session_key: sessionKey,
    target_attempt_key: identity.attemptKey,
  });
}
