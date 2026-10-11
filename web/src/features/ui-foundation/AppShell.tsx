import Link from "next/link";
import type { ReactNode } from "react";
import { F1Logo } from "./F1Logo";
import {
  ChevronDownIcon,
  DriveIcon,
  ReviewIcon,
  SettingsIcon,
  StatusGlyph,
} from "./icons";
import styles from "./AppShell.module.css";

export type ShellTone = "green" | "amber" | "red" | "grey";

export type ShellStatus = {
  tone: ShellTone;
  label: string;
  menu?: boolean;
};

export type ShellFooter = {
  tone: ShellTone;
  label: string;
  lines: string[];
};

export type NavKey = "drive" | "review" | "settings";

const NAV: Array<{ key: NavKey; label: string; href: string; Icon: typeof DriveIcon }> = [
  { key: "drive", label: "Drive", href: "/drive", Icon: DriveIcon },
  { key: "review", label: "Review", href: "/review", Icon: ReviewIcon },
  { key: "settings", label: "Settings", href: "/settings", Icon: SettingsIcon },
];

export function AppShell({
  active,
  status,
  context,
  footer,
  children,
}: {
  active: NavKey;
  status: ShellStatus;
  context: ReactNode;
  footer: ShellFooter;
  children: ReactNode;
}) {
  return (
    <div className={styles.app}>
      <a className={styles.skip} href="#main">
        Skip to content
      </a>
      <header className={styles.header}>
        <Link href="/drive" className={styles.brand} aria-label="Race Engineer home">
          <F1Logo className={styles.logo} />
          <span className={styles.brandName}>Race Engineer</span>
        </Link>
        <StatusPill status={status} />
        <div className={styles.context}>{context}</div>
      </header>
      <aside className={styles.sidebar}>
        <nav aria-label="Primary">
          <ul className={styles.nav}>
            {NAV.map(({ key, label, href, Icon }) => (
              <li key={key}>
                <Link
                  href={href}
                  className={styles.navItem}
                  aria-current={key === active ? "page" : undefined}
                >
                  <Icon size={17} className={styles.navIcon} />
                  {label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <div className={styles.footer} role="status">
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
      </aside>
      <main id="main" className={styles.main}>
        {children}
      </main>
    </div>
  );
}

function ToneDot({ tone }: { tone: ShellTone }) {
  return (
    <StatusGlyph
      tone={tone}
      glyph={tone === "green" ? "check" : tone === "grey" ? "dot" : "alert"}
      size={13}
    />
  );
}

function StatusPill({ status }: { status: ShellStatus }) {
  return (
    <span className={styles.pill} data-tone={status.tone} role="status">
      {status.tone === "amber" ? (
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <circle cx="8" cy="8" r="7.5" fill="var(--amber)" />
          <path d="M6.3 5.2v5.6L10.8 8z" fill="#fff" />
        </svg>
      ) : (
        <ToneDot tone={status.tone} />
      )}
      {status.label}
      {status.menu && <ChevronDownIcon size={14} className={styles.pillChevron} />}
    </span>
  );
}

/** Header context: "A • B • C" with optional trailing segment after a divider. */
export function HeaderContext({
  items,
  trailing,
  action,
}: {
  items: string[];
  trailing?: string;
  action?: ReactNode;
}) {
  return (
    <>
      <span className={styles.contextItems}>
        {items.map((item, index) => (
          <span key={`${item}-${index}`} className={styles.contextItem}>
            {index > 0 && (
              <span className={styles.contextDot} aria-hidden="true">
                •
              </span>
            )}
            {item}
          </span>
        ))}
      </span>
      {trailing && (
        <>
          <span className={styles.contextDivider} aria-hidden="true" />
          <span className={styles.contextItem}>{trailing}</span>
        </>
      )}
      {action}
    </>
  );
}
