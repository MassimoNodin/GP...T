import {
  LapAttemptPage,
  CarObservationInventory,
  CarObservationPreview,
  ProcessingRunArtifactInventory,
  ProcessingRunArtifactKind,
  ProcessingRunDetail,
  ProcessingRunPage,
  ProcessingRunSummary,
  RunArchiveFilterValues,
  RunArchiveFilters,
  SessionBestOverview,
  requestApi,
} from "@/lib/api";
import { attemptInventoryUrl } from "@/lib/attempt-inventory";
import {
  isSelectionTransferBlocked,
  preservedAppStateQuery,
  type AppScreen,
  type AppSearchParams,
} from "@/lib/navigation";
import {
  resolveLapOrderAnchor,
  sessionBestOverviewMatchesAnchor,
  type LapOrderAssessmentState,
} from "@/lib/session-best-assessment";
import RunEvidencePanel from "./RunEvidencePanel";

export default async function SessionsRunEvidence({
  searchParams,
  screen,
}: {
  searchParams: Promise<AppSearchParams>;
  screen: AppScreen;
}) {
  const params = await searchParams;
  const preservedQuery = preservedAppStateQuery(params);
  const selectionTransferBlocked = isSelectionTransferBlocked(preservedQuery);
  const requestedRunId = firstParam(params.run_id);
  const runId =
    !selectionTransferBlocked && /^[a-f0-9]{64}$/.test(requestedRunId ?? "")
      ? requestedRunId!
      : null;
  const requestedRunUnavailable =
    !selectionTransferBlocked && requestedRunId !== undefined && runId === null;
  const runOffset = pageOffset(firstParam(params.run_offset));
  const sessionOffset = pageOffset(firstParam(params.session_offset));
  const attemptOffset = pageOffset(firstParam(params.attempt_offset));
  const lifecycleEventOffset = pageOffset(
    firstParam(params.lifecycle_event_offset),
  );
  const artifactRequest = inspectArtifactRequest(params);
  const artifactSelectionMatches = singleParam(params.run_id) === runId;
  const archiveFilterRequest =
    screen === "sessions"
      ? inspectArchiveFilterRequest(params, runOffset)
      : null;
  const archiveFilterValues =
    archiveFilterRequest?.values ?? emptyArchiveFilterValues();
  const detailQuery = new URLSearchParams({
    session_limit: "20",
    session_offset: String(sessionOffset),
    attempt_limit: "20",
    attempt_offset: String(attemptOffset),
    lifecycle_event_limit: "50",
    lifecycle_event_offset: String(lifecycleEventOffset),
  });
  const runListQuery = new URLSearchParams({
    limit: "10",
    offset: String(runOffset),
  });
  if (
    screen === "sessions" &&
    archiveFilterRequest &&
    !archiveFilterRequest.failure
  ) {
    appendArchiveFilterParams(runListQuery, archiveFilterRequest.entries);
  }
  const [runsResponse, detailResponse, artifactResponse] = await Promise.all([
    archiveFilterRequest?.failure
      ? Promise.resolve(null)
      : requestApi<ProcessingRunPage<ProcessingRunSummary>>(
          `/api/v1/processing-runs?${runListQuery}`,
        ),
    runId
      ? requestApi<ProcessingRunDetail>(
          `/api/v1/processing-runs/${encodeURIComponent(runId)}?${detailQuery}`,
        )
      : Promise.resolve(null),
    screen === "sessions" && runId && artifactSelectionMatches && !artifactRequest.failure
      ? requestApi<ProcessingRunArtifactInventory>(
          `/api/v1/processing-runs/${encodeURIComponent(runId)}/artifacts?${new URLSearchParams({
            kind: artifactRequest.kind,
            limit: "50",
            offset: String(artifactRequest.offset),
          })}`,
        )
      : Promise.resolve(null),
  ]);
  const artifactInventory =
    artifactResponse?.status === "ok" &&
    artifactResponse.data &&
    isMatchingArtifactInventory(
      artifactResponse.data,
      runId,
      artifactRequest.kind,
      artifactRequest.offset,
    )
      ? artifactResponse.data
      : null;
  const artifactInventoryFailure =
    screen !== "sessions" || !runId
      ? null
      : !artifactSelectionMatches
        ? { kind: "unavailable" as const, reason: "selected_run_identity_repeated" }
        : artifactRequest.failure
          ? { kind: "unavailable" as const, reason: artifactRequest.failure }
          : artifactResponse === null
            ? { kind: "request_failed" as const, reason: null }
            : artifactResponse.status !== "ok" || !artifactResponse.data
              ? { kind: "unavailable" as const, reason: artifactResponse.reason }
              : artifactInventory === null
                ? { kind: "unavailable" as const, reason: "processing_run_artifact_response_mismatch" }
                : null;
  const detail = detailResponse?.status === "ok" ? detailResponse.data : null;
  const lapOrderAssessment = await loadLapOrderAssessment({
    requested: params.assess_lap_order !== undefined,
    marker: singleParam(params.assess_lap_order),
    anchorAttemptKey: singleParam(params.target_attempt_key),
    sessionUid: singleParam(params.lap_order_session_uid),
    requestedRunId: singleParam(params.run_id),
    screen,
    selectionTransferBlocked,
    runId,
    detail,
  });
  const requestedObservationSessionUid = firstParam(
    params.observation_session_uid,
  );
  const observationSessionUid = requestedObservationSessionUid?.trim()
    ? requestedObservationSessionUid
    : null;
  const observationSession =
    observationSessionUid && detail
      ? (detail.sessions.items.find(
          (item) => item.session_uid === observationSessionUid,
        ) ?? null)
      : null;
  const inventoryResponse =
    runId && observationSession
      ? await requestApi<CarObservationInventory>(
          `/api/v1/processing-runs/${encodeURIComponent(runId)}/sessions/${encodeURIComponent(observationSession.session_uid)}/cars?limit=24&offset=0`,
        )
      : null;
  const inventory =
    inventoryResponse?.status === "ok" ? inventoryResponse.data : null;
  const observationCarIndexParam = firstParam(
    params.observation_car_index,
  )?.trim()
    ? firstParam(params.observation_car_index)!
    : null;
  const observationCarIndex = parseCarIndex(observationCarIndexParam);
  const observationOffsetParam = firstParam(params.observation_offset);
  const observationOffset = parseObservationOffset(observationOffsetParam);
  const selectedObservationSlot =
    observationCarIndex === null
      ? null
      : (inventory?.slots.items.find(
          (slot) => slot.car_index === observationCarIndex,
        ) ?? null);
  const shouldLoadPreview = Boolean(
    runId &&
    observationSession &&
    selectedObservationSlot &&
    inventory?.archive_status === "available" &&
    observationOffset !== null,
  );
  const previewResponse =
    shouldLoadPreview && runId && observationSession && selectedObservationSlot
      ? await requestApi<CarObservationPreview>(
          `/api/v1/processing-runs/${encodeURIComponent(runId)}/sessions/${encodeURIComponent(observationSession.session_uid)}/cars/${selectedObservationSlot.car_index}/observations?limit=50&offset=${observationOffset}`,
        )
      : null;
  const preview =
    previewResponse?.status === "ok" ? previewResponse.data : null;
  const inventoryFailure = observationSession
    ? inventoryResponse === null
      ? { kind: "request_failed" as const, reason: null }
      : inventoryResponse.status !== "ok" || !inventoryResponse.data
        ? {
            kind: "unavailable" as const,
            reason: inventoryResponse.reason,
          }
        : null
    : null;
  const previewFailure = shouldLoadPreview
    ? previewResponse === null
      ? { kind: "request_failed" as const, reason: null }
      : previewResponse.status !== "ok" || !previewResponse.data
        ? { kind: "unavailable" as const, reason: previewResponse.reason }
        : null
    : null;
  const archivePageMatchesRequest =
    screen !== "sessions" ||
    (runsResponse?.status === "ok" &&
      archiveFilterRequest?.expected !== null &&
      archiveFilterRequest?.expected !== undefined &&
      isMatchingArchiveRunPage(
        runsResponse.data,
        archiveFilterRequest.expected,
        runOffset,
      ));
  const runsPage =
    runsResponse?.status === "ok" &&
    (screen !== "sessions" || archivePageMatchesRequest)
      ? runsResponse.data
      : null;
  const runsFailure = archiveFilterRequest?.failure
    ? "archive_filter_request_invalid"
    : runsResponse === null
      ? "request_failed"
      : runsResponse.status === "unavailable"
        ? runsResponse.reason
        : screen === "sessions" && !archivePageMatchesRequest
          ? "archive_filter_response_mismatch"
          : null;

  return (
    <>
      {selectionTransferBlocked ? (
        <section className="connection-state panel" role="status">
          <span className="state-icon">!</span>
          <div>
            <h2>Selection transfer is paused</h2>
            <p>
              GP...T did not carry an incomplete selection into this archive.
              Choose a run from the list below to inspect it explicitly.
            </p>
          </div>
        </section>
      ) : null}
      <RunEvidencePanel
        runsPage={runsPage}
        runsFailure={runsFailure}
        archiveFilterValues={archiveFilterValues}
        detail={detail}
        artifactInventory={artifactInventory}
        artifactInventoryFailure={artifactInventoryFailure}
        artifactOffset={artifactRequest.offset}
        artifactKind={artifactRequest.kind}
        observationInventory={inventory}
        observationPreview={preview}
        observationSessionUid={observationSessionUid}
        observationCarIndexParam={observationCarIndexParam}
        observationCarIndex={observationCarIndex}
        observationOffset={observationOffset}
        observationOffsetParam={observationOffsetParam ?? null}
        observationInventoryFailure={inventoryFailure}
        observationPreviewFailure={previewFailure}
        runId={runId}
        runUnavailable={requestedRunUnavailable}
        runOffset={runOffset}
        sessionOffset={sessionOffset}
        attemptOffset={attemptOffset}
        lifecycleEventOffset={lifecycleEventOffset}
        screen={screen}
        preservedQuery={preservedQuery}
        lapOrderAssessment={lapOrderAssessment}
      />
    </>
  );
}

