import type {
  LapRecord,
  ObservationOnsetAttemptState,
  ObservationOnsetSpreadMetric,
  ObservationSetReport,
  ObservationThresholdEventSummary,
} from "@/lib/api";

export default function ObservationSetPanel({
  laps,
  report,
  unavailableReason,
  selectedAttemptKeys,
  sessionKey,
  targetAttemptKey,
  referenceChoice,
  comparisonPolicy,
  windowStartM,
  windowEndM,
  trackModelKey,
  positionProbeM,
}: {
  laps: LapRecord[];
  report: ObservationSetReport | null;
  unavailableReason: string | null;
  selectedAttemptKeys: string[];
  sessionKey: string | null;
  targetAttemptKey: string | null;
  referenceChoice: string | null;
  comparisonPolicy: "time_trial" | "practice_qualifying";
  windowStartM: string | null;
  windowEndM: string | null;
  trackModelKey: string | null;
  positionProbeM: string | null;
}) {
  const targetCarIndex = laps.find(
    (lap) => lap.attempt_key === targetAttemptKey,
  )?.car_index;
  const selectableAttempts = laps.filter(
    (lap) => targetCarIndex === undefined || lap.car_index === targetCarIndex,
  );
  const selected = new Set(selectedAttemptKeys);
  const selectionCount = selectedAttemptKeys.length;
  const selectedRangeValid = selectionCount >= 2 && selectionCount <= 8;
  const windowSelected = Boolean(windowStartM?.trim() && windowEndM?.trim());

  return (
    <section className="observation-set panel">
      <header className="observation-set-heading">
        <div>
          <div className="eyebrow">REPEATED WINDOW OBSERVATIONS</div>
          <h2>Inspect selected attempts together</h2>
          <p>
            Select 2–8 attempts and enter one numeric distance window above.
            Supported speed and brake values show their observed range; pedal
            onset evidence stays as per-attempt brackets, with a separate
            bracket-aware spread summary when enough attempts are supported.
          </p>
        </div>
        <span className="protocol-tag">DIAGNOSTIC ONLY</span>
      </header>

      <form className="observation-set-form" method="get" action="/">
        {sessionKey ? <input type="hidden" name="session_key" value={sessionKey} /> : null}
        {targetAttemptKey ? <input type="hidden" name="target_attempt_key" value={targetAttemptKey} /> : null}
        {referenceChoice ? <input type="hidden" name="reference_choice" value={referenceChoice} /> : null}
        <input type="hidden" name="comparison_policy" value={comparisonPolicy} />
        {windowStartM ? <input type="hidden" name="window_start_m" value={windowStartM} /> : null}
        {windowEndM ? <input type="hidden" name="window_end_m" value={windowEndM} /> : null}
        {trackModelKey ? <input type="hidden" name="track_model_key" value={trackModelKey} /> : null}
        {positionProbeM ? <input type="hidden" name="position_probe_m" value={positionProbeM} /> : null}

        <fieldset className="observation-attempt-picker">
          <legend>
            Attempts · {selectionCount} selected · must share run, session and player
          </legend>
          {selectableAttempts.length ? (
            selectableAttempts.map((lap) => {
              const checked = selected.has(lap.attempt_key);
              return (
                <label className="observation-attempt-option" key={lap.attempt_key}>
                  <input
                    type="checkbox"
                    name="observation_attempt_key"
                    value={lap.attempt_key}
                    defaultChecked={checked}
                  />
                  <span>
                    <strong>Attempt {lap.attempt_number}</strong>
                    <small>
                      {label(lap.disposition)} · {lapTime(lap.lap_time_ms)} · {validity(lap.game_valid)}
                    </small>
                  </span>
                </label>
              );
            })
          ) : (
            <p>No attempts are available for the selected player.</p>
          )}
        </fieldset>
        <button className="compare-button" type="submit">
          Load observation set <span>↗</span>
        </button>
      </form>

      {!selectedRangeValid && selectionCount > 0 ? (
        <p className="observation-set-note">Choose between 2 and 8 unique attempts.</p>
      ) : !windowSelected ? (
        <p className="observation-set-note">Enter both interval bounds in the distance comparison form first.</p>
      ) : null}

      {report ? <ObservationReport report={report} /> : null}
      {!report && selectedRangeValid && windowSelected ? (
        <p className="observation-set-unavailable">
          Observation set unavailable: {humanize(unavailableReason ?? "no_report_returned")}.
        </p>
      ) : null}
    </section>
  );
}

