import type {
  Comparison,
  ComparisonWindow,
  DistanceWindowBrief,
  LapRecord,
} from "@/lib/api";
import {
  distanceBracket,
  distanceWindowBriefMatches,
} from "@/lib/comparison-window-brief";

export default function ComparisonWindowPanel({
  comparison,
  report,
  brief,
  target,
  reference,
  window,
}: {
  comparison: Comparison;
  report: ComparisonWindow;
  brief: DistanceWindowBrief | null;
  target: LapRecord | null;
  reference: LapRecord | null;
  window: [number, number] | null;
}) {
  const briefAligned = brief && window
    ? distanceWindowBriefMatches(brief, comparison, target, reference, window)
    : false;

  return (
    <section className="comparison-window panel">
      <header className="comparison-window-heading">
        <div>
          <span className="eyebrow">SELECTED DISTANCE INTERVAL · DIAGNOSTIC</span>
          <h3>
            {report.window_m.start_m.toFixed(1)}–{report.window_m.end_m.toFixed(1)} m
          </h3>
        </div>
        <p>
          Numeric interval {report.interval_convention}; this is not a corner
          definition. Measurements retain source gaps and event censoring.
        </p>
      </header>
      {brief && briefAligned ? (
        <section
          className="window-measured-brief"
          aria-label="Deterministic distance-window brief"
        >
          <div className="window-measured-brief-heading">
            <span className="eyebrow">DETERMINISTIC MEASUREMENTS · DIAGNOSTIC ONLY</span>
            <span className="brief-version">
              {brief.status === "available" ? brief.analysis_version : "UNAVAILABLE"}
            </span>
          </div>
          <p>{brief.text}</p>
          <details className="comparison-brief-details">
            <summary>
              {brief.facts.length} supported facts · source and support details
            </summary>
            {brief.facts.length ? (
              <ul>
                {brief.facts.map((fact, index) => (
                  <li key={`${fact.kind}-${index}`}>
                    <strong>{fact.text}</strong>
                    <small>{Object.values(fact.source_fields).join(" / ")}</small>
                  </li>
                ))}
              </ul>
            ) : null}
            {brief.limitations.length ? (
              <ul className="brief-limitations">
                {brief.limitations.map((item) => (
                  <li key={item.code}>{item.text}</li>
                ))}
              </ul>
            ) : null}
            {brief.warnings.length ? (
              <ul className="brief-limitations">
                {brief.warnings.map((item) => (
                  <li key={`${item.code}-${item.text}`}>{item.text}</li>
                ))}
              </ul>
            ) : null}
            <small className="window-brief-provenance">
              Target {brief.provenance.target.attempt_key ?? "unknown"} · trace{" "}
              {brief.provenance.target.trace_sha256 ?? "unknown"}
              {" · "}Reference {brief.provenance.reference.attempt_key ?? "unknown"} ·
              trace {brief.provenance.reference.trace_sha256 ?? "unknown"}
            </small>
          </details>
        </section>
      ) : (
        <p className="window-brief-unavailable" role="status">
          The deterministic window brief {brief ? "does not match this exact pair and interval" : "is not present in this response"}.
          It is unavailable; the selected-window measurements below remain available.
        </p>
      )}
      <div className="window-source-grid">
        <ComparisonWindowSource label="TARGET" summary={report.target} />
        <ComparisonWindowSource label="REFERENCE" summary={report.reference} />
      </div>
      <div className="window-delta-summary">
        <div>
          <span className="condition-label">BOUNDARY DELTA · TARGET − REFERENCE</span>
          <strong>
            Start {seconds(report.delta.start_boundary.target_minus_reference_s)}
            <i> → </i>
            End {seconds(report.delta.end_boundary.target_minus_reference_s)}
          </strong>
          <small>
            Target time {percent(report.delta.target_time_coverage)} · Reference time {percent(report.delta.reference_time_coverage)} · Shared {percent(report.delta.shared_time_coverage)}
          </small>
        </div>
        <div>
          <span className="condition-label">INTERVAL DELTA CHANGE</span>
          <strong>{seconds(report.delta.delta_change_s)}</strong>
          <small>
            {report.delta.status === "supported"
              ? "Connected time evidence supports both boundaries and the interval."
              : report.delta.unavailable_reason === "unsupported_boundary_evidence"
                ? "A selected boundary has no supported shared lap-clock delta."
                : !report.delta.target_source_session_time_connected ||
                    !report.delta.reference_source_session_time_connected
                  ? "A raw source session-time gap or rewind prevents a connected interval delta."
                  : "An unsupported or disconnected interior prevents this interval delta."}
          </small>
        </div>
      </div>
    </section>
  );
}

