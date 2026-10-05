import type { Comparison, LapRecord, ProcessingRunSummary } from "@/lib/api";
import { appScreenHref } from "@/lib/navigation";
import LinkedComparisonCharts from "./LinkedComparisonCharts";
import ComparisonWindowPanel from "./ComparisonWindowPanel";
import { buildComparisonCharts } from "./comparison-charts";
import ComparisonConditionsPanel from "./ComparisonConditionsPanel";
import PlayerParticipantContextPanel from "./PlayerParticipantContextPanel";
import PlayerCarSetupContextPanel from "./PlayerCarSetupContextPanel";

export default function CompareResultPanel({
  comparison,
  target,
  reference,
  window,
  preservedQuery,
}: {
  comparison: Comparison;
  target: LapRecord;
  reference: LapRecord;
  window: [number, number] | null;
  preservedQuery: string;
}) {
  const officialDifference = comparison.official_lap_time_difference_s;
  const observedDifference = comparison.observed_range_delta.observed_range_change_s;
  const unsupportedSpanCount =
    comparison.quality.target_excluded_spans.length +
    comparison.quality.reference_excluded_spans.length;
  const unsupportedSpans = [
    ...readExcludedSpans(comparison.quality.target_excluded_spans, "TARGET"),
    ...readExcludedSpans(comparison.quality.reference_excluded_spans, "REFERENCE"),
  ];
  return (
    <section className="compare-result-stack" aria-label="Selected pair comparison">
      <section className="result-heading">
        <div>
          <div className="eyebrow">
            {comparison.track.track_name} · {label(target.context?.session_type)}
          </div>
          <h2>
            Lap delta <span>across distance.</span>
          </h2>
          <p>
            Target attempt {target.attempt_number} − reference attempt {reference.attempt_number}
          </p>
        </div>
        <div className="result-distance">
          0 <i>—</i> {Math.round(comparison.track.track_length_m).toLocaleString()}
          <small>METRES</small>
        </div>
      </section>

      <section className="compare-pair-evidence">
        <AttemptEvidence
          label="TARGET"
          attempt={target}
          run={comparison.processing_run_evidence?.target ?? null}
        />
        <AttemptEvidence
          label="REFERENCE"
          attempt={reference}
          run={comparison.processing_run_evidence?.reference ?? null}
        />
      </section>

      <ComparisonConditionsPanel
        target={comparison.observed_conditions?.target}
        reference={comparison.observed_conditions?.reference}
      />
      <PlayerParticipantContextPanel
        target={target.player_participant_context}
        reference={reference.player_participant_context}
      />
      <PlayerCarSetupContextPanel
        target={target.player_car_setup_context}
        reference={reference.player_car_setup_context}
      />

      <section className="metric-row compare-metric-row">
        <Metric
          label="OFFICIAL LAP TIME"
          value={signedSeconds(officialDifference)}
          detail="Reported time · target minus reference"
          tone={officialDifference != null && officialDifference > 0 ? "warm" : "cool"}
        />
        <Metric
          label="OBSERVED-RANGE DELTA"
          value={signedSeconds(observedDifference)}
          detail={
            comparison.observed_range_delta.first_supported_distance_m == null
              ? "No shared supported span"
              : `${Math.round(comparison.observed_range_delta.first_supported_distance_m)}–${Math.round(comparison.observed_range_delta.last_supported_distance_m ?? 0)} m supported`
          }
          tone="neutral"
        />
        <Metric
          label="DELTA COVERAGE"
          value={percentage(comparison.quality.delta_time_coverage)}
          detail={`${comparison.analysis_version} · ${comparison.config.max_bracket_time_s} s maximum bracket`}
          tone="neutral"
        />
      </section>

      {window && comparison.comparison_window ? (
        <ComparisonWindowPanel
          comparison={comparison}
          report={comparison.comparison_window}
          brief={comparison.distance_window_brief ?? null}
          target={target}
          reference={reference}
          window={window}
        />
      ) : null}

      <section className="quality-row panel" aria-label="Reported lap and sector timing">
        <div className="quality-title">
          <span className="eyebrow">SESSION HISTORY</span>
          <strong>Reported timing</strong>
        </div>
        {(["sector1", "sector2", "sector3"] as const).map((sector, index) => {
          const item = comparison.sector_timing_difference_ms.sectors[sector];
          return (
            <div key={sector}>
              <span>SECTOR {index + 1} · TARGET − REFERENCE</span>
              <strong>
                {signedSeconds(
                  item?.target_minus_reference_ms == null
                    ? null
                    : item.target_minus_reference_ms / 1000,
                )}
              </strong>
              <small>
                {item?.target_valid === false || item?.reference_valid === false
                  ? "At least one reported sector is invalid"
                  : item?.status === "matched_values"
                    ? "Reported sector values"
                    : "Timing evidence unavailable"}
              </small>
            </div>
          );
        })}
      </section>

      <LinkedComparisonCharts
        key={JSON.stringify([
          target.attempt_key,
          reference.attempt_key,
          window,
        ])}
        distance={comparison.distance_m}
        charts={buildComparisonCharts(comparison, target, reference)}
        initialWindowM={window}
      />

      <section className="quality-row panel" aria-label="Comparison data quality">
        <div className="quality-title">
          <span className="eyebrow">DATA QUALITY</span>
          <strong>Coverage and gaps</strong>
        </div>
        <div>
          <span>TARGET SOURCE SAMPLES</span>
          <strong>{comparison.target_trace.source_sample_count.toLocaleString()}</strong>
        </div>
        <div>
          <span>REFERENCE SOURCE SAMPLES</span>
          <strong>{comparison.reference_trace.source_sample_count.toLocaleString()}</strong>
        </div>
        <div>
          <span>DELTA SUPPORTED</span>
          <strong>{percentage(comparison.quality.delta_time_coverage)}</strong>
        </div>
        <div>
          <span>UNSUPPORTED SPANS</span>
          <strong>{unsupportedSpanCount.toLocaleString()}</strong>
        </div>
      </section>
      {unsupportedSpanCount > 0 && (
        <details className="compare-unsupported-spans panel">
          <summary>Unsupported channel spans and reasons</summary>
          <ul>
            {unsupportedSpans.slice(0, 100).map((span, index) => (
                <li key={`${span.side}-${span.start_distance_m}-${span.end_distance_m}-${index}`}>
                  {span.side} · {span.channel ?? "channel unknown"} · {span.start_distance_m.toFixed(1)}–{span.end_distance_m.toFixed(1)} m · {span.reason.replaceAll("_", " ")}
                </li>
              ))}
          </ul>
          {unsupportedSpanCount > 100 && (
            <p>{(unsupportedSpanCount - 100).toLocaleString()} additional spans omitted from this list.</p>
          )}
        </details>
      )}
      <p className="chart-footnote">
        Positive lap delta means the target is slower. Lines stop where a channel
        is unsupported; missing telemetry is not interpolated across.
        {window ? ` The selected interval is ${window[0]}–${window[1]} m.` : ""}
      </p>

      <section className="comparison-brief panel" aria-label="Deterministic comparison brief">
        <div className="comparison-brief-heading">
          <div>
            <span className="eyebrow">DETERMINISTIC BRIEF</span>
            <h3>What this selected evidence says</h3>
          </div>
          <span className="brief-version">{comparison.comparison_brief.analysis_version}</span>
        </div>
        <p className="comparison-brief-text">{comparison.comparison_brief.text}</p>
        <p className="compare-report-scope">
          {comparison.diagnostic_only ? "Diagnostic comparison only." : "Comparison follows the selected policy."}
        </p>
        {comparison.policy_limitations.length > 0 && (
          <ul className="brief-limitations">
            {comparison.policy_limitations.map((limitation, index) => (
              <li key={`${index}-${limitation}`}>{limitation.replaceAll("_", " ")}</li>
            ))}
          </ul>
        )}
        <details className="comparison-brief-details">
          <summary>{comparison.comparison_brief.facts.length} supported facts · source fields</summary>
          {comparison.comparison_brief.facts.length ? (
            <ul>
              {comparison.comparison_brief.facts.map((fact, index) => (
                <li key={`${fact.kind}-${index}`}>
                  <strong>{fact.text}</strong>
                  <small>
                    Target {fact.provenance.target.attempt_key ?? "unknown"} · Reference {fact.provenance.reference.attempt_key ?? "unknown"}
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
          {comparison.comparison_brief.limitations.length > 0 && (
            <ul className="brief-limitations">
              {comparison.comparison_brief.limitations.map((limitation, index) => (
                <li key={`${index}-${limitation.code}`}>{limitation.text}</li>
              ))}
            </ul>
          )}
        </details>
      </section>

      <a
        className="compare-open-sessions"
        href={appScreenHref("sessions", preservedQuery, {
          run_id: target.run_id,
        }) ?? undefined}
      >
        Open selected target run in Sessions <span aria-hidden="true">↗</span>
      </a>
    </section>
  );
}

function AttemptEvidence({
  label: side,
  attempt,
  run,
}: {
  label: string;
  attempt: LapRecord;
  run: ProcessingRunSummary | null;
}) {
  return (
    <article className="compare-attempt-evidence panel">
      <div className="eyebrow">{side} · ATTEMPT {attempt.attempt_number}</div>
      <h3>{lapTime(attempt.lap_time_ms)}</h3>
      <code title={attempt.attempt_key}>{attempt.attempt_key}</code>
      <dl>
        <div><dt>Disposition</dt><dd>{label(attempt.disposition)}</dd></div>
        <div><dt>Game validity</dt><dd>{validity(attempt.game_valid)}</dd></div>
        <div><dt>Stored reference eligibility</dt><dd>{attempt.reference_eligible ? "Eligible" : "Excluded"}</dd></div>
        <div><dt>Lifecycle</dt><dd>{attempt.lifecycle_assessed ? "Assessed" : "Not assessed"}{attempt.superseded ? " · superseded" : ""}</dd></div>
        <div><dt>Stored samples</dt><dd>{attempt.sample_count.toLocaleString()}</dd></div>
      </dl>
      {attempt.exclusion_reasons.length > 0 && (
        <p className="compare-attempt-warning">{attempt.exclusion_reasons.map(label).join(" · ")}</p>
      )}
      {run && <CaptureFacts run={run} />}
    </article>
  );
}

function CaptureFacts({ run }: { run: ProcessingRunSummary }) {
  return (
    <div className="compare-capture-facts">
      <span>RUN {run.run_id.slice(0, 8)}</span>
      <span>CAPTURE {run.capture.complete === true ? "complete" : run.capture.complete === false ? "incomplete" : "unknown"}</span>
      <span>{run.capture.footer_status ?? "footer status unknown"}</span>
      <span>{count(run.capture.recording_counters?.queue_dropped)} recording queue drops</span>
      <span>{count(run.processing.replay_counters?.import_frame_overflow_packets_dropped)} replay frame-overflow drops</span>
      <span>{count(run.processing.replay_counters?.import_late_packets_ignored)} replay late packets ignored</span>
    </div>
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
    <article className={`metric-card panel metric-${tone}`}>
      <span>{title}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function signedSeconds(value: number | null | undefined) {
  if (value == null) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toFixed(3)} s`;
}

function percentage(value: number | null | undefined) {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function lapTime(ms: number | null) {
  return ms == null
    ? "No official time"
    : `${Math.floor(ms / 60_000)}:${((ms % 60_000) / 1000).toFixed(3).padStart(6, "0")}`;
}

function validity(value: boolean | null) {
  return value === true ? "Game valid" : value === false ? "Game invalid" : "Unknown";
}

function label(value: string | null | undefined) {
  return value ? value.replaceAll("_", " ").toUpperCase() : "UNKNOWN";
}

function count(value: number | null | undefined) {
  return value == null ? "Unknown" : value.toLocaleString();
}

function readExcludedSpans(values: unknown[], side: "TARGET" | "REFERENCE") {
  return values.flatMap((value) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return [];
    const span = value as Record<string, unknown>;
    if (
      typeof span.start_distance_m !== "number" ||
      !Number.isFinite(span.start_distance_m) ||
      typeof span.end_distance_m !== "number" ||
      !Number.isFinite(span.end_distance_m) ||
      typeof span.reason !== "string"
    ) {
      return [];
    }
    return [{
      side,
      start_distance_m: span.start_distance_m,
      end_distance_m: span.end_distance_m,
      reason: span.reason,
      channel: typeof span.channel === "string" ? span.channel : null,
    }];
  });
}
