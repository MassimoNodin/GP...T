import {
  CarObservationInventory,
  CarObservationPreview,
  ProcessingRunDetail,
  ProcessingRunPage,
  ProcessingRunSummary,
  requestApi,
} from "@/lib/api";
import {
  isSelectionTransferBlocked,
  preservedAppStateQuery,
  type AppScreen,
  type AppSearchParams,
} from "@/lib/navigation";
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
  const detailQuery = new URLSearchParams({
    session_limit: "20",
    session_offset: String(sessionOffset),
    attempt_limit: "20",
    attempt_offset: String(attemptOffset),
    lifecycle_event_limit: "50",
    lifecycle_event_offset: String(lifecycleEventOffset),
  });
  const [runsResponse, detailResponse] = await Promise.all([
    requestApi<ProcessingRunPage<ProcessingRunSummary>>(
      `/api/v1/processing-runs?limit=10&offset=${runOffset}`,
    ),
    runId
      ? requestApi<ProcessingRunDetail>(
          `/api/v1/processing-runs/${encodeURIComponent(runId)}?${detailQuery}`,
        )
      : Promise.resolve(null),
  ]);
  const detail = detailResponse?.status === "ok" ? detailResponse.data : null;
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
        runsPage={runsResponse?.status === "ok" ? runsResponse.data : null}
        detail={detail}
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
      />
    </>
  );
}

function firstParam(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
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
