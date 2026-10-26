"use client";

import { useSyncExternalStore } from "react";

const subscribe = () => () => {};

/**
 * False while rendering on the server and during hydration, true afterwards.
 * Dates are formatted in UTC until then so markup matches, then in the
 * viewer's own time zone.
 */
export function useLocalTime(): boolean {
  return useSyncExternalStore(subscribe, () => true, () => false);
}
