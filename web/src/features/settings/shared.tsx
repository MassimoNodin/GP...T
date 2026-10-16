import type { ReactNode } from "react";
import { Badge } from "@/features/ui-foundation/primitives";
import type { DisplayRow } from "./display";
import type { SettingsModel } from "./load";
import styles from "./Settings.module.css";

/** Engineer runtime status sits in a card header so the five reference tiles stay intact. */
export function RuntimeChip({ model }: { model: SettingsModel }) {
  return (
    <span className={styles.chip}>
      <Badge tone={model.runtime.tone}>Engineer: {model.runtime.statusLabel}</Badge>
    </span>
  );
}

export function barWidth(share: number) {
  const clamped = Math.min(1, Math.max(0, share));
  return clamped === 0 ? "0" : `${Math.max(1, clamped * 100).toFixed(1)}%`;
}

export function KeyValues({ rows }: { rows: DisplayRow[] }) {
  return (
    <dl className={styles.kv}>
      {rows.map((row) => (
        <div key={row.label}>
          <dt>{row.label}</dt>
          <dd data-tone={row.tone}>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Field({ label, help, children }: { label: string; help: string; children: ReactNode }) {
  return (
    <div className={styles.field}>
      <div className={styles.label}>{label}</div>
      {children}
      <p className={styles.help}>{help}</p>
    </div>
  );
}