type ArtifactRequest = {
  kind: "all" | ProcessingRunArtifactKind;
  offset: number;
  failure: string | null;
};

function inspectArtifactRequest(params: AppSearchParams): ArtifactRequest {
  const rawKinds = params.artifact_kind;
  const kindValues = rawKinds === undefined ? [] : Array.isArray(rawKinds) ? rawKinds : [rawKinds];
  if (kindValues.length > 1) {
    return { kind: "all", offset: 0, failure: "invalid_processing_run_artifact_repeated_parameter" };
  }
  const kindValue = kindValues[0] ?? "all";
  if (!["all", "player_trace", "car_observation_chunk"].includes(kindValue)) {
    return { kind: "all", offset: 0, failure: "invalid_processing_run_artifact_kind" };
  }

  const rawOffsets = params.artifact_offset;
  const offsetValues = rawOffsets === undefined ? [] : Array.isArray(rawOffsets) ? rawOffsets : [rawOffsets];
  if (offsetValues.length > 1) {
    return { kind: kindValue as ArtifactRequest["kind"], offset: 0, failure: "invalid_processing_run_artifact_repeated_parameter" };
  }
  const offsetValue = offsetValues[0];
  if (offsetValue === undefined) {
    return { kind: kindValue as ArtifactRequest["kind"], offset: 0, failure: null };
  }
  if (!/^(?:0|[1-9][0-9]{0,5})$/.test(offsetValue)) {
    return { kind: kindValue as ArtifactRequest["kind"], offset: 0, failure: "invalid_processing_run_artifact_offset" };
  }
  const offset = Number(offsetValue);
  if (offset > 100_000) {
    return { kind: kindValue as ArtifactRequest["kind"], offset: 0, failure: "invalid_processing_run_artifact_offset" };
  }
  return { kind: kindValue as ArtifactRequest["kind"], offset, failure: null };
}

