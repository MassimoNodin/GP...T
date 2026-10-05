import {
  appScreenHref,
  isSelectionTransferBlocked,
  type AppScreen,
} from "@/lib/navigation";

const links = [
  { screen: "dashboard" as const, label: "Dashboard", icon: "⌂" },
  { screen: "live" as const, label: "Live Telemetry", icon: "∿" },
  { screen: "sessions" as const, label: "Sessions", icon: "▤" },
  { screen: "compare" as const, label: "Lap Comparison", icon: "▥" },
  { screen: "track" as const, label: "Track Analysis", icon: "⌁" },
  { screen: "engineer" as const, label: "AI Engineer", icon: "▢" },
  { screen: "recordings" as const, label: "Recordings", icon: "◉" },
  { screen: "settings" as const, label: "Settings", icon: "⚙" },
];

export default function AppHeader({
  active,
  preservedQuery,
}: {
  active: AppScreen;
  preservedQuery: string;
}) {
  const navigation = links.map((item) => ({
    ...item,
    href: appScreenHref(item.screen, preservedQuery),
  }));
  const dashboardHref = navigation.find(
    (item) => item.screen === "dashboard",
  )?.href;
  const transferBlocked =
    isSelectionTransferBlocked(preservedQuery) ||
    navigation.some((item) => item.href === null);
  const brand = (
    <>
      <span className="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 36 36">
          <path d="M4 23 10 7h8l-5 13h6l5-13h8l-8 22h-8l4-11h-6l-4 11H2z" />
        </svg>
      </span>
      <span className="brand-lockup">
        <span className="brand-name">
          GP<span className="brand-dots">...</span>T
        </span>
        <span className="brand-descriptor">AI F1 RACE ENGINEER</span>
      </span>
    </>
  );

  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <aside className="app-rail" aria-label="Application navigation">
        {transferBlocked ? (
          <span
            className="brand brand-disabled"
            aria-label="GP...T dashboard"
            aria-disabled="true"
          >
            {brand}
          </span>
        ) : (
          <a className="brand" href={dashboardHref!} aria-label="GP...T dashboard">
            {brand}
          </a>
        )}
        <nav className="app-nav" aria-label="Primary">
          {navigation.map((item) =>
            transferBlocked ? (
              <span
                className={`app-nav-disabled ${active === item.screen ? "is-current" : ""}`}
                aria-disabled="true"
                key={item.screen}
              >
                <span className="nav-icon" aria-hidden="true">{item.icon}</span>
                <span>{item.label}</span>
              </span>
            ) : (
              <a
                href={item.href!}
                aria-current={active === item.screen ? "page" : undefined}
                key={item.screen}
              >
                <span className="nav-icon" aria-hidden="true">{item.icon}</span>
                <span>{item.label}</span>
              </a>
            ),
          )}
          {transferBlocked ? (
            <span className="app-nav-status" role="status">
              Selection is too large to carry safely. Navigation is paused.
            </span>
          ) : null}
        </nav>
        <div className="rail-footnote">
          <span className="rail-footnote-icon" aria-hidden="true">▰</span>
          <span><strong>Local workspace</strong><small>Telemetry analysis</small></span>
        </div>
      </aside>
      <header className="topbar">
        <div className="topbar-context">
          <span className="local-status-dot" aria-hidden="true" />
          <span><strong>Local mode</strong><small>Data stays on this computer</small></span>
        </div>
        <div className="topbar-right">
          <span className="topbar-evidence">EVIDENCE-BASED ANALYSIS</span>
          <span className="topbar-version">F1 TELEMETRY</span>
        </div>
      </header>
    </>
  );
}
