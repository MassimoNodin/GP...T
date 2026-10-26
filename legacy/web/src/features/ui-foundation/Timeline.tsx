import type { CSSProperties, ReactNode } from "react";
import styles from "./primitives-extra.module.css";

export type TimelineItem = {
  id: string;
  /** Decorative SVG from icons.tsx. */
  icon: ReactNode;
  lap: number | null;
  /** Session time; `null` (or omitted) when absent. Pass `NotAvailable` for a missing value that could exist. */
  time: ReactNode | null;
  label: ReactNode;
  chips?: ReactNode;
  action?: ReactNode;
  emphasis?: "highlight" | "warning";
};

export type TimelineProps = {
  items: TimelineItem[];
  label: string;
  dense?: boolean;
  /** Rendered instead of the list when there are no items. */
  empty?: ReactNode;
};

/**
 * Vertical event list: icon, lap, optional time, label with chips and an
 * optional trailing action. Rows are 37px (dense) or 44px. The time and action
 * columns exist only when at least one item uses them.
 */
export function Timeline({ items, label, dense, empty }: TimelineProps) {
  if (items.length === 0) return <>{empty ?? null}</>;
  const hasTime = items.some((item) => item.time !== null && item.time !== undefined && item.time !== false);
  const hasAction = items.some((item) => item.action);
  const columns = ["14px", "64px", ...(hasTime ? ["72px"] : []), "minmax(0, 1fr)", ...(hasAction ? ["auto"] : [])];
  return (
    <ol
      className={styles.timeline}
      aria-label={label}
      data-dense={dense ? "true" : undefined}
      style={{ "--timeline-columns": columns.join(" ") } as CSSProperties}
    >
      {items.map((item) => (
        <li key={item.id} className={styles.timelineRow} data-emphasis={item.emphasis}>
          <span className={styles.timelineIcon} aria-hidden="true">
            {item.icon}
          </span>
          <span className={styles.timelineLap}>{item.lap === null ? "" : `Lap ${item.lap}`}</span>
          {hasTime && <span className={styles.timelineTime}>{item.time ?? null}</span>}
          <span className={styles.timelineLabel}>
            {item.label}
            {item.chips && <span className={styles.timelineChips}>{item.chips}</span>}
          </span>
          {hasAction && <span className={styles.timelineAction}>{item.action}</span>}
        </li>
      ))}
    </ol>
  );
}
