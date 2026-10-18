"use client";

import { formatDay } from "./format";
import { useLocalTime } from "./use-local-time";

/** A capture date in the viewer's time zone (UTC during the server render and hydration). */
export function LocalDay({ iso }: { iso: string }) {
  const local = useLocalTime();
  return <>{formatDay(iso, local)}</>;
}
