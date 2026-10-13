import { formatSessionType } from "@/features/review-data/format";
import type { EvidenceSession } from "@/features/review-data/session-evidence";

// Notices sessions that finished since the browser last looked, for the
// "ready to review" toast and the Review badge. Pure; the watcher component
// does the polling and storage.

/** Lifecycles of a session that will not change any more. */
const FINISHED = new Set(["ended", "interrupted"]);

export type ReadySession = { id: string; title: string };

export function isFinished(session: Pick<EvidenceSession, "lifecycle">): boolean {
  return FINISHED.has(session.lifecycle);
}

/** "Jeddah · Race", or "Session" parts when the context is unknown. */
export function readyTitle(session: EvidenceSession): string {
  const track = session.context?.trackName ?? "Unknown circuit";
  return `${track} · ${formatSessionType(session.context?.sessionType)}`;
}

/**
 * Finished sessions not in `known`. With no \`known\` set yet (first visit),
 * nothing is new: existing sessions are not announced.
 */
export function newlyFinished(known: ReadonlySet<string> | null, sessions: readonly EvidenceSession[]): ReadySession[] {
  if (known === null) return [];
  return sessions
    .filter((session) => isFinished(session) && !known.has(session.id))
    .map((session) => ({ id: session.id, title: readyTitle(session) }));
}

/** Ids worth remembering: every finished session in the list. */
export function finishedIds(sessions: readonly EvidenceSession[]): string[] {
  return sessions.filter(isFinished).map((session) => session.id);
}

/** Unread ids after visiting a page: the library clears them all, a session page clears its own. */
export function readAfterVisit(unread: readonly string[], pathname: string): string[] {
  if (pathname === "/review") return [];
  const match = /^\/review\/([^/]+)/.exec(pathname);
  if (!match) return [...unread];
  const id = decodeURIComponent(match[1]);
  return unread.filter((entry) => entry !== id);
}
