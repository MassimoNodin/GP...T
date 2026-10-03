import type { ReactNode } from "react";
import type {
  ProcessingRunAttempt,
  ProcessingRunDetail,
  ProcessingRunPage,
  ProcessingRunLifecycleEvent,
  ProcessingRunSession,
  ProcessingRunSummary,
} from "@/lib/api";

export default function RunEvidencePanel({
  runsPage,
  detail,
  runId,
  runOffset,
  sessionOffset,
  attemptOffset,
  lifecycleEventOffset,
}: {
  runsPage: ProcessingRunPage<ProcessingRunSummary> | null;
  detail: ProcessingRunDetail | null;
  runId: string | null;
  runOffset: number;
  sessionOffset: number;
  attemptOffset: number;
  lifecycleEventOffset: number;
}) {
  return (
    <section className="run-evidence panel" aria-labelledby="run-evidence-title">
      <div className="run-evidence-heading">
        <div>
          <span className="eyebrow">IMPORTED CAPTURE EVIDENCE</span>
          <h2 id="run-evidence-title">Run summaries</h2>
          <p>
            Persisted import evidence for multi-session captures. Capture
            completion, packet losses, lap validity, and reference eligibility
            are shown separately.
          </p>
        </div>
        <span className="run-evidence-badge">READ ONLY</span>
      </div>

      {!runsPage ? (
        <p className="run-evidence-empty">
          Run summaries are unavailable from the local API.
        </p>
      ) : runsPage.items.length === 0 ? (
        <p className="run-evidence-empty">
          No imported captures are in the archive yet.
        </p>
      ) : (
        <>
          <div className="run-evidence-list">
            {runsPage.items.map((run) => (
              <div className="run-evidence-item" key={run.run_id}>
                <div className="run-evidence-file">
                  <strong>{run.capture.sha256.slice(0, 12)}…</strong>
                  <span>
                    {formatBytes(run.capture.byte_size)} · run {run.run_id.slice(0, 8)} · {run.processing.pipeline_version} · {run.totals.session_count} sessions · {run.totals.attempt_count} attempts · {run.totals.stored_reference_eligible_count} stored eligible flags
                  </span>
                </div>
                <div className="run-evidence-actions">
                  <span className={`run-status run-status-${run.processing.status}`}>
                    {humanize(run.processing.status)}
                  </span>
                  <a
                    className="run-evidence-link"
                    href={runUrl(run.run_id, runsPage.offset, 0, 0)}
                  >
                    Inspect evidence ↗
                  </a>
                </div>
              </div>
            ))}
          </div>
          <PageNavigation
            label="Imported captures"
            offset={runsPage.offset}
            limit={runsPage.limit}
            total={runsPage.total}
            hrefForOffset={(offset) =>
              runId
                ? runUrl(runId, offset, sessionOffset, attemptOffset)
                : `/?run_offset=${offset}`
            }
          />
        </>
      )}

      {runId && !detail ? (
        <p className="run-evidence-empty">
          The selected processing run is unavailable in this local archive.
        </p>
      ) : detail ? (
        <RunDetail
          detail={detail}
          runOffset={runOffset}
          sessionOffset={sessionOffset}
          attemptOffset={attemptOffset}
          lifecycleEventOffset={lifecycleEventOffset}
        />
      ) : null}
    </section>
  );
}

