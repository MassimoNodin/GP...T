"use client";

import type { KeyboardEvent } from "react";
import { nextRovingIndex } from "./primitives";
import styles from "./primitives-extra.module.css";

export type FilterChipItem<K extends string> = { key: K; label: string; count?: number };

export type FilterChipsProps<K extends string> = {
  items: Array<FilterChipItem<K>>;
  /** One pressed key (single select) or several (multi select). */
  value: K | readonly K[];
  /** Called with the clicked key; multi select callers toggle it themselves. */
  onChange: (key: K) => void;
  label: string;
};

/**
 * Filter chips as a button group: each chip is a toggle button with
 * `aria-pressed`. Left/Right/Home/End move focus between chips (focus only;
 * pressing is Enter or Space). Labels render as `Questions (6)`.
 */
export function FilterChips<K extends string>({ items, value, onChange, label }: FilterChipsProps<K>) {
  const pressed = (key: K) => (Array.isArray(value) ? (value as readonly K[]).includes(key) : value === key);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const chips = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("button"));
    const current = chips.indexOf(event.target as HTMLButtonElement);
    if (current < 0) return;
    const next = nextRovingIndex(event.key, current, chips.length, "horizontal");
    if (next === null) return;
    event.preventDefault();
    chips[next]?.focus();
  }

  return (
    <div className={styles.chips} role="group" aria-label={label} onKeyDown={onKeyDown}>
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          className={styles.chip}
          aria-pressed={pressed(item.key)}
          onClick={() => onChange(item.key)}
        >
          {item.label}
          {item.count !== undefined && <span className={styles.chipCount}> ({item.count})</span>}
        </button>
      ))}
    </div>
  );
}
