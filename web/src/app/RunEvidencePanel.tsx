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
import {
  appScreenHref,
  appScreenPath,
  isSelectionTransferBlocked,
  type AppScreen,
} from "@/lib/navigation";

type ObservationRequestFailure = {
  kind: "request_failed" | "unavailable";
  reason: string | null;
};

type ObservationUrlState = {
  sessionUid: string | null;
  carIndexParam: string | null;
  offsetParam: string | null;
};

export default function RunEvidencePanel({
  runsPage,
  detail,
  observationInventory,
  observationPreview,
  observationSessionUid,
  observationCarIndexParam,
  observationCarIndex,
  observationOffset,
  observationOffsetParam,
  observationInventoryFailure,
  observationPreviewFailure,
  runId,
  runOffset,
  sessionOffset,
  attemptOffset,
  lifecycleEventOffset,
  runUnavailable,
  screen,
  preservedQuery,
}: {
  runsPage: ProcessingRunPage<ProcessingRunSummary> | null;
  detail: ProcessingRunDetail | null;
  observationInventory: CarObservationInventory | null;
  observationPreview: CarObservationPreview | null;
  observationSessionUid: string | null;
  observationCarIndexParam: string | null;
  observationCarIndex: number | null;
  observationOffset: number | null;
  observationOffsetParam: string | null;
  observationInventoryFailure: ObservationRequestFailure | null;
  observationPreviewFailure: ObservationRequestFailure | null;
  runId: string | null;
  runOffset: number;
  sessionOffset: number;
  attemptOffset: number;
  lifecycleEventOffset: number;
  runUnavailable: boolean;
  screen: AppScreen;
  preservedQuery: string;
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
                    href={runUrl(screen, preservedQuery, run.run_id, runsPage.offset, 0, 0) ?? undefined}
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
                ? runUrl(screen, preservedQuery, runId, offset, sessionOffset, attemptOffset, lifecycleEventOffset, {
                    sessionUid: observationSessionUid,
                    carIndexParam: observationCarIndexParam,
                    offsetParam: observationOffsetParam,
                  })
                : runUrl(screen, preservedQuery, null, offset, 0, 0)
            }
          />
        </>
      )}

      {runUnavailable ? (
        <p className="run-evidence-error" role="status">
          The requested processing-run identity is malformed. No run was
          selected; choose an available run from the archive.
        </p>
      ) : null}

      {runId && !detail ? (
        <p className="run-evidence-empty">
          The selected processing run is unavailable in this local archive. No
          replacement run was selected.
        </p>
      ) : detail ? (
        <RunDetail
          detail={detail}
          observationInventory={observationInventory}
          observationPreview={observationPreview}
          observationSessionUid={observationSessionUid}
          observationCarIndexParam={observationCarIndexParam}
          observationCarIndex={observationCarIndex}
          observationOffset={observationOffset}
          observationOffsetParam={observationOffsetParam}
          observationInventoryFailure={observationInventoryFailure}
          observationPreviewFailure={observationPreviewFailure}
          observationUrlState={{
            sessionUid: observationSessionUid,
            carIndexParam: observationCarIndexParam,
            offsetParam: observationOffsetParam,
          }}
          runOffset={runOffset}
          sessionOffset={sessionOffset}
          attemptOffset={attemptOffset}
          lifecycleEventOffset={lifecycleEventOffset}
          screen={screen}
          preservedQuery={preservedQuery}
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
  observationCarIndexParam,
  observationCarIndex,
  observationOffset,
  observationOffsetParam,
  observationInventoryFailure,
  observationPreviewFailure,
  observationUrlState,
  runOffset,
  sessionOffset,
  attemptOffset,
  lifecycleEventOffset,
  screen,
  preservedQuery,
}: {
  detail: ProcessingRunDetail;
  observationInventory: CarObservationInventory | null;
  observationPreview: CarObservationPreview | null;
  observationSessionUid: string | null;
  observationCarIndexParam: string | null;
  observationCarIndex: number | null;
  observationOffset: number | null;
  observationOffsetParam: string | null;
  observationInventoryFailure: ObservationRequestFailure | null;
  observationPreviewFailure: ObservationRequestFailure | null;
  observationUrlState: ObservationUrlState;
  runOffset: number;
  sessionOffset: number;
  attemptOffset: number;
  lifecycleEventOffset: number;
  screen: AppScreen;
  preservedQuery: string;
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
        <a
          className="run-evidence-link"
          href={screenHref(screen, preservedQuery, {}, [
            "run_id",
            "session_offset",
            "attempt_offset",
            "lifecycle_event_offset",
            "observation_session_uid",
            "observation_car_index",
            "observation_offset",
          ]) ?? undefined}
        >
          Close details
        </a>
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
        selectedCarIndexParam={observationCarIndexParam}
        selectedCarIndex={observationCarIndex}
        offset={observationOffset}
        offsetParam={observationOffsetParam}
        inventoryFailure={observationInventoryFailure}
        previewFailure={observationPreviewFailure}
        sessions={sessions.items}
        runId={summary.run_id}
        runOffset={runOffset}
        sessionOffset={sessionOffset}
        attemptOffset={attemptOffset}
        lifecycleEventOffset={lifecycleEventOffset}
        screen={screen}
        preservedQuery={preservedQuery}
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
              {sessions.items.map((session) => {
                const currentSessionKey = new URLSearchParams(
                  isSelectionTransferBlocked(preservedQuery) ? "" : preservedQuery,
                ).get("session_key");
                const changingSession = currentSessionKey !== session.session_key;
                return (
                  <SessionRow
                    key={session.session_key}
                    session={session}
                    href={screenHref(
                      "dashboard",
                      preservedQuery,
                      { session_key: session.session_key },
                      [
                        "run_id",
                        "session_offset",
                        "attempt_offset",
                        "lifecycle_event_offset",
                        "observation_session_uid",
                        "observation_car_index",
                        "observation_offset",
                        ...(changingSession
                          ? [
                              "target_attempt_key",
                              "reference_choice",
                              "comparison_policy",
                              "window_start_m",
                              "window_end_m",
                              "observation_attempt_key",
                              "track_model_key",
                              "position_probe_m",
                              "engineer_intent",
                              "engineer_region_identifier",
                            ]
                          : []),
                      ],
                    )}
                  />
                );
              })}
            </ul>
          )}
          <PageNavigation
            label="Sessions"
            offset={sessions.offset}
            limit={sessions.limit}
            total={sessions.total}
            hrefForOffset={(offset) =>
              runUrl(screen, preservedQuery, summary.run_id, runOffset, offset, attemptOffset, lifecycleEventOffset, observationUrlState)
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
                <AttemptRow
                  key={attempt.attempt_key}
                  attempt={attempt}
                  href={screenHref(
                    "dashboard",
                    preservedQuery,
                    {
                      session_key: attempt.session_key,
                      target_attempt_key: attempt.attempt_key,
                    },
                    [
                      "run_id",
                      "reference_choice",
                      "window_start_m",
                      "window_end_m",
                      "session_offset",
                      "attempt_offset",
                      "lifecycle_event_offset",
                      "observation_session_uid",
                      "observation_car_index",
                      "observation_offset",
                      "engineer_intent",
                      "engineer_region_identifier",
                    ],
                  )}
                />
              ))}
            </ul>
          )}
          <PageNavigation
            label="Attempts"
            offset={attempts.offset}
            limit={attempts.limit}
            total={attempts.total}
            hrefForOffset={(offset) =>
              runUrl(screen, preservedQuery, summary.run_id, runOffset, sessionOffset, offset, lifecycleEventOffset, observationUrlState)
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
              runUrl(screen, preservedQuery, summary.run_id, runOffset, sessionOffset, attemptOffset, offset, observationUrlState)
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
  selectedCarIndexParam,
  selectedCarIndex,
  offset,
  offsetParam,
  inventoryFailure,
  previewFailure,
  sessions,
  runId,
  runOffset,
  sessionOffset,
  attemptOffset,
  lifecycleEventOffset,
  screen,
  preservedQuery,
}: {
  inventory: CarObservationInventory | null;
  preview: CarObservationPreview | null;
  selectedSessionUid: string | null;
  selectedCarIndexParam: string | null;
  selectedCarIndex: number | null;
  offset: number | null;
  offsetParam: string | null;
  inventoryFailure: ObservationRequestFailure | null;
  previewFailure: ObservationRequestFailure | null;
  sessions: ProcessingRunDetail["sessions"]["items"];
  runId: string;
  runOffset: number;
  sessionOffset: number;
  attemptOffset: number;
  lifecycleEventOffset: number;
  screen: AppScreen;
  preservedQuery: string;
}) {
  const formHref = runUrl(
    screen,
    preservedQuery,
    runId,
    runOffset,
    sessionOffset,
    attemptOffset,
    lifecycleEventOffset,
  );
  const formHiddenEntries = queryEntriesFromHref(formHref, [
    "observation_session_uid",
    "observation_car_index",
    "observation_offset",
  ]);

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
        formHref ? (
          <form className="run-observation-controls" action={appScreenPath(screen)} method="get">
            {formHiddenEntries.map(([key, value], index) => (
              <input key={`${key}:${index}`} type="hidden" name={key} value={value} />
            ))}
            <label htmlFor="observation-session">Session</label>
            <select
              id="observation-session"
              name="observation_session_uid"
              defaultValue={selectedSessionUid ?? ""}
            >
              <option value="">Choose a session</option>
              {selectedSessionUid && !sessions.some((session) => session.session_uid === selectedSessionUid) ? (
                <option value={selectedSessionUid}>Requested session {selectedSessionUid} · outside this page</option>
              ) : null}
              {sessions.map((session) => (
                <option key={session.session_key} value={session.session_uid}>
                  {session.session_uid} · {contextLabel(session.latest_context_snapshot, "Context")}
                </option>
              ))}
            </select>
            <label htmlFor="observation-car-index">Car slot</label>
            <select
              id="observation-car-index"
              name="observation_car_index"
              defaultValue={selectedCarIndexParam ?? ""}
            >
              <option value="">Choose a slot</option>
              {selectedCarIndexParam !== null &&
              !inventory?.slots.items.some(
                (slot) => String(slot.car_index) === selectedCarIndexParam,
              ) ? (
                <option value={selectedCarIndexParam}>
                  Requested slot {selectedCarIndexParam} · unavailable
                </option>
              ) : null}
              {inventory?.slots.items.map((slot) => (
                <option key={slot.car_index} value={slot.car_index}>
                  Slot {slot.car_index} · {slot.observation_count.toLocaleString()} observations · {slot.nonzero_speed_count.toLocaleString()} nonzero-speed samples
                </option>
              ))}
            </select>
            <button className="run-observation-submit" type="submit">Load selection</button>
          </form>
        ) : (
          <p className="run-evidence-error" role="status">
            The current selections are too large to carry safely into the observation browser.
            Shorten the selection before loading another page.
          </p>
        )
      ) : null}
      {!inventory ? (
        <p className="run-evidence-empty">
          {selectedSessionUid && !sessions.some((session) => session.session_uid === selectedSessionUid)
            ? `Session ${selectedSessionUid} is not present on this run detail page, so its observation inventory was not loaded.`
            : inventoryFailure
              ? observationRequestFailureText("inventory", inventoryFailure)
              : selectedSessionUid
                ? "Car observation inventory is unavailable for the selected session."
                : "Choose a session above to load its car observation inventory."}
        </p>
      ) : (
        <>
          <p className="run-evidence-empty">
            Session {inventory.session_uid} · footer {humanize(inventory.capture.footer_status ?? "unknown")} · capture completion {inventory.capture.complete ? "complete" : "incomplete"}
          </p>
          <p className="run-evidence-empty">
            Replay quality · late packets ignored {countText(inventory.replay_quality.late_packets_ignored)} · frame overflow drops {countText(inventory.replay_quality.frame_overflow_packets_dropped)} · conflicting observation frames {countText(inventory.replay_quality.conflicting_observation_frames)}
          </p>
          {!inventory.capture.complete ? (
            <p className="run-evidence-error" role="status">
              The source capture is incomplete; archived observations remain diagnostic evidence.
            </p>
          ) : null}
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
          {inventory.archive_status === "available" && selectedCarIndexParam === null ? (
            <p className="run-evidence-empty">
              Choose a car slot to inspect its ordered observation pages. Slot numbers are session-local and do not identify a driver.
            </p>
          ) : null}
          {inventory.archive_status === "available" && selectedCarIndexParam !== null && selectedCarIndex === null ? (
            <p className="run-evidence-error" role="status">
              Requested slot “{selectedCarIndexParam}” is invalid. Choose a slot from the selected session; no other slot was selected.
            </p>
          ) : null}
          {inventory.archive_status === "available" && selectedCarIndex !== null && !inventory.slots.items.some((slot) => slot.car_index === selectedCarIndex) ? (
            <p className="run-evidence-error" role="status">
              Slot {selectedCarIndex} is not present in this session inventory; no other slot was selected.
            </p>
          ) : null}
          {inventory.archive_status === "available" && selectedCarIndex !== null && selectedCarIndexParam !== null && inventory.slots.items.some((slot) => slot.car_index === selectedCarIndex) && offset === null ? (
            <p className="run-evidence-error" role="status">
              Observation offset “{offsetParam}” is invalid or exceeds the 100,000-row request bound. The selected session and slot are preserved.
              <a href={observationPageUrl(screen, preservedQuery, runId, runOffset, sessionOffset, attemptOffset, lifecycleEventOffset, inventory.session_uid, selectedCarIndex, 0)}>Open the first page</a>.
            </p>
          ) : null}
          {inventory.archive_status === "available" && selectedCarIndex !== null && previewFailure ? (
            <p className="run-evidence-error" role="status">
              {observationRequestFailureText("preview", previewFailure)}
            </p>
          ) : null}
          {inventory.archive_status === "available" && selectedCarIndex !== null ? (preview ? (
            preview.archive_status === "available" ? (
            <div className="run-observation-preview">
              <h5>
                Slot {preview.car_index} · {preview.observations.total.toLocaleString()} observations in this session
              </h5>
              <p>
                This bounded preview is diagnostic. Zero-valued slots are retained without being marked as active cars or eligible opponents.
              </p>
              <nav className="run-page-navigation" aria-label="Car observation pages">
                <span>
                  {preview.observations.items.length.toLocaleString()} displayed · {preview.observations.returned.toLocaleString()} returned · {preview.observations.total.toLocaleString()} total
                  {preview.observations.returned > 0
                    ? ` · rows ${preview.observations.offset + 1}–${preview.observations.offset + preview.observations.returned}`
                    : ` · no rows at offset ${preview.observations.offset.toLocaleString()}`}
                </span>
                <div>
                  {preview.observations.offset > 0 ? (
                    <a href={observationPageUrl(
                      screen,
                      preservedQuery,
                      runId,
                      runOffset,
                      sessionOffset,
                      attemptOffset,
                      lifecycleEventOffset,
                      preview.session_uid,
                      preview.car_index,
                      previousObservationOffset(preview.observations.offset, preview.observations.total, preview.observations.limit),
                    )}>Previous page</a>
                  ) : null}
                  {preview.observations.offset + preview.observations.limit <= 100_000 &&
                  preview.observations.offset + preview.observations.returned < preview.observations.total ? (
                    <a href={observationPageUrl(
                      screen,
                      preservedQuery,
                      runId,
                      runOffset,
                      sessionOffset,
                      attemptOffset,
                      lifecycleEventOffset,
                      preview.session_uid,
                      preview.car_index,
                      preview.observations.offset + preview.observations.limit,
                    )}>Next page</a>
                  ) : null}
                </div>
              </nav>
              {preview.observations.offset + preview.observations.returned < preview.observations.total &&
              preview.observations.offset + preview.observations.limit > 100_000 ? (
                <p className="run-evidence-empty">The next page is outside the API offset bound of 100,000; later observations are beyond the current preview range.</p>
              ) : null}
              {preview.observations.offset >= preview.observations.total && preview.observations.total > 0 ? (
                <p className="run-evidence-empty">This requested page is past the end of the observation list. Use Previous page to return to the final available page.</p>
              ) : null}
              <div className="run-observation-table-wrap">
                <table className="run-observation-table">
                  <thead>
                    <tr>
                      <th scope="col">Frame ordinal</th>
                      <th scope="col">Frame</th>
                      <th scope="col">Time (s)</th>
                      <th scope="col">Epoch</th>
                      <th scope="col">Wire format</th>
                      <th scope="col">Lap</th>
                      <th scope="col">Distance (m)</th>
                      <th scope="col">Speed (km/h)</th>
                      <th scope="col">Throttle</th>
                      <th scope="col">Brake</th>
                      <th scope="col">Steering</th>
                      <th scope="col">Gear</th>
                      <th scope="col">Car Telemetry</th>
                      <th scope="col">Motion</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.observations.items.map((item) => (
                      <tr key={`${item.frame_ordinal}:${item.frame_identifier}`}>
                        <td>{item.frame_ordinal.toLocaleString()}</td>
                        <td>{item.frame_identifier}</td>
                        <td>{item.session_time_s.toFixed(3)}</td>
                        <td>{item.lifecycle_epoch}</td>
                        <td>{item.packet_format}</td>
                        <td>{item.lap_number}</td>
                        <td>{item.lap_distance_m?.toFixed(1) ?? "—"}</td>
                        <td>{item.speed_mps === null ? "—" : (item.speed_mps * 3.6).toFixed(1)}</td>
                        <td>{item.throttle === null ? "—" : `${(item.throttle * 100).toFixed(0)}%`}</td>
                        <td>{item.brake === null ? "—" : `${(item.brake * 100).toFixed(0)}%`}</td>
                        <td>{item.steering === null ? "—" : item.steering.toFixed(3)}</td>
                        <td>{item.gear ?? "—"}</td>
                        <td>{availabilityText(item.car_telemetry_available, item.car_telemetry_unavailable_reason)}</td>
                        <td>{availabilityText(item.motion_available, item.motion_unavailable_reason)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            ) : (
              <p className="run-evidence-empty">The selected slot reports archive state {humanize(preview.archive_status)}; no observation rows were substituted.</p>
            )
          ) : null) : null}
        </>
      )}
    </section>
  );
}

function SessionRow({
  session,
  href,
}: {
  session: ProcessingRunSession;
  href: string | null;
}) {
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
      <a href={href ?? undefined}>Open session ↗</a>
    </li>
  );
}

