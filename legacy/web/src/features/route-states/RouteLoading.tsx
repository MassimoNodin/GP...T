import type { ReactNode } from "react";
import { AppShell, HeaderContext, type NavKey } from "@/features/ui-foundation/AppShell";
import { SkeletonBody, type SkeletonVariant } from "./Skeleton";
import styles from "./route-states.module.css";

/**
 * Route-level loading fallback: the real shell with neutral status props and a
 * skeleton body, so navigation keeps the header, sidebar and main surface.
 */
export function RouteLoading({
  active,
  variant,
  body,
}: {
  active: NavKey;
  variant: SkeletonVariant;
  /** Replaces the `variant` skeleton, e.g. one chosen from the query string. */
  body?: ReactNode;
}) {
  return (
    <AppShell
      active={active}
      status={{ tone: "grey", label: "Loading…" }}
      context={<HeaderContext items={["Loading…"]} />}
      footer={{ tone: "grey", label: "Loading…", lines: [] }}
    >
      <div className={styles.stack} aria-busy="true">
        <p className="sr-only" role="status">
          Loading
        </p>
        {body ?? <SkeletonBody variant={variant} />}
      </div>
    </AppShell>
  );
}
