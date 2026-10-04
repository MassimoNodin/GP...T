import type { EngineerQueryReport } from "@/lib/api";
import RecordedEvidenceSpeech from "./RecordedEvidenceSpeech";
import { buildRecordedSpeechPlan } from "@/lib/recorded-speech-plan";

type RegionOption = { identifier: string; label: string };

type Props = {
  report: EngineerQueryReport | null;
  requestState: "not_requested" | "not_ready" | "ok" | "failed";
  intent: string | null;
  sessionKey: string | null;
  targetAttemptKey: string | null;
  referenceAttemptKey: string | null;
  referenceChoice: string | null;
  comparisonPolicy: "time_trial" | "practice_qualifying";
  trackModelKey: string | null;
  regions: RegionOption[];
  targetHref: string | null;
  referenceHref: string | null;
  comparisonHref: string | null;
  requestReason: string | null;
};

export default function EngineerQueryPanel({
  report,
  requestState,
  intent,
  sessionKey,
  targetAttemptKey,
  referenceAttemptKey,
  referenceChoice,
  comparisonPolicy,
  trackModelKey,
  regions,
  targetHref,
  referenceHref,
  comparisonHref,
  requestReason,
}: Props) {
  const submitted = intent === "attempt_summary" || intent === "region_comparison";
  const requestNotReady = submitted && requestState === "not_ready";
  const requestFailed = submitted && requestState === "failed";
  const evidenceUnavailable = report?.status === "unavailable";
  const hiddenSelection = (
    <>
      {sessionKey ? <input type="hidden" name="session_key" value={sessionKey} /> : null}
      {targetAttemptKey ? (
        <input type="hidden" name="target_attempt_key" value={targetAttemptKey} />
      ) : null}
      {referenceChoice ? (
        <input type="hidden" name="reference_choice" value={referenceChoice} />
      ) : null}
      {comparisonPolicy !== "time_trial" ? (
        <input type="hidden" name="comparison_policy" value={comparisonPolicy} />
      ) : null}
      {trackModelKey ? (
        <input type="hidden" name="track_model_key" value={trackModelKey} />
      ) : null}
    </>
  );

  return (
    <section className="panel engineer-query-panel" aria-labelledby="engineer-query-title">
      <header className="engineer-query-heading">
        <div>
          <div className="eyebrow">ENGINEER · RECORDED EVIDENCE</div>
          <h2 id="engineer-query-title">Ask about this evidence</h2>
          <p>Short, deterministic summaries from the selected attempt or region.</p>
        </div>
        <span className="diagnostic-tag">NO COACHING</span>
      </header>

      <div className="engineer-query-actions">
        {targetAttemptKey ? (
          <form action="/" method="get">
            {hiddenSelection}
            <input type="hidden" name="engineer_intent" value="attempt_summary" />
            <button className="button-secondary" type="submit">
              Summarize selected attempt
            </button>
          </form>
        ) : (
          <p className="engineer-query-hint">
            Select an attempt from the lap inventory to request its summary.
          </p>
        )}

        {targetAttemptKey && referenceAttemptKey && trackModelKey && regions.length > 0 ? (
          <form action="/" method="get" className="engineer-query-region-form">
            {hiddenSelection}
            <input type="hidden" name="engineer_intent" value="region_comparison" />
            <label>
              <span>Selected region</span>
              <select name="engineer_region_identifier" defaultValue={report?.selected.region_identifier ?? regions[0]?.identifier}>
                {regions.map((region) => (
                  <option key={region.identifier} value={region.identifier}>
                    {region.identifier} · {region.label}
                  </option>
                ))}
              </select>
            </label>
            <button className="button-secondary" type="submit">
              Explain region
            </button>
          </form>
        ) : null}
      </div>

      {submitted ? (
        requestNotReady ? (
          <div className="engineer-query-state is-unavailable" role="status">
            <strong>QUERY NOT RUN</strong>
            <p>
              {requestReason ??
                "The current selection does not include all evidence required for this query."}
            </p>
          </div>
        ) : requestFailed ? (
          <div className="engineer-query-state is-failed" role="status">
            <strong>QUERY REQUEST FAILED</strong>
            <p>
              {requestReason ??
                "The local API did not return the engineer query. Check that it is running, then retry."}
            </p>
          </div>
        ) : evidenceUnavailable ? (
          <div className="engineer-query-state is-unavailable" role="status">
            <strong>SELECTED EVIDENCE UNAVAILABLE</strong>
            <p>
              {report.reason_codes.length > 0
                ? report.reason_codes.map((reason) => reason.replaceAll("_", " ")).join(" · ")
                : "No evidence was returned for the selected request."}
            </p>
          </div>
        ) : report ? (
          <div className="engineer-query-result">
            <div className={`engineer-query-status is-${report.status}`}>
              {report.status === "partial" ? "PARTIAL EVIDENCE" : "EVIDENCE AVAILABLE"}
              <span>{report.analysis_version}</span>
            </div>
            <div className="engineer-query-sources">
              {targetHref ? <a href={targetHref}>Open target attempt</a> : null}
              {referenceHref ? <a href={referenceHref}>Open reference attempt</a> : null}
              {comparisonHref ? <a href={comparisonHref}>Return to selected pair and region list</a> : null}
              {typeof report.provenance.verification_scope === "string" ? (
                <span>Scope: {report.provenance.verification_scope.replaceAll("_", " ")}</span>
              ) : null}
            </div>
            {report.facts.length > 0 ? (
              <ul className="engineer-query-facts">
                {report.facts.map((fact, index) => (
                  <li key={`${fact.kind}-${index}`}>
                    <span>{fact.kind.replaceAll("_", " ")}</span>
                    <p>{fact.text}</p>
                  </li>
                ))}
              </ul>
            ) : null}
            {report.warnings.length > 0 ? (
              <ul className="engineer-query-warnings" aria-label="Evidence qualifications">
                {report.warnings.map((warning, index) => (
                  <li key={`${warning.code}-${index}`}>
                    <strong>{warning.sides?.length ? `${warning.sides.join(" / ")} · ` : ""}</strong>
                    {warning.text}
                  </li>
                ))}
              </ul>
            ) : null}
            {report.omitted_fact_count > 0 || report.omitted_warning_count > 0 ? (
              <p className="engineer-query-omissions">
                Omitted {report.omitted_fact_count} facts and {report.omitted_warning_count} qualifications to keep this summary bounded.
              </p>
            ) : null}
            <p className="engineer-query-footnote">
              Diagnostic evidence only. It does not rank laps or recommend driving changes.
            </p>
          </div>
        ) : (
          <div className="engineer-query-state is-failed" role="status">
            <strong>QUERY RESPONSE MISSING</strong>
            <p>The API returned no structured engineer evidence. Retry after the local API is available.</p>
          </div>
        )
      ) : (
        <p className="engineer-query-hint">
          {referenceAttemptKey && trackModelKey && regions.length === 0
            ? "A region explanation needs an available paired-region report and registered model."
            : "Choose an action to summarize the explicit attempt selection or explain one configured region."}
        </p>
      )}
      {submitted && report ? (
        <RecordedEvidenceSpeech
          planResult={buildRecordedSpeechPlan({
            kind: "engineer_query",
            report,
          })}
        />
      ) : null}
    </section>
  );
}