function AttemptRow({
  attempt,
  href,
}: {
  attempt: ProcessingRunAttempt;
  href: string | null;
}) {
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
      <a href={href ?? undefined}>
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
  hrefForOffset: (offset: number) => string | null;
}) {
  if (total <= limit) return null;
  const start = offset + 1;
  const end = Math.min(offset + limit, total);
  const previousHref = hrefForOffset(Math.max(0, offset - limit));
  const nextHref = hrefForOffset(offset + limit);
  return (
    <nav className="run-page-navigation" aria-label={`${label} pages`}>
      <span>{start}–{end} of {total.toLocaleString()}</span>
      <div>
        {offset > 0 && previousHref ? <a href={previousHref}>Previous</a> : null}
        {offset + limit < total && nextHref ? <a href={nextHref}>Next</a> : null}
      </div>
    </nav>
  );
}

function runUrl(
  screen: AppScreen,
  preservedQuery: string,
  runId: string | null,
  runOffset: number,
  sessionOffset: number,
  attemptOffset: number,
  lifecycleEventOffset = 0,
  observationState?: ObservationUrlState,
) {
  return screenHref(
    screen,
    preservedQuery,
    {
      ...(runId ? { run_id: runId } : {}),
      run_offset: String(runOffset),
      session_offset: String(sessionOffset),
      attempt_offset: String(attemptOffset),
      lifecycle_event_offset: String(lifecycleEventOffset),
      observation_session_uid: observationState?.sessionUid ?? "",
      observation_car_index: observationState?.carIndexParam ?? "",
      observation_offset: observationState?.offsetParam ?? (observationState ? "0" : ""),
    },
    runId ? [] : ["run_id"],
  );
}

