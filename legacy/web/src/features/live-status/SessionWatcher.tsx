"use client";

import { usePathname } from "next/navigation";
import { useEffect, useSyncExternalStore } from "react";
import { parseEvidenceSessions, type EvidenceSession } from "@/features/review-data/session-evidence";
import { useToast } from "@/features/ui-foundation/Toast";
import { finishedIds, newlyFinished, readAfterVisit } from "./session-watch";

const KNOWN_KEY = "f1-engineer:known-sessions";
const UNREAD_KEY = "f1-engineer:review-unread";
const UNREAD_EVENT = "f1-engineer:review-unread";
const POLL_MS = 20_000;
const MAX_PAGES = 5;
const MAX_REMEMBERED = 2000;

function readList(key: string): string[] | null {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : null;
  } catch {
    return null;
  }
}

function writeList(key: string, list: string[]) {
  try {
    window.localStorage.setItem(key, JSON.stringify(list.slice(-MAX_REMEMBERED)));
  } catch {
    // Storage full or blocked: the watcher still works for this page.
  }
}

function setUnread(list: string[]) {
  writeList(UNREAD_KEY, list);
  window.dispatchEvent(new Event(UNREAD_EVENT));
}

async function readSessions(signal: AbortSignal): Promise<EvidenceSession[] | null> {
  const sessions: EvidenceSession[] = [];
  let after = "";
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const response = await fetch(`/api/review/sessions${after ? `?after=${encodeURIComponent(after)}` : ""}`, {
      signal,
      cache: "no-store",
    }).catch(() => null);
    if (!response?.ok) return page === 0 ? null : sessions;
    const batch = parseEvidenceSessions(await response.json().catch(() => null));
    sessions.push(...batch);
    if (batch.length < 100) break;
    after = batch[batch.length - 1].id;
  }
  return sessions;
}

/**
 * Watches for sessions that finish while the app is open: a toast links to
 * each one and the Review item counts them until they are looked at.
 */
export function SessionWatcher() {
  const toast = useToast();
  const pathname = usePathname();

  // Visiting the library or a session marks it read.
  useEffect(() => {
    const unread = readList(UNREAD_KEY) ?? [];
    const next = readAfterVisit(unread, pathname);
    if (next.length !== unread.length) setUnread(next);
  }, [pathname]);

  useEffect(() => {
    let stopped = false;
    let controller: AbortController | null = null;
    async function poll() {
      if (document.hidden) return;
      controller?.abort();
      controller = new AbortController();
      const sessions = await readSessions(controller.signal);
      if (stopped || !sessions) return;
      const stored = readList(KNOWN_KEY);
      const known = stored ? new Set(stored) : null;
      const ready = newlyFinished(known, sessions);
      writeList(KNOWN_KEY, [...new Set([...(stored ?? []), ...finishedIds(sessions)])]);
      if (ready.length === 0) return;
      const onPage = window.location.pathname;
      const unread = readList(UNREAD_KEY) ?? [];
      setUnread([...unread, ...ready.map((session) => session.id).filter((id) => !onPage.endsWith(id))]);
      for (const session of ready.slice(0, 3)) {
        toast.push({
          tone: "success",
          title: `${session.title} is ready to review`,
          body: "Its laps and events are processed.",
          action: { label: "Open review", href: `/review/${encodeURIComponent(session.id)}` },
          durationMs: 10_000,
        });
      }
    }
    void poll();
    const timer = window.setInterval(() => void poll(), POLL_MS);
    const onVisible = () => {
      if (!document.hidden) void poll();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      controller?.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [toast]);

  return null;
}

function subscribe(callback: () => void) {
  window.addEventListener(UNREAD_EVENT, callback);
  window.addEventListener("storage", callback);
  return () => {
    window.removeEventListener(UNREAD_EVENT, callback);
    window.removeEventListener("storage", callback);
  };
}

/** Sessions finished since the library was last opened; 0 on the server. */
export function useReviewUnread(): number {
  return useSyncExternalStore(
    subscribe,
    () => readList(UNREAD_KEY)?.length ?? 0,
    () => 0,
  );
}
