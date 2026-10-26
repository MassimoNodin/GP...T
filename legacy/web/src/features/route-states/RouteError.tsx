"use client";

import Link from "next/link";
import { useEffect } from "react";
import { AppShell, HeaderContext, type NavKey } from "@/features/ui-foundation/AppShell";
import { AlertTriangleIcon } from "@/features/ui-foundation/icons";
import { Button } from "@/features/ui-foundation/primitives";
import styles from "./route-states.module.css";

/**
 * Error boundary body shared by every route's error.tsx. `retry` re-fetches and
 * re-renders the segment (stable `retry` prop since Next.js 16.3).
 */
export function RouteError({
  active,
  error,
  retry,
  title = "Something went wrong",
  message = "This screen could not be loaded. Check that the local backend is running, then try again.",
  backHref,
  backLabel,
}: {
  active: NavKey;
  error: Error & { digest?: string };
  retry: () => void;
  title?: string;
  message?: string;
  backHref?: string;
  backLabel?: string;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <AppShell
      active={active}
      status={{ tone: "red", label: "Unavailable" }}
      context={<HeaderContext items={["Error"]} />}
      footer={{ tone: "red", label: "Unavailable", lines: [] }}
    >
      <section className={styles.state} role="alert" aria-labelledby="route-state-title">
        <span className={styles.stateIcon} data-tone="red" aria-hidden="true">
          <AlertTriangleIcon size={24} />
        </span>
        <h1 className={styles.stateTitle} id="route-state-title">
          {title}
        </h1>
        <p className={styles.stateText}>{message}</p>
        {error.digest && <p className={styles.stateRef}>Reference: {error.digest}</p>}
        <div className={styles.actions}>
          <Button variant="primary" onClick={() => retry()}>
            Try again
          </Button>
          {backHref && backLabel && (
            <Link href={backHref} className={styles.linkButton}>
              {backLabel}
            </Link>
          )}
        </div>
      </section>
    </AppShell>
  );
}
