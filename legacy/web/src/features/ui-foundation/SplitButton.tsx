"use client";

import { useId, type ReactNode } from "react";
import { ChevronDownIcon } from "./icons";
import { Menu } from "./primitives";
import styles from "./primitives-extra.module.css";

export type SplitButtonProps = {
  label: string;
  onClick: () => void;
  /** Accessible name of the chevron button, e.g. "More import options". */
  menuLabel: string;
  /** `MenuItem`s (or links) for the chevron menu. */
  children: ReactNode;
  variant?: "primary" | "secondary";
  disabled?: boolean;
  /** Tooltip and screen reader explanation while `disabled`. */
  disabledReason?: string;
  /**
   * With `disabled`: keep the main button focusable (`aria-disabled`) so its
   * reason can be reached by keyboard and screen reader, instead of the native
   * `disabled` attribute. Clicks do nothing while disabled either way.
   */
  ariaDisabled?: boolean;
  /** Marks the action as running: the main button is disabled and `aria-busy`. */
  busy?: boolean;
};

/** Primary action plus a chevron menu: the reference's red Import Session button. */
export function SplitButton({
  label,
  onClick,
  menuLabel,
  children,
  variant = "primary",
  disabled,
  disabledReason,
  ariaDisabled,
  busy,
}: SplitButtonProps) {
  const reasonId = useId();
  const explained = Boolean(disabled && disabledReason);
  return (
    <div className={styles.split} data-variant={variant} data-busy={busy ? "true" : undefined}>
      <button
        type="button"
        className={styles.splitMain}
        onClick={disabled ? undefined : onClick}
        disabled={(disabled && !ariaDisabled) || busy}
        aria-disabled={disabled && ariaDisabled ? true : undefined}
        aria-busy={busy ? true : undefined}
        aria-describedby={explained ? reasonId : undefined}
        title={explained ? disabledReason : undefined}
      >
        {label}
      </button>
      {explained && (
        <span id={reasonId} className="sr-only">
          {disabledReason}
        </span>
      )}
      <Menu
        label={menuLabel}
        trigger={<ChevronDownIcon size={15} />}
        triggerClassName={styles.splitChevron}
        className={styles.splitMenu}
        disabled={disabled}
      >
        {children}
      </Menu>
    </div>
  );
}
