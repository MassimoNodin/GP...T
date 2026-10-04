import type { ReactNode } from "react";
import type {
  CarObservationInventory,
  CarObservationPreview,
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
  observationInventory,
  observationPreview,
  observationSessionUid,
  runId,
  runOffset,
  sessionOffset,
  attemptOffset,
  lifecycleEventOffset,
}: {
  runsPage: ProcessingRunPage<ProcessingRunSummary> | null;
  detail: ProcessingRunDetail | null;
  observationInventory: CarObservationInventory | null;
  observationPreview: CarObservationPreview | null;
  observationSessionUid: string | null;
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
          observationInventory={observationInventory}
          observationPreview={observationPreview}
          observationSessionUid={observationSessionUid}
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
  observationInventory,
  observationPreview,
  observationSessionUid,
  runOffset,
  sessionOffset,
  attemptOffset,
  lifecycleEventOffset,
}: {
  detail: ProcessingRunDetail;
  observationInventory: CarObservationInventory | null;
  observationPreview: CarObservationPreview | null;
  observationSessionUid: string | null;
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
          <EvidenceValue label="Admitted Event packets" value={lifecycle?.event_packets_decoded} />
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
        <EvidenceGroup title="Reported lap and sector timing">
          <EvidenceValue label="Admitted Session History" value={imported?.session_history_packets_admitted} />
          <EvidenceValue label="Player snapshots decoded" value={imported?.session_history_packets_decoded} />
          <EvidenceValue label="Non-player snapshots skipped" value={imported?.session_history_non_player_packets} />
          <EvidenceValue label="Decode errors" value={imported?.session_history_decode_errors} />
          <EvidenceValue label="Unique lap candidates" value={imported?.session_history_candidates} />
          <EvidenceValue label="Association work" value={imported?.session_history_association_work} />
          <EvidenceValue label="Matched attempts" value={imported?.session_history_matched_attempts} />
          <EvidenceValue label="Ambiguous / conflicting" value={(imported?.session_history_ambiguous_attempts ?? 0) + (imported?.session_history_conflicting_attempts ?? 0)} />
          <EvidenceValue label="Unavailable / truncated" value={(imported?.session_history_unavailable_attempts ?? 0) + (imported?.session_history_truncated_attempts ?? 0)} />
          <EvidenceValue label="Truncated sessions" value={imported?.session_history_truncated_sessions} />
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

      <CarObservationArchive
        inventory={observationInventory}
        preview={observationPreview}
        selectedSessionUid={observationSessionUid}
        sessions={sessions.items}
        runId={summary.run_id}
        runOffset={runOffset}
        sessionOffset={sessionOffset}
        attemptOffset={attemptOffset}
        lifecycleEventOffset={lifecycleEventOffset}
      />

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

function CarObservationArchive({
  inventory,
  preview,
  selectedSessionUid,
  sessions,
  runId,
  runOffset,
  sessionOffset,
  attemptOffset,
  lifecycleEventOffset,
}: {
  inventory: CarObservationInventory | null;
  preview: CarObservationPreview | null;
  selectedSessionUid: string | null;
  sessions: ProcessingRunDetail["sessions"]["items"];
  runId: string;
  runOffset: number;
  sessionOffset: number;
  attemptOffset: number;
  lifecycleEventOffset: number;
}) {
  return (
    <section className="run-page-group" aria-labelledby="car-observations-title">
      <div className="run-page-heading">
        <div>
          <h4 id="car-observations-title">All-car observation archive</h4>
          <p>
            Frame-aligned slot observations are stored separately from player lap attempts. A slot is not a stable driver identity, and opponent eligibility has not been assessed.
          </p>
        </div>
      </div>
      {sessions.length > 0 ? (
        <form className="run-observation-controls" action="/" method="get">
          <input type="hidden" name="run_id" value={runId} />
          <input type="hidden" name="run_offset" value={runOffset} />
          <input type="hidden" name="session_offset" value={sessionOffset} />
          <input type="hidden" name="attempt_offset" value={attemptOffset} />
          <input type="hidden" name="lifecycle_event_offset" value={lifecycleEventOffset} />
          <label htmlFor="observation-session">Session</label>
          <select
            id="observation-session"
            name="observation_session_uid"
            defaultValue={selectedSessionUid ?? inventory?.session_uid ?? sessions[0].session_uid}
          >
            {selectedSessionUid && !sessions.some((session) => session.session_uid === selectedSessionUid) ? (
              <option value={selectedSessionUid}>Requested session {selectedSessionUid} · outside this page</option>
            ) : null}
            {sessions.map((session) => (
              <option key={session.session_key} value={session.session_uid}>
                {session.session_uid} · {contextLabel(session.latest_context_snapshot, "Context")}
              </option>
            ))}
          </select>
          <button className="run-observation-submit" type="submit">Load observations</button>
        </form>
      ) : null}
      {!inventory ? (
        <p className="run-evidence-empty">
          {selectedSessionUid && !sessions.some((session) => session.session_uid === selectedSessionUid)
            ? `Session ${selectedSessionUid} is not present on this run detail page, so its observation inventory was not loaded.`
            : "Car observation inventory is unavailable for the selected session."}
        </p>
      ) : (
        <>
          <p className="run-evidence-empty">
            Session {inventory.session_uid} · {inventory.capture.complete ? "capture finalized" : `capture ${inventory.capture.footer_status ?? "incomplete"}`} · {inventory.replay_quality.conflicting_observation_frames === null ? "replay conflict count unknown" : `${inventory.replay_quality.conflicting_observation_frames.toLocaleString()} conflicting observation frame(s)`}
          </p>
          {inventory.archive_status === "not_archived" ? (
            <p className="run-evidence-empty">This run predates the all-car observation archive. Replay quality and slot counts are unavailable for this run.</p>
          ) : inventory.archive_status === "empty" ? (
            <p className="run-evidence-empty">The run records the all-car archive, but it contains no admitted Lap Data observations.</p>
          ) : inventory.archive_status === "unavailable" ? (
            <p className="run-evidence-empty">Archive metadata could not be verified, so slot counts and replay quality are unavailable.</p>
          ) : (
            <div className="run-observation-table-wrap">
              <table className="run-observation-table">
              <thead>
                <tr>
                  <th scope="col">Slot</th>
                  <th scope="col">Observations</th>
                  <th scope="col">Telemetry</th>
                  <th scope="col">Motion</th>
                  <th scope="col">Nonzero speed</th>
                  <th scope="col">Header player</th>
                  <th scope="col">Participant snapshots</th>
                </tr>
              </thead>
              <tbody>
                {inventory.slots.items.map((slot) => (
                  <tr key={slot.car_index}>
                    <th scope="row">{slot.car_index}</th>
                    <td>{slot.observation_count.toLocaleString()}</td>
                    <td>{slot.car_telemetry_count.toLocaleString()}</td>
                    <td>{slot.motion_count.toLocaleString()}</td>
                    <td>{slot.nonzero_speed_count.toLocaleString()}</td>
                    <td>{slot.header_player_count.toLocaleString()}</td>
                    <td>{slot.participant_snapshot_count.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
              </table>
            </div>
          )}
          {inventory.archive_status === "available" ? (preview ? (
            <div className="run-observation-preview">
              <h5>
                Slot {preview.car_index} · first {preview.observations.returned} of {preview.observations.total.toLocaleString()} observations
              </h5>
              <p>
                This bounded preview is diagnostic. Zero-valued slots are retained without being marked as active cars or eligible opponents.
              </p>
              <div className="run-observation-table-wrap">
                <table className="run-observation-table">
                  <thead>
                    <tr>
                      <th scope="col">Frame</th>
                      <th scope="col">Time (s)</th>
                      <th scope="col">Lap</th>
                      <th scope="col">Distance (m)</th>
                      <th scope="col">Speed (km/h)</th>
                      <th scope="col">Throttle</th>
                      <th scope="col">Brake</th>
                      <th scope="col">Motion</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.observations.items.slice(0, 12).map((item) => (
                      <tr key={`${item.frame_ordinal}:${item.frame_identifier}`}>
                        <td>{item.frame_identifier}</td>
                        <td>{item.session_time_s.toFixed(3)}</td>
                        <td>{item.lap_number}</td>
                        <td>{item.lap_distance_m?.toFixed(1) ?? "—"}</td>
                        <td>{item.speed_mps === null ? "—" : (item.speed_mps * 3.6).toFixed(1)}</td>
                        <td>{item.throttle === null ? "—" : `${(item.throttle * 100).toFixed(0)}%`}</td>
                        <td>{item.brake === null ? "—" : `${(item.brake * 100).toFixed(0)}%`}</td>
                        <td>{item.motion_available ? "available" : item.motion_unavailable_reason ?? "unknown"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!preview.capture.complete ? (
                <p className="run-evidence-error" role="status">
                  Source capture footer is {preview.capture.footer_status ?? "incomplete"}; these observations remain diagnostic.
                </p>
              ) : null}
            </div>
          ) : (
            <p className="run-evidence-empty">No bounded slot preview is available.</p>
          )) : null}
        </>
      )}
    </section>
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
  const timing = attempt.timing_evidence;
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
          {timing?.status === "matched"
            ? ` · reported ${formatLapTime(timing.reported_lap_time_ms ?? null)} · sectors ${formatLapTime(timing.sector1_time_ms ?? null)} / ${formatLapTime(timing.sector2_time_ms ?? null)} / ${formatLapTime(timing.sector3_time_ms ?? null)}`
            : ` · reported timing ${humanize(timing?.status ?? "unavailable")}`}
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
