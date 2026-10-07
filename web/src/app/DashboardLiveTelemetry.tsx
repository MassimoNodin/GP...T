"use client";

import { readPinnedLiveSelection } from "@/lib/live";
import {
  isSelectionTransferBlocked,
  type AppSearchParams,
} from "@/lib/navigation";
import { usePinnedLiveCurrent } from "@/lib/use-pinned-live-current";
import { LiveTelemetryCard } from "./LiveTelemetryScreen";

export default function DashboardLiveTelemetry({
  preservedQuery,
}: {
  preservedQuery: string;
}) {
  const params: AppSearchParams = {};
  for (const [key, value] of new URLSearchParams(preservedQuery)) {
    const previous = params[key];
    params[key] =
      previous === undefined
        ? value
        : Array.isArray(previous)
          ? [...previous, value]
          : [previous, value];
  }
  const selection = readPinnedLiveSelection(
    params,
    isSelectionTransferBlocked(preservedQuery),
  );
  return (
    <PinnedCard
      key={JSON.stringify([
        selection.source,
        selection.operationId,
        selection.status,
      ])}
      selection={selection}
    />
  );
}
function PinnedCard({
  selection,
}: {
  selection: ReturnType<typeof readPinnedLiveSelection>;
}) {
  const state = usePinnedLiveCurrent({
    initialStatus: selection.status === "ready" ? "error" : selection.status,
    initialSnapshot: null,
    source: selection.source,
    operationId: selection.operationId,
  });
  return (
    <div className="dynamic-live dashboard-live-card">
      <LiveTelemetryCard snapshot={state.snapshot} status={state.status} />
    </div>
  );
}
