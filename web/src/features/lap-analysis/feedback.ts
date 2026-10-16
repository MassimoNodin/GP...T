// Answer feedback ("Was this helpful?"). There is no backend endpoint, so it is
// stored on this computer only (localStorage) and the UI says so. Pure list
// operations plus the storage key; the component does the storage access.

export const FEEDBACK_KEY = "race-engineer.answer-feedback.v1";
export const FEEDBACK_LIMIT = 200;

export const FEEDBACK_REASONS = ["Inaccurate", "Not specific", "Missing data", "Other"] as const;
export type FeedbackReason = (typeof FEEDBACK_REASONS)[number];
export type FeedbackRating = "up" | "down";

export type FeedbackEntry = {
  question: string;
  session: string;
  targetLap: number;
  referenceLap: number | null;
  rating: FeedbackRating;
  reason: FeedbackReason | null;
  /** ISO time the feedback was given. */
  at: string;
};

export const FEEDBACK_SAVED = "Thanks. Your feedback is saved on this computer only.";
export const FEEDBACK_UP = "Thanks for the feedback.";

export function sameAnswer(a: Pick<FeedbackEntry, "question" | "session" | "targetLap" | "referenceLap">, b: typeof a): boolean {
  return a.question === b.question && a.session === b.session && a.targetLap === b.targetLap && a.referenceLap === b.referenceLap;
}

/** Adds or replaces the feedback for one answer; keeps the newest `FEEDBACK_LIMIT` entries. */
export function recordFeedback(list: readonly FeedbackEntry[], entry: FeedbackEntry): FeedbackEntry[] {
  const rest = list.filter((item) => !sameAnswer(item, entry));
  const next = [...rest, entry];
  return next.length > FEEDBACK_LIMIT ? next.slice(next.length - FEEDBACK_LIMIT) : next;
}

export function findFeedback(list: readonly FeedbackEntry[], key: Parameters<typeof sameAnswer>[0]): FeedbackEntry | null {
  return list.find((item) => sameAnswer(item, key)) ?? null;
}

export function parseFeedback(raw: string | null | undefined): FeedbackEntry[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const entries: FeedbackEntry[] = [];
    for (const item of parsed) {
      if (item === null || typeof item !== "object") continue;
      const value = item as Record<string, unknown>;
      if (
        typeof value.question !== "string" ||
        typeof value.session !== "string" ||
        typeof value.targetLap !== "number" ||
        (value.rating !== "up" && value.rating !== "down") ||
        typeof value.at !== "string"
      ) {
        continue;
      }
      entries.push({
        question: value.question,
        session: value.session,
        targetLap: value.targetLap,
        referenceLap: typeof value.referenceLap === "number" ? value.referenceLap : null,
        rating: value.rating,
        reason: (FEEDBACK_REASONS as readonly unknown[]).includes(value.reason) ? (value.reason as FeedbackReason) : null,
        at: value.at,
      });
    }
    return entries.slice(-FEEDBACK_LIMIT);
  } catch {
    return [];
  }
}

export function serializeFeedback(list: readonly FeedbackEntry[]): string {
  return JSON.stringify(list.slice(-FEEDBACK_LIMIT));
}

/* -------------------------------------------------------- storage access */

/** Reads the stored list; storage may be unavailable (private mode), then empty. */
export function loadFeedback(storage: Pick<Storage, "getItem"> | null): FeedbackEntry[] {
  try {
    return parseFeedback(storage?.getItem(FEEDBACK_KEY));
  } catch {
    return [];
  }
}

/** Saves one entry; returns false when the browser refused to store it. */
export function saveFeedback(storage: Pick<Storage, "getItem" | "setItem"> | null, entry: FeedbackEntry): boolean {
  if (!storage) return false;
  try {
    storage.setItem(FEEDBACK_KEY, serializeFeedback(recordFeedback(loadFeedback(storage), entry)));
    return true;
  } catch {
    return false;
  }
}