function isMatchingArtifactInventory(
  value: ProcessingRunArtifactInventory,
  runId: string | null,
  kind: ArtifactRequest["kind"],
  offset: number,
): boolean {
  if (!runId || !value || typeof value !== "object") return false;
  if (
    value.run_id !== runId ||
    value.query?.kind !== kind ||
    value.query?.limit !== 50 ||
    value.query?.offset !== offset ||
    !Number.isSafeInteger(value.total) ||
    value.total < 0 ||
    !Array.isArray(value.items) ||
    value.items.length > 50 ||
    typeof value.has_more !== "boolean"
  ) {
    return false;
  }
  if (
    !(value.capture_sha256 === null || /^[a-f0-9]{64}$/.test(value.capture_sha256)) ||
    !(value.processing_status === null || ["processing", "complete", "failed"].includes(value.processing_status)) ||
    !Array.isArray(value.run_metadata_reasons) ||
    value.run_metadata_reasons.length > 8 ||
    typeof value.metadata_snapshot_at_utc !== "string" ||
    value.metadata_snapshot_at_utc.length > 40 ||
    typeof value.filesystem_observed_at_utc !== "string" ||
    value.filesystem_observed_at_utc.length > 40 ||
    value.has_more !== (offset + value.items.length < value.total)
  ) {
    return false;
  }
  return value.items.every((item) => {
    if (!item || typeof item !== "object") return false;
    const nullableInteger = (candidate: unknown, minimum = 0, maximum = 2_147_483_647) =>
      candidate === null ||
      (Number.isSafeInteger(candidate) && Number(candidate) >= minimum && Number(candidate) <= maximum);
    const nullableString = (candidate: unknown, maximumLength = 512) =>
      candidate === null || (typeof candidate === "string" && candidate.length <= maximumLength);
    return (
      item.run_id === runId &&
      /^[a-f0-9]{64}$/.test(item.artifact_id) &&
      ["player_trace", "car_observation_chunk"].includes(item.artifact_kind) &&
      (kind === "all" || item.artifact_kind === kind) &&
      nullableString(item.session_uid, 20) &&
      nullableString(item.attempt_key) &&
      nullableInteger(item.attempt_number, 1) &&
      nullableInteger(item.car_index, 0, 23) &&
      nullableInteger(item.packet_format, 1, 65_535) &&
      nullableInteger(item.lifecycle_epoch) &&
      nullableInteger(item.chunk_ordinal) &&
      nullableInteger(item.schema_version, 1, 65_535) &&
      nullableInteger(item.row_count) &&
      (item.stored_sha256 === null || /^[a-f0-9]{64}$/.test(item.stored_sha256)) &&
      ["ready", "not_ready", "unknown"].includes(item.registration_readiness) &&
      ["present", "missing", "unavailable"].includes(item.filesystem_availability) &&
      nullableString(item.filesystem_reason, 128) &&
      (item.observed_size_bytes === null ||
        (Number.isSafeInteger(item.observed_size_bytes) && item.observed_size_bytes >= 0)) &&
      item.checksum_verification === "not_performed" &&
      Array.isArray(item.metadata_reasons) &&
      item.metadata_reasons.length <= 16 &&
      item.metadata_reasons.every((reason) => typeof reason === "string" && reason.length <= 128)
    );
  });
}

