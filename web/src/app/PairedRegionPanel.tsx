import type { PairedRegionDifference, PairedRegionReport } from "@/lib/api";

type Props = {
  report: PairedRegionReport | null;
  unavailableReason: string | null;
  selectionReady: boolean;
  navigation: {
    sessionKey: string | null;
    runId: string | null;
    runOffset: string | null;
    sessionOffset: string | null;
    attemptOffset: string | null;
    lifecycleEventOffset: string | null;
    observationAttemptKeys: string[];
    positionProbeM: string | null;
  };
};

const numberText = (value: number | null | undefined, digits = 2) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(digits)
    : "—";

const distanceRange = (value: [number, number] | undefined) =>
  value ? `${numberText(value[0], 1)}–${numberText(value[1], 1)} m` : "—";

const distanceBracket = (value: [number, number] | undefined) => {
  if (!value) return "—";
  const scale = 10;
  const outward = (number: number, direction: "lower" | "upper") => {
    const scaled = number * scale;
    const tolerance = Number.EPSILON * Math.max(1, Math.abs(scaled)) * 4;
    const rounded = direction === "lower"
      ? Math.floor(scaled + tolerance)
      : Math.ceil(scaled - tolerance);
    return numberText(rounded / scale, 1);
  };
  return `${outward(value[0], "lower")}–${outward(value[1], "upper")} m`;
};

function DifferenceValue({
  difference,
  metric,
}: {
  difference: PairedRegionDifference | undefined;
  metric: string;
}) {
  if (!difference || difference.status !== "supported") {
    const reason = difference?.unavailable_reason?.replaceAll("_", " ") ?? "unavailable";
    return <span className="paired-region-unsupported">{reason}</span>;
  }

  if (difference.target_start_bracket_m && difference.reference_start_bracket_m) {
    return (
      <>
        <strong>
          {distanceBracket(difference.target_start_bracket_m)} target · {distanceBracket(difference.reference_start_bracket_m)} reference
        </strong>
        <small>
          Target − reference: {distanceBracket(difference.target_minus_reference_start_bracket_m)}
          {difference.right_censored?.target || difference.right_censored?.reference
            ? " · an event continued to observed support end"
            : ""}
        </small>
      </>
    );
  }

  if (metric === "connected_interval_time") {
    return (
      <strong>
        {numberText(difference.value, 3)} {difference.unit ?? "s"} target − reference
      </strong>
    );
  }

  return (
    <>
      <strong>
        {numberText(difference.value, 1)} {difference.unit ?? ""} target − reference
      </strong>
      {typeof difference.target_value === "number" &&
      typeof difference.reference_value === "number" ? (
        <small>
          {numberText(difference.target_value, 1)} target · {numberText(difference.reference_value, 1)} reference
        </small>
      ) : null}
    </>
  );
}

