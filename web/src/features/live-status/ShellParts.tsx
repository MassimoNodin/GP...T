import type { ShellFooter, ShellStatus, ShellTone } from "@/features/ui-foundation/AppShell";
import { ChevronDownIcon, StatusGlyph } from "@/features/ui-foundation/icons";
import styles from "@/features/ui-foundation/AppShell.module.css";

// Presentational pieces of the app shell. They hold no state, so the server
// shell and the live (client) shell render identical markup.

export function ToneDot({ tone }: { tone: ShellTone }) {
  return (
    <StatusGlyph
      tone={tone}
      glyph={tone === "green" ? "check" : tone === "grey" ? "dot" : "alert"}
      size={13}
    />
  );
}

const DETAILS_ID = "shell-status-details";

export function StatusPill({ status }: { status: ShellStatus }) {
  const pill = <PillBody status={status} details={Boolean(status.details?.length)} />;
  if (!status.details?.length) return pill;
  return (
    <span className={styles.pillWrap}>
      {pill}
      <span id={DETAILS_ID} role="tooltip" className={styles.pillDetails}>
        {status.details.map((row) => (
          <span key={row.label} className={styles.pillDetail}>
            <ToneDot tone={row.tone} />
            <span className={styles.pillDetailLabel}>{row.label}</span>
            <span>{row.value}</span>
          </span>
        ))}
      </span>
    </span>
  );
}

function PillBody({ status, details }: { status: ShellStatus; details: boolean }) {
  return (
    <span
      className={styles.pill}
      data-tone={status.tone}
      role="status"
      tabIndex={details ? 0 : undefined}
      aria-describedby={details ? DETAILS_ID : undefined}
    >
      {status.tone === "amber" ? (
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <circle cx="8" cy="8" r="7.5" fill="var(--amber)" />
          <path d="M6.3 5.2v5.6L10.8 8z" fill="var(--on-accent)" />
        </svg>
      ) : (
        <ToneDot tone={status.tone} />
      )}
      {status.label}
      {status.menu && <ChevronDownIcon size={14} className={styles.pillChevron} />}
    </span>
  );
}

export function ShellFooterBlock({ footer }: { footer: ShellFooter }) {
  return (
    <div className={styles.footer}>
      <div className={styles.footerHead}>
        <ToneDot tone={footer.tone} />
        {footer.label}
      </div>
      {footer.lines.map((line) => (
        <div key={line} className={styles.footerLine}>
          {line}
        </div>
      ))}
    </div>
  );
}
