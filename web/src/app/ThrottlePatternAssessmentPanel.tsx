import type {
  ThrottlePatternAssessment,
  ThrottlePatternAssessmentRegion,
} from "@/lib/api";

const numberText = (value: number, digits = 1) => value.toFixed(digits);

function signed(value: number, digits = 1) {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toFixed(digits)}`;
}

function bracket(value: [number, number]) {
  const scale = 10;
  const outward = (number: number, direction: "lower" | "upper") => {
    const scaled = number * scale;
    const tolerance = Number.EPSILON * Math.max(1, Math.abs(scaled)) * 4;
    const rounded = direction === "lower"
      ? Math.floor(scaled + tolerance)
      : Math.ceil(scaled - tolerance);
    return numberText(rounded / scale);
  };
  return `[${outward(value[0], "lower")}, ${outward(value[1], "upper")}] m`;
}

function statusText(status: ThrottlePatternAssessmentRegion["status"]) {
  if (status === "matched") return "Pattern matched";
  if (status === "contradicted") return "Pattern contradicted";
  return "Evidence unavailable";
}

function reasonText(reason: string) {
  return reason.replaceAll("_", " ");
}

export default function ThrottlePatternAssessmentPanel({
  report,
}: {
  report: ThrottlePatternAssessment | undefined;
}) {
  if (!report) return null;

  return (
    <section
      className="driving-pattern panel"
      aria-label="Experimental throttle and exit pattern assessment"
    >
      <header className="region-card-header">
        <div>
          <div className="region-index">THROTTLE / EXIT PATTERN CHECK</div>
          <h3>Throttle and exit assessment</h3>
          <p>
            Farther-along 50% throttle onset with lower configured exit speed.
            This describes measured co-occurrence; it does not establish a cause
            or recommend a driving change.
          </p>
        </div>
        <span className="brief-version">EXPERIMENTAL · COACHING DISABLED</span>
      </header>

      {report.status === "abstained" ? (
        <div className="driving-pattern-empty" role="status">
          <strong>Throttle assessment abstained.</strong>
          <ul>
            {report.gate_reasons.map((reason) => (
              <li key={reason}>{reasonText(reason)}</li>
            ))}
          </ul>
          {report.gate_reasons_omitted_count > 0 ? (
            <small>{report.gate_reasons_omitted_count} additional gate reasons omitted.</small>
          ) : null}
        </div>
      ) : report.status === "no_ranked_candidates" ? (
        <p className="driving-pattern-empty" role="status">
          No ranked region measurements are available for this rule.
        </p>
      ) : (
        <div className="driving-pattern-regions">
          {report.regions.map((region) => (
            <article key={region.region_id}>
              <div className="driving-pattern-region-heading">
                <strong>#{region.rank} {region.region_label}</strong>
                <span className={`pattern-state is-${region.status}`}>
                  {statusText(region.status)}
                </span>
              </div>
              <p className="driving-pattern-window">
                [{region.analysis_window_m[0].toFixed(1)}, {region.analysis_window_m[1].toFixed(1)}) m
              </p>
              <ul className="driving-pattern-evidence">
                {typeof region.evidence.recorded_interval_time_difference_s === "number" ? (
                  <li>
                    Interval time: {signed(region.evidence.recorded_interval_time_difference_s, 3)} s
                    target − reference
                  </li>
                ) : null}
                {region.evidence.throttle_onset ? (
                  <li>
                    Throttle onset ({region.evidence.throttle_onset.threshold_percent}%): target{" "}
                    {bracket(region.evidence.throttle_onset.target_bracket_m)} · reference{" "}
                    {bracket(region.evidence.throttle_onset.reference_bracket_m)} · difference bounds{" "}
                    {bracket(region.evidence.throttle_onset.target_minus_reference_bounds_m)}
                  </li>
                ) : null}
                {region.evidence.exit_speed ? (
                  <li>
                    Exit speed at {numberText(region.evidence.exit_speed.distance_m)} m:{" "}
                    {numberText(region.evidence.exit_speed.target_speed_kph)} vs{" "}
                    {numberText(region.evidence.exit_speed.reference_speed_kph)} km/h · Δ{" "}
                    {signed(region.evidence.exit_speed.target_minus_reference_kph)} km/h
                  </li>
                ) : null}
              </ul>
              {region.reasons.length ? (
                <ul className="driving-pattern-reasons">
                  {region.reasons.map((reason) => (
                    <li key={`${region.region_id}-${reason}`}>{reasonText(reason)}</li>
                  ))}
                </ul>
              ) : null}
            </article>
          ))}
        </div>
      )}

      <p className="driving-pattern-admission">
        Coaching admission: {report.coaching_admission.status.replaceAll("_", " ")} ·
        real eligible capture and rule validation required.
      </p>
    </section>
  );
}