function observationPageUrl(
  screen: AppScreen,
  preservedQuery: string,
  runId: string,
  runOffset: number,
  sessionOffset: number,
  attemptOffset: number,
  lifecycleEventOffset: number,
  sessionUid: string,
  carIndex: number,
  observationOffset: number,
) {
  return runUrl(
    screen,
    preservedQuery,
    runId,
    runOffset,
    sessionOffset,
    attemptOffset,
    lifecycleEventOffset,
    {
      sessionUid,
      carIndexParam: String(carIndex),
      offsetParam: String(observationOffset),
    },
  ) ?? undefined;
}

function screenHref(
  screen: AppScreen,
  preservedQuery: string,
  overrides: Record<string, string>,
  removeKeys: string[] = [],
) {
  const state = new URLSearchParams(
    isSelectionTransferBlocked(preservedQuery) ? "" : preservedQuery,
  );
  for (const key of removeKeys) state.delete(key);
  return appScreenHref(screen, state.toString(), overrides);
}

function queryEntriesFromHref(
  href: string | null,
  excluded: string[],
) {
  if (!href) return [];
  const excludedKeys = new Set(excluded);
  const query = href.includes("?") ? href.slice(href.indexOf("?") + 1) : "";
  return Array.from(new URLSearchParams(query).entries()).filter(
    ([key]) => !excludedKeys.has(key),
  );
}

function previousObservationOffset(offset: number, total: number, limit: number) {
  if (offset >= total && total > 0) {
    return Math.floor((total - 1) / limit) * limit;
  }
  return Math.max(0, offset - limit);
}

function observationRequestFailureText(
  resource: "inventory" | "preview",
  failure: ObservationRequestFailure,
) {
  if (failure.kind === "request_failed") {
    return `The local API request for the selected ${resource} failed. The requested session and slot remain selected.`;
  }
  const reason = failure.reason ?? `${resource}_unavailable`;
  if (reason.includes("limit") || reason.includes("bounds")) {
    return `Read-limit abstention for the selected ${resource}: ${humanize(reason)}. The requested session and slot remain selected.`;
  }
  return `The selected ${resource} is unavailable: ${humanize(reason)}. The requested session and slot remain selected.`;
}

function availabilityText(available: boolean, reason: string | null) {
  return available ? "available" : reason ? humanize(reason) : "unknown";
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
