"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type FocusEvent,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";
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

/**
 * Segmented tab strip with the reference's red underline on the active tab.
 * Uses a roving tabindex: only the selected tab is in the tab order, and
 * Left/Right/Home/End move selection and focus between tabs. `idPrefix` gives
 * each tab an id (`<idPrefix>-<key>`); `controls` returns the id of the panel
 * a tab controls (aria-controls), for screens that render tab panels.
 */
export function Tabs<K extends string>({
  items,
  value,
  onChange,
  label,
  className,
  size = "md",
  idPrefix,
  controls,
}: {
  items: Array<TabItem<K>>;
  value: K;
  onChange?: (key: K) => void;
  label: string;
  className?: string;
  size?: "md" | "sm";
  idPrefix?: string;
  controls?: (key: K) => string | undefined;
}) {
  const selectedIndex = items.findIndex((item) => item.key === value);
  const tabbableIndex = selectedIndex >= 0 ? selectedIndex : 0;

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.altKey || event.ctrlKey || event.metaKey || items.length === 0) return;
    const tabs = Array.from(
      event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
    );
    const current = tabs.indexOf(event.target as HTMLButtonElement);
    if (current < 0) return;
    let next: number;
    switch (event.key) {
      case "ArrowRight":
        next = (current + 1) % tabs.length;
        break;
      case "ArrowLeft":
        next = (current - 1 + tabs.length) % tabs.length;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = tabs.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    tabs[next]?.focus();
    const key = items[next]?.key;
    if (onChange && key !== undefined && key !== value) onChange(key);
  }

  return (
    <div
      className={cx(styles.tabs, className)}
      data-size={size}
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
    >
      {items.map((item, index) => (
        <button
          key={item.key}
          type="button"
          role="tab"
          id={idPrefix ? `${idPrefix}-${item.key}` : undefined}
          aria-selected={item.key === value}
          aria-controls={controls?.(item.key)}
          tabIndex={index === tabbableIndex ? 0 : -1}
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

/**
 * Icon-only button. `label` is required and always becomes the accessible name
 * (aria-label) and the tooltip; `rest` cannot override or drop it.
 */
export function IconButton({
  label,
  className,
  children,
  title,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      className={cx(styles.iconButton, className)}
      {...rest}
      aria-label={label}
      title={title ?? label}
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
      <span className={styles.toggleKnob} aria-hidden="true" />
    </button>
  );
}

/**
 * Disclosure menu: a trigger button that reveals a panel of links/buttons.
 * Closes on Escape (focus returns to the trigger), on selecting a link or
 * button inside the panel (focus returns to the trigger), on outside click and
 * when keyboard focus leaves the menu. Put `<Link>`s or `<MenuItem>`s in
 * `children`; any `a[href]`, enabled `button` or `[data-menu-close]` element
 * closes the menu when clicked.
 */
export function Menu({
  label,
  trigger,
  children,
  align = "end",
  className,
  triggerClassName,
  panelClassName,
}: {
  /** Accessible name of the trigger button (required: the trigger is icon-only). */
  label: string;
  /** Trigger content, usually a decorative SVG icon. */
  trigger: ReactNode;
  children: ReactNode;
  align?: "start" | "end";
  className?: string;
  triggerClassName?: string;
  panelClassName?: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  function closeAndRestoreFocus() {
    setOpen(false);
    triggerRef.current?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (open && event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeAndRestoreFocus();
    }
  }

  function onBlur(event: FocusEvent<HTMLDivElement>) {
    const next = event.relatedTarget as Node | null;
    if (open && next && !event.currentTarget.contains(next)) setOpen(false);
  }

  function onPanelClick(event: MouseEvent<HTMLDivElement>) {
    const target = event.target as Element;
    if (target.closest("a[href], button:not(:disabled), [data-menu-close]")) {
      closeAndRestoreFocus();
    }
  }

  return (
    <div
      ref={rootRef}
      className={cx(styles.menu, className)}
      onKeyDown={onKeyDown}
      onBlur={onBlur}
    >
      <button
        ref={triggerRef}
        type="button"
        className={cx(styles.iconButton, triggerClassName)}
        aria-label={label}
        title={label}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((current) => !current)}
      >
        {trigger}
      </button>
      {open && (
        <div
          id={panelId}
          className={cx(styles.menuPanel, panelClassName)}
          data-align={align}
          onClick={onPanelClick}
        >
          {children}
        </div>
      )}
    </div>
  );
}

/** Button row for a `Menu` panel. Selecting it runs `onSelect` and closes the menu. */
export function MenuItem({
  onSelect,
  children,
  disabled,
}: {
  onSelect?: () => void;
  children: ReactNode;
  disabled?: boolean;
}) {
  return (
    <button type="button" className={styles.menuItem} disabled={disabled} onClick={onSelect}>
      {children}
    </button>
  );
}