export default function PairedRegionPanel({
  report,
  unavailableReason,
  selectionReady,
  navigation,
}: Props) {
  if (!selectionReady) {
    return (
      <section className="panel paired-region-panel">
        <div className="eyebrow">PAIRED DISTANCE REGIONS</div>
        <h2>Compare two selected attempts across the same windows</h2>
        <p>
          Choose a reference attempt and a distance-region model above. This report
          keeps each measurement diagnostic and shows unsupported evidence per region.
        </p>
      </section>
    );
  }

  if (!report) {
    return (
      <section className="panel paired-region-panel">
        <div className="eyebrow">PAIRED DISTANCE REGIONS UNAVAILABLE</div>
        <h2>The selected pair could not be compared across these windows.</h2>
        <p>
          {unavailableReason?.replaceAll("_", " ") ?? "The local analysis API did not return a report."}
          {" "}Ordinary lap comparison and single-attempt inspection remain available.
        </p>
      </section>
    );
  }

  const sideWarnings = (side: "target" | "reference") => report.warnings[side] ?? [];
  const modelOrigin = report.model.origin === "local_draft" ? "local draft" : "packaged model";

  return (
    <section className="panel paired-region-panel">
      <header className="paired-region-heading">
        <div>
          <div className="eyebrow">PAIRED DISTANCE REGIONS</div>
          <h2>Same windows, both attempts</h2>
          <p>
            {report.model.track_name} · {report.model.model_id} r{report.model.revision} · {modelOrigin} · {report.comparison_policy.replaceAll("_", " / ")}
          </p>
        </div>
        <span className="diagnostic-tag">DIAGNOSTIC ONLY</span>
      </header>

      <div className="paired-region-provenance">
        <span>Target {report.attempts.target.attempt_key.slice(0, 12)} · {report.attempts.target.trace_sha256.slice(0, 12)}</span>
        <span>Reference {report.attempts.reference.attempt_key.slice(0, 12)} · {report.attempts.reference.trace_sha256.slice(0, 12)}</span>
        <span>Layout identity: caller declared</span>
      </div>

      {sideWarnings("target").length || sideWarnings("reference").length ? (
        <div className="paired-region-warnings" aria-label="Attempt evidence warnings">
          {(["target", "reference"] as const).flatMap((side) =>
            sideWarnings(side).map((warning) => (
              <p key={`${side}:${warning.code}`}>
                <b>{side}</b> · {warning.text}
              </p>
            )),
          )}
        </div>
      ) : null}

      <div className="paired-region-list">
        {report.regions.map((region) => (
          <article className="paired-region-card" key={region.identifier}>
            <header>
              <div>
                <div className="eyebrow">DISTANCE REGION · {region.identifier}</div>
                <h3>{region.label}</h3>
              </div>
              <span>{numberText(region.analysis_window_m[0], 0)}–{numberText(region.analysis_window_m[1], 0)} m</span>
            </header>
            {region.debrief ? (
              <section className="paired-region-debrief" aria-label="Measured diagnostic debrief">
                <strong>Measured debrief</strong>
                {region.debrief.facts.length ? (
                  <ul>
                    {region.debrief.facts.map((fact) => (
                      <li key={fact.kind}>{fact.text}</li>
                    ))}
                  </ul>
                ) : <p>No supported debrief facts for this region.</p>}
                {region.debrief.omissions.length ? (
                  <details>
                    <summary>Omitted evidence ({region.debrief.omissions.length})</summary>
                    <ul>
                      {region.debrief.omissions.map((omission) => (
                        <li key={omission.kind}>{omission.text}</li>
                      ))}
                    </ul>
                  </details>
                ) : null}
              </section>
            ) : null}
            <a
              className="compare-button paired-region-inspect-link"
              href={pairedRegionInspectionHref(
                report,
                region.analysis_window_m,
                navigation,
              )}
              aria-label={`Inspect ${region.label} comparison traces from ${region.analysis_window_m[0]} to ${region.analysis_window_m[1]} metres`}
            >
              Inspect interval traces <span aria-hidden="true">↗</span>
            </a>
            <p className="paired-region-inspect-note">
              Opens the comparison charts for this exact distance interval.
              Configured event search bounds remain separate.
            </p>
            <div className="paired-region-facts">
              {[
                ["connected_interval_time", "Connected time change"],
                ["minimum_speed", "Observed minimum speed"],
                ["brake_10_percent_onset", "10% brake onset"],
                ["throttle_50_percent_onset", "50% throttle onset"],
                ["exit_speed", "Speed at configured exit"],
              ].map(([key, label]) => (
                <div className="paired-region-fact" key={key}>
                  <span>{label}</span>
                  <DifferenceValue
                    metric={key}
                    difference={region.supported_differences[key]}
                  />
                </div>
              ))}
            </div>
            <details>
              <summary>Coverage and event evidence</summary>
              <div className="paired-region-coverage">
                {(["target", "reference"] as const).map((side) => (
                  <div key={side}>
                    <strong>{side}</strong>
                    <small>
                      Brake {numberText(region[side].event_channel_coverage.brake * 100, 0)}% ·
                      steering {numberText(region[side].event_channel_coverage.steering * 100, 0)}% ·
                      throttle {numberText(region[side].event_channel_coverage.throttle * 100, 0)}%
                    </small>
                  </div>
                ))}
              </div>
              <p>
                Brake search: {region.configured_windows_m.braking_search
                  ? distanceRange(region.configured_windows_m.braking_search)
                  : "unconfigured"}
                {" · "}Throttle search: {region.configured_windows_m.throttle_pickup_search
                  ? distanceRange(region.configured_windows_m.throttle_pickup_search)
                  : "unconfigured"}
              </p>
              <p>
                Entry delta {numberText(region.delta_change.entry_delta_s, 3)} s · exit delta {numberText(region.delta_change.exit_delta_s, 3)} s · interval {region.delta_change.interval_connected_supported_time ? "connected" : "unsupported"}
              </p>
            </details>
          </article>
        ))}
      </div>
      <p className="paired-region-footer">
        These are configured distance windows. The report does not identify official corner names, rank losses, or provide coaching.
      </p>
    </section>
  );
}

function pairedRegionInspectionHref(
  report: PairedRegionReport,
  bounds: [number, number],
  navigation: Props["navigation"],
) {
  const query = new URLSearchParams();
  if (navigation.sessionKey) query.set("session_key", navigation.sessionKey);
  query.set("target_attempt_key", report.attempts.target.attempt_key);
  query.set("reference_choice", report.attempts.reference.attempt_key);
  query.set("comparison_policy", report.comparison_policy);
  query.set(
    "track_model_key",
    `${report.model.model_id}@${report.model.revision}`,
  );
  query.set("window_start_m", String(bounds[0]));
  query.set("window_end_m", String(bounds[1]));
  if (navigation.runId) query.set("run_id", navigation.runId);
  if (navigation.runOffset) query.set("run_offset", navigation.runOffset);
  if (navigation.sessionOffset) {
    query.set("session_offset", navigation.sessionOffset);
  }
  if (navigation.attemptOffset) {
    query.set("attempt_offset", navigation.attemptOffset);
  }
  if (navigation.lifecycleEventOffset) {
    query.set("lifecycle_event_offset", navigation.lifecycleEventOffset);
  }
  if (navigation.positionProbeM) {
    query.set("position_probe_m", navigation.positionProbeM);
  }
  for (const attemptKey of navigation.observationAttemptKeys) {
    query.append("observation_attempt_key", attemptKey);
  }
  return `/?${query.toString()}#comparison-charts`;
}
