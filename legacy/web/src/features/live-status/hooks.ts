"use client";

import { useEffect, useState } from "react";
import { createAcquisitionPoller, type AcquisitionStatus } from "@/lib/acquisition-status";
import { parseRuntimeInfo, type RuntimeInfo } from "./map";
import { createEndpointPoller, createFailureFilter } from "./poller";

// `undefined` = not known on the client yet (the server-rendered props are
// shown); `null` = the backend did not answer.

export const POLL_INTERVAL_MS = 2000;

export function useAcquisitionFeed(
  initial: AcquisitionStatus | null | undefined,
  intervalMs = POLL_INTERVAL_MS,
): AcquisitionStatus | null | undefined {
  const [value, setValue] = useState(initial);
  useEffect(() => {
    const push = createFailureFilter<AcquisitionStatus>((next) => setValue(next));
    // The poller reports null on every hide/show; those are not poll failures.
    let syncing = false;
    const poller = createAcquisitionPoller({
      intervalMs,
      onStatus: (status) => {
        if (!syncing) push(status);
      },
    });
    const apply = () => {
      syncing = true;
      poller.setHidden(document.hidden);
      syncing = false;
    };
    apply();
    document.addEventListener("visibilitychange", apply);
    return () => {
      document.removeEventListener("visibilitychange", apply);
      poller.stop();
    };
  }, [intervalMs]);
  return value;
}

export function useRuntimeFeed(
  initial: RuntimeInfo | null | undefined,
  intervalMs = POLL_INTERVAL_MS,
): RuntimeInfo | null | undefined {
  const [value, setValue] = useState(initial);
  useEffect(() => {
    const push = createFailureFilter<RuntimeInfo>((next) => setValue(next));
    const poller = createEndpointPoller({
      url: "/api/engineer/runtime",
      parse: (body) => parseRuntimeInfo(body),
      onResult: push,
      intervalMs,
    });
    const apply = () => poller.setHidden(document.hidden);
    apply();
    document.addEventListener("visibilitychange", apply);
    return () => {
      document.removeEventListener("visibilitychange", apply);
      poller.stop();
    };
  }, [intervalMs]);
  return value;
}
