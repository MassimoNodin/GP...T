"use client";

import { useReviewUnread } from "./SessionWatcher";
import styles from "@/features/ui-foundation/AppShell.module.css";

/** Count of new sessions on the Review nav item. */
export function ReviewBadge() {
  const count = useReviewUnread();
  if (count === 0) return null;
  return (
    <span className={styles.navBadge}>
      {count > 9 ? "9+" : count}
      <span className="sr-only">{count === 1 ? " new session" : " new sessions"}</span>
    </span>
  );
}
