import type { RecordingJobRecord, ReplayRecord } from "@/lib/api";
import { requestApi } from "@/lib/api";
import {
  readPinnedLiveCurrent,
  readPinnedLiveSelection,
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
import { testDataRoute } from "../TestDataRoute";
import LiveTelemetryScreen from "../LiveTelemetryScreen";

type InitialStatus = PinnedLiveRead["status"] | "unselected" | "invalid";

export default async function LivePage({
  searchParams,
}: {
  searchParams: Promise<AppSearchParams>;
}) {
  const params = await searchParams;
  const syntheticPage = testDataRoute("live", params);
  if (syntheticPage) return syntheticPage;
  const preservedQuery = preservedAppStateQuery(params);
  const blocked = isSelectionTransferBlocked(preservedQuery);
  const selection = readPinnedLiveSelection(params, blocked);

  let status: InitialStatus;
  let snapshot: PinnedLiveSnapshot | null = null;
  let source: LiveSource | null = null;
  if (selection.status !== "ready") {
    status = selection.status;
  } else {
    source = selection.source;
    const response =
      source === "recording"
        ? await requestApi<RecordingJobRecord>("/api/v1/recordings/current")
        : await requestApi<ReplayRecord>("/api/v1/replays/current");
    const current = readPinnedLiveCurrent(
      source,
      selection.operationId,
      response,
    );
    status = current.status;
    snapshot = current.snapshot;
  }
  const operationId =
    selection.status === "ready" ? selection.operationId : null;

  return (
    <div className="app-shell">
      <AppHeader active="live" preservedQuery={preservedQuery} />
      <main
        className="page-content reference-page-content"
        id="main-content"
        tabIndex={-1}
      >
        <LiveTelemetryScreen
          key={JSON.stringify([source, operationId])}
          initialStatus={status}
          initialSnapshot={snapshot}
          source={source}
          operationId={operationId}
          recordingsHref={
            blocked ? null : appScreenHref("recordings", preservedQuery)
          }
        />
        <details className="ref-workflow-details">
          <summary>Open the pinned live telemetry workflow</summary>
          <div className="ref-workflow-content">
            <section className="live-page-intro">
              <div className="eyebrow">LIVE / PINNED TELEMETRY SOURCE</div>
              <h1>
                Stay with the
                <br />
                <span>same operation.</span>
              </h1>
              <p>
                Live monitoring is read-only. This view stays attached to the
                recording or replay selected from its controls and will never
                switch to a newer operation by itself.
              </p>
            </section>
            <a
              className="ref-button"
              href={
                appScreenHref("recordings", preservedQuery) ?? "/recordings"
              }
            >
              Recording and replay controls
            </a>
          </div>
        </details>
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
