import styles from "./primitives-extra.module.css";

export type ProgressBarProps = {
  /** Percent complete, 0 to 100. `null` is indeterminate. */
  value: number | null;
  label: string;
  tone?: "blue" | "green" | "amber" | "red";
  /** sm: 4px, md: 6px. */
  size?: "sm" | "md";
};

export function ProgressBar({ value, label, tone = "blue", size = "md" }: ProgressBarProps) {
  const percent = value === null || !Number.isFinite(value) ? null : Math.min(100, Math.max(0, value));
  const indeterminate = percent === null;
  return (
    <div
      className={styles.progress}
      data-tone={tone}
      data-size={size}
      data-indeterminate={indeterminate ? "true" : undefined}
      role="progressbar"
      aria-label={label}
      aria-valuemin={indeterminate ? undefined : 0}
      aria-valuemax={indeterminate ? undefined : 100}
      aria-valuenow={indeterminate ? undefined : Math.round(percent ?? 0)}
    >
      <span
        className={styles.progressFill}
        style={percent === null ? undefined : { width: `${percent}%` }}
      />
    </div>
  );
}