function RunDetail({
  detail,
  runOffset,
  sessionOffset,
  attemptOffset,
  lifecycleEventOffset,
}: {
  detail: ProcessingRunDetail;
  runOffset: number;
  sessionOffset: number;
  attemptOffset: number;
  lifecycleEventOffset: number;
}) {
  const { summary, sessions, attempts, lifecycle_events: lifecycleEvents } = detail;
  const { capture, processing, totals } = summary;
  const recording = capture.recording_counters;
  const observer = capture.recording_observer_counters;
  const imported = processing.import_counters;
  const replay = processing.replay_counters;
  const lifecycle = processing.lifecycle_evidence;

  return (
    <div className="run-detail">
      <div className="run-detail-heading">
        <div>
          <span className="eyebrow">PROCESSING RUN {summary.run_id.slice(0, 12)}</span>
          <h3>{humanize(processing.status)} · {humanize(processing.pipeline_version)}</h3>
          <p>
            Processing capture-complete flag: {capture.complete === true ? "yes" : capture.complete === false ? "no" : "unknown"} · capture footer: {humanize(capture.footer_status ?? "unknown")} ·
            capture size: {formatBytes(capture.byte_size)} · SHA-256 {capture.sha256}
          </p>
        </div>
        <a className="run-evidence-link" href="/">Close details</a>
      </div>

      {processing.error ? (
        <p className="run-evidence-error" role="status">{processing.error}</p>
      ) : null}

      <div className="run-evidence-groups">
        <EvidenceGroup title="Recorder acquisition">
          <EvidenceValue label="Received datagrams" value={recording?.received} />
          <EvidenceValue label="Recorded datagrams" value={recording?.recorded} />
          <EvidenceValue label="Queue drops" value={recording?.queue_dropped} />
          <EvidenceValue label="Unpersisted at stop" value={recording?.unpersisted_on_shutdown} />
          <EvidenceValue label="Socket errors" value={recording?.socket_errors} />
          <EvidenceValue label="Observer late packets" value={observer?.late_packets_ignored} />
          <EvidenceValue label="Observer frame drops" value={observer?.frame_overflow_packets_dropped} />
          <EvidenceValue label="Observer lap decode errors" value={observer?.lap_data_decode_errors} />
          <EvidenceValue label="Observer context decode errors" value={observer?.session_context_decode_errors} />
        </EvidenceGroup>
        <EvidenceGroup title="Replay and decoding">
          <EvidenceValue label="Imported packets" value={imported?.packet_count} />
          <EvidenceValue label="Malformed packets" value={imported?.malformed_packet_count} />
          <EvidenceValue label="Late packets ignored" value={replay?.import_late_packets_ignored} />
          <EvidenceValue label="Frame overflow drops" value={replay?.import_frame_overflow_packets_dropped} />
          <EvidenceValue label="Lap data decode errors" value={imported?.lap_data_errors} />
          <EvidenceValue label="Car telemetry decode errors" value={imported?.car_telemetry_errors} />
          <EvidenceValue label="Participant decode errors" value={imported?.participant_errors} />
          <EvidenceValue label="Motion decode errors" value={imported?.motion_decode_errors} />
          <EvidenceValue label="Car Status decode errors" value={imported?.car_status_decode_errors} />
          <EvidenceValue label="Telemetry join gaps" value={imported?.missing_car_telemetry_samples} />
        </EvidenceGroup>
        <EvidenceGroup title="Event lifecycle decoding">
          <EvidenceValue label="Event packets" value={lifecycle?.event_packets_decoded} />
          <EvidenceValue label="Decode errors" value={lifecycle?.event_decode_errors} />
          <EvidenceValue label="Persisted lifecycle records" value={lifecycle?.lifecycle_event_count} />
          <EvidenceValue label="Dropped lifecycle records" value={lifecycle?.lifecycle_events_dropped} />
          <EvidenceValue label="Reconciliation work" value={lifecycle?.lifecycle_reconciliation_work} />
          <EvidenceValue label="Reconciliation truncated sessions" value={lifecycle?.lifecycle_reconciliation_truncated_session_count} />
          <div>
            <dt>Event codes</dt>
            <dd>
              {lifecycle?.event_code_counts && Object.keys(lifecycle.event_code_counts).length
                ? Object.entries(lifecycle.event_code_counts).map(([code, count]) => `${code} ${count.toLocaleString()}`).join(" · ")
                : "unknown"}
            </dd>
          </div>
        </EvidenceGroup>
        <EvidenceGroup title="Stored lap evidence">
          <EvidenceValue label="Sessions" value={totals.session_count} />
          <EvidenceValue label="Attempts" value={totals.attempt_count} />
          <EvidenceValue label="Completed" value={totals.disposition_counts.completed ?? 0} />
          <EvidenceValue label="Partial" value={totals.disposition_counts.partial ?? 0} />
          <EvidenceValue label="Abandoned" value={totals.disposition_counts.abandoned ?? 0} />
          <EvidenceValue
            label="Other dispositions"
            value={Object.entries(totals.disposition_counts)
              .filter(([disposition]) => !["completed", "partial", "abandoned"].includes(disposition))
              .reduce((total, [, count]) => total + count, 0)}
          />
          <EvidenceValue label="Game valid" value={totals.game_validity_counts.valid} />
          <EvidenceValue label="Game invalid" value={totals.game_validity_counts.invalid} />
          <EvidenceValue label="Validity unknown" value={totals.game_validity_counts.unknown} />
          <EvidenceValue label="Stored eligible flags" value={totals.stored_reference_eligible_count} />
          <EvidenceValue label="Lifecycle events" value={totals.lifecycle_event_count} />
          <EvidenceValue label="Flashback boundaries" value={totals.flashback_event_count} />
          <EvidenceValue label="Time regressions" value={totals.session_time_regression_count} />
          <EvidenceValue label="Uncertain events" value={totals.uncertain_lifecycle_event_count} />
        </EvidenceGroup>
      </div>

      <p className="run-evidence-note">
        Stored eligibility flags are not successful reference selections. Import
        completion and capture-footer completion do not establish a valid lap,
        calibrated geometry, or coaching readiness.
        {observer?.late_packets_ignored != null || observer?.frame_overflow_packets_dropped != null
          ? ` Capture-time observer counts: ${countText(observer.late_packets_ignored)} late packets ignored · ${countText(observer.frame_overflow_packets_dropped)} frame overflow drops.`
          : " Capture-time observer counts are unknown."}
      </p>

      <div className="run-exclusion-summary">
        <h4>Recorded exclusion reasons</h4>
        {totals.exclusion_reason_counts.length === 0 ? (
          <p>{totals.exclusion_reasons_complete ? "No exclusions were recorded." : "Exclusion evidence is incomplete."}</p>
        ) : (
          <ul>
            {totals.exclusion_reason_counts.map((entry) => (
              <li key={entry.reason}>
                <span>{humanize(entry.reason)}</span>
                <strong>{entry.attempt_count}</strong>
              </li>
            ))}
          </ul>
        )}
        {!totals.exclusion_reasons_complete ? (
          <p>Some legacy exclusion records could not be read.</p>
        ) : null}
      </div>

      <div className="run-evidence-pages">
        <section className="run-page-group" aria-labelledby="run-sessions-title">
          <div className="run-page-heading">
            <div>
              <h4 id="run-sessions-title">Sessions</h4>
              <p>Context text names the latest stored snapshot, with history counts alongside.</p>
            </div>
            <span>{sessions.total.toLocaleString()} total</span>
          </div>
          {sessions.items.length === 0 ? (
            <p className="run-evidence-empty">No session rows were stored for this run.</p>
          ) : (
            <ul className="run-link-list">
              {sessions.items.map((session) => (
                <SessionRow key={session.session_key} session={session} />
              ))}
            </ul>
          )}
          <PageNavigation
            label="Sessions"
            offset={sessions.offset}
            limit={sessions.limit}
            total={sessions.total}
            hrefForOffset={(offset) =>
              runUrl(summary.run_id, runOffset, offset, attemptOffset)
            }
          />
        </section>

        <section className="run-page-group" aria-labelledby="run-attempts-title">
          <div className="run-page-heading">
            <div>
              <h4 id="run-attempts-title">Attempts</h4>
              <p>Disposition, game validity, and stored eligibility stay distinct.</p>
            </div>
            <span>{attempts.total.toLocaleString()} total</span>
          </div>
          {attempts.items.length === 0 ? (
            <p className="run-evidence-empty">No attempt rows were stored for this run.</p>
          ) : (
            <ul className="run-link-list">
              {attempts.items.map((attempt) => (
                <AttemptRow key={attempt.attempt_key} attempt={attempt} />
              ))}
            </ul>
          )}
          <PageNavigation
            label="Attempts"
            offset={attempts.offset}
            limit={attempts.limit}
            total={attempts.total}
            hrefForOffset={(offset) =>
              runUrl(summary.run_id, runOffset, sessionOffset, offset)
            }
          />
        </section>

        <section className="run-page-group" aria-labelledby="run-lifecycle-title">
          <div className="run-page-heading">
            <div>
              <h4 id="run-lifecycle-title">Session lifecycle evidence</h4>
              <p>Event codes and rewind boundaries retain a bounded details prefix, original byte length, source frames, and monotonic frame ordinals.</p>
            </div>
            <span>{lifecycleEvents.total.toLocaleString()} total</span>
          </div>
          {lifecycleEvents.items.length === 0 ? (
            <p className="run-evidence-empty">No lifecycle events or rewind boundaries were stored for this run.</p>
          ) : (
            <ul className="run-link-list">
              {lifecycleEvents.items.map((event) => (
                <LifecycleEventRow key={`${event.session_uid}:${event.event_ordinal}`} event={event} />
              ))}
            </ul>
          )}
          <PageNavigation
            label="Lifecycle events"
            offset={lifecycleEvents.offset}
            limit={lifecycleEvents.limit}
            total={lifecycleEvents.total}
            hrefForOffset={(offset) =>
              runUrl(summary.run_id, runOffset, sessionOffset, attemptOffset, offset)
            }
          />
        </section>
      </div>
    </div>
  );
}

