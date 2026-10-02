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
            Motion missing {number(captureCounters.missing_player_motion_sample_count)} · telemetry packets {number(captureCounters.car_telemetry_packets_decoded)}
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
        Full-track support uses the track length reported in session context. Unobserved distance is unknown and does not establish packet loss.
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
