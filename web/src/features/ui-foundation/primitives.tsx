import type { ButtonHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { ChevronDownIcon, StatusGlyph } from "./icons";
import styles from "./primitives.module.css";

export type Tone = "green" | "amber" | "red" | "blue" | "purple" | "grey";

function cx(...names: Array<string | false | null | undefined>) {
  return names.filter(Boolean).join(" ");
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <div className={styles.pageHeader}>
      <div>
        <h1 className={styles.pageTitle}>{title}</h1>
        {subtitle && <p className={styles.pageSubtitle}>{subtitle}</p>}
      </div>
      {actions}
    </div>
  );
}

export function Card({
  title,
  aside,
  children,
  className,
  labelledBy,
}: {
  title?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  labelledBy?: string;
}) {
  return (
    <section className={cx(styles.card, className)} aria-labelledby={labelledBy}>
      {(title || aside) && (
        <div className={styles.cardHead}>
          {title && (
            <h2 className={styles.cardTitle} id={labelledBy}>
              {title}
            </h2>
          )}
          {aside}
        </div>
      )}
      {children}
    </section>
  );
}

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={styles.badge} data-tone={tone}>
      {children}
    </span>
  );
}

export function StatusBadge({
  tone,
  children,
  chevron,
  glyph,
}: {
  tone: Tone;
  children: ReactNode;
  chevron?: boolean;
  glyph?: "check" | "alert" | "dot" | "info";
}) {
  const statusTone = tone === "purple" ? "blue" : tone;
  return (
    <span className={styles.statusBadge} data-tone={tone}>
      <StatusGlyph
        tone={statusTone}
        glyph={glyph ?? (tone === "green" ? "check" : tone === "blue" ? "dot" : "alert")}
        size={13}
      />
      {children}
      {chevron && <ChevronDownIcon size={13} />}
    </span>
  );
}

export function DeltaPill({ ms, children }: { ms: number; children: ReactNode }) {
  return (
    <span className={styles.delta} data-tone={ms > 0 ? "red" : ms < 0 ? "green" : "grey"}>
      {children}
    </span>
  );
}

export type TabItem<K extends string> = { key: K; label: string };

/** Segmented tab strip with the reference's red underline on the active tab. */
export function Tabs<K extends string>({
  items,
  value,
  onChange,
  label,
  className,
  size = "md",
}: {
  items: Array<TabItem<K>>;
  value: K;
  onChange?: (key: K) => void;
  label: string;
  className?: string;
  size?: "md" | "sm";
}) {
  return (
    <div className={cx(styles.tabs, className)} data-size={size} role="tablist" aria-label={label}>
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          role="tab"
          aria-selected={item.key === value}
          className={styles.tab}
          onClick={onChange ? () => onChange(item.key) : undefined}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

export function Select({
  className,
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className={cx(styles.select, className)}>
      <select {...rest}>{children}</select>
      <ChevronDownIcon size={15} className={styles.selectChevron} />
    </span>
  );
}

export function Button({
  variant = "secondary",
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "muted" | "outline-blue";
}) {
  return (
    <button type="button" className={cx(styles.button, className)} data-variant={variant} {...rest}>
      {children}
    </button>
  );
}

export function IconButton({
  label,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      className={cx(styles.iconButton, className)}
      aria-label={label}
      title={label}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Checkbox({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange?: (checked: boolean) => void;
}) {
  return (
    <label className={styles.checkbox}>
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange ? (event) => onChange(event.target.checked) : undefined}
        readOnly={!onChange}
      />
      <span className={styles.checkboxBox} aria-hidden="true">
        <svg viewBox="0 0 16 16" width="11" height="11" focusable="false">
          <path d="M3.5 8.3l3 3 6-6.3" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      {label}
    </label>
  );
}

export function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange?: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={styles.toggle}
      onClick={onChange ? () => onChange(!checked) : undefined}
    >
      <span className={styles.toggleKnob} />
    </button>
  );
}
