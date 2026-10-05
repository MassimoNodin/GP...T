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
import BrowserHud from "../BrowserHud";

type InitialStatus = PinnedLiveRead["status"] | "unselected" | "invalid";

export default async function HudPage({
  searchParams,
}: {
  searchParams: Promise<AppSearchParams>;
}) {
  const params = await searchParams;
  const preservedQuery = preservedAppStateQuery(params);
  const blocked = isSelectionTransferBlocked(preservedQuery);
  const selection = readPinnedLiveSelection(params, blocked);

  let status: InitialStatus = selection.status;
  let snapshot: PinnedLiveSnapshot | null = null;
  let source: LiveSource | null = null;
  if (selection.status === "ready") {
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
  const liveHref =
    selection.status === "ready" && !blocked
      ? appScreenHref("live", preservedQuery)
      : null;

  return (
    <BrowserHud
      key={JSON.stringify([source, operationId, status, snapshot])}
      initialStatus={status}
      initialSnapshot={snapshot}
      source={source}
      operationId={operationId}
      liveHref={liveHref}
    />
  );
}