function SessionRow({ session }: { session: ProcessingRunSession }) {
  return (
    <li className="run-link-item">
      <div>
        <strong>Session {session.session_uid}</strong>
        <span>
          {contextLabel(session.latest_context_snapshot, "Latest snapshot")} · context snapshot {humanize(session.context_snapshot_status)}
        </span>
        <small>
          {session.context_update_count.toLocaleString()} context updates · {session.context_invalidation_count.toLocaleString()} invalidations · {session.attempt_count.toLocaleString()} attempts
        </small>
      </div>
      <a href={`/?session_key=${encodeURIComponent(session.session_key)}`}>Open session ↗</a>
    </li>
  );
}

function AttemptRow({ attempt }: { attempt: ProcessingRunAttempt }) {
  const lifecycleStatus = !attempt.lifecycle_assessed || attempt.superseded === null
    ? "lifecycle unassessed"
    : attempt.superseded
      ? "superseded by flashback"
      : "lifecycle assessed";
  return (
    <li className="run-link-item">
      <div>
        <strong>
          Attempt {attempt.attempt_number} · {humanize(attempt.disposition)} · {formatLapTime(attempt.lap_time_ms)}
        </strong>
        <span>
          Game {attempt.game_valid === true ? "valid" : attempt.game_valid === false ? "invalid" : "validity unknown"} · {lifecycleStatus} · stored reference eligibility {attempt.reference_eligible ? "eligible" : "not eligible"}
        </span>
        <small>
          {contextLabel(attempt.first_context_snapshot, "First context snapshot")} · {attempt.context_segment_count} context segments · {attempt.missing_context_segment_count} missing context segments
          {attempt.exclusion_reasons === null
            ? " · exclusion reasons unknown"
            : attempt.exclusion_reasons.length
              ? ` · ${attempt.exclusion_reasons.map(humanize).join(" · ")}`
              : ""}
        </small>
      </div>
      <a
        href={`/?session_key=${encodeURIComponent(attempt.session_key)}&target_attempt_key=${encodeURIComponent(attempt.attempt_key)}`}
      >
        Open attempt ↗
      </a>
    </li>
  );
}