function ComparisonWindowSource({
  label: sourceLabel,
  summary,
}: {
  label: string;
  summary: ComparisonWindow["target"];
}) {
  const eventLabels: Record<string, string> = {
    brake_10_percent: "Brake ≥ 10%",
    steering_absolute_15_percent: "Absolute steering ≥ 15%",
    throttle_10_percent: "Throttle ≥ 10%",
    throttle_50_percent: "Throttle ≥ 50%",
    throttle_90_percent: "Throttle ≥ 90%",
    throttle_99_percent: "Throttle ≥ 99%",
  };
  return (
    <article className="window-source">
      <div className="window-source-heading">
        <span className="eyebrow">{sourceLabel}</span>
        <small>
          {summary.source_sample_count.toLocaleString()} source samples · {summary.excluded_spans.count} excluded spans
          {summary.excluded_spans.truncated ? " · examples capped" : ""}
        </small>
      </div>
      <div className="window-observation-grid">
        <div>
          <span>MINIMUM SPEED</span>
          <strong>
            {summary.minimum_speed.speed_kph == null
              ? "Unavailable"
              : `${summary.minimum_speed.speed_kph.toFixed(1)} km/h`}
          </strong>
          {summary.minimum_speed.anchor && (
            <small>{windowAnchor(summary.minimum_speed.anchor)}</small>
          )}
        </div>
        <div>
          <span>PEAK BRAKE</span>
          <strong>
            {summary.peak_brake.value == null
              ? "Unavailable"
              : percent(summary.peak_brake.value)}
          </strong>
          {summary.peak_brake.anchor && (
            <small>{windowAnchor(summary.peak_brake.anchor)}</small>
          )}
        </div>
      </div>
      <div className="window-coverage-grid">
        {(["speed_mps", "brake", "throttle", "steering"] as const).map(
          (channel) => (
            <div key={channel}>
              <span>{label(channel)}</span>
              <strong>{percent(summary.coverage[channel])}</strong>
            </div>
          ),
        )}
      </div>
      <div className="window-events">
        <span className="condition-label">SUSTAINED THRESHOLD EPISODES</span>
        {Object.entries(summary.threshold_events).map(([key, evidence]) => {
          const first = evidence.events[0];
          return (
            <div className="window-event-row" key={key}>
              <span>{eventLabels[key] ?? label(key)}</span>
              <strong>{evidence.event_count}</strong>
              <small>
                {first
                  ? `First onset ${distanceBracket(first.start_distance_m, first.start_distance_bracket_m)} · end ${distanceBracket(first.end_distance_m, first.end_distance_bracket_m)} · ${first.duration_s.toFixed(2)} s${first.left_censored ? " · left-censored" : ""}${first.right_censored ? " · right-censored" : ""}`
                  : label(evidence.status)}
                {evidence.events_truncated ? " · examples capped" : ""}
                {` · ${evidence.left_censored_event_count} left / ${evidence.right_censored_event_count} right censored`}
                {` · ${evidence.unsupported_break_count} unsupported breaks · ${evidence.rejected_short_event_count} short rejected`}
              </small>
            </div>
          );
        })}
      </div>
      {summary.excluded_spans.examples.length > 0 && (
        <details className="window-excluded-details">
          <summary>
            Excluded source spans · showing {summary.excluded_spans.examples.length}
          </summary>
          <ul>
            {summary.excluded_spans.examples.map((span, index) => (
              <li key={`${span.reason}:${span.start_distance_m}:${index}`}>
                {span.start_distance_m.toFixed(1)}–{span.end_distance_m.toFixed(1)} m · {span.reason} · {span.channel ?? "all channels"}
              </li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}

function windowAnchor(anchor: {
  frame_identifier: number;
  session_time_s: number | null;
  lap_distance_m: number;
}) {
  return [
    `frame ${anchor.frame_identifier}`,
    anchor.session_time_s == null ? null : `${anchor.session_time_s.toFixed(3)} s`,
    `${anchor.lap_distance_m.toFixed(1)} m`,
  ]
    .filter((part): part is string => part !== null)
    .join(" · ");
}

function label(value: string) {
  return value.replaceAll("_", " ").toUpperCase();
}

function seconds(value: number | null | undefined) {
  return value == null
    ? "—"
    : `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(3)} s`;
}

function percent(value: number | null | undefined) {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}
