"use client";

import { useEffect, useState } from "react";
import {
  isPinnedLiveActive,
  readPinnedLiveCurrent,
  type LiveSource,
  type PinnedLiveSnapshot,
} from "@/lib/live";
import {
  createPinnedLivePoller,
  type PinnedLiveMonitorState,
} from "@/lib/pinned-live-poller";

export type PinnedLiveViewStatus = PinnedLiveMonitorState["status"];

export function usePinnedLiveCurrent({
  initialStatus,
  initialSnapshot,
  source,
  operationId,
}: {
  initialStatus: PinnedLiveViewStatus;
  initialSnapshot: PinnedLiveSnapshot | null;
  source: LiveSource | null;
  operationId: string | null;
}) {
  const [state, setState] = useState<PinnedLiveMonitorState>({
    status: initialStatus,
    snapshot: initialSnapshot,
  });

  useEffect(() => {
    const poller = createPinnedLivePoller({
      initialStatus,
      initialSnapshot,
      source,
      operationId,
      readCurrent: readPinnedLiveCurrent,
      isActive: isPinnedLiveActive,
    });
    const update = () => setState(poller.getState());
    const unsubscribe = poller.subscribe(update);
    const handleVisibilityChange = () =>
      poller.setHidden(document.visibilityState === "hidden");
    document.addEventListener("visibilitychange", handleVisibilityChange);
    poller.start(document.visibilityState === "hidden");
    return () => {
      unsubscribe();
      poller.stop();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [initialSnapshot, initialStatus, operationId, source]);

  return state;
}
