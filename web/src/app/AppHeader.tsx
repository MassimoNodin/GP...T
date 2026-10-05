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
  const links = [
    { screen: "dashboard" as const, label: "Dashboard" },
    { screen: "recordings" as const, label: "Recordings" },
    { screen: "sessions" as const, label: "Sessions" },
    { screen: "compare" as const, label: "Compare" },
    { screen: "engineer" as const, label: "Engineer" },
    { screen: "live" as const, label: "Live" },
  ].map((item) => ({
    ...item,
    href: appScreenHref(item.screen, preservedQuery),
  }));
  const dashboardHref = links.find((item) => item.screen === "dashboard")?.href;
  const transferBlocked =
    isSelectionTransferBlocked(preservedQuery) ||
    links.some((item) => item.href === null);
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
              {links.map((item) => (
                <span
                  className={`app-nav-disabled ${active === item.screen ? "is-current" : ""}`}
                  aria-disabled="true"
                  key={item.screen}
                >
                  {item.label}
                </span>
              ))}
              <span className="app-nav-status" role="status">
                Selection too large to carry safely; navigation is paused.
              </span>
            </>
          ) : (
            links.map((item) => (
              <a
                href={item.href!}
                aria-current={active === item.screen ? "page" : undefined}
                key={item.screen}
              >
                {item.label}
              </a>
            ))
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