function LifecycleEventRow({ event }: { event: ProcessingRunLifecycleEvent }) {
  const target = event.target_session_time_s === null
    ? "target unavailable"
    : `target ${event.target_session_time_s.toFixed(3)} s · game frame ${event.target_game_frame_identifier ?? "unknown"}`;
  return (
    <li className="run-link-item">
      <div>
        <strong>
          {event.event_code ?? humanize(event.event_kind)} · {humanize(event.cause)} · session {event.session_uid}
        </strong>
        <span>
          {humanize(event.evidence_status)} · header frame {event.current_frame_identifier} · overall frame {event.current_overall_frame_identifier} · ordinal {event.frame_ordinal} · session time {event.session_time_s.toFixed(3)} s
        </span>
        <small>
          {target} · {event.duplicate_count > 1 ? `${event.duplicate_count} matching signals · ` : ""}raw details prefix {event.details_hex || "empty"} · {event.details_length_bytes} bytes{event.details_truncated ? " · truncated" : ""}
        </small>
      </div>
    </li>
  );
}

function EvidenceGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="run-evidence-group" aria-label={title}>
      <h4>{title}</h4>
      <dl>{children}</dl>
    </section>
  );
}

function EvidenceValue({
  label,
  value,
}: {
  label: string;
  value: number | null | undefined;
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{countText(value)}</dd>
    </div>
  );
}

function PageNavigation({
  label,
  offset,
  limit,
  total,
  hrefForOffset,
}: {
  label: string;
  offset: number;
  limit: number;
  total: number;
  hrefForOffset: (offset: number) => string;
}) {
  if (total <= limit) return null;
  const start = offset + 1;
  const end = Math.min(offset + limit, total);
  return (
    <nav className="run-page-navigation" aria-label={`${label} pages`}>
      <span>{start}–{end} of {total.toLocaleString()}</span>
      <div>
        {offset > 0 ? <a href={hrefForOffset(Math.max(0, offset - limit))}>Previous</a> : null}
        {offset + limit < total ? <a href={hrefForOffset(offset + limit)}>Next</a> : null}
      </div>
    </nav>
  );
}

function runUrl(
  runId: string,
  runOffset: number,
  sessionOffset: number,
  attemptOffset: number,
  lifecycleEventOffset = 0,
) {
  const params = new URLSearchParams({
    run_id: runId,
    run_offset: String(runOffset),
    session_offset: String(sessionOffset),
    attempt_offset: String(attemptOffset),
    lifecycle_event_offset: String(lifecycleEventOffset),
  });
  return `/?${params.toString()}`;
}

function contextLabel(context: Record<string, unknown> | null, prefix: string) {
  if (!context) return `${prefix}: unknown`;
  const values = [context.game_mode, context.session_type, context.track_name]
    .map((value) => typeof value === "string" && value.length ? humanize(value) : "unknown");
  return `${prefix}: ${values.join(" · ")}`;
}

function countText(value: number | null | undefined) {
  return typeof value === "number" ? value.toLocaleString() : "unknown";
}

function formatBytes(value: number | null) {
  if (value === null) return "size unknown";
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatLapTime(value: number | null) {
  if (value === null) return "no official time";
  const minutes = Math.floor(value / 60_000);
  const seconds = ((value % 60_000) / 1000).toFixed(3).padStart(6, "0");
  return `${minutes}:${seconds}`;
}

function humanize(value: string) {
  return value.replaceAll("_", " ");
}
