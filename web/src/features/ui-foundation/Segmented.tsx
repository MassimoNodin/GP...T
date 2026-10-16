"use client";

import type { KeyboardEvent } from "react";
import { nextRovingIndex } from "./primitives";
import styles from "./primitives.module.css";

export type SegmentedItem<K extends string> = { key: K; label: string; disabled?: boolean };

export type SegmentedProps<K extends string> = {
  items: Array<SegmentedItem<K>>;
  value: K;
  onChange: (key: K) => void;
  label: string;
  size?: "md" | "sm";
  className?: string;
  /** Disables every option; `disabledReason` becomes the tooltip. */
  disabled?: boolean;
  disabledReason?: string;
};

/**
 * Mode switch that does not own a panel (Single/Compare, Time Delta/Distance).
 * Looks like `Tabs` but is a radio group: the selected radio is the only tab
 * stop and arrow keys (any direction) move focus and selection.
 */
export function Segmented<K extends string>({
  items,
  value,
  onChange,
  label,
  size = "md",
  className,
  disabled,
  disabledReason,
}: SegmentedProps<K>) {
  const enabledItems = items.filter((item) => !item.disabled);
  const selectedIndex = items.findIndex((item) => item.key === value && !item.disabled);
  const tabbableIndex = selectedIndex >= 0 ? selectedIndex : items.findIndex((item) => !item.disabled);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (disabled || event.altKey || event.ctrlKey || event.metaKey || items.length === 0) return;
    const radios = Array.from(
      event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="radio"]:not(:disabled)'),
    );
    const current = radios.indexOf(event.target as HTMLButtonElement);
    if (current < 0) return;
    const next = nextRovingIndex(event.key, current, radios.length, "both");
    if (next === null) return;
    event.preventDefault();
    radios[next]?.focus();
    const key = enabledItems[next]?.key;
    if (key !== undefined && key !== value) onChange(key);
  }

  return (
    <div
      className={[styles.tabs, className].filter(Boolean).join(" ")}
      data-size={size}
      role="radiogroup"
      aria-label={label}
      aria-disabled={disabled ? true : undefined}
      title={disabled ? disabledReason : undefined}
      onKeyDown={onKeyDown}
    >
      {items.map((item, index) => (
        <button
          key={item.key}
          type="button"
          role="radio"
          aria-checked={item.key === value}
          tabIndex={index === tabbableIndex ? 0 : -1}
          disabled={disabled || item.disabled}
          className={styles.tab}
          onClick={() => {
            if (item.key !== value) onChange(item.key);
          }}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
