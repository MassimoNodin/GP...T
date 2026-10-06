import {
  appScreenHref,
  isSelectionTransferBlocked,
  type AppScreen,
} from "@/lib/navigation";

function NavIcon({ screen, active }: { screen: AppScreen; active: boolean }) {
  switch (screen) {
    case "dashboard":
      return active ? (
        <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
          <path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 9.5L12 3l9 6.5V20a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9.5z" />
          <path d="M9 21V12h6v9" />
        </svg>
      );
    case "live":
      return (
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M2 12h4l2.5-7 4 14 3-9 2.5 5 2-3H22" />
        </svg>
      );
    case "sessions":
      return (
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <ellipse cx="12" cy="5" rx="8" ry="3" />
          <path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
          <path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" />
        </svg>
      );
    case "compare":
      return (
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="11" width="4" height="10" rx="1.5" />
          <rect x="10" y="7" width="4" height="14" rx="1.5" />
          <rect x="17" y="3" width="4" height="18" rx="1.5" />
        </svg>
      );
    case "track":
      return (
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M7 18c-2.5 0-4-1.5-4-3.5 0-2.5 2-4.5 4.5-4.5h2c2.5 0 3.5-1.5 4.5-3 1.2-1.8 3-2 5-1 2.2 1.1 3 3.5 1.5 5.8l-2.5 4c-1.2 2-3.2 3.2-5.5 2.2H7z" />
        </svg>
      );
    case "engineer":
      return (
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="4" width="18" height="13" rx="3" />
          <path d="M8 17v4l4-4" />
          <circle cx="8" cy="10.5" r="1" fill="currentColor" />
          <circle cx="12" cy="10.5" r="1" fill="currentColor" />
          <circle cx="16" cy="10.5" r="1" fill="currentColor" />
        </svg>
      );
    case "recordings":
      return (
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="9" />
          <circle cx="12" cy="12" r="4.5" fill="currentColor" stroke="none" />
        </svg>
      );
    case "settings":
      return (
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      );
  }
}

const links = [
  { screen: "dashboard" as const, label: "Dashboard" },
  { screen: "live" as const, label: "Live Telemetry" },
  { screen: "sessions" as const, label: "Sessions" },
  { screen: "compare" as const, label: "Lap Comparison" },
  { screen: "track" as const, label: "Track Analysis" },
  { screen: "engineer" as const, label: "AI Engineer" },
  { screen: "recordings" as const, label: "Recordings" },
  { screen: "settings" as const, label: "Settings" },
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
  const recordingsHref = appScreenHref("recordings", preservedQuery);
  const settingsHref = appScreenHref("settings", preservedQuery);
  const transferBlocked =
    isSelectionTransferBlocked(preservedQuery) ||
    navigation.some((item) => item.href === null);

  const brand = (
    <>
      <span className="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 42 32" width="40" height="30" fill="none">
          <g transform="skewX(-15)">
            <rect x="7" y="2" width="11" height="8" rx="2" fill="#ef1827" />
            <rect x="23" y="2" width="11" height="8" rx="2" fill="#ef1827" />
            <rect x="15" y="12" width="11" height="8" rx="2" fill="#ef1827" />
            <rect x="7" y="22" width="11" height="8" rx="2" fill="#ef1827" />
            <rect x="23" y="22" width="11" height="8" rx="2" fill="#ef1827" />
          </g>
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
          {navigation.map((item) => {
            const isCurrent = active === item.screen;
            return transferBlocked ? (
              <span
                className={`app-nav-disabled ${isCurrent ? "is-current" : ""}`}
                aria-disabled="true"
                key={item.screen}
              >
                <span className="nav-icon" aria-hidden="true">
                  <NavIcon screen={item.screen} active={isCurrent} />
                </span>
                <span>{item.label}</span>
              </span>
            ) : (
              <a
                href={item.href!}
                aria-current={isCurrent ? "page" : undefined}
                key={item.screen}
              >
                <span className="nav-icon" aria-hidden="true">
                  <NavIcon screen={item.screen} active={isCurrent} />
                </span>
                <span>{item.label}</span>
              </a>
            );
          })}
          {transferBlocked ? (
            <span className="app-nav-status" role="status">
              Selection is too large to carry safely. Navigation is paused.
            </span>
          ) : null}
        </nav>
        <div className="rail-footnote">
          <div className="rail-footnote-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#101827" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 4h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" />
              <line x1="3" y1="14" x2="21" y2="14" />
              <line x1="16" y1="7" x2="16" y2="10" />
              <circle cx="7" cy="17" r="1.2" fill="#101827" stroke="none" />
              <circle cx="12" cy="17" r="1.2" fill="#101827" stroke="none" />
            </svg>
          </div>
          <div className="rail-footnote-body">
            <div className="rail-footnote-top">
              <strong>Storage</strong>
            </div>
            <div className="rail-storage-track" aria-hidden="true">
              <i style={{ width: "21.3%" }} />
            </div>
            <small>42.6 GB / 200 GB</small>
          </div>
        </div>
      </aside>
      <header className="topbar">
        <div className="topbar-context">
          <span className="local-status-dot" aria-hidden="true" />
          <div className="topbar-text">
            <strong>Local Mode</strong>
            <small>All data stays on your computer</small>
          </div>
        </div>
        <div className="topbar-right">
          <span className="topbar-udp-badge">
            <span className="topbar-dot-green" aria-hidden="true" />
            UDP Recording Active
          </span>
          {transferBlocked || !recordingsHref ? (
            <span className="topbar-stop-btn is-disabled" aria-disabled="true">
              <span className="stop-square" aria-hidden="true" />
              Stop Recording
            </span>
          ) : (
            <a className="topbar-stop-btn" href={recordingsHref}>
              <span className="stop-square" aria-hidden="true" />
              Stop Recording
            </a>
          )}
          {transferBlocked || !settingsHref ? (
            <span className="topbar-icon-btn is-disabled" aria-disabled="true" aria-label="Settings">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="3"/>
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
              </svg>
            </span>
          ) : (
            <a className="topbar-icon-btn" href={settingsHref} aria-label="Settings">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="3"/>
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
              </svg>
            </a>
          )}
          <div className="topbar-window-controls" aria-hidden="true">
            <span className="win-btn">
              <svg viewBox="0 0 10 10" width="10" height="10" stroke="currentColor" strokeWidth="1.2">
                <line x1="1" y1="5" x2="9" y2="5" />
              </svg>
            </span>
            <span className="win-btn">
              <svg viewBox="0 0 10 10" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="1.2">
                <rect x="1.5" y="1.5" width="7" height="7" rx="1" />
              </svg>
            </span>
            <span className="win-btn">
              <svg viewBox="0 0 10 10" width="10" height="10" stroke="currentColor" strokeWidth="1.2">
                <line x1="2" y1="2" x2="8" y2="8" />
                <line x1="8" y1="2" x2="2" y2="8" />
              </svg>
            </span>
          </div>
        </div>
      </header>
    </>
  );
}
