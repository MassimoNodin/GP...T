import {
  Comparison,
  CornerAnalysis,
  CornerRegion,
  LapRecord,
  RegionEvent,
  ReferenceSelection,
  RecordingJobRecord,
  SessionRecord,
  TrackModelRecord,
  requestApi,
} from "@/lib/api";
import RecordingInbox from "./RecordingInbox";
import type { ImportJobRecord, RecordingSourceRecord } from "@/lib/api";

type SearchParams = {
  session_key?: string | string[];
  target_attempt_key?: string | string[];
  reference_choice?: string | string[];
  track_model_key?: string | string[];
  import_job_id?: string | string[];
  import_error?: string | string[];
};

type Series = {
  label: string;
  color: string;
  values: Array<number | null>;
  mask?: boolean[];
};

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const rawParams = await searchParams;
  const params = {
    session_key: firstParam(rawParams.session_key),
    target_attempt_key: firstParam(rawParams.target_attempt_key),
    reference_choice: firstParam(rawParams.reference_choice),
    track_model_key: firstParam(rawParams.track_model_key),
    import_job_id: firstParam(rawParams.import_job_id),
    import_error: firstParam(rawParams.import_error),
  };
  const [
    sessionResponse,
    trackModelsResponse,
    recordingSourcesResponse,
    recordingResponse,
  ] = await Promise.all([
    requestApi<SessionRecord[]>("/api/v1/sessions"),
    requestApi<TrackModelRecord[]>("/api/v1/track-models"),
    requestApi<RecordingSourceRecord[]>("/api/v1/recording-sources"),
    requestApi<RecordingJobRecord>("/api/v1/recordings/current"),
  ]);
  const jobResponse =
    params.import_job_id && /^[a-f0-9]{32}$/.test(params.import_job_id)
      ? await requestApi<ImportJobRecord>(
          `/api/v1/import-jobs/${params.import_job_id}`,
        )
      : null;
  const trackModels = trackModelsResponse?.data ?? [];
  const selectedModel =
    trackModels.find((item) => modelKey(item) === params.track_model_key) ??
    null;
  const staleModelChoice = Boolean(params.track_model_key) && !selectedModel;
  const importedSessions = (sessionResponse?.data ?? []).filter(
    (item) => item.run_status === "complete",
  );
  const latestRunByCapture = new Map<string, SessionRecord>();
  for (const item of importedSessions) {
    const key = `${item.capture_sha256}:${item.session_uid}`;
    const current = latestRunByCapture.get(key);
    const itemFinishedAt = item.finished_at_utc ?? item.started_at_utc;
    const currentFinishedAt =
      current?.finished_at_utc ?? current?.started_at_utc;
    if (
      !current ||
      itemFinishedAt > currentFinishedAt! ||
      (itemFinishedAt === currentFinishedAt && item.run_id > current.run_id)
    )
      latestRunByCapture.set(key, item);
  }
  const sessions = [...latestRunByCapture.values()].sort((a, b) => {
    const aFinishedAt = a.finished_at_utc ?? a.started_at_utc;
    const bFinishedAt = b.finished_at_utc ?? b.started_at_utc;
    return (
      aFinishedAt.localeCompare(bFinishedAt) || a.run_id.localeCompare(b.run_id)
    );
  });
  const session =
    sessions.find((item) => item.session_key === params.session_key) ??
    sessions.at(-1) ??
    null;
  const isTimeTrial = session?.context?.session_type === "time_trial";
  const trackModelCatalogUnavailable =
    !trackModelsResponse || trackModelsResponse.status === "unavailable";
  const lapResponse = session
    ? await requestApi<LapRecord[]>(
        `/api/v1/laps?${new URLSearchParams({ run_id: session.run_id, session_uid: session.session_uid })}`,
      )
    : null;
  const laps = lapResponse?.data ?? [];
  const completed = laps.filter((lap) => lap.disposition === "completed");
  const target =
    laps.find((lap) => lap.attempt_key === params.target_attempt_key) ??
    laps.at(-1) ??
    null;
  const defaultReference =
    completed.find((lap) => lap.attempt_key !== target?.attempt_key) ?? null;
  const referenceChoice =
    params.reference_choice ?? defaultReference?.attempt_key ?? "session_best";
  const autoReference = referenceChoice === "session_best";

  const manualReference = autoReference
    ? null
    : (completed.find((lap) => lap.attempt_key === referenceChoice) ?? null);
  const selectionRequest = target
    ? requestApi<ReferenceSelection>(
        `/api/v1/references/session-best?${new URLSearchParams({ target_attempt_key: target.attempt_key })}`,
      )
    : Promise.resolve(null);
  const manualComparisonRequest =
    target && manualReference
      ? requestApi<Comparison>(
          `/api/v1/compare/laps?${comparisonQuery(target.attempt_key, manualReference.attempt_key, selectedModel)}`,
        )
      : Promise.resolve(null);
  const [selectionResponse, manualComparisonResponse] = await Promise.all([
    selectionRequest,
    manualComparisonRequest,
  ]);
  const selection = selectionResponse?.data ?? null;
  const referenceKey = autoReference
    ? String(selection?.selected_reference?.attempt_key ?? "") || null
    : (manualReference?.attempt_key ?? null);
  const comparisonResponse =
    autoReference && target && referenceKey
      ? await requestApi<Comparison>(
          `/api/v1/compare/laps?${comparisonQuery(target.attempt_key, referenceKey, selectedModel)}`,
        )
      : manualComparisonResponse;
  const comparison =
    comparisonResponse?.status === "ok" ? comparisonResponse.data : null;
  const reference =
    completed.find((lap) => lap.attempt_key === referenceKey) ?? null;
  const staleManualReference =
    Boolean(params.reference_choice) &&
    params.reference_choice !== "session_best" &&
    !manualReference;
  const apiUnavailable =
    !sessionResponse || sessionResponse.status === "unavailable";
  const lapApiUnavailable =
    Boolean(session) && (!lapResponse || lapResponse.status === "unavailable");
  const selectionUnavailable =
    !selectionResponse || selectionResponse.status === "unavailable";
  const selectionState = selection?.status;
  const policyBadge =
    selectionState === "selected"
      ? "READY"
      : selectionState === "no_eligible_reference"
        ? "ABSTAINED"
        : selectionState
          ? label(selectionState)
          : "UNAVAILABLE";
  const policyDescription =
    selection?.status === "selected" && selection.selected_reference
      ? `Attempt ${selection.selected_reference.attempt_number} · ${lapTime(Number(selection.selected_reference.lap_time_ms))} · same run, session and driver`
      : selection?.reasons.length
        ? selection.reasons.map(reason).join(" · ")
        : selection?.status === "no_eligible_reference"
          ? "No prior lap meets the session-best policy."
          : selection?.status === "target_not_completed"
            ? "Automatic reference selection requires a completed target lap."
            : selection?.status === "target_unavailable"
              ? "The selected target attempt is unavailable in the local archive."
              : selection?.status === "unsupported_policy"
                ? "Automatic session-best selection is not supported for this mode."
                : !target
                  ? "Select a recorded attempt to inspect reference eligibility."
                  : (selectionResponse?.reason ??
                    (selectionUnavailable
                      ? "Could not load reference policy evidence from the local API."
                      : "The API returned no reference policy evidence."));
  const noComparison = !target
    ? {
        eyebrow: "NO TARGET SELECTED",
        title: "Choose an attempt to inspect.",
        body: "Select a recorded lap or partial attempt from the inventory.",
      }
    : staleManualReference
      ? {
          eyebrow: "REFERENCE UNAVAILABLE",
          title: "The selected reference is not in this recording.",
          body: "Choose an available completed lap or use automatic session best.",
        }
      : autoReference && !selectionResponse
        ? {
            eyebrow: "REFERENCE REQUEST FAILED",
            title: "Reference evidence could not be loaded.",
            body: "The local API did not return a response. Check that it is running, then reload.",
          }
        : autoReference && selectionResponse?.status === "unavailable"
          ? {
              eyebrow: "REFERENCE API UNAVAILABLE",
              title: "Automatic reference evidence is unavailable.",
              body:
                selectionResponse.reason ??
                "The local API could not provide reference evidence.",
            }
          : autoReference && !selection
            ? {
                eyebrow: "REFERENCE DATA MISSING",
                title: "The API returned no reference selection.",
                body: "Reload the recording to request current policy evidence.",
              }
            : autoReference && selection?.status === "no_eligible_reference"
              ? {
                  eyebrow: "NO ELIGIBLE REFERENCE",
                  title: "The automatic policy abstained.",
                  body:
                    selection.reasons.map(reason).join(" · ") ||
                    "No prior lap meets the current policy.",
                }
              : autoReference && selection?.status === "target_not_completed"
                ? {
                    eyebrow: "TARGET INCOMPLETE",
                    title: "A partial attempt cannot be compared yet.",
                    body:
                      selection.reasons.map(reason).join(" · ") ||
                      "Comparison requires a completed lap.",
                  }
                : autoReference && selection?.status === "unsupported_policy"
                  ? {
                      eyebrow: "POLICY UNSUPPORTED",
                      title:
                        "Automatic reference selection is unavailable for this mode.",
                      body:
                        selection.reasons.map(reason).join(" · ") ||
                        "This mode has no validated reference policy.",
                    }
                  : autoReference && selection?.status === "target_unavailable"
                    ? {
                        eyebrow: "TARGET UNAVAILABLE",
                        title:
                          "The selected attempt is not available for analysis.",
                        body:
                          selection.reasons.map(reason).join(" · ") ||
                          "Reload the recording to refresh its attempts.",
                      }
                    : !autoReference && !manualReference
                      ? {
                          eyebrow: "NO COMPLETED REFERENCE",
                          title: "Choose another completed attempt.",
                          body: "A reference lap is required for distance comparison.",
                        }
                      : {
                          eyebrow: "COMPARISON REQUEST FAILED",
                          title: "Comparison evidence could not be loaded.",
                          body: "The local API did not return a comparison. Check that it is running, then reload.",
                        };
  const urlFor = (values: Record<string, string>) => {
    const query = new URLSearchParams(values);
    if (selectedModel) query.set("track_model_key", modelKey(selectedModel));
    return `/?${query.toString()}`;
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Apex Engineer home">
          <span className="brand-mark">A</span>
          <span>
            APEX <em>ENGINEER</em>
          </span>
        </a>
        <div className="topbar-right">
          <span className="local-indicator">
            <i /> LOCAL TELEMETRY
          </span>
          <span className="topbar-version">HISTORICAL ANALYSIS · V1</span>
        </div>
      </header>

      <div className="page-content">
        <section className="intro-row">
          <div>
            <div className="eyebrow">DRIVER ANALYSIS / SESSION REVIEW</div>
            <h1>
              Find the time
              <br />
              <span>between the lines.</span>
            </h1>
            <p className="intro-copy">
              Compare recorded laps by distance. Every gap stays visible, and
              every conclusion carries its source and quality with it.
            </p>
          </div>
          <div className="lap-stamp">
            <span className="stamp-ring">
              F1
              <br />
              25
            </span>
            <span>
              DATA-LED
              <br />
              BY DESIGN
            </span>
          </div>
        </section>

        <RecordingInbox
          sourcesResponse={recordingSourcesResponse}
          recordingResponse={recordingResponse}
          jobResponse={jobResponse}
          importError={params.import_error}
        />

        {apiUnavailable ? (
          <section className="connection-state panel">
            <span className="state-icon">!</span>
            <div>
              <h2>Analysis API is offline</h2>
              <p>Start the local API, then reload this page:</p>
              <code>
                uv run --extra app f1-engineer api --database data/dev.sqlite3
              </code>
            </div>
          </section>
        ) : sessions.length === 0 ? (
          <section className="empty-state panel">
            <div className="eyebrow">NO COMPLETED RECORDINGS</div>
            <h2>Record and import a session to begin.</h2>
            <p>
              APEX keeps the recording local and builds a replayable lap
              archive.
            </p>
          </section>
        ) : lapApiUnavailable ? (
          <section className="connection-state panel">
            <span className="state-icon">!</span>
            <div>
              <h2>Lap inventory is unavailable</h2>
              <p>
                The local API could not load this recording's attempts. Check
                the API, then reload.
              </p>
            </div>
          </section>
        ) : (
          <>
            <section className="session-strip panel">
              <div className="session-main">
                <span className="strip-label">SELECTED RECORDING</span>
                <div className="session-title-row">
                  <h2>{session?.context?.track_name ?? "Unknown circuit"}</h2>
                  <span className="mode-badge">
                    {label(session?.context?.session_type)}
                  </span>
                </div>
                <div className="session-meta">
                  <span>
                    {session?.context?.weather_name ?? "Conditions unknown"}
                  </span>
                  <span className="meta-dot">·</span>
                  <span>{session?.lap_attempts ?? 0} attempts</span>
                  <span className="meta-dot">·</span>
                  <span>Run {session?.run_id.slice(0, 8)}</span>
                </div>
              </div>
              <div className="session-stats">
                <div>
                  <strong>{String(laps.length).padStart(2, "0")}</strong>
                  <span>RECORDED</span>
                </div>
                <div>
                  <strong>{String(completed.length).padStart(2, "0")}</strong>
                  <span>COMPLETED</span>
                </div>
                <div className="stat-mode">
                  <strong>{label(session?.context?.game_mode)}</strong>
                  <span>GAME MODE</span>
                </div>
              </div>
            </section>

            <div className="workspace-grid">
              <aside className="sidebar">
                <section className="panel sidebar-panel">
                  <div className="section-heading">
                    <span className="eyebrow">RECORDINGS</span>
                    <span className="count-pill">{sessions.length}</span>
                  </div>
                  <div className="recording-list">
                    {[...sessions].reverse().map((item, index) => (
                      <a
                        className={`recording-item ${item.session_key === session?.session_key ? "selected" : ""}`}
                        href={urlFor({ session_key: item.session_key })}
                        key={item.session_key}
                      >
                        <span className="recording-index">
                          {String(index + 1).padStart(2, "0")}
                        </span>
                        <span className="recording-copy">
                          <strong>
                            {item.context?.track_name ?? "Unknown circuit"}
                          </strong>
                          <small>
                            {label(item.context?.session_type)} ·{" "}
                            {item.run_id.slice(0, 8)}
                          </small>
                        </span>
                        <span className="recording-chevron">↗</span>
                      </a>
                    ))}
                  </div>
                  <div className="archive-note">
                    <i /> All recordings are stored locally.
                  </div>
                </section>
                <section className="panel sidebar-panel">
                  <div className="section-heading">
                    <span className="eyebrow">LAP INVENTORY</span>
                    <span className="count-pill">{laps.length}</span>
                  </div>
                  <div className="attempt-list">
                    {laps.map((lap) => (
                      <a
                        className={`attempt-item ${lap.attempt_key === target?.attempt_key ? "active" : ""}`}
                        href={urlFor({
                          session_key: session!.session_key,
                          target_attempt_key: lap.attempt_key,
                        })}
                        key={lap.attempt_key}
                      >
                        <span className="attempt-number">
                          {String(lap.attempt_number).padStart(2, "0")}
                        </span>
                        <span className="attempt-copy">
                          <strong>Attempt {lap.attempt_number}</strong>
                          <small>
                            {label(lap.disposition)} ·{" "}
                            {lapTime(lap.lap_time_ms)}
                          </small>
                        </span>
                        <span
                          className={`validity ${lap.game_valid === true ? "valid" : lap.game_valid === false ? "invalid" : "partial"}`}
                        >
                          {lap.game_valid === true
                            ? "GAME VALID"
                            : lap.game_valid === false
                              ? "GAME INVALID"
                              : "UNKNOWN"}
                        </span>
                      </a>
                    ))}
                  </div>
                </section>
              </aside>

              <section className="analysis-column">
                <section className="panel compare-panel">
                  <div className="compare-header">
                    <div>
                      <div className="eyebrow">DISTANCE COMPARISON</div>
                      <h2>Choose your reference</h2>
                    </div>
                    <span className="protocol-tag">1 M RESAMPLE GRID</span>
                  </div>
                  <form className="compare-form" method="get">
                    <label>
                      <span>SESSION</span>
                      <select
                        name="session_key"
                        defaultValue={session?.session_key}
                      >
                        {sessions
                          .slice()
                          .reverse()
                          .map((item) => (
                            <option
                              value={item.session_key}
                              key={item.session_key}
                            >
                              {item.context?.track_name ?? "Unknown"} ·{" "}
                              {label(item.context?.session_type)} ·{" "}
                              {item.run_id.slice(0, 8)}
                            </option>
                          ))}
                      </select>
                    </label>
                    <label>
                      <span>TARGET LAP</span>
                      <select
                        name="target_attempt_key"
                        defaultValue={target?.attempt_key}
                      >
                        {laps.map((lap) => (
                          <option value={lap.attempt_key} key={lap.attempt_key}>
                            Attempt {lap.attempt_number} ·{" "}
                            {label(lap.disposition)} ·{" "}
                            {lapTime(lap.lap_time_ms)}
                            {lap.game_valid === false
                              ? " · game invalid"
                              : lap.game_valid === true
                                ? " · game valid"
                                : " · validity unknown"}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      <span>REFERENCE</span>
                      <select
                        name="reference_choice"
                        defaultValue={referenceChoice}
                      >
                        <option value="session_best">
                          Automatic session best
                        </option>
                        {completed
                          .filter(
                            (lap) => lap.attempt_key !== target?.attempt_key,
                          )
                          .map((lap) => (
                            <option
                              value={lap.attempt_key}
                              key={lap.attempt_key}
                            >
                              Attempt {lap.attempt_number} ·{" "}
                              {lapTime(lap.lap_time_ms)}
                              {lap.game_valid === false
                                ? " · diagnostic only"
                                : ""}
                            </option>
                          ))}
                      </select>
                    </label>
                    <label>
                      <span>DIAGNOSTIC REGION MODEL</span>
                      <select
                        name="track_model_key"
                        defaultValue={
                          selectedModel ? modelKey(selectedModel) : ""
                        }
                        disabled={!isTimeTrial || trackModelCatalogUnavailable}
                      >
                        <option value="">No region analysis</option>
                        {trackModels.map((model) => (
                          <option value={modelKey(model)} key={modelKey(model)}>
                            {model.track_name} · {model.validation_status} · rev{" "}
                            {model.revision} · {model.region_count ?? 0} regions
                          </option>
                        ))}
                      </select>
                    </label>
                    <button className="compare-button" type="submit">
                      Compare laps <span>↗</span>
                    </button>
                  </form>
                  {trackModelCatalogUnavailable ? (
                    <p className="model-note model-warning">
                      Track model metadata could not be loaded from the local
                      API.
                    </p>
                  ) : staleModelChoice ? (
                    <p className="model-note model-warning">
                      The selected model revision is no longer available. The
                      regular lap comparison remains available.
                    </p>
                  ) : !isTimeTrial ? (
                    <p className="model-note">
                      Diagnostic region comparison currently requires a Time
                      Trial session.
                    </p>
                  ) : selectedModel ? (
                    <p className="model-note">
                      Explicit revision selected. Draft windows remain
                      diagnostic and do not represent validated circuit corners.
                    </p>
                  ) : (
                    <p className="model-note">
                      Select a packaged model revision to add its distance
                      regions to this comparison.
                    </p>
                  )}
                  {target?.disposition !== "completed" && target ? (
                    <div className="diagnostic-banner">
                      <b>i</b>
                      <span>
                        {label(target.disposition)} attempt · comparison
                        requires a completed lap. The selected attempt is
                        preserved.
                      </span>
                    </div>
                  ) : target?.game_valid === false ||
                    reference?.game_valid === false ? (
                    <div className="diagnostic-banner">
                      <b>i</b>
                      <span>
                        Diagnostic comparison · one or both laps are
                        game-invalid. This describes recorded telemetry and is
                        not coaching.
                      </span>
                    </div>
                  ) : null}
                </section>

                <section className="panel reference-panel">
                  <div className="reference-icon">PB</div>
                  <div className="reference-copy">
                    <div className="eyebrow">AUTOMATIC REFERENCE POLICY</div>
                    <h3>
                      {selection?.status === "selected"
                        ? "Session best identified"
                        : selection
                          ? label(selection.status)
                          : target
                            ? "Reference policy unavailable"
                            : "No target selected"}
                    </h3>
                    <p>{policyDescription}</p>
                  </div>
                  <span
                    className={`policy-state ${selection?.status === "selected" ? "policy-ready" : selection?.status === "no_eligible_reference" ? "policy-abstain" : "policy-unavailable"}`}
                  >
                    {policyBadge}
                  </span>
                </section>
                {selection?.candidates.length ? (
                  <details className="candidate-details panel">
                    <summary>
                      Reference candidate evidence{" "}
                      <span>{selection.candidates.length} attempts</span>
                    </summary>
                    <div className="candidate-list">
                      {selection.candidates.map((candidate) => (
                        <div
                          className="candidate-row"
                          key={candidate.attempt_key}
                        >
                          <span>Attempt {candidate.attempt_number}</span>
                          <span>
                            {candidate.eligible
                              ? "ELIGIBLE"
                              : candidate.exclusion_reasons
                                  .map(reason)
                                  .join(" · ")}
                          </span>
                        </div>
                      ))}
                    </div>
                  </details>
                ) : null}

                {comparisonResponse?.status === "unavailable" ? (
                  <section className="unavailable-panel panel">
                    <span className="state-icon">!</span>
                    <div>
                      <div className="eyebrow">COMPARISON UNAVAILABLE</div>
                      <h3>This pair does not support a distance comparison.</h3>
                      <p>{comparisonResponse.reason}</p>
                    </div>
                  </section>
                ) : comparison ? (
                  <>
                    <section className="result-heading">
                      <div>
                        <div className="eyebrow">
                          {comparison.track.track_name} ·{" "}
                          {label(target?.context?.session_type)}
                        </div>
                        <h2>
                          Lap delta <span>across distance.</span>
                        </h2>
                      </div>
                      <div className="result-distance">
                        0 <i>—</i>{" "}
                        {Math.round(
                          comparison.track.track_length_m,
                        ).toLocaleString()}
                        <small>METRES</small>
                      </div>
                    </section>
                    <section className="metric-row">
                      <Metric
                        label="OFFICIAL LAP TIME"
                        value={seconds(
                          comparison.official_lap_time_difference_s,
                        )}
                        detail="Target minus reference"
                        tone={
                          comparison.official_lap_time_difference_s != null &&
                          comparison.official_lap_time_difference_s > 0
                            ? "warm"
                            : "cool"
                        }
                      />
                      <Metric
                        label="OBSERVED-RANGE DELTA"
                        value={seconds(
                          comparison.observed_range_delta
                            .observed_range_change_s,
                        )}
                        detail={
                          comparison.observed_range_delta
                            .first_supported_distance_m == null
                            ? "No shared supported span"
                            : `${Math.round(comparison.observed_range_delta.first_supported_distance_m)}–${Math.round(comparison.observed_range_delta.last_supported_distance_m ?? 0)} m supported`
                        }
                        tone="neutral"
                      />
                      <Metric
                        label="DELTA COVERAGE"
                        value={percent(comparison.quality.delta_time_coverage)}
                        detail={`${comparison.analysis_version} · ${comparison.config.max_bracket_time_s} s max bracket`}
                        tone="neutral"
                      />
                    </section>
                    <div className="chart-stack">
                      <Chart
                        title="Speed trace"
                        subtitle="Vehicle speed · km/h"
                        unit="KM/H"
                        distance={comparison.distance_m}
                        series={[
                          {
                            label: `Target · ${lapTime(target?.lap_time_ms)}`,
                            color: "#f06a4f",
                            values: speed(
                              comparison.target_trace.values.speed_mps,
                            ),
                            mask: comparison.target_trace.masks.speed_mps,
                          },
                          {
                            label: `Reference · ${lapTime(reference?.lap_time_ms)}`,
                            color: "#71c7b5",
                            values: speed(
                              comparison.reference_trace.values.speed_mps,
                            ),
                            mask: comparison.reference_trace.masks.speed_mps,
                          },
                        ]}
                      />
                      <Chart
                        title="Lap delta"
                        subtitle="Positive means target is slower · seconds"
                        unit="SECONDS"
                        distance={comparison.distance_m}
                        zero
                        series={[
                          {
                            label: "Target − reference",
                            color: "#f0b45c",
                            values: comparison.delta_s,
                            mask: comparison.delta_mask,
                          },
                        ]}
                      />
                      <Chart
                        title="Driver inputs"
                        subtitle="Brake and throttle · percent"
                        unit="%"
                        distance={comparison.distance_m}
                        range={[0, 1]}
                        percentAxis
                        series={[
                          {
                            label: "Target brake",
                            color: "#f06a4f",
                            values: numeric(
                              comparison.target_trace.values.brake,
                            ),
                            mask: comparison.target_trace.masks.brake,
                          },
                          {
                            label: "Target throttle",
                            color: "#71c7b5",
                            values: numeric(
                              comparison.target_trace.values.throttle,
                            ),
                            mask: comparison.target_trace.masks.throttle,
                          },
                          {
                            label: "Reference brake",
                            color: "#d89079",
                            values: numeric(
                              comparison.reference_trace.values.brake,
                            ),
                            mask: comparison.reference_trace.masks.brake,
                          },
                          {
                            label: "Reference throttle",
                            color: "#97b2a9",
                            values: numeric(
                              comparison.reference_trace.values.throttle,
                            ),
                            mask: comparison.reference_trace.masks.throttle,
                          },
                        ]}
                      />
                    </div>
                    <section className="quality-row panel">
                      <div className="quality-title">
                        <span className="eyebrow">DATA QUALITY</span>
                        <strong>Coverage and gaps</strong>
                      </div>
                      <div>
                        <span>TARGET SAMPLES</span>
                        <strong>
                          {comparison.target_trace.source_sample_count.toLocaleString()}
                        </strong>
                      </div>
                      <div>
                        <span>REFERENCE SAMPLES</span>
                        <strong>
                          {comparison.reference_trace.source_sample_count.toLocaleString()}
                        </strong>
                      </div>
                      <div>
                        <span>DELTA SUPPORTED</span>
                        <strong>
                          {percent(comparison.quality.delta_time_coverage)}
                        </strong>
                      </div>
                      <div>
                        <span>UNSUPPORTED SPANS</span>
                        <strong>
                          {comparison.quality.target_excluded_spans.length +
                            comparison.quality.reference_excluded_spans.length}
                        </strong>
                      </div>
                    </section>
                    <p className="chart-footnote">
                      Lines stop where a channel is unsupported. Missing
                      telemetry is not interpolated across.
                    </p>
                    {comparison.corner_analysis ? (
                      <RegionAnalysisPanel
                        analysis={comparison.corner_analysis}
                        comparison={comparison}
                      />
                    ) : (
                      <section className="region-prompt panel">
                        <div className="eyebrow">
                          {selectedModel
                            ? "REGION ANALYSIS UNAVAILABLE"
                            : trackModelCatalogUnavailable
                              ? "REGION MODEL CATALOG UNAVAILABLE"
                              : isTimeTrial
                                ? "NO REGION MODEL SELECTED"
                                : "REGION POLICY UNSUPPORTED"}
                        </div>
                        <h3>
                          {selectedModel
                            ? "The selected model returned no region analysis."
                            : trackModelCatalogUnavailable
                              ? "Track model metadata could not be loaded."
                              : isTimeTrial
                                ? "Choose an explicit model to inspect distance regions."
                                : "Diagnostic region analysis currently requires Time Trial."}
                        </h3>
                        <p>
                          {selectedModel
                            ? "The API returned the lap comparison without region evidence. Reload or choose another registered revision."
                            : trackModelCatalogUnavailable
                              ? "The local API did not provide the model catalog. Reload after the API is available to select a registered revision."
                              : isTimeTrial
                                ? "No circuit geometry is inferred from session telemetry. Models are versioned and selected explicitly."
                                : "This comparison keeps the mode boundary explicit; no region results are inferred for this session."}
                        </p>
                      </section>
                    )}
                  </>
                ) : (
                  <section className="empty-state panel">
                    <div className="eyebrow">{noComparison.eyebrow}</div>
                    <h2>{noComparison.title}</h2>
                    <p>{noComparison.body}</p>
                  </section>
                )}
              </section>
            </div>
          </>
        )}
      </div>
      <footer className="footer-bar">
        <span>
          APEX ENGINEER <b>·</b> LOCAL FIRST
        </span>
        <span>Recorded telemetry only · No generated coaching</span>
      </footer>
    </main>
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
    <div className={`metric-card panel metric-${tone}`}>
      <span>{title}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function RegionAnalysisPanel({
  analysis,
  comparison,
}: {
  analysis: CornerAnalysis;
  comparison: Comparison;
}) {
  return (
    <section className="region-analysis">
      <header className="region-overview panel">
        <div>
          <div className="eyebrow">
            {label(analysis.model.validation_status)} DISTANCE REGIONS ·{" "}
            {analysis.regions.length} WINDOWS
          </div>
          <h2>{analysis.model.track_name} region inspection</h2>
          <p>{analysis.model.provenance}</p>
        </div>
        <span className="region-state">
          {analysis.diagnostic_only ? "DIAGNOSTIC ONLY" : "MEASURED"}
        </span>
      </header>
      <p className="region-caveat">
        {label(analysis.layout_validation_status)}. Regions are not verified
        corner numbers. Steering turn-in is a proxy; driver apex and
        track-relative geometry are unavailable. No coaching is generated.
      </p>
      <div className="region-grid">
        {analysis.regions.map((region, index) => (
          <RegionCard
            key={region.identifier}
            region={region}
            index={index}
            comparison={comparison}
          />
        ))}
      </div>
    </section>
  );
}

function RegionCard({
  region,
  index,
  comparison,
}: {
  region: CornerRegion;
  index: number;
  comparison: Comparison;
}) {
  const [start, end] = region.analysis_window_m;
  const plotIndices = comparison.distance_m.flatMap((distance, sampleIndex) =>
    distance >= start && distance <= end ? [sampleIndex] : [],
  );
  const plotDistance = plotIndices.map(
    (sampleIndex) => comparison.distance_m[sampleIndex],
  );
  const valueSlice = <T extends number | boolean | null>(values: T[]) =>
    plotIndices.map((sampleIndex) => values[sampleIndex] ?? null);
  const maskSlice = (values: boolean[]) =>
    plotIndices.map((sampleIndex) => values[sampleIndex] ?? false);
  const targetSpeed = region.target.minimum_speed;
  const referenceSpeed = region.reference.minimum_speed;
  const speedDifference = region.differences.minimum_speed_kph;

  return (
    <article className="region-card panel">
      <header className="region-card-header">
        <div>
          <div className="region-index">
            WINDOW {String(index + 1).padStart(2, "0")} · {Math.round(start)}–
            {Math.round(end)} M
          </div>
          <h3>{region.label}</h3>
        </div>
        <span className="region-state region-state-draft">
          {region.diagnostic_only ? "DIAGNOSTIC" : "SUPPORTED"}
        </span>
      </header>

      <div className="region-stat-grid">
        <RegionStat
          title="REGION DELTA CHANGE"
          value={seconds(region.delta_change.delta_change_s)}
          detail={`${label(region.delta_change.status)} · target minus reference from entry to exit`}
        />
        <RegionStat
          title="TARGET OBSERVED MINIMUM"
          value={speedValue(targetSpeed.speed_kph)}
          detail={`${label(targetSpeed.status)} · ${percent(targetSpeed.supported_grid_coverage)} speed coverage`}
        />
        <RegionStat
          title="REFERENCE OBSERVED MINIMUM"
          value={speedValue(referenceSpeed.speed_kph)}
          detail={`${label(referenceSpeed.status)} · ${percent(referenceSpeed.supported_grid_coverage)} speed coverage`}
        />
        <RegionStat
          title="MINIMUM SPEED DIFFERENCE"
          value={signedValue(speedDifference, "km/h")}
          detail="Target minus reference"
        />
        <RegionStat
          title="BRAKING ONSET SHIFT"
          value={signedValue(region.differences.braking_onset_distance_m, "m")}
          detail="Target minus reference · event evidence below"
        />
        <RegionStat
          title="50% THROTTLE SHIFT"
          value={signedValue(region.differences.throttle_50_distance_m, "m")}
          detail="Target minus reference · event evidence below"
        />
      </div>

      <div className="region-event-grid">
        <RegionEventEvidence
          title="Braking onset"
          target={region.target.braking}
          reference={region.reference.braking}
          targetCoverage={region.target.event_channel_coverage.brake}
          referenceCoverage={region.reference.event_channel_coverage.brake}
        />
        <RegionEventEvidence
          title="Turn-in proxy"
          target={region.target.turn_in_proxy}
          reference={region.reference.turn_in_proxy}
          targetCoverage={region.target.event_channel_coverage.steering}
          referenceCoverage={region.reference.event_channel_coverage.steering}
          note="Absolute steering threshold; not a geometric turn-in point."
        />
        <RegionEventEvidence
          title="50% throttle pickup"
          target={region.target.throttle_pickup["0.5"]}
          reference={region.reference.throttle_pickup["0.5"]}
          targetCoverage={region.target.event_channel_coverage.throttle}
          referenceCoverage={region.reference.event_channel_coverage.throttle}
        />
      </div>

      <div className="region-exits">
        <div className="region-subhead">
          <strong>Exit speed observations</strong>
          <span>Measured at configured window offsets</span>
        </div>
        <div className="region-table-wrap">
          <table>
            <thead>
              <tr>
                <th>OFFSET</th>
                <th>TARGET</th>
                <th>REFERENCE</th>
                <th>SUPPORT</th>
              </tr>
            </thead>
            <tbody>
              {region.target.exit_speeds.map((targetExit, exitIndex) => {
                const referenceExit = region.reference.exit_speeds[exitIndex];
                return (
                  <tr key={targetExit.offset_m}>
                    <td>+{targetExit.offset_m} m</td>
                    <td>{speedValue(targetExit.speed_kph)}</td>
                    <td>{speedValue(referenceExit?.speed_kph ?? null)}</td>
                    <td>
                      {label(targetExit.status)} /{" "}
                      {label(referenceExit?.status)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <p className="region-apex-note">
        Driver apex: {label(region.target.driver_apex.status)} ·{" "}
        {label(region.target.driver_apex.reason)}. The saved traces do not
        include calibrated track-relative geometry; stored world positions
        remain in world coordinates.
      </p>

      <details className="region-traces">
        <summary>Open local speed, delta and input traces</summary>
        {plotDistance.length ? (
          <div className="chart-stack">
            <Chart
              title={`${region.label} speed`}
              subtitle={`${Math.round(start)}–${Math.round(end)} m · km/h`}
              unit="KM/H"
              distance={plotDistance}
              series={[
                {
                  label: "Target",
                  color: "#f06a4f",
                  values: speed(
                    valueSlice(comparison.target_trace.values.speed_mps),
                  ),
                  mask: maskSlice(comparison.target_trace.masks.speed_mps),
                },
                {
                  label: "Reference",
                  color: "#71c7b5",
                  values: speed(
                    valueSlice(comparison.reference_trace.values.speed_mps),
                  ),
                  mask: maskSlice(comparison.reference_trace.masks.speed_mps),
                },
              ]}
            />
            <Chart
              title={`${region.label} delta`}
              subtitle="Target minus reference · seconds"
              unit="SECONDS"
              distance={plotDistance}
              zero
              series={[
                {
                  label: "Target − reference",
                  color: "#f0b45c",
                  values: valueSlice(comparison.delta_s),
                  mask: maskSlice(comparison.delta_mask),
                },
              ]}
            />
            <Chart
              title={`${region.label} driver inputs`}
              subtitle="Brake and throttle · percent"
              unit="%"
              distance={plotDistance}
              range={[0, 1]}
              percentAxis
              series={[
                {
                  label: "Target brake",
                  color: "#f06a4f",
                  values: numeric(
                    valueSlice(comparison.target_trace.values.brake),
                  ),
                  mask: maskSlice(comparison.target_trace.masks.brake),
                },
                {
                  label: "Target throttle",
                  color: "#71c7b5",
                  values: numeric(
                    valueSlice(comparison.target_trace.values.throttle),
                  ),
                  mask: maskSlice(comparison.target_trace.masks.throttle),
                },
                {
                  label: "Reference brake",
                  color: "#d89079",
                  values: numeric(
                    valueSlice(comparison.reference_trace.values.brake),
                  ),
                  mask: maskSlice(comparison.reference_trace.masks.brake),
                },
                {
                  label: "Reference throttle",
                  color: "#97b2a9",
                  values: numeric(
                    valueSlice(comparison.reference_trace.values.throttle),
                  ),
                  mask: maskSlice(comparison.reference_trace.masks.throttle),
                },
              ]}
            />
          </div>
        ) : (
          <p className="region-empty-trace">
            No shared distance-grid samples fall inside this analysis window.
          </p>
        )}
      </details>
    </article>
  );
}

function RegionStat({
  title,
  value,
  detail,
}: {
  title: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="region-stat">
      <span>{title}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function RegionEventEvidence({
  title,
  target,
  reference,
  targetCoverage,
  referenceCoverage,
  note,
}: {
  title: string;
  target: RegionEvent;
  reference: RegionEvent;
  targetCoverage: number | null;
  referenceCoverage: number | null;
  note?: string;
}) {
  return (
    <section className="region-event">
      <div className="region-subhead">
        <strong>{title}</strong>
        <span>
          Coverage {percent(targetCoverage)} / {percent(referenceCoverage)}
        </span>
      </div>
      <RegionEventLine label="TARGET" event={target} />
      <RegionEventLine label="REFERENCE" event={reference} />
      {note ? <p>{note}</p> : null}
    </section>
  );
}

function RegionEventLine({
  label: title,
  event,
}: {
  label: string;
  event: RegionEvent;
}) {
  return (
    <div className="region-event-line">
      <span>
        {title} · {label(event.status)}
      </span>
      {event.events.length ? (
        <ul>
          {event.events.map((item, index) => (
            <li key={`${item.start_distance_m}-${index}`}>
              {item.start_distance_bracket_m
                ? `${formatDistance(item.start_distance_bracket_m[0])}–${formatDistance(item.start_distance_bracket_m[1])} m onset bracket`
                : `${formatDistance(item.start_distance_m)} m onset; unbracketed`}
              {` · ${item.duration_s.toFixed(2)} s`}
              {item.left_censored ? " · left-censored" : ""}
              {item.right_censored ? " · right-censored" : ""}
            </li>
          ))}
        </ul>
      ) : (
        <small>
          {event.reason ? label(event.reason) : "No supported event"}
        </small>
      )}
    </div>
  );
}

function formatDistance(value: number) {
  return value.toFixed(1);
}

function speedValue(value: number | null) {
  return value == null ? "—" : `${value.toFixed(1)} km/h`;
}

function signedValue(value: number | null, unit: string) {
  if (value == null) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toFixed(1)} ${unit}`;
}

function Chart({
  title,
  subtitle,
  unit,
  distance,
  series,
  range,
  zero = false,
  percentAxis = false,
}: {
  title: string;
  subtitle: string;
  unit: string;
  distance: number[];
  series: Series[];
  range?: [number, number];
  zero?: boolean;
  percentAxis?: boolean;
}) {
  const w = 1000,
    h = 220,
    left = 58,
    right = 18,
    top = 17,
    bottom = 30;
  const plotH = h - top - bottom,
    plotW = w - left - right;
  const numbers = series.flatMap((line) =>
    line.values.filter(
      (value, index): value is number =>
        value !== null &&
        Number.isFinite(value) &&
        (line.mask?.[index] ?? true),
    ),
  );
  let min = range?.[0] ?? Math.min(...numbers, zero ? 0 : Infinity);
  let max = range?.[1] ?? Math.max(...numbers, zero ? 0 : -Infinity);
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    min = 0;
    max = 1;
  }
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const pad = range ? 0 : (max - min) * 0.08;
  min -= pad;
  max += pad;
  const y = (value: number) => top + ((max - value) / (max - min)) * plotH;
  const x = (index: number) =>
    left + (index / Math.max(1, distance.length - 1)) * plotW;
  const ticks = Array.from(
    { length: 4 },
    (_, index) => min + ((max - min) * index) / 3,
  );
  return (
    <section className="chart-card panel">
      <div className="chart-header">
        <div>
          <h3>{title}</h3>
          <p>{subtitle}</p>
        </div>
        <span className="chart-unit">{unit}</span>
      </div>
      <div className="chart-wrap">
        <svg
          viewBox={`0 0 ${w} ${h}`}
          role="img"
          aria-label={`${title} over lap distance`}
        >
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                className="grid-line"
                x1={left}
                x2={w - right}
                y1={y(tick)}
                y2={y(tick)}
              />
              <text
                className="axis-label"
                x={left - 9}
                y={y(tick) + 4}
                textAnchor="end"
              >
                {percentAxis ? Math.round(tick * 100) : tick.toFixed(1)}
              </text>
            </g>
          ))}
          {zero && min < 0 && max > 0 ? (
            <line
              className="zero-line"
              x1={left}
              x2={w - right}
              y1={y(0)}
              y2={y(0)}
            />
          ) : null}
          {series.map((line) => (
            <path
              key={line.label}
              d={makePath(line, x, y, displayIndices(line, distance.length))}
              fill="none"
              stroke={line.color}
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {distance.length ? (
            <>
              <text className="axis-label" x={left} y={h - 6}>
                {Math.round(distance[0]).toLocaleString()} m
              </text>
              <text
                className="axis-label"
                x={w - right}
                y={h - 6}
                textAnchor="end"
              >
                {Math.round(distance.at(-1) ?? 0).toLocaleString()} m
              </text>
            </>
          ) : null}
        </svg>
      </div>
      <div className="chart-legend">
        {series.map((line) => (
          <span key={line.label}>
            <i style={{ background: line.color }} />
            {line.label}
          </span>
        ))}
        <span className="legend-gap">Gaps break the line</span>
      </div>
    </section>
  );
}

function makePath(
  line: Series,
  x: (index: number) => number,
  y: (value: number) => number,
  indices: number[],
) {
  const segments: string[] = [];
  let current: string[] = [];
  indices.forEach((index) => {
    const value = line.values[index];
    if (
      value === null ||
      value === undefined ||
      !Number.isFinite(value) ||
      !(line.mask?.[index] ?? true)
    ) {
      if (current.length > 1) segments.push(current.join(" "));
      current = [];
      return;
    }
    current.push(
      `${current.length ? "L" : "M"}${x(index).toFixed(2)},${y(value).toFixed(2)}`,
    );
  });
  if (current.length > 1) segments.push(current.join(" "));
  return segments.join(" ");
}

function displayIndices(line: Series, length: number) {
  if (length <= 1200) return Array.from({ length }, (_, index) => index);

  const stride = Math.ceil(length / 1200);
  const indices = new Set<number>();
  const supported = (index: number) => {
    const value = line.values[index];
    return (
      value !== null &&
      value !== undefined &&
      Number.isFinite(value) &&
      (line.mask?.[index] ?? true)
    );
  };

  for (let index = 0; index < length; index += stride) indices.add(index);
  indices.add(length - 1);

  for (let index = 1; index < length; index++) {
    if (supported(index) !== supported(index - 1)) {
      indices.add(index - 1);
      indices.add(index);
    }
  }

  return [...indices].sort((a, b) => a - b);
}

const speed = (values: Array<number | boolean | null>) =>
  values.map((value) => (typeof value === "number" ? value * 3.6 : null));
const firstParam = (value: string | string[] | undefined) =>
  Array.isArray(value) ? value[0] : value;
const modelKey = (model: TrackModelRecord) =>
  `${model.model_id}@${model.revision}`;
function comparisonQuery(
  targetAttemptKey: string,
  referenceAttemptKey: string,
  model: TrackModelRecord | null,
) {
  const query = new URLSearchParams({
    target_attempt_key: targetAttemptKey,
    reference_attempt_key: referenceAttemptKey,
  });
  if (model) {
    query.set("track_model_id", model.model_id);
    query.set("track_model_revision", String(model.revision));
  }
  return query.toString();
}
const numeric = (values: Array<number | boolean | null>) =>
  values.map((value) => (typeof value === "number" ? value : null));
const label = (value: unknown) =>
  typeof value === "string" && value
    ? value.replaceAll("_", " ").toUpperCase()
    : "MODE UNKNOWN";
const lapTime = (ms: number | null | undefined) =>
  ms == null
    ? "No official time"
    : `${Math.floor(ms / 60_000)}:${((ms % 60_000) / 1000).toFixed(3).padStart(6, "0")}`;
const seconds = (value: number | null | undefined) =>
  value == null
    ? "—"
    : `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(3)} s`;
const percent = (value: number | null | undefined) =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const reason = (value: string) =>
  ({
    no_prior_candidate_passed_policy: "No prior lap meets policy",
    game_invalid: "Game-invalid",
    game_marked_invalid: "Marked invalid by the game",
    not_reference_eligible: "Not reference-eligible",
    target_attempt: "Target lap",
    recorded_after_target: "Recorded later",
    import_frame_overflow_drops: "Replay assembler dropped packets",
    import_late_packet_drops: "Replay discarded late packets",
  })[value] ?? value.replaceAll("_", " ");
