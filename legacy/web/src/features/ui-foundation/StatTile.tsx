import type { ReactNode } from "react";
import { NotAvailable } from "./states";
import { DeltaPill } from "./primitives";
import styles from "./primitives-extra.module.css";

export type StatTileProps = {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  /** `ms` picks the pill tone (positive is slower); omit for a neutral pill. */
  delta?: { ms?: number; text: string };
  /** Decorative SVG shown before the label. */
  icon?: ReactNode;
  /** When set, the value renders as `NotAvailable` with this reason. */
  unavailableReason?: string;
};

/** Label, value, optional delta pill and sub line: hero stats, Inputs summary, Settings. */
export function StatTile({ label, value, sub, delta, icon, unavailableReason }: StatTileProps) {
  return (
    <div className={styles.tile}>
      <div className={styles.tileLabel}>
        {icon && (
          <span className={styles.tileIcon} aria-hidden="true">
            {icon}
          </span>
        )}
        {label}
      </div>
      <div className={styles.tileValue}>
        {unavailableReason ? <NotAvailable reason={unavailableReason} /> : value}
        {delta && <DeltaPill ms={delta.ms ?? 0}>{delta.text}</DeltaPill>}
      </div>
      {sub && <div className={styles.tileSub}>{sub}</div>}
    </div>
  );
}
