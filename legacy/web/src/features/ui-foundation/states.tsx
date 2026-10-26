import type { ReactNode } from "react";
import { AlertTriangleIcon, CloseIcon, InfoIcon, StatusGlyph } from "./icons";
import { IconButton } from "./primitives";
import styles from "./primitives-extra.module.css";

/**
 * The single "data unavailable" treatment for a value that could exist but was
 * not reported. `compact` drops the tooltip (dense tables carry a footnote).
 */
export function NotAvailable({ reason, compact }: { reason: string; compact?: boolean }) {
  return (
    <span className={styles.notAvailable} title={compact ? undefined : reason}>
      <span aria-hidden="true">—</span>
      <span className="sr-only">Not available: {reason}</span>
    </span>
  );
}

export type EmptyStateProps = {
  title: string;
  body?: ReactNode;
  /** Decorative SVG from icons.tsx. Card and page sizes get a tone default. */
  icon?: ReactNode;
  tone?: "neutral" | "warning" | "error";
  /** inline: text only; card: centred 40px icon disc; page: full-page panel. */
  size?: "inline" | "card" | "page";
  action?: ReactNode;
  /** "alert" is honoured only for the error tone. */
  role?: "status" | "alert";
};

export function EmptyState({
  title,
  body,
  icon,
  tone = "neutral",
  size = "card",
  action,
  role,
}: EmptyStateProps) {
  const resolvedRole = tone === "error" ? (role ?? "alert") : "status";
  const glyph =
    icon ??
    (size === "inline" ? null : tone === "neutral" ? <InfoIcon size={20} /> : <AlertTriangleIcon size={20} />);
  const Title = size === "page" ? "h2" : "p";
  return (
    <div className={styles.empty} data-size={size} data-tone={tone} role={resolvedRole}>
      {glyph && (
        <span className={styles.emptyIcon} aria-hidden="true">
          {glyph}
        </span>
      )}
      <Title className={styles.emptyTitle}>{title}</Title>
      {body && <div className={styles.emptyBody}>{body}</div>}
      {action && <div className={styles.emptyAction}>{action}</div>}
    </div>
  );
}

export type InlineAlertProps = {
  tone: "info" | "warning" | "error" | "success";
  title?: string;
  children: ReactNode;
  actions?: ReactNode;
  onDismiss?: () => void;
};

const ALERT_GLYPH = {
  info: { tone: "blue", glyph: "info" },
  warning: { tone: "amber", glyph: "alert" },
  error: { tone: "red", glyph: "alert" },
  success: { tone: "green", glyph: "check" },
} as const;

/** Banner inside a card or above a grid. Errors are announced as alerts. */
export function InlineAlert({ tone, title, children, actions, onDismiss }: InlineAlertProps) {
  const glyph = ALERT_GLYPH[tone];
  return (
    <div className={styles.alert} data-tone={tone} role={tone === "error" ? "alert" : "status"}>
      <span className={styles.alertGlyph}>
        <StatusGlyph tone={glyph.tone} glyph={glyph.glyph} size={16} />
      </span>
      <div className={styles.alertText}>
        {title && <strong className={styles.alertTitle}>{title}</strong>}
        <div className={styles.alertBody}>{children}</div>
      </div>
      {actions && <div className={styles.alertActions}>{actions}</div>}
      {onDismiss && (
        <IconButton label="Dismiss" className={styles.alertDismiss} onClick={onDismiss}>
          <CloseIcon size={13} />
        </IconButton>
      )}
    </div>
  );
}