const archiveFilterKeys = [
  "q",
  "packet_format",
  "track_id",
  "session_category",
  "started_from",
  "started_through",
] as const;

const archiveCategories = [
  "time_trial",
  "practice",
  "qualifying",
  "race",
  "unknown",
] as const;
const maximumArchiveFilterValueLength = 512;
const maximumArchiveFilterQueryLength = 4096;
const maximumArchiveFilterValuesPerKey = 8;

type ArchiveFilterRequest = {
  entries: Array<[string, string]>;
  expected: RunArchiveFilters | null;
  failure: boolean;
  values: RunArchiveFilterValues;
};

function emptyArchiveFilterValues(): RunArchiveFilterValues {
  return {
    q: "",
    packet_format: "",
    track_id: "",
    session_category: "",
    started_from: "",
    started_through: "",
  };
}

function inspectArchiveFilterRequest(
  params: AppSearchParams,
  runOffset: number,
): ArchiveFilterRequest {
  const values = emptyArchiveFilterValues();
  const entries: Array<[string, string]> = [];
  for (const key of archiveFilterKeys) {
    const value = params[key];
    const incoming =
      value === undefined ? [] : Array.isArray(value) ? value : [value];
    if (
      incoming.length > maximumArchiveFilterValuesPerKey ||
      incoming.length > 1 ||
      incoming.some((item) => item.length > maximumArchiveFilterValueLength)
    ) {
      return {
        entries: [],
        expected: null,
        failure: true,
        values: emptyArchiveFilterValues(),
      };
    }
    const item = incoming[0] ?? "";
    values[key] = item;
    if (item.trim()) entries.push([key, item]);
  }

  const boundedQuery = new URLSearchParams({
    limit: "10",
    offset: String(runOffset),
  });
  for (const [key, value] of entries) boundedQuery.append(key, value);
  if (boundedQuery.toString().length > maximumArchiveFilterQueryLength) {
    return {
      entries: [],
      expected: null,
      failure: true,
      values: emptyArchiveFilterValues(),
    };
  }

  return {
    entries,
    expected: normalizeArchiveFilterValues(values),
    failure: false,
    values,
  };
}

