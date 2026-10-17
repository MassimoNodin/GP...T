"use client";

import { useEffect, useState } from "react";
import { TrackMap } from "./TrackMap";
import { measuredPathData, parseTrackLine } from "./measured-track";
import type { TracePath } from "@/features/lap-analysis/lap-telemetry";

// Circuit map drawn from a session's own recorded car positions (the same line
// Lap Analysis draws). The schematic outline shows until the line arrives and
// stays when the session has no position data, so the box never changes size.

const RETRY_MS = [1500, 4000];
const START_DELAY_MS = 250;
const lines = new Map<string, Promise<TracePath | null>>();

async function readLine(sessionId: string): Promise<TracePath | null> {
  for (let attempt = 0; attempt <= RETRY_MS.length; attempt += 1) {
    try {
      const response = await fetch(`/api/review/sessions/${encodeURIComponent(sessionId)}/track-line`, {
        cache: "no-store",
        headers: { accept: "application/json" },
      });
      if (response.ok) return parseTrackLine(await response.json().catch(() => null));
      if (response.status !== 409 || attempt === RETRY_MS.length) return null;
    } catch {
      return null;
    }
    await new Promise((resolve) => setTimeout(resolve, RETRY_MS[attempt]));
  }
  return null;
}

function loadLine(sessionId: string): Promise<TracePath | null> {
  let value = lines.get(sessionId);
  if (!value) {
    value = readLine(sessionId);
    lines.set(sessionId, value);
    void value.then((line) => {
      if (!line) lines.delete(sessionId);
    });
  }
  return value;
}

export function MeasuredTrackMap({
  sessionId,
  track,
  className,
  strokeWidth = 2.2,
  title,
}: {
  /** v2 session id; without one the schematic shows. */
  sessionId: string | null;
  track: string;
  className?: string;
  strokeWidth?: number;
  title?: string;
}) {
  const [loaded, setLoaded] = useState<{ id: string; d: string } | null>(null);
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      void loadLine(sessionId).then((line) => {
        if (!cancelled && line) setLoaded({ id: sessionId, d: measuredPathData(line) });
      });
    }, START_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [sessionId]);

  if (!sessionId || loaded?.id !== sessionId) {
    return <TrackMap track={track} className={className} strokeWidth={strokeWidth} title={title} />;
  }
  const labelled = typeof title === "string" && title.length > 0;
  return (
    <svg
      className={className}
      viewBox="0 0 120 80"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinejoin="round"
      strokeLinecap="round"
      focusable="false"
      role={labelled ? "img" : undefined}
      aria-hidden={labelled ? undefined : true}
      aria-label={labelled ? title : undefined}
    >
      <path d={loaded.d} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
