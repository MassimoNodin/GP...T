"use client";

import type { ReactNode } from "react";
import { ChevronLeftIcon, ChevronRightIcon } from "./icons";
import { IconButton, Menu, MenuItem } from "./primitives";
import styles from "./primitives-extra.module.css";

export type StepperLap = { id: string; lap: number; ms: number; valid: boolean };

export type LapStepperProps = {
  /** "Target" or "Reference": names the group and the previous/next buttons. */
  label: string;
  /** Laps in lap-number order. */
  laps: StepperLap[];
  /** Id of the selected lap, or null for none. */
  value: string | null;
  onChange: (id: string) => void;
  tone?: "target" | "reference";
  /**
   * Draw a dot in the label cell too: `true` repeats the tone colour, a string
   * is a CSS colour for a small dot (Lap Analysis uses green for Reference Lap).
   */
  labelDot?: boolean | string;
  /** Small note after the time, e.g. "Session best". */
  note?: ReactNode;
  /** Extra content after the value, e.g. a delta. */
  trailing?: ReactNode;
  formatTime?: (ms: number) => string;
};

export function formatStepperTime(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const minutes = Math.floor(ms / 60000);
  const rest = ms - minutes * 60000;
  return `${minutes}:${(rest / 1000).toFixed(3).padStart(6, "0")}`;
}

function Dot({ tone, color, size = 10 }: { tone?: "target" | "reference"; color?: string; size?: number }) {
  return (
    <svg className={styles.stepperDot} viewBox="0 0 10 10" width={size} height={size} aria-hidden="true" focusable="false">
      <circle cx="5" cy="5" r="5" fill={color ?? `var(--${tone})`} />
    </svg>
  );
}

/**
 * Previous/next lap picker with a lap list menu. Shared by Lap Analysis
 * (target and reference) and the Overview Compare mode. The list shows valid
 * laps first, then an "Other laps" group for invalid ones.
 */
export function LapStepper({
  label,
  laps,
  value,
  onChange,
  tone = "target",
  labelDot,
  note,
  trailing,
  formatTime = formatStepperTime,
}: LapStepperProps) {
  const index = laps.findIndex((lap) => lap.id === value);
  const lap = index >= 0 ? laps[index] : null;
  const lower = label.toLowerCase();
  const valid = laps.filter((entry) => entry.valid);
  const other = laps.filter((entry) => !entry.valid);
  const summary = lap ? `Lap ${lap.lap}, ${formatTime(lap.ms)}` : "no lap";

  function item(entry: StepperLap) {
    const selected = entry.id === value;
    return (
      <MenuItem key={entry.id} onSelect={() => onChange(entry.id)}>
        <span className={styles.stepperItem} data-selected={selected ? "true" : undefined}>
          Lap {entry.lap} · {formatTime(entry.ms)}
          {!entry.valid && " · Invalid"}
          {selected && <span className="sr-only"> (selected)</span>}
        </span>
      </MenuItem>
    );
  }

  return (
    <div className={styles.stepper} role="group" aria-label={label}>
      <span className={styles.stepperLabel}>
        {labelDot && (typeof labelDot === "string" ? <Dot color={labelDot} size={6} /> : <Dot tone={tone} />)}
        {label}
        <ChevronRightIcon size={11} />
      </span>
      <span className={styles.stepperValue}>
        <Dot tone={tone} />
        {laps.length > 0 ? (
          <Menu
            label={`${label}: ${summary}. Choose another lap`}
            align="start"
            className={styles.stepperMenu}
            triggerClassName={styles.stepperTrigger}
            panelClassName={styles.stepperPanel}
            trigger={
              <>
                <strong>{lap ? `Lap ${lap.lap}` : "None"}</strong>
                <span className={styles.stepperTime}>{lap ? formatTime(lap.ms) : "—"}</span>
              </>
            }
          >
            {valid.map(item)}
            {valid.length > 0 && other.length > 0 && <div className={styles.menuHeading}>Other laps</div>}
            {other.map(item)}
          </Menu>
        ) : (
          <>
            <strong>None</strong>
            <span className={styles.stepperTime}>—</span>
          </>
        )}
        {note && <span className={styles.stepperNote}>{note}</span>}
        {trailing}
        <span className="sr-only" aria-live="polite">
          {label}: {summary}
        </span>
      </span>
      <span className={styles.stepperNav}>
        <IconButton
          label={`Previous ${lower}`}
          className={styles.stepperButton}
          disabled={index <= 0}
          onClick={() => onChange(laps[index - 1].id)}
        >
          <ChevronLeftIcon size={13} />
        </IconButton>
        <IconButton
          label={`Next ${lower}`}
          className={styles.stepperButton}
          disabled={index < 0 || index >= laps.length - 1}
          onClick={() => onChange(laps[index + 1].id)}
        >
          <ChevronRightIcon size={13} />
        </IconButton>
      </span>
    </div>
  );
}
