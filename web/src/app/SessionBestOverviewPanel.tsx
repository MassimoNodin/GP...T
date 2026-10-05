import type { SessionBestOverview } from "@/lib/api";

function lapTime(value: number | null) {
  if (value === null || !Number.isFinite(value) || value <= 0)
    return "TIME N/A";
  const minutes = Math.floor(value / 60_000);
  const seconds = ((value % 60_000) / 1_000).toFixed(3).padStart(6, "0");
  return `${minutes}:${seconds}`;
}

function label(value: string) {
  return value.replaceAll("_", " ");
}

function bestState(status: string) {
  switch (status) {
    case "selected":
      return "VERIFIED TIME TRIAL BEST";
    case "unsupported_mode":
      return "RECORDED TIMES ONLY";
    case "unknown_mode":
    case "context_unavailable":
      return "TIME TRIAL CONTEXT UNAVAILABLE";
    case "assessment_limit_exceeded":
      return "ASSESSMENT ABSTAINED";
    case "anchor_unavailable":
      return "ANCHOR UNAVAILABLE";
    default:
      return "NO ELIGIBLE TIME TRIAL LAP";
  }
}

export default function SessionBestOverviewPanel({
  report,
  anchorSelected,
  unavailableReason,
}: {
  report: SessionBestOverview | null;
  anchorSelected: boolean;
  unavailableReason?: string | null;
}) {
  const best = report?.eligible_time_trial_best;
  const recorded = report?.recorded_time_ordering;

  return (
    <section
      className="session-best-overview panel"
      aria-label="Run and session best laps"
    >
      <div className="quality-panel-heading">
        <div>
          <span className="eyebrow">RUN / SESSION BEST</span>
          <h2>Recorded lap order</h2>
        </div>
        <span className="policy-state policy-manual">
          {recorded?.status === "available"
            ? "DIAGNOSTIC"
            : anchorSelected
              ? "UNAVAILABLE"
              : "SELECT ANCHOR"}
        </span>
      </div>

      <div className="session-best-summary">
        <div>
          <span>Fastest eligible Time Trial lap</span>
          {best?.attempt ? (
            <strong>
              {lapTime(best.attempt.lap_time_ms)} · attempt{" "}
              {best.attempt.attempt_number}
            </strong>
          ) : (
            <strong>
              {anchorSelected
                ? bestState(best?.status ?? "anchor_unavailable")
                : "SELECT AN ATTEMPT"}
            </strong>
          )}
        </div>
        <span className="quality-state">
          {best
            ? bestState(best.status)
            : anchorSelected
              ? bestState("anchor_unavailable")
              : "EXPLICIT ATTEMPT REQUIRED"}
        </span>
      </div>

      {unavailableReason || report?.reasons.length ? (
        <p className="session-best-warning" role="status">
          {unavailableReason ?? report?.reasons.map(label).join(" · ")}
        </p>
      ) : null}

      {report?.candidates.length ? (
        <ol className="session-best-list">
          {report.candidates.map((candidate) => (
            <li key={candidate.attempt_key}>
              <span className="session-best-rank">
                {candidate.recorded_time_rank === null
                  ? "—"
                  : `#${candidate.recorded_time_rank}`}
              </span>
              <span className="session-best-attempt">
                Attempt {candidate.attempt_number}
                {candidate.disposition !== "completed"
                  ? ` · ${label(candidate.disposition)}`
                  : ""}
              </span>
              <strong>{lapTime(candidate.lap_time_ms)}</strong>
              <span
                className={`session-best-eligibility session-best-${candidate.time_trial_eligibility}`}
              >
                {label(candidate.time_trial_eligibility)}
              </span>
              {candidate.exclusion_reasons.length ? (
                <small>
                  {candidate.exclusion_reasons.map(label).join(" · ")}
                </small>
              ) : null}
            </li>
          ))}
        </ol>
      ) : (
        <p className="session-best-empty">
          {anchorSelected
            ? "No recorded lap times are available in this scope."
            : "Select an attempt from the lap inventory to anchor this run and session overview."}
        </p>
      )}

      <p className="session-best-note">
        Recorded-time order is diagnostic and can include invalid laps. The Time
        Trial best is selected only after context, lifecycle, capture, and trace
        checks. This overview is not a comparison reference.
      </p>
      {recorded?.candidates_omitted_count ? (
        <small className="session-best-omitted">
          {recorded.candidates_omitted_count} more attempt(s) omitted by the
          display limit.
        </small>
      ) : null}
    </section>
  );
}
