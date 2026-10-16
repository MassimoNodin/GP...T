"use client";
import { useEffect, useState } from "react";
import type { RadioPresence } from "./radio-settings";

export function useRadioPresence() {
  const [presence, setPresence] = useState<RadioPresence>({ connected: false });
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let lastSeen = 0;
    async function poll() {
      try {
        const response = await fetch("/api/engineer/radio/presence", {
          cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(3000)]),
        });
        if (!response.ok) throw new Error("unavailable");
        const body = await response.json();
        if (typeof body.data?.connected !== "boolean") throw new Error("invalid");
        if (!controller.signal.aborted) {
          lastSeen = Date.now();
          setPresence(body.data);
        }
      } catch {
        if (!controller.signal.aborted && Date.now() - lastSeen >= 6000) setPresence({ connected: false });
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 2000);
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, []);
  return presence;
}
