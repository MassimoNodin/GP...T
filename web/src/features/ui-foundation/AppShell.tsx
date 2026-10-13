import Link from "next/link";
import type { ReactNode } from "react";
import { LiveShellFooter, LiveShellProvider, LiveStatusPill } from "@/features/live-status/LiveShell";
import { ShellFooterBlock, StatusPill } from "@/features/live-status/ShellParts";
import { F1Logo } from "./F1Logo";
import { DriveIcon, ReviewIcon, SettingsIcon } from "./icons";
import { ToastProvider } from "./Toast";
import styles from "./AppShell.module.css";

export type ShellTone = "green" | "amber" | "red" | "grey";

export type ShellStatus = {
  tone: ShellTone;
  label: string;
  menu?: boolean;
  /** Rows for the hover and focus popover under the pill. */
  details?: Array<{ label: string; value: string; tone: ShellTone }>;
};

export type ShellFooter = {
  tone: ShellTone;
  label: string;
  lines: string[];
};

/**
 * Live mode for the header pill and sidebar footer. Omitted keeps the static,
 * server-rendered values. "telemetry" follows the UDP receiver state;
 * "engineer" follows the engineer runtime. Both start from the props below and
 * refresh in the browser while the tab is visible.
 */
export type ShellLiveMode = "telemetry" | "engineer";

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
  live,
  children,
}: {
  active: NavKey;
  status: ShellStatus;
  context: ReactNode;
  footer: ShellFooter;
  live?: ShellLiveMode;
  children: ReactNode;
}) {
  const shell = (
    <div className={styles.app}>
      <a className={styles.skip} href="#main">
        Skip to content
      </a>
      <header className={styles.header}>
        <Link href="/drive" className={styles.brand} aria-label="Race Engineer home">
          <F1Logo className={styles.logo} />
          <span className={styles.brandName}>Race Engineer</span>
        </Link>
        {live ? <LiveStatusPill fallback={status} /> : <StatusPill status={status} />}
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
        {live ? <LiveShellFooter fallback={footer} /> : <ShellFooterBlock footer={footer} />}
      </aside>
      <main id="main" className={styles.main} tabIndex={-1}>
        <ToastProvider>{children}</ToastProvider>
      </main>
    </div>
  );
  return live ? (
    <LiveShellProvider mode={live} status={status} footer={footer}>
      {shell}
    </LiveShellProvider>
  ) : (
    shell
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
