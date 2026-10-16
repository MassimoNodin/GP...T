"use client";

import { useEffect, useState } from "react";
import type { AcquisitionStatus } from "@/lib/acquisition-status";

// The receiver reports "stale" whenever no packet has arrived for a few
// seconds, which is also its normal state while the game is closed. Only a
// silence shortly after live data means a session actually went quiet, so the
// browser remembers when it last saw packets arriving.

const KEY = "f1-engineer:last-receiving-at";
/** How long after the last live packet a silence still counts as "gone quiet". */
export const RECENT_LIVE_MS = 5 * 60 * 1000;

let lastReceivingAt: number | null = null;

function read(): number | null {
  if (lastReceivingAt !== null) return lastReceivingAt;
  try {
    const stored = Number(window.sessionStorage.getItem(KEY));
    lastReceivingAt = Number.isFinite(stored) && stored > 0 ? stored : null;
  } catch {
    lastReceivingAt = null;
  }
  return lastReceivingAt;
}

/** Records a poll result; call with every acquisition status the browser reads. */
export function noteAcquisition(status: AcquisitionStatus | null | undefined, now = Date.now()) {
  if (status?.state !== "receiving") return;
  lastReceivingAt = now;
  try {
    window.sessionStorage.setItem(KEY, String(now));
  } catch {
    // Memory alone still covers this page.
  }
}

export function wasRecentlyLive(now = Date.now()): boolean {
  const at = read();
  return at !== null && now - at <= RECENT_LIVE_MS;
}

/**
 * Whether live data arrived recently. False on the server and the first client
 * render (so hydration matches), then follows each acquisition update.
 */
export function useRecentlyLive(acquisition: AcquisitionStatus | null | undefined): boolean {
  const [recent, setRecent] = useState(false);
  useEffect(() => {
    noteAcquisition(acquisition);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRecent(wasRecentlyLive());
  }, [acquisition]);
  return recent;
}