function appendArchiveFilterParams(
  query: URLSearchParams,
  entries: Array<[string, string]>,
) {
  for (const [key, value] of entries) query.append(key, value);
}

function normalizeArchiveFilterValues(
  values: RunArchiveFilterValues,
): RunArchiveFilters | null {
  const q = values.q.trim();
  if (q && !/^[a-f\d]{3,64}$/i.test(q)) return null;

  const packetFormat = values.packet_format.trim();
  const trackIdValue = values.track_id.trim();
  if (Boolean(packetFormat) !== Boolean(trackIdValue)) return null;
  if (packetFormat && !["2025", "2026"].includes(packetFormat)) return null;
  if (trackIdValue && !/^(?:0|[1-9]\d{0,2})$/.test(trackIdValue)) return null;
  const trackId = trackIdValue ? Number(trackIdValue) : null;
  if (trackId !== null && trackId > 127) return null;

  const sessionCategory = values.session_category.trim().toLowerCase();
  if (
    sessionCategory &&
    !archiveCategories.includes(sessionCategory as (typeof archiveCategories)[number])
  ) {
    return null;
  }

  const startedFrom = values.started_from.trim();
  const startedThrough = values.started_through.trim();
  if (
    (startedFrom && !isCalendarDate(startedFrom)) ||
    (startedThrough && !isCalendarDate(startedThrough)) ||
    (startedFrom && startedThrough && startedFrom > startedThrough)
  ) {
    return null;
  }

  return {
    q: q ? q.toLowerCase() : null,
    packet_format: packetFormat ? Number(packetFormat) : null,
    track_id: trackId,
    session_category: sessionCategory
      ? (sessionCategory as RunArchiveFilters["session_category"])
      : null,
    started_from: startedFrom || null,
    started_through: startedThrough || null,
  };
}

function isCalendarDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) {
    return false;
  }
  const parsed = new Date(`${value}T00:00:00.000Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function isMatchingArchiveRunPage(
  value: unknown,
  expectedFilters: RunArchiveFilters,
  expectedOffset: number,
): value is ProcessingRunPage<ProcessingRunSummary> {
  if (!value || typeof value !== "object") return false;
  const page = value as Partial<ProcessingRunPage<ProcessingRunSummary>>;
  if (
    !Array.isArray(page.items) ||
    page.items.length > 10 ||
    !Number.isSafeInteger(page.total) ||
    (page.total as number) < 0 ||
    page.limit !== 10 ||
    page.offset !== expectedOffset ||
    (page.total as number) < page.items.length ||
    !isRunArchiveFilterEcho(page.filters)
  ) {
    return false;
  }
  return archiveFiltersEqual(page.filters, expectedFilters);
}

function isRunArchiveFilterEcho(value: unknown): value is RunArchiveFilters {
  if (!value || typeof value !== "object") return false;
  const filters = value as Partial<RunArchiveFilters>;
  return (
    (filters.q === null || typeof filters.q === "string") &&
    (filters.packet_format === null ||
      (Number.isSafeInteger(filters.packet_format) &&
        typeof filters.packet_format === "number")) &&
    (filters.track_id === null ||
      (Number.isSafeInteger(filters.track_id) &&
        typeof filters.track_id === "number")) &&
    (filters.session_category === null ||
      (typeof filters.session_category === "string" &&
        archiveCategories.includes(
          filters.session_category as (typeof archiveCategories)[number],
        ))) &&
    (filters.started_from === null || typeof filters.started_from === "string") &&
    (filters.started_through === null ||
      typeof filters.started_through === "string")
  );
}

function archiveFiltersEqual(
  actual: RunArchiveFilters,
  expected: RunArchiveFilters,
) {
  return archiveFilterKeys.every((key) => actual[key] === expected[key]);
}

function firstParam(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

function singleParam(value: string | string[] | undefined) {
  if (typeof value === "string") return value;
  return Array.isArray(value) && value.length === 1 ? value[0] : null;
}

async function loadLapOrderAssessment({
  requested,
  marker,
  anchorAttemptKey,
  sessionUid,
  requestedRunId,
  screen,
  selectionTransferBlocked,
  runId,
  detail,
}: {
  requested: boolean;
  marker: string | null;
  anchorAttemptKey: string | null;
  sessionUid: string | null;
  requestedRunId: string | null;
  screen: AppScreen;
  selectionTransferBlocked: boolean;
  runId: string | null;
  detail: ProcessingRunDetail | null;
}): Promise<LapOrderAssessmentState> {
  const emptyState: LapOrderAssessmentState = {
    requested,
    anchorAttempt: null,
    report: null,
    unavailableReason: null,
  };
  if (!requested) return emptyState;
  if (screen !== "sessions") {
    return {
      ...emptyState,
      unavailableReason: "Lap-order assessment is available from Sessions.",
    };
  }
  if (
    selectionTransferBlocked ||
    marker !== "1" ||
    !runId ||
    requestedRunId !== runId ||
    !detail ||
    detail.summary.run_id !== runId ||
    !anchorAttemptKey ||
    anchorAttemptKey.length > 256 ||
    !sessionUid ||
    !/^\d{1,20}$/.test(sessionUid)
  ) {
    return {
      ...emptyState,
      unavailableReason:
        "The selected run, session, or anchor is missing, repeated, or malformed. No substitute attempt was selected.",
    };
  }

  const attemptPageResponse = await requestApi<LapAttemptPage>(
    attemptInventoryUrl({
      runId,
      sessionUid,
      offset: 0,
      limit: 1,
      targetAttemptKey: anchorAttemptKey,
    }),
  );
  if (attemptPageResponse?.status !== "ok" || !attemptPageResponse.data) {
    return {
      ...emptyState,
      unavailableReason: safeReason(
        attemptPageResponse?.reason,
        "The selected attempt could not be resolved in this run and session.",
      ),
    };
  }
  const anchorAttempt = resolveLapOrderAnchor(
    attemptPageResponse.data,
    anchorAttemptKey,
    runId,
    sessionUid,
  );
  if (!anchorAttempt) {
    return {
      ...emptyState,
      unavailableReason:
        "The requested anchor does not match this run and session. No substitute attempt was selected.",
    };
  }

  const response = await requestApi<SessionBestOverview>(
    `/api/v1/analysis/session-best?${new URLSearchParams({
      anchor_attempt_key: anchorAttempt.attempt_key,
    })}`,
  );
  if (response?.status !== "ok" || !response.data) {
    return {
      ...emptyState,
      anchorAttempt,
      unavailableReason: safeReason(
        response?.reason,
        "The lap-order assessment is unavailable from the local API.",
      ),
    };
  }
  if (!sessionBestOverviewMatchesAnchor(response.data, anchorAttempt)) {
    return {
      ...emptyState,
      anchorAttempt,
      unavailableReason:
        "The returned assessment did not match the selected anchor, run, session, or player. It was not shown.",
    };
  }
  return {
    requested: true,
    anchorAttempt,
    report: response.data,
    unavailableReason: null,
  };
}

function safeReason(value: string | null | undefined, fallback: string) {
  return typeof value === "string" && value.length > 0 && value.length <= 160
    ? value.replaceAll("_", " ")
    : fallback;
}

function pageOffset(value: string | undefined) {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= 0
    ? Math.min(parsed, 1_000_000)
    : 0;
}

function parseCarIndex(value: string | null) {
  if (value === null || !/^(?:0|[1-9]\d*)$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed <= 23 ? parsed : null;
}

function parseObservationOffset(value: string | undefined) {
  if (value === undefined) return 0;
  if (!/^(?:0|[1-9]\d*)$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed <= 100_000 ? parsed : null;
}
