import type { AttemptQualityReport } from "@/lib/api";

const DISPLAY_CHANNELS = [
  ["time_s", "LAP CLOCK"],
  ["speed_mps", "SPEED"],
  ["throttle", "THROTTLE"],
  ["brake", "BRAKE"],
  ["steering", "STEERING"],
  ["gear", "GEAR"],
  ["drs_active", "DRS"],
] as const;

export default function AttemptQualityPanel({
  report,
  unavailableReason,
}: {
  report: AttemptQualityReport | null;
  unavailableReason: string | null;
}) {
  if (!report) {
    return (
      <section className="attempt-quality panel">
        <div className="quality-panel-heading">
          <div>
            <span className="eyebrow">ATTEMPT EVIDENCE</span>
            <h2>Telemetry quality</h2>
          </div>
          <span className="quality-state">UNAVAILABLE</span>
        </div>
        <p className="quality-unavailable">
          {unavailableReason ?? "The local API did not return quality evidence for this attempt."}
        </p>
      </section>
    );
  }

  const recording = report.evidence.recording;
  const recordingObserver = report.evidence.recording_observer;
  const captureCounters = report.evidence.capture_decode.capture_wide_counters;
  const assembly = report.evidence.replay_assembly.counters;
  const speed = report.distance_support.channels.speed_mps;
  const observedRange = report.distance_support.observed_distance_range_m;
  const status = report.observed_status;
  const validity =
    report.attempt.game_valid === true
      ? "GAME VALID"
      : report.attempt.game_valid === false
        ? "GAME INVALID"
        : "GAME UNKNOWN";

  return (
    <section className="attempt-quality panel">
      <div className="quality-panel-heading">
        <div>
          <span className="eyebrow">ATTEMPT EVIDENCE · REPORT V{report.report_version}</span>
          <h2>Telemetry quality</h2>
        </div>
        <span className="quality-state">{validity}</span>
      </div>
      <div className="quality-summary-grid">
        <div>
          <span>TRACE SAMPLES</span>
          <strong>{report.identity.trace_row_count.toLocaleString()}</strong>
        </div>
        <div>
          <span>OBSERVED DISTANCE</span>
          <strong>{range(observedRange)}</strong>
        </div>
        <div>
          <span>SPEED SUPPORT · OBSERVED RANGE</span>
          <strong>{percent(speed?.observed_range_coverage ?? null)}</strong>
        </div>
        <div>
          <span>SPEED SUPPORT · FULL TRACK</span>
          <strong>{percent(speed?.full_track_coverage ?? null)}</strong>
        </div>
      </div>
      <div className="quality-evidence-grid">
        <div>
          <span>RECORDING</span>
          <strong>
            {recording.footer_evidence_status === "invalid_shape"
              ? "FOOTER INVALID"
              : !recording.footer_available
                ? "FOOTER UNKNOWN"
                : recording.footer_status_evidence === "invalid"
                  ? "STATUS INVALID"
                  : recording.footer_status?.toUpperCase() ?? "STATUS UNAVAILABLE"}
          </strong>
          <small>
            Queue drops {number(recording.counters.queue_dropped)} · unpersisted {number(recording.counters.unpersisted_on_shutdown)} · socket errors {number(recording.counters.socket_errors)}
          </small>
        </div>
        <div>
          <span>RECORDING OBSERVER</span>
          <strong>
            {recordingObserver.available ? "CAPTURE COUNTERS RECORDED" : "SOME COUNTERS UNAVAILABLE"}
          </strong>
          <small>
            Late packets {number(recordingObserver.counters.late_packets_ignored)} · frame overflow {number(recordingObserver.counters.frame_overflow_packets_dropped)}
          </small>
        </div>
        <div>
          <span>REPLAY ASSEMBLY</span>
          <strong>
            {assembly.import_late_packets_ignored == null && assembly.import_frame_overflow_packets_dropped == null
              ? "COUNTERS UNAVAILABLE"
              : "COUNTERS RECORDED"}
          </strong>
          <small>
            Late packets {number(assembly.import_late_packets_ignored)} · frame overflow {number(assembly.import_frame_overflow_packets_dropped)}
          </small>
        </div>
        <div>
          <span>CAPTURE DECODE</span>
          <strong>
            {typeof captureCounters.missing_car_telemetry_lap_sample_count === "number"
              ? `${number(captureCounters.missing_car_telemetry_lap_sample_count)} TELEMETRY SAMPLES MISSING`
              : "COUNTERS UNAVAILABLE"}
          </strong>
          <small>
            Motion missing {number(captureCounters.missing_player_motion_sample_count)} · Status packets {number(captureCounters.car_status_packets_decoded)}
          </small>
        </div>
        <div>
          <span>CONTINUITY</span>
          <strong>
            {report.continuity.frame_gaps.count.toLocaleString()} frame gaps · {report.continuity.lap_clock.gap_count.toLocaleString()} clock gaps
          </strong>
          <small>
            {report.continuity.lap_distance.gap_count.toLocaleString()} distance gaps · {report.continuity.lap_distance.regression_count.toLocaleString()} distance regressions
          </small>
        </div>
      </div>
      <div className="quality-table-wrap attempt-channel-table">
        <table>
          <thead>
            <tr>
              <th>CHANNEL</th>
              <th>AVAILABLE SAMPLES</th>
              <th>OBSERVED RANGE</th>
              <th>FULL TRACK</th>
            </tr>
          </thead>
          <tbody>
            {DISPLAY_CHANNELS.map(([key, title]) => {
              const channel = report.channels[key];
              const support = report.distance_support.channels[key];
              return (
                <tr key={key}>
                  <td>{title}</td>
                  <td>
                    {channel?.status === "unavailable_in_trace_schema"
                      ? "NOT IN SCHEMA"
                      : `${number(channel?.available_samples ?? null)} / ${channel?.sample_count.toLocaleString() ?? "—"}`}
                  </td>
                  <td>{percent(support?.observed_range_coverage ?? null)}</td>
                  <td>{percent(support?.full_track_coverage ?? null)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <section className="quality-status-evidence">
        <div className="quality-panel-heading">
          <div>
            <span className="eyebrow">EXACT FRAME OBSERVATIONS</span>
            <h3>Car Status</h3>
          </div>
          <span className="quality-state">
            {status.status === "available" ? "DIAGNOSTIC" : "UNAVAILABLE"}
          </span>
        </div>
        {status.status !== "available" ? (
          <p className="quality-unavailable">
            Car Status evidence is {status.status.replaceAll("_", " ")} for this trace.
          </p>
        ) : (
          <>
            <p className="quality-status-summary">
              Matched {number(status.matched_sample_count)} / {status.sample_count.toLocaleString()} player samples
              {Object.keys(status.unavailable_reason_counts ?? {}).length > 0 && (
                <> · unavailable joins {Object.entries(status.unavailable_reason_counts ?? {})
                  .map(([reason, count]) => `${reason.replaceAll("_", " ")}: ${number(count)}`)
                  .join(" · ")}</>
              )}
            </p>
            <div className="quality-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>FIELD</th>
                    <th>VALID</th>
                    <th>MISSING</th>
                    <th>INVALID</th>
                    <th>FIRST OBSERVED</th>
                    <th>LAST OBSERVED</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(status.fields ?? {}).map(([field, counts]) => {
                    const observed = status.first_last_observed?.[field];
                    return (
                      <tr key={field}>
                        <td>{field.replaceAll("_", " ").toUpperCase()}</td>
                        <td>{number(counts.valid_count)}</td>
                        <td>{number(counts.missing_count)}</td>
                        <td>{number(counts.invalid_count)}</td>
                        <td>{observation(observed?.first ?? null)}</td>
                        <td>{observation(observed?.last ?? null)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="quality-status-summary">
              <strong>Observed compounds</strong>
              <span>{compoundLabels(status.distinct_compounds?.actual ?? [])}</span>
              <span>{compoundLabels(status.distinct_compounds?.visual ?? [])}</span>
            </div>
            <div className="quality-table-wrap">
              <table>
                <thead>
                  <tr><th>SAMPLED CHANGE</th><th>FROM FRAME</th><th>TO FRAME</th><th>VALUE</th></tr>
                </thead>
                <tbody>
                  {(status.discrete_changes ?? []).length === 0 ? (
                    <tr><td colSpan={4}>No discrete changes between adjacent matched samples.</td></tr>
                  ) : status.discrete_changes?.map((change, index) => (
                    <tr key={`${change.field}-${change.to_frame_identifier}-${index}`}>
                      <td>{change.field.replaceAll("_", " ").toUpperCase()}</td>
                      <td>{number(change.from_frame_identifier)}</td>
                      <td>{number(change.to_frame_identifier)}</td>
                      <td>{displayValue(change.from_value)} → {displayValue(change.to_value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {status.discrete_changes_truncated && (
              <p className="quality-status-summary">Showing the first 20 sampled changes.</p>
            )}
          </>
        )}
      </section>
      <div className="quality-provenance">
        <span>
          {report.attempt.disposition.toUpperCase()} · {report.context.game_modes.join(" / ") || "MODE UNKNOWN"}
        </span>
        <span>
          TRACE V{report.identity.trace_schema_version} · SHA-256 {report.identity.trace_sha256.slice(0, 12)}…
        </span>
        {report.channels.motion_available?.status === "unavailable_in_trace_schema" ? (
          <span>MOTION UNAVAILABLE IN TRACE SCHEMA V1</span>
        ) : (
          <span>
            MOTION SAMPLES {number(report.channels.motion_available?.available_samples)} / {report.channels.motion_available?.sample_count.toLocaleString() ?? "—"}
          </span>
        )}
      </div>
      <p className="quality-footnote">
        Full-track support uses the track length reported in session context. Unobserved distance is unknown and does not establish packet loss. Car Status is joined only to its exact frame; fuel values are {status.fuel_quantity_unit_note}, and the report does not infer fuel consumption or unobserved tyre changes.
      </p>
    </section>
  );
}

function percent(value: number | null): string {
  return value == null ? "Unavailable" : `${Math.round(value * 100)}%`;
}

function number(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString()
    : "Unavailable";
}

function range(value: [number, number] | null): string {
  return value == null ? "Unavailable" : `${Math.round(value[0])}–${Math.round(value[1])} m`;
}

function displayValue(value: unknown): string {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return Number.isFinite(value) ? value.toLocaleString() : "Unavailable";
  return typeof value === "string" ? value : "Unavailable";
}

function observation(value: { value: unknown; frame_identifier: number } | null): string {
  return value == null
    ? "Unavailable"
    : `${displayValue(value.value)} · frame ${number(value.frame_identifier)}`;
}

function compoundLabels(values: Array<{ raw_id: number; label: string | null }>): string {
  if (values.length === 0) return "No compound values observed";
  const names = values.map(({ raw_id, label }) => label ?? `unknown ID ${raw_id}`);
  return [...new Set(names)].join(" · ");
}
