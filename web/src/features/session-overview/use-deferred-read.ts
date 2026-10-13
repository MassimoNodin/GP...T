"use client";

import { useEffect, useRef, useState } from "react";
import { humanReason } from "@/features/review-data/reasons";
import { BUSY_RETRY_DELAYS_MS, interpretDeferredResponse } from "./deferred";
import type { LiveRead } from "./overview-data";

/** Fetches `url` once the component mounts (null: nothing to fetch). Retries a busy engine. */
export function useDeferredRead<R>(url: string | null, parse: (body: unknown) => R | null): LiveRead<R> {
  const [state, setState] = useState<LiveRead<R>>(url ? { status: "loading" } : { status: "idle" });
  const parseRef = useRef(parse);
  parseRef.current = parse;

  useEffect(() => {
    if (!url) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function attempt(index: number) {
      try {
        const response = await fetch(url as string, {
          signal: controller.signal,
          cache: "no-store",
          headers: { accept: "application/json" },
        });
        const body: unknown = await response.json().catch(() => null);
        const outcome = interpretDeferredResponse(response.status, body, parseRef.current);
        if (controller.signal.aborted) return;
        if (outcome.kind === "ok") {
          setState({ status: "loaded", value: outcome.value });
        } else if (outcome.kind === "busy" && index < BUSY_RETRY_DELAYS_MS.length) {
          timer = setTimeout(() => void attempt(index + 1), BUSY_RETRY_DELAYS_MS[index]);
        } else {
          setState({ status: "unavailable", reason: outcome.reason });
        }
      } catch {
        if (!controller.signal.aborted) {
          setState({ status: "unavailable", reason: humanReason("local_engine_unavailable") });
        }
      }
    }

    setState({ status: "loading" });
    void attempt(0);
    return () => {
      controller.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [url]);

  return state;
}
