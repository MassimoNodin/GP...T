import type { EngineerQueryReport } from "@/lib/api";
import { requestApiPost } from "@/lib/api";
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
  const ambiguousAttempt =
    Array.isArray(params.target_attempt_key) &&
    params.target_attempt_key.length !== 1;
  const ambiguousIntent =
    Array.isArray(params.engineer_intent) &&
    params.engineer_intent.length !== 1;
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
  const canRequestSummary = Boolean(
    !selectionTransferBlocked &&
    !malformedAttempt &&
    !ambiguousIntent &&
    attemptKey &&
    requestedIntent === "attempt_summary",
  );
  const reportResponse = canRequestSummary
    ? await requestApiPost<EngineerQueryReport>("/api/v1/engineer/query", {
        intent: "attempt_summary",
        target_attempt_key: attemptKey,
      })
    : null;
  const candidateReport =
    reportResponse?.status === "ok" ? reportResponse.data : null;
  const report =
    candidateReport?.intent === "attempt_summary" &&
    candidateReport?.selected?.target_attempt_key === attemptKey
      ? candidateReport
      : null;
  const requestState =
    requestedIntent !== "attempt_summary"
      ? "not_requested"
      : selectionTransferBlocked ||
          malformedAttempt ||
          ambiguousIntent ||
          !attemptKey
        ? "not_ready"
        : report
          ? "ok"
          : "failed";
  const requestReason =
    requestState === "not_ready"
      ? selectionTransferBlocked
        ? "The selection was too large to carry safely. No attempt was requested. Choose the attempt again from its source."
        : ambiguousIntent
          ? "The request contains multiple engineer intents. Choose one action from the selected attempt again."
          : malformedAttempt
            ? "The requested attempt identity is malformed. No attempt was requested; choose it again from the inventory."
            : "Select an explicit attempt before requesting a summary."
      : (reportResponse?.reason ??
        (candidateReport && !report
          ? "The API response did not match the requested attempt identity. No substitute evidence was shown."
          : null));
  const returnedTargetHref = report ? sourceAttemptHref(report) : null;
  const dashboardHref = selectionTransferBlocked
    ? null
    : appScreenHref("dashboard", preservedQuery);
  const preservedFormEntries = selectionTransferBlocked
    ? []
    : Array.from(new URLSearchParams(preservedQuery).entries()).filter(
        ([key]) => key !== "target_attempt_key" && key !== "engineer_intent",
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
            One attempt.
            <br />
            <span>Evidence in context.</span>
          </h1>
          <p>
            GP...T builds a short, deterministic summary from stored attempt
            metadata. It does not read trace samples, generate advice, or decide
            whether the attempt is eligible for comparison or coaching.
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
        ) : regionIntent ? (
          <section className="connection-state panel" role="status">
            <span className="state-icon">i</span>
            <div>
              <h2>Regional explanations stay on the Dashboard</h2>
              <p>
                This screen only summarizes one recorded attempt. No request was
                sent and the regional selection was not converted into an
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
            intent={
              requestedIntent === "attempt_summary" ? requestedIntent : null
            }
            sessionKey={null}
            targetAttemptKey={attemptKey}
            referenceAttemptKey={null}
            referenceChoice={null}
            comparisonPolicy="time_trial"
            trackModelKey={null}
            regions={[]}
            targetHref={returnedTargetHref}
            referenceHref={null}
            comparisonHref={null}
            requestReason={requestReason}
            actionPath="/engineer"
            summaryOnly
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

function validAttemptKey(value: string | undefined): value is string {
  if (!value || value.length > 256) return false;
  const match = /^([a-f0-9]{64}):(\d{1,20}):(\d{1,2}):(\d{1,10})$/.exec(value);
  if (!match) return false;
  const carIndex = Number(match[3]);
  const ordinal = Number(match[4]);
  return (
    Number.isSafeInteger(carIndex) &&
    carIndex <= 23 &&
    Number.isSafeInteger(ordinal) &&
    ordinal > 0
  );
}

function sourceAttemptHref(report: EngineerQueryReport) {
  const attemptKey = report.selected.target_attempt_key;
  const match = validAttemptKey(attemptKey)
    ? /^([a-f0-9]{64}):(\d{1,20}):/.exec(attemptKey)
    : null;
  if (!match) return null;
  const runId = report.provenance.run_id;
  const sessionUid = report.provenance.session_uid;
  if (runId !== match[1] || sessionUid !== match[2]) return null;
  const sessionKey = `${runId}:${sessionUid}`;
  return appScreenHref("dashboard", "", {
    session_key: sessionKey,
    target_attempt_key: attemptKey,
  });
}
