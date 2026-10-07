import type { ReactNode } from "react";
import {
  appScreenHref,
  isSelectionTransferBlocked,
  type AppScreen,
} from "@/lib/navigation";

export function FlagIT() {
  return (
    <svg viewBox="0 0 3 2" width="16" height="11" style={{ borderRadius: "2px", display: "inline-block", verticalAlign: "middle", marginRight: "5px" }}>
      <rect width="1" height="2" fill="#009246" />
      <rect x="1" width="1" height="2" fill="#ffffff" />
      <rect x="2" width="1" height="2" fill="#ce2b37" />
    </svg>
  );
}

export function Panel({
  title,
  icon,
  action,
  subtitle,
  className = "",
  id,
  children,
}: {
  title: string;
  icon?: ReactNode;
  action?: ReactNode;
  subtitle?: string;
  className?: string;
  id?: string;
  children: ReactNode;
}) {
  return (
    <section className={`ref-panel ${className}`} id={id}>
      <header className="ref-panel-heading">
        <span className="ref-panel-icon" aria-hidden="true">{icon ?? "•"}</span>
        <div className="ref-panel-title">
          <h2>{title}</h2>
          {subtitle ? <small>{subtitle}</small> : null}
        </div>
        {action ? <div className="ref-panel-action">{action}</div> : null}
      </header>
      {children}
    </section>
  );
}

export function PageHeading({
  title,
  subtitle,
  icon = "◈",
  status,
}: {
  title: string;
  subtitle?: string;
  icon?: ReactNode;
  status?: ReactNode;
}) {
  return (
    <header className="ref-page-heading">
      <span className="ref-page-icon" aria-hidden="true">{icon}</span>
      <div>
        <h1>{title}</h1>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
      {status ? <div className="ref-page-status">{status}</div> : null}
    </header>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  return <span className={`ref-badge ref-badge-${tone}`}>{children}</span>;
}

export function ActionLink({
  href,
  screen,
  preservedQuery = "",
  children,
  primary = false,
}: {
  href?: string;
  screen?: AppScreen;
  preservedQuery?: string;
  children: ReactNode;
  primary?: boolean;
}) {
  const route = screen ? appScreenHref(screen, preservedQuery) : null;
  const blocked =
    screen !== undefined &&
    (route === null || isSelectionTransferBlocked(preservedQuery));
  const destination = route
    ? `${route}${href?.startsWith("#") ? href : ""}`
    : href;
  const className = `ref-button ${primary ? "is-primary" : ""}`;
  if (!destination || blocked) {
    return (
      <span
        className={`${className} is-disabled`}
        aria-disabled="true"
      >
        {children}
      </span>
    );
  }
  return <a className={className} href={destination}>{children}</a>;
}

export function Metric({
  label,
  value = "—",
  unit,
  detail,
  tone,
  icon,
}: {
  label: string;
  value?: string;
  unit?: string;
  detail?: string;
  tone?: "good" | "bad" | "blue";
  icon?: ReactNode;
}) {
  return (
    <div className={`ref-metric ${tone ? `ref-metric-${tone}` : ""}`}>
      <span className="ref-metric-label">
        {icon ? <i aria-hidden="true">{icon}</i> : null}
        {label}
      </span>
      <strong>
        {value}
        {unit ? <small>{unit}</small> : null}
      </strong>
      {detail ? <span className="ref-metric-detail">{detail}</span> : null}
    </div>
  );
}

export function ChipRow({ children }: { children: ReactNode }) {
  return <div className="ref-chip-row">{children}</div>;
}
