import {
  appScreenHref,
  isSelectionTransferBlocked,
  type AppScreen,
} from "@/lib/navigation";

export default function AppHeader({
  active,
  preservedQuery,
}: {
  active: AppScreen;
  preservedQuery: string;
}) {
  const dashboardHref = appScreenHref("dashboard", preservedQuery);
  const recordingsHref = appScreenHref("recordings", preservedQuery);
  const transferBlocked =
    isSelectionTransferBlocked(preservedQuery) ||
    dashboardHref === null ||
    recordingsHref === null;
  const brand = (
    <>
      <svg className="brand-mark" viewBox="0 0 64 64" aria-hidden="true">
        <path d="M12 44h11V31h10v7h10V19h9" />
        <circle cx="23" cy="31" r="3" />
        <circle cx="33" cy="38" r="3" />
        <circle cx="43" cy="19" r="3" />
      </svg>
      <span className="brand-name">
        GP<span className="brand-dots">...</span>T
      </span>
      <span className="brand-descriptor">PERSONAL AI RACE ENGINEER</span>
    </>
  );
  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="topbar">
        {transferBlocked ? (
          <span
            className="brand brand-disabled"
            aria-label="GP...T dashboard"
            aria-disabled="true"
          >
            {brand}
          </span>
        ) : (
          <a
            className="brand"
            href={dashboardHref!}
            aria-label="GP...T dashboard"
          >
            {brand}
          </a>
        )}
        <nav
          className={`app-nav ${transferBlocked ? "app-nav-blocked" : ""}`}
          aria-label="Primary"
        >
          {transferBlocked ? (
            <>
              <span
                className={`app-nav-disabled ${active === "dashboard" ? "is-current" : ""}`}
                aria-disabled="true"
              >
                Dashboard
              </span>
              <span
                className={`app-nav-disabled ${active === "recordings" ? "is-current" : ""}`}
                aria-disabled="true"
              >
                Recordings
              </span>
              <span className="app-nav-status" role="status">
                Selection too large to carry safely; navigation is paused.
              </span>
            </>
          ) : (
            <>
              <a
                href={dashboardHref!}
                aria-current={active === "dashboard" ? "page" : undefined}
              >
                Dashboard
              </a>
              <a
                href={recordingsHref!}
                aria-current={active === "recordings" ? "page" : undefined}
              >
                Recordings
              </a>
            </>
          )}
        </nav>
        <div className="topbar-right">
          <span className="local-indicator">
            <i /> LOCAL TELEMETRY
          </span>
          <span className="topbar-version">HISTORICAL ANALYSIS · V1</span>
        </div>
      </header>
    </>
  );
}