function ObservationReport({ report }: { report: ObservationSetReport }) {
  const speed = report.aggregates.minimum_speed;
  const brake = report.aggregates.peak_brake;
  const onsetRepeatability = report.onset_repeatability;
  const speedContributors = new Set(speed.contributors.map((item) => item.attempt_key));
  const brakeContributors = new Set(brake.contributors.map((item) => item.attempt_key));

  return (
    <div className="observation-set-report">
      <div className="observation-set-summary">
        <MetricRange title="Observed minimum speed" metric={speed} digits={1} />
        <MetricRange title="Peak recorded brake input" metric={brake} digits={1} />
      </div>
      {onsetRepeatability ? (
        <section className="observation-set-repeatability" aria-label="Onset bracket spread">
          <div>
            <div className="eyebrow">REPEATED ONSET EVIDENCE</div>
            <h3>Across-lap onset spread</h3>
          </div>
          <div className="observation-set-summary">
            <OnsetSpread
              title="10% brake onset"
              metric={onsetRepeatability.metrics.brake_10_percent}
            />
            <OnsetSpread
              title="50% throttle onset"
              metric={onsetRepeatability.metrics.throttle_50_percent}
            />
          </div>
          <p className="observation-set-onset-note">
            Each range shows the minimum to maximum possible spread across distinct attempts,
            calculated from sampled onset brackets. No bracket midpoint is treated as exact;
            this is not a consistency score.
          </p>
        </section>
      ) : null}
      {report.warnings.map((warning) => (
        <p className="observation-set-warning" key={warning.code}>{warning.text}</p>
      ))}
      <p className="observation-set-caveat">
        {report.track.track_name ?? "Unknown circuit"} · {report.window_m.start_m}–{report.window_m.end_m} m · half-open interval · run {report.scope.run_id.slice(0, 8)} · player {report.scope.car_index}.
        Invalid laps can show recorded values. Superseded, lifecycle-unassessed and incomplete attempts stay visible but do not contribute to ranges. This is not a consistency score or coaching recommendation.
      </p>
      <div className="observation-set-table-wrap">
        <table className="observation-set-table">
          <thead>
            <tr>
              <th>ATTEMPT</th>
              <th>MIN SPEED</th>
              <th>PEAK BRAKE</th>
              <th>10% BRAKE ONSET BRACKETS</th>
              <th>50% THROTTLE ONSET BRACKETS</th>
              <th>SUPPORT</th>
            </tr>
          </thead>
          <tbody>
            {report.attempts.map((attempt) => {
              const measurements = attempt.measurements;
              const minimum = measurements?.minimum_speed;
              const peakBrake = measurements?.peak_brake;
              const brakeEvents = measurements?.threshold_events.brake_10_percent;
              const throttleEvents = measurements?.threshold_events.throttle_50_percent;
              return (
                <tr key={attempt.attempt_key}>
                  <td>
                    <strong>Attempt {attempt.attempt_number}</strong>
                    <small>{label(attempt.disposition)} · {validity(attempt.game_valid)}</small>
                    {attempt.warnings.map((warning) => (
                      <small className="observation-warning-code" key={warning.code}>{warning.code.replaceAll("_", " ")}</small>
                    ))}
                  </td>
                  <td>
                    <strong>{minimum?.speed_kph === null || minimum?.speed_kph === undefined ? "—" : `${minimum.speed_kph.toFixed(1)} km/h`}</strong>
                    <small>{measurements ? `${percent(measurements.coverage.speed_mps)} support` : humanize(attempt.analysis_reason ?? "unavailable")}</small>
                  </td>
                  <td>
                    <strong>{peakBrake?.value === null || peakBrake?.value === undefined ? "—" : `${(peakBrake.value * 100).toFixed(1)}%`}</strong>
                    <small>{measurements ? `${percent(measurements.coverage.brake)} support` : humanize(attempt.analysis_reason ?? "unavailable")}</small>
                  </td>
                  <td><EventBrackets event={brakeEvents} /></td>
                  <td><EventBrackets event={throttleEvents} /></td>
                  <td>
                    <small>Speed: {speedContributors.has(attempt.attempt_key) ? "contributes" : metricExclusion(attempt, "minimum_speed")}</small>
                    <small>Brake: {brakeContributors.has(attempt.attempt_key) ? "contributes" : metricExclusion(attempt, "peak_brake")}</small>
                    <small>10% onset: {onsetStateText(attempt.onset_repeatability?.brake_10_percent)}</small>
                    <small>50% onset: {onsetStateText(attempt.onset_repeatability?.throttle_50_percent)}</small>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="observation-set-onset-note">
        Onset brackets remain tied to each attempt. The report does not average bracket midpoints or assign a precise shared onset.
      </p>
    </div>
  );
}

function OnsetSpread({
  title,
  metric,
}: {
  title: string;
  metric: ObservationOnsetSpreadMetric;
}) {
  const supported = metric.status === "supported";
  return (
    <article className="observation-metric">
      <span>{title}</span>
      {supported ? (
        <strong>
          {spreadBoundary(metric.minimum_possible_spread_m ?? 0, "lower")}–
          {spreadBoundary(metric.maximum_possible_spread_m ?? 0, "upper")} m
        </strong>
      ) : (
        <strong>Insufficient support</strong>
      )}
      <small>
        {metric.contributor_count} contributor{metric.contributor_count === 1 ? "" : "s"}
        {supported
          ? ` · ${metric.right_censored_contributor_count} right-censored continuation${metric.right_censored_contributor_count === 1 ? "" : "s"}`
          : ` · requires ${metric.required_contributor_count}`}
        {metric.excluded_attempt_count ? ` · ${metric.excluded_attempt_count} excluded` : ""}
      </small>
    </article>
  );
}

function MetricRange({
  title,
  metric,
  digits,
}: {
  title: string;
  metric: ObservationSetReport["aggregates"]["minimum_speed"];
  digits: number;
}) {
  const available = metric.status === "supported";
  const rangeUnit = metric.unit === "percentage_points" ? "percentage points" : metric.unit;
  const valueUnit = metric.unit === "percentage_points" ? "%" : metric.unit;
  return (
    <article className="observation-metric">
      <span>{title}</span>
      {available ? (
        <strong>{metric.minimum?.toFixed(digits)}–{metric.maximum?.toFixed(digits)} {valueUnit}</strong>
      ) : (
        <strong>Insufficient support</strong>
      )}
      <small>
        {metric.contributor_count} contributor{metric.contributor_count === 1 ? "" : "s"}
        {available ? ` · range ${metric.range?.toFixed(digits)} ${rangeUnit}` : ` · requires ${metric.required_contributor_count}`}
      </small>
    </article>
  );
}

function EventBrackets({
  event,
}: {
  event: ObservationThresholdEventSummary | undefined;
}) {
  if (!event || !Array.isArray(event.events) || event.events.length === 0) {
    return <small>{event ? humanize(event.status) : "unavailable"}</small>;
  }
  return (
    <div className="observation-event-list">
      {event.events.map((raw, index) => {
        const bracket = raw.start_distance_bracket_m;
        const low = Array.isArray(bracket) && typeof bracket[0] === "number" && Number.isFinite(bracket[0]) ? bracket[0] : null;
        const high = Array.isArray(bracket) && typeof bracket[1] === "number" && Number.isFinite(bracket[1]) ? bracket[1] : null;
        const displayedBracket =
          low !== null && high !== null
            ? `${outwardBound(low, "lower")}–${outwardBound(high, "upper")}`
            : null;
        const leftCensored = raw.left_censored === true;
        return (
          <small key={index}>
            {leftCensored ? "left-censored" : displayedBracket ? `[${displayedBracket}] m` : "onset bracket unavailable"}
            {raw.right_censored === true ? " · continues beyond support" : ""}
          </small>
        );
      })}
      {event.events_truncated ? <small>Additional events omitted</small> : null}
    </div>
  );
}

function metricExclusion(attempt: ObservationSetReport["attempts"][number], metric: "minimum_speed" | "peak_brake") {
  if (attempt.aggregate_exclusion_reasons.length) return attempt.aggregate_exclusion_reasons.map(humanize).join(", ");
  if (attempt.analysis_status !== "available") return humanize(attempt.analysis_reason ?? "unavailable");
  const channel = metric === "minimum_speed" ? "speed_mps" : "brake";
  const coverage = attempt.measurements?.coverage[channel];
  return coverage === 1 ? "measurement unavailable" : "incomplete support";
}

function onsetStateText(
  value: ObservationOnsetAttemptState | undefined,
) {
  if (!value) return "unavailable";
  if (value.status === "contributes") {
    return value.right_censored ? "contributes · continuation censored" : "contributes";
  }
  return value.reasons.length ? value.reasons.map(humanize).join(", ") : value.status;
}

function validity(value: boolean | null) {
  return value === true ? "game valid" : value === false ? "game invalid" : "validity unknown";
}

function lapTime(value: number | null) {
  if (value === null) return "time unavailable";
  const seconds = Math.floor(value / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}.${String(value % 1000).padStart(3, "0")}`;
}

function label(value: string) {
  return value.replaceAll("_", " ").toUpperCase();
}

function humanize(value: string) {
  return value.replaceAll("_", " ").replaceAll(":", " · ");
}

function percent(value: number | undefined) {
  return value === undefined ? "unknown" : `${(value * 100).toFixed(1)}%`;
}

function spreadBoundary(value: number, side: "lower" | "upper") {
  const scaled = value * 10;
  const tolerance = Number.EPSILON * Math.max(1, Math.abs(scaled)) * 4;
  const rounded = side === "lower"
    ? Math.floor(scaled + tolerance)
    : Math.ceil(scaled - tolerance);
  return (rounded / 10).toFixed(1);
}

function outwardBound(value: number, side: "lower" | "upper") {
  const scaled = value * 10;
  const rounded = side === "lower"
    ? Math.floor(scaled + 1e-8)
    : Math.ceil(scaled - 1e-8);
  return (rounded / 10).toFixed(1);
}
