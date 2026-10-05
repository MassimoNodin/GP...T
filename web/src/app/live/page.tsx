import type { RecordingJobRecord, ReplayRecord } from "@/lib/api";
import { requestApi } from "@/lib/api";
import {
  readPinnedLiveCurrent,
  type LiveSource,
  type PinnedLiveRead,
  type PinnedLiveSnapshot,
} from "@/lib/live";
import {
  appScreenHref,
  isSelectionTransferBlocked,
  preservedAppStateQuery,
  type AppSearchParams,
} from "@/lib/navigation";
import AppHeader from "../AppHeader";
import LiveTelemetryView from "../LiveTelemetryView";

type InitialStatus = PinnedLiveRead["status"] | "unselected" | "invalid";

export default async function LivePage({
  searchParams,
}: {
  searchParams: Promise<AppSearchParams>;
}) {
  const params = await searchParams;
  const preservedQuery = preservedAppStateQuery(params);
  const blocked = isSelectionTransferBlocked(preservedQuery);
  const sourceParam = singleParam(params.live_source);
  const operationId = singleParam(params.live_operation_id);
  const noSelection = sourceParam === undefined && operationId === undefined;
  const duplicated = hasMultiple(params.live_source) || hasMultiple(params.live_operation_id);
  const validSource =
    sourceParam === "recording" || sourceParam === "replay"
      ? sourceParam
      : null;
  const validOperationId =
    typeof operationId === "string" && /^[a-f0-9]{32}$/.test(operationId)
      ? operationId
      : null;

  let status: InitialStatus;
  let snapshot: PinnedLiveSnapshot | null = null;
  let source: LiveSource | null = null;
  if (blocked) {
    status = "invalid";
  } else if (noSelection) {
    status = "unselected";
  } else if (duplicated || !validSource || !validOperationId) {
    status = "invalid";
  } else {
    source = validSource;
    const response =
      source === "recording"
        ? await requestApi<RecordingJobRecord>("/api/v1/recordings/current")
        : await requestApi<ReplayRecord>("/api/v1/replays/current");
    const current = readPinnedLiveCurrent(source, validOperationId, response);
    status = current.status;
    snapshot = current.snapshot;
  }

  return (
    <div className="app-shell">
      <AppHeader active="live" preservedQuery={preservedQuery} />
      <main className="page-content live-page-content" id="main-content" tabIndex={-1}>
        <section className="live-page-intro">
          <div className="eyebrow">LIVE / PINNED TELEMETRY SOURCE</div>
          <h1>
            Stay with the
            <br />
            <span>same operation.</span>
          </h1>
          <p>
            Live monitoring is read-only. This view stays attached to the
            recording or replay selected from its controls and will never switch
            to a newer operation by itself.
          </p>
        </section>
        <LiveTelemetryView
          key={JSON.stringify([source, validOperationId, status, snapshot])}
          initialStatus={status}
          initialSnapshot={snapshot}
          source={source}
          operationId={validOperationId}
          recordingsHref={
            blocked ? null : appScreenHref("recordings", preservedQuery)
          }
        />
      </main>
      <footer className="footer-bar">
        <span>
          GP...T <b>·</b> LOCAL FIRST
        </span>
        <span>Live telemetry · Pinned local operation</span>
      </footer>
    </div>
  );
}

function singleParam(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

function hasMultiple(value: string | string[] | undefined) {
  return Array.isArray(value) && value.length !== 1;
}
