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
  actionPath?: string;
  summaryOnly?: boolean;
  displayOnly?: boolean;
  summaryScreenHref?: string | null;
  preservedFormEntries?: Array<[string, string]>;
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
  actionPath = "/",
  summaryOnly = false,
  displayOnly = false,
  summaryScreenHref = null,
  preservedFormEntries = [],
}: Props) {
  const submitted =
    intent === "attempt_summary" || intent === "region_comparison";
  const requestNotReady = submitted && requestState === "not_ready";
  const requestFailed = submitted && requestState === "failed";
  const evidenceUnavailable = report?.status === "unavailable";
  const hiddenSelection = (
    <>
      {sessionKey ? (
        <input type="hidden" name="session_key" value={sessionKey} />
      ) : null}
      {targetAttemptKey ? (
        <input
          type="hidden"
          name="target_attempt_key"
          value={targetAttemptKey}
        />
      ) : null}
      {referenceChoice ? (
        <input type="hidden" name="reference_choice" value={referenceChoice} />
      ) : null}
      {!summaryOnly ? (
        <input
          type="hidden"
          name="comparison_policy"
          value={comparisonPolicy}
        />
      ) : null}
      {trackModelKey ? (
        <input type="hidden" name="track_model_key" value={trackModelKey} />
      ) : null}
    </>
  );

  return (
    <section
      className="panel engineer-query-panel"
      aria-labelledby="engineer-query-title"
    >
      <header className="engineer-query-heading">
        <div>
          <div className="eyebrow">ENGINEER · RECORDED EVIDENCE</div>
          <h2 id="engineer-query-title">
            {displayOnly
              ? "Region comparison explanation"
              : summaryOnly
              ? "Recorded attempt summary"
              : "Ask about this evidence"}
          </h2>
          <p>
            {displayOnly
              ? "Deterministic facts from the explicitly selected target, reference, model revision, and distance region."
              : summaryOnly
              ? "Bounded facts from the selected attempt’s stored metadata, with source qualifications."
              : "Short, deterministic summaries from the selected attempt or region."}
          </p>
        </div>
        <span className="diagnostic-tag">NO COACHING</span>
      </header>

      <div className="engineer-query-actions">
        {!displayOnly && targetAttemptKey ? (
          <form action={actionPath} method="get">
            {hiddenSelection}
            {preservedFormEntries.map(([name, value], index) => (
              <input
                key={`${name}:${index}`}
                type="hidden"
                name={name}
                value={value}
              />
            ))}
            <input
              type="hidden"
              name="engineer_intent"
              value="attempt_summary"
            />
            <button className="button-secondary" type="submit">
              Summarize selected attempt
            </button>
          </form>
        ) : !displayOnly ? (
          <p className="engineer-query-hint">
            Select an attempt from the lap inventory to request its summary.
          </p>
        ) : null}

        {!displayOnly &&
        !summaryOnly &&
        targetAttemptKey &&
        referenceAttemptKey &&
        trackModelKey &&
        regions.length > 0 ? (
          <form
            action={actionPath}
            method="get"
            className="engineer-query-region-form"
          >
            {hiddenSelection}
            {preservedFormEntries.map(([name, value], index) => (
              <input
                key={`${name}:${index}`}
                type="hidden"
                name={name}
                value={value}
              />
            ))}
            <input
              type="hidden"
              name="engineer_intent"
              value="region_comparison"
            />
            <label>
              <span>Selected region</span>
              <select
                name="engineer_region_identifier"
                defaultValue={
                  report?.selected.region_identifier ?? ""
                }
                required
              >
                <option value="">Choose a region</option>
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
        {summaryScreenHref ? (
          <a
            className="button-secondary engineer-query-screen-link"
            href={summaryScreenHref}
          >
            Open Engineer summary ↗
          </a>
        ) : null}
      </div>

      {summaryOnly && targetAttemptKey && !report ? (
        <p className="engineer-query-requested-identity">
          Requested selection · not yet verified <code>{targetAttemptKey}</code>
        </p>
      ) : null}

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
                ? report.reason_codes
                    .map((reason) => reason.replaceAll("_", " "))
                    .join(" · ")
                : "No evidence was returned for the selected request."}
            </p>
            <EngineerQueryProvenance report={report} />
          </div>
        ) : report ? (
          <div className="engineer-query-result">
            <div className={`engineer-query-status is-${report.status}`}>
              {report.status === "partial"
                ? "PARTIAL EVIDENCE"
                : "EVIDENCE AVAILABLE"}
              <span>{report.analysis_version}</span>
            </div>
            <div className="engineer-query-sources">
              {targetHref ? <a href={targetHref}>Open target attempt</a> : null}
              {referenceHref ? (
                <a href={referenceHref}>Open reference attempt</a>
              ) : null}
              {comparisonHref ? (
                <a href={comparisonHref}>
                  Return to selected pair and region list
                </a>
              ) : null}
              {typeof report.provenance.verification_scope === "string" ? (
                <span>
                  Scope:{" "}
                  {report.provenance.verification_scope.replaceAll("_", " ")}
                </span>
              ) : null}
            </div>
            <EngineerQueryProvenance report={report} />
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
              <ul
                className="engineer-query-warnings"
                aria-label="Evidence qualifications"
              >
                {report.warnings.map((warning, index) => (
                  <li key={`${warning.code}-${index}`}>
                    <strong>
                      {warning.sides?.length
                        ? `${warning.sides.join(" / ")} · `
                        : ""}
                    </strong>
                    {warning.text}
                  </li>
                ))}
              </ul>
            ) : null}
            {report.omitted_fact_count > 0 ||
            report.omitted_warning_count > 0 ? (
              <p className="engineer-query-omissions">
                Omitted {report.omitted_fact_count} facts and{" "}
                {report.omitted_warning_count} qualifications to keep this
                summary bounded.
              </p>
            ) : null}
            <p className="engineer-query-footnote">
              Diagnostic evidence only. It does not rank laps or recommend
              driving changes.
            </p>
          </div>
        ) : (
          <div className="engineer-query-state is-failed" role="status">
            <strong>QUERY RESPONSE MISSING</strong>
            <p>
              The API returned no structured engineer evidence. Retry after the
              local API is available.
            </p>
          </div>
        )
      ) : (
        <p className="engineer-query-hint">
          {summaryOnly
            ? "Choose an attempt from Dashboard or Sessions, then request its recorded summary."
            : referenceAttemptKey && trackModelKey && regions.length === 0
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

function EngineerQueryProvenance({ report }: { report: EngineerQueryReport }) {
  const provenance = report.provenance;
  const capture = record(provenance.capture);
  const processing = record(provenance.processing);
  const trace = record(provenance.trace_metadata);
  const attempts = record(provenance.attempts);
  const targetTrace = record(attempts?.target);
  const referenceTrace = record(attempts?.reference);
  const model = record(provenance.model);
  const entries: Array<[string, string | null]> = [
    ["Returned attempt", report.selected.target_attempt_key],
    ["Run", stringValue(provenance.run_id)],
    ["Session UID", stringValue(provenance.session_uid)],
    ["Capture SHA-256", stringValue(capture?.sha256)],
    ["Capture size", numberValue(capture?.byte_size)],
    ["Capture completion", booleanValue(capture?.complete)],
    [
      "Processing",
      joinedValues(processing?.status, processing?.pipeline_version),
    ],
    [
      "Trace checksum",
      trace?.checksum_verified === true
        ? "verified"
        : trace
          ? "not verified"
          : null,
    ],
  ].filter((entry): entry is [string, string] => entry[1] !== null);

  if (report.intent === "region_comparison") {
    entries.push(
      ...[
        ["Selected reference", report.selected.reference_attempt_key ?? null],
        [
          "Comparison policy",
          report.selected.comparison_policy?.replaceAll("_", " ") ?? null,
        ],
        [
          "Track model revision",
          joinedValues(
            stringValue(report.selected.track_model_id),
            positiveIntegerText(report.selected.track_model_revision, "r"),
          ),
        ],
        ["Distance region", report.selected.region_identifier ?? null],
        ["Target trace SHA-256", stringValue(targetTrace?.trace_sha256)],
        [
          "Target trace schema",
          positiveIntegerText(targetTrace?.trace_schema_version, "v"),
        ],
        ["Reference trace SHA-256", stringValue(referenceTrace?.trace_sha256)],
        [
          "Reference trace schema",
          positiveIntegerText(referenceTrace?.trace_schema_version, "v"),
        ],
        ["Target capture SHA-256", stringValue(capture?.target_sha256)],
        ["Target capture complete", booleanValue(capture?.target_complete)],
        ["Reference capture SHA-256", stringValue(capture?.reference_sha256)],
        [
          "Reference capture complete",
          booleanValue(capture?.reference_complete),
        ],
        ["Model content SHA-256", stringValue(model?.content_sha256)],
        ["Model origin", stringValue(model?.origin)],
      ].filter((entry): entry is [string, string] => entry[1] !== null),
    );
  }

  if (entries.length === 0) return null;
  return (
    <dl
      className="engineer-query-provenance"
      aria-label="Returned evidence provenance"
    >
      {entries.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>
            <code>{value}</code>
          </dd>
        </div>
      ))}
    </dl>
  );
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function numberValue(value: unknown): string | null {
  return typeof value === "number" && Number.isFinite(value)
    ? `${value.toLocaleString()} bytes`
    : null;
}

function positiveIntegerText(value: unknown, prefix = ""): string | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0
    ? `${prefix}${value}`
    : null;
}

function booleanValue(value: unknown): string | null {
  return typeof value === "boolean"
    ? value
      ? "complete"
      : "incomplete"
    : null;
}

function joinedValues(...values: unknown[]): string | null {
  const parts = values.filter(
    (value): value is string => typeof value === "string" && value.length > 0,
  );
  return parts.length > 0 ? parts.join(" · ") : null;
}
