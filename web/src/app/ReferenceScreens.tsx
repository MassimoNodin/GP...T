import type { ReactNode } from "react";
import {
  appScreenHref,
  isSelectionTransferBlocked,
  type AppScreen,
} from "@/lib/navigation";

import {
  ChevronDownIcon,
  FuelIcon,
  ZapIcon,
  SunIcon,
  ThermometerIcon,
  StopwatchIcon,
  GearIcon,
  CalendarIcon,
  CheckIcon,
  WarningIcon,
  CrossIcon,
  FileIcon,
  FolderIcon,
  BarChartIcon,
  LineChartIcon,
  DatabaseIcon,
  SearchIcon,
  StarIcon,
  LightbulbIcon,
  CarIcon,
  MapPinIcon,
  RefreshIcon,
  ChatBubbleIcon,
  SendIcon,
  ThumbUpIcon,
  ThumbDownIcon,
  CopyClipboardIcon,
  TargetIcon,
  FlagOutlineIcon,
  PauseIcon,
  PlayIcon,
  StopIcon,
  SkipBackIcon,
  SkipForwardIcon,
  TrashIcon,
  BrainIcon,
  SpeakerIcon,
  GamepadIcon,
  MonitorIcon,
  MicrophoneIcon,
  KeyboardIcon,
  UserIcon,
  WaveIcon,
  FlagCheckeredIcon,
  SignalIcon,
  CircuitIcon,
  TableIcon,
  UploadIcon,
  DownloadIcon,
  ArrowUpIcon,
  ArrowDownIcon,
  ArrowUpRightIcon,
  InfoIcon,
} from "./Icons";

function FlagIT() {
  return (
    <svg viewBox="0 0 3 2" width="16" height="11" style={{ borderRadius: "2px", display: "inline-block", verticalAlign: "middle", marginRight: "5px" }}>
      <rect width="1" height="2" fill="#009246" />
      <rect x="1" width="1" height="2" fill="#ffffff" />
      <rect x="2" width="1" height="2" fill="#ce2b37" />
    </svg>
  );
}

export type ReferenceScreenId =
  | "dashboard"
  | "live"
  | "engineer"
  | "compare"
  | "settings"
  | "recordings"
  | "track"
  | "sessions";

export default function ReferenceScreen({
  screen,
  preservedQuery,
}: {
  screen: ReferenceScreenId;
  preservedQuery: string;
}) {
  const content = {
    dashboard: <Dashboard preservedQuery={preservedQuery} />,
    live: <Live preservedQuery={preservedQuery} />,
    engineer: <Engineer preservedQuery={preservedQuery} />,
    compare: <Compare preservedQuery={preservedQuery} />,
    settings: <Settings preservedQuery={preservedQuery} />,
    recordings: <Recordings preservedQuery={preservedQuery} />,
    track: <Track preservedQuery={preservedQuery} />,
    sessions: <Sessions preservedQuery={preservedQuery} />,
  }[screen];

  return (
    <section className={`reference-screen reference-${screen}`} aria-label={`${screen} screen`}>
      {content}
    </section>
  );
}

function Panel({
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

function PageHeading({
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

function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  return <span className={`ref-badge ref-badge-${tone}`}>{children}</span>;
}

function ActionLink({
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

function Metric({
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

function ChipRow({ children }: { children: ReactNode }) {
  return <div className="ref-chip-row">{children}</div>;
}

/* =========================================================================
   1. DASHBOARD (Mock Image 1)
   ========================================================================= */
function Dashboard({ preservedQuery }: { preservedQuery: string }) {
  return (
    <>
      <div className="ref-grid dash-top">
        {/* Panel 1: Recording & Session */}
        <Panel
          title="Recording & Session"
          icon={<span className="dot-red" />}
        >
          <div className="ref-recording-summary">
            <div className="ref-recording-header-row">
              <Badge tone="good">
                <span className="dot-green-pulse" /> RECORDING (UDP)
              </Badge>
              <span className="ref-f1-badge"><CarIcon size={12} style={{ marginRight: 4 }} /> F1 124 <SignalIcon size={11} style={{ marginLeft: 4 }} /></span>
            </div>
            <div className="ref-receiving-port">Receiving telemetry on 20777</div>
            <div className="ref-recording-big">
              <strong>00:18:42</strong>
              <ActionLink screen="recordings" preservedQuery={preservedQuery} primary={false}>
                <span className="stop-sq-btn" /> Stop Recording
              </ActionLink>
            </div>
            <div className="ref-lap-capture-sub">
              <span>Lap 12</span> <span className="sep-pipe">|</span> <span>2.4 GB captured</span>
            </div>
          </div>
          <div className="ref-current-session-box">
            <span className="ref-session-label">Current Session</span>
            <div className="ref-session-card">
              <img src="/monza-thumb.png" alt="Monza" className="ref-session-thumb" />
              <div className="ref-session-info">
                <div className="ref-track-title"><FlagIT /> Monza</div>
                <div className="ref-gp-name">Italian Grand Prix</div>
                <div className="ref-session-type">Practice 1</div>
                <div className="ref-session-date">06 Sep 2026 11:23</div>
              </div>
              <ActionLink screen="sessions" preservedQuery={preservedQuery}>
                Change
              </ActionLink>
            </div>
          </div>
        </Panel>

        {/* Panel 2: Live Telemetry */}
        <Panel
          title="Live Telemetry"
          icon={
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
              <path d="M2 12h4l2.5-7 4 14 3-9 2.5 5 2-3H22" />
            </svg>
          }
          action={
            <div className="ref-live-tags">
              <Badge tone="good"><span className="dot-green" /> Live</Badge>
              <span className="ref-tag-sub">Car On Track</span>
              <span className="ref-tag-sub">Lap 12</span>
            </div>
          }
        >
          {/* Speed Arc Gauge */}
          <div className="ref-speedo-gauge">
            <svg viewBox="0 0 240 100" className="gauge-arc-svg">
              <path
                d="M 25 90 A 95 95 0 0 1 215 90"
                fill="none"
                stroke="#e2e8f0"
                strokeWidth="10"
                strokeLinecap="round"
                strokeDasharray="4 8"
              />
              <path
                d="M 25 90 A 95 95 0 0 1 185 45"
                fill="none"
                stroke="url(#speedoGrad)"
                strokeWidth="10"
                strokeLinecap="round"
                strokeDasharray="4 8"
              />
              <defs>
                <linearGradient id="speedoGrad" x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor="#0284c7" />
                  <stop offset="60%" stopColor="#22c55e" />
                  <stop offset="90%" stopColor="#ef4444" />
                </linearGradient>
              </defs>
            </svg>
            <div className="gauge-readout-row">
              <div className="gauge-main">
                <span className="gauge-num">312</span>
                <span className="gauge-unit">KM/H</span>
              </div>
              <div className="gauge-sub">
                <span className="gauge-label">Gear</span>
                <span className="gauge-gear">8</span>
              </div>
              <div className="gauge-sub">
                <span className="gauge-rpm">11,842</span>
                <span className="gauge-label">RPM</span>
              </div>
            </div>
          </div>

          {/* Telemetry Input Bars */}
          <div className="ref-telemetry-bars">
            <div className="ref-bar-row">
              <span>Throttle</span>
              <div className="bar-track"><div className="bar-fill fill-green" style={{ width: "87%" }} /></div>
              <strong>87%</strong>
            </div>
            <div className="ref-bar-row">
              <span>Brake</span>
              <div className="bar-track"><div className="bar-fill fill-red" style={{ width: "0%" }} /></div>
              <strong>0%</strong>
            </div>
            <div className="ref-bar-row">
              <span>DRS</span>
              <div className="bar-track"><div className="bar-fill fill-blue" style={{ width: "100%" }} /></div>
              <strong className="text-blue">Open</strong>
            </div>
          </div>

          {/* Bottom quick stats */}
          <div className="ref-dash-quick-stats">
            <div><span>Tyres</span> <strong><span className="badge-tyre-m">M</span> 63%</strong></div>
            <div><span>Fuel</span> <strong><FuelIcon size={12} style={{ marginRight: 3 }} /> 22.4 L</strong></div>
            <div><span>ERS</span> <strong><ZapIcon size={12} style={{ marginRight: 3 }} /> 78%</strong></div>
          </div>
        </Panel>

        {/* Panel 3: AI Race Engineer */}
        <Panel
          title="AI Race Engineer"
          icon={
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
              <rect x="3" y="4" width="18" height="13" rx="3" />
              <path d="M8 17v4l4-4" />
            </svg>
          }
          action={<Badge tone="neutral"><span className="dot-green" /> Local AI (Offline)</Badge>}
        >
          <div className="ref-engineer-welcome">
            <span className="ai-bot-avatar"><ChatBubbleIcon size={14} /></span>
            <p>
              I'm your AI race engineer. Ask me about pace, braking, traction, strategy or anything about your driving. I'll use your live and recorded data.
            </p>
          </div>
          <div className="ref-prompt-list">
            {[
              "How's my tyre wear looking?",
              "Where can I find more time at Monza?",
              "Compare my last 3 laps.",
              "Am I braking too early for Turn 1?",
              "What should I focus on in the next run?",
            ].map((prompt) => (
              <button type="button" key={prompt}>
                <span>{prompt}</span>
                <i>→</i>
              </button>
            ))}
          </div>
          <div className="ref-composer">
            <input type="text" placeholder="Ask your engineer..." readOnly />
            <button type="button" aria-label="Send"><SendIcon size={13} /></button>
          </div>
        </Panel>
      </div>

      {/* Middle Row */}
      <div className="ref-grid dash-middle">
        {/* Panel 4: Lap & Session Selector */}
        <Panel
          title="Lap & Session Selector"
          icon={
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
              <circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" />
            </svg>
          }
          action={<ActionLink screen="sessions" preservedQuery={preservedQuery}>View all sessions →</ActionLink>}
        >
          <div className="ref-tab-pills">
            <button className="tab-pill active">Current Session</button>
            <button className="tab-pill">Imported Sessions</button>
          </div>
          <div className="ref-select-row">
            <button type="button">Practice 1 <ChevronDownIcon size={11} /></button>
            <button type="button">Monza <ChevronDownIcon size={11} /></button>
          </div>
          <div className="ref-table-wrap is-compact">
            <table className="ref-table">
              <thead>
                <tr>
                  <th>Lap</th><th>Lap Time</th><th>Δ Best</th><th>S1</th><th>S2</th><th>S3</th>
                </tr>
              </thead>
              <tbody>
                <tr className="is-selected">
                  <td><strong>12</strong></td>
                  <td><strong>1:20.412</strong></td>
                  <td><span className="badge-delta-good">-0.317</span></td>
                  <td><span className="badge-sec-blue">27.321</span></td>
                  <td><span className="badge-sec-blue">28.547</span></td>
                  <td>24.544</td>
                </tr>
                <tr><td>11</td><td>1:20.941</td><td><span className="badge-delta-bad">+0.212</span></td><td>27.682</td><td>28.691</td><td>24.568</td></tr>
                <tr><td>10</td><td>1:21.003</td><td><span className="badge-delta-bad">+0.274</span></td><td>27.804</td><td>28.920</td><td>24.723</td></tr>
                <tr><td>9</td><td>1:21.287</td><td><span className="badge-delta-bad">+0.558</span></td><td>28.021</td><td>29.103</td><td>24.863</td></tr>
                <tr><td>8</td><td>1:21.198</td><td><span className="badge-delta-bad">+0.469</span></td><td>28.003</td><td>29.012</td><td>24.875</td></tr>
                <tr><td>7</td><td>1:21.991</td><td><span className="badge-delta-bad">+1.269</span></td><td>28.412</td><td>29.301</td><td>24.878</td></tr>
                <tr><td>6</td><td>1:22.304</td><td><span className="badge-delta-bad">+1.582</span></td><td>28.691</td><td>29.512</td><td>24.901</td></tr>
                <tr><td>5</td><td>1:22.865</td><td><span className="badge-delta-bad">+2.143</span></td><td>29.004</td><td>29.874</td><td>24.987</td></tr>
              </tbody>
            </table>
          </div>
        </Panel>

        {/* Panel 5: Selected Lap Summary */}
        <Panel
          title="Selected Lap Summary"
          icon={
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
              <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/>
            </svg>
          }
          action={
            <div className="ref-lap-actions">
              <span className="lap-nav-arrows">‹ Lap 12 of 18 ›</span>
              <button className="ref-btn-subtle">Set as Reference</button>
              <button className="ref-btn-icon">•••</button>
            </div>
          }
        >
          <div className="ref-lap-summary-header">
            <div>
              <span className="ref-metric-label">Lap Time</span>
              <div className="ref-hero-time">
                <strong>1:20.412</strong>
                <span className="badge-delta-good">-0.317 vs Session Best</span>
              </div>
            </div>
            <div>
              <span className="ref-metric-label">Position</span>
              <strong>P6</strong>
            </div>
            <div>
              <span className="ref-metric-label">Track</span>
              <strong><FlagIT /> Monza</strong>
              <small>Practice 1</small>
            </div>
          </div>
          <div className="ref-metric-grid four">
            <Metric label="Sector 1" value="27.321" detail="-0.123" tone="good" />
            <Metric label="Sector 2" value="28.547" detail="-0.041" tone="good" />
            <Metric label="Sector 3" value="24.544" detail="-0.153" tone="good" />
            <Metric label="Top Speed" value="339 km/h" detail="+2 km/h" tone="blue" />
          </div>
          <div className="ref-metric-grid five">
            <Metric label="Tyre" value="Medium" detail="63% · Est. 7 laps" icon={<span className="badge-tyre-m">M</span>} />
            <Metric label="Fuel Load" value="22.4 L" detail="Est. 5.1 laps" icon={<FuelIcon size={13} />} />
            <Metric label="ERS Usage" value="78%" detail="Hotlap" icon={<ZapIcon size={13} />} />
            <Metric label="Track Temp" value="33°C" icon={<SunIcon size={13} />} />
            <Metric label="Air Temp" value="28°C" icon={<SunIcon size={13} />} />
          </div>
        </Panel>

        {/* Panel 6: Track & Region Analysis */}
        <Panel
          title="Track & Region Analysis"
          icon={
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
              <path d="M7 18c-2.5 0-4-1.5-4-3.5 0-2.5 2-4.5 4.5-4.5h2c2.5 0 3.5-1.5 4.5-3 1.2-1.8 3-2 5-1 2.2 1.1 3 3.5 1.5 5.8l-2.5 4c-1.2 2-3.2 3.2-5.5 2.2H7z" />
            </svg>
          }
          action={<button className="ref-dropdown-btn">Monza <ChevronDownIcon size={11} /></button>}
        >
          <div className="ref-track-map-wrapper">
            <img src="/track-monza.png" alt="Monza Track Analysis" className="ref-monza-img" />
          </div>
        </Panel>
      </div>

      {/* Bottom Row */}
      <div className="ref-grid dash-bottom">
        {/* Panel 7: Recent Recordings */}
        <Panel
          title="Recent Recordings"
          icon={
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
              <ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/><path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/>
            </svg>
          }
          action={<ActionLink screen="recordings" preservedQuery={preservedQuery}>View Files →</ActionLink>}
        >
          <div className="ref-table-wrap is-compact">
            <table className="ref-table">
              <thead>
                <tr><th>Name</th><th>Track</th><th>Type</th><th>Date</th><th>Laps</th><th>Size</th><th></th></tr>
              </thead>
              <tbody>
                <tr>
                  <td><span className="icon-play"><PlayIcon size={10} /></span> Monza_Practice1_2026_09_06_1123</td>
                  <td>Monza</td><td>Practice</td><td>Today, 11:23</td><td>18</td><td>2.4 GB</td><td>•••</td>
                </tr>
                <tr>
                  <td><span className="icon-play"><PlayIcon size={10} /></span> Zandvoort_Race_2026_08_31</td>
                  <td>Zandvoort</td><td>Race</td><td>31 Aug 2026</td><td>72</td><td>2.1 GB</td><td>•••</td>
                </tr>
                <tr>
                  <td><span className="icon-play"><PlayIcon size={10} /></span> Spa_Qualifying_2026_08_24</td>
                  <td>Spa</td><td>Qualifying</td><td>24 Aug 2026</td><td>12</td><td>420 MB</td><td>•••</td>
                </tr>
                <tr>
                  <td><span className="icon-play"><PlayIcon size={10} /></span> Silverstone_Practice_2026_08_17</td>
                  <td>Silverstone</td><td>Practice</td><td>17 Aug 2026</td><td>46</td><td>1.4 GB</td><td>•••</td>
                </tr>
                <tr>
                  <td><span className="icon-play"><PlayIcon size={10} /></span> Imola_Race_2026_08_10</td>
                  <td>Imola</td><td>Race</td><td>10 Aug 2026</td><td>63</td><td>2.2 GB</td><td>•••</td>
                </tr>
              </tbody>
            </table>
          </div>
        </Panel>

        {/* Panel 8: Lap Comparison */}
        <Panel
          title="Lap Comparison"
          icon={
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
              <rect x="3" y="11" width="4" height="10" rx="1.5"/><rect x="10" y="7" width="4" height="14" rx="1.5"/><rect x="17" y="3" width="4" height="18" rx="1.5"/>
            </svg>
          }
          action={
            <div className="ref-chart-header-controls">
              <span className="ref-compare-to">Compare to</span>
              <button className="ref-dropdown-btn">Session Best (1:20.095) <ChevronDownIcon size={11} /></button>
              <span className="ref-legend-item"><span className="dot-blue" /> Current Lap (1:20.412)</span>
              <span className="ref-legend-item"><span className="dot-red" /> Reference Lap (1:20.095)</span>
              <div className="ref-tab-pills small">
                <button className="tab-pill">Sectors</button>
                <button className="tab-pill active">Full Lap</button>
              </div>
              <button className="ref-dropdown-btn">Speed <ChevronDownIcon size={11} /></button>
              <button className="ref-btn-icon"><GearIcon size={12} /></button>
            </div>
          }
        >
          <div className="ref-chart-img-wrapper">
            <img src="/chart-comp.png" alt="Lap Comparison Traces" className="ref-comp-chart-img" />
          </div>
        </Panel>
      </div>
    </>
  );
}

/* =========================================================================
   2. LIVE TELEMETRY (Mock Image 2)
   ========================================================================= */
function Live({ preservedQuery }: { preservedQuery: string }) {
  return (
    <>
      <div className="ref-grid live-top">
        {/* Panel 1: Session Information */}
        <Panel title="Session Information" icon={<span className="dot-red" />}>
          <div className="ref-session-card">
            <img src="/monza-thumb.png" alt="Monza" className="ref-session-thumb large" />
            <div className="ref-session-info">
              <div className="ref-track-title"><FlagIT /> Monza</div>
              <div className="ref-gp-name">Italian Grand Prix</div>
              <div className="ref-session-type">Practice 1</div>
            </div>
          </div>
          <div className="ref-facts-box">
            <div><span>Session Time</span> <strong>00:18:42</strong></div>
            <div><span>Local Time</span> <strong>08 Sep 2026 11:23</strong></div>
            <div><span>Track Temperature</span> <strong>33°C</strong></div>
            <div><span>Air Temperature</span> <strong>28°C</strong></div>
          </div>
        </Panel>

        {/* Panel 2: Live Telemetry */}
        <Panel
          title="Live Telemetry"
          icon={
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
              <path d="M2 12h4l2.5-7 4 14 3-9 2.5 5 2-3H22" />
            </svg>
          }
          action={
            <div className="ref-live-tags">
              <Badge tone="good"><span className="dot-green" /> Live</Badge>
              <Badge tone="good"><span className="dot-green" /> Fresh 124 ms</Badge>
              <span className="ref-btn-icon" title="Telemetry Rate">
                <svg viewBox="0 0 16 16" width="12" height="12" fill="#0260e8">
                  <rect x="1" y="11" width="2.5" height="5" rx="0.5" />
                  <rect x="5" y="7" width="2.5" height="9" rx="0.5" />
                  <rect x="9" y="3" width="2.5" height="13" rx="0.5" />
                  <rect x="13" y="0" width="2.5" height="16" rx="0.5" />
                </svg>
              </span>
            </div>
          }
        >
          <div className="ref-speedo-gauge">
            <svg viewBox="0 0 240 100" className="gauge-arc-svg">
              <path d="M 25 90 A 95 95 0 0 1 215 90" fill="none" stroke="#e2e8f0" strokeWidth="10" strokeLinecap="round" strokeDasharray="4 8" />
              <path d="M 25 90 A 95 95 0 0 1 185 45" fill="none" stroke="url(#speedoGradLive)" strokeWidth="10" strokeLinecap="round" strokeDasharray="4 8" />
              <defs>
                <linearGradient id="speedoGradLive" x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor="#0284c7" />
                  <stop offset="60%" stopColor="#22c55e" />
                  <stop offset="90%" stopColor="#ef4444" />
                </linearGradient>
              </defs>
            </svg>
            <div className="gauge-readout-row">
              <div className="gauge-main">
                <span className="gauge-num">312</span>
                <span className="gauge-unit">KM/H</span>
              </div>
              <div className="gauge-sub">
                <span className="gauge-label">Gear</span>
                <span className="gauge-gear">8</span>
              </div>
              <div className="gauge-sub">
                <span className="gauge-rpm">11,842</span>
                <span className="gauge-label">RPM</span>
              </div>
            </div>
          </div>
          <div className="ref-live-lower">
            <div className="ref-telemetry-bars">
              <div className="ref-bar-row"><span>Throttle</span><div className="bar-track"><div className="bar-fill fill-green" style={{ width: "87%" }} /></div><strong>87%</strong></div>
              <div className="ref-bar-row"><span>Brake</span><div className="bar-track"><div className="bar-fill fill-red" style={{ width: "2%" }} /></div><strong>0%</strong></div>
              <div className="ref-bar-row"><span>DRS</span><div className="bar-track"><div className="bar-fill fill-blue" style={{ width: "100%" }} /></div><strong className="text-blue">Open</strong></div>
            </div>
            <div className="ref-live-tyre-fuel-grid">
              <div><span>Fuel</span> <strong><FuelIcon size={12} style={{ marginRight: 3 }} /> 22.4 L</strong></div>
              <div><span>ERS</span> <strong><ZapIcon size={12} style={{ marginRight: 3 }} /> 78% <small className="text-blue">Deploy</small></strong></div>
              <div><span>Tyres</span> <strong><span className="badge-tyre-m">M</span> 63%</strong></div>
              <div className="ref-tyre-wear-4">
                <span>Tyre Wear</span>
                <div className="tyre-quad">
                  <span>FL 62%</span><span>FR 65%</span>
                  <span>RL 58%</span><span>RR 61%</span>
                </div>
              </div>
            </div>
          </div>
        </Panel>

        {/* Panel 3: Telemetry Data Status */}
        <Panel
          title="Telemetry Data Status"
          icon={
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
              <ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/><path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/>
            </svg>
          }
        >
          <div className="ref-status-list">
            {[
              ["Player Telemetry", "Fresh", "124 ms", "good"],
              ["Car Status", "Fresh", "118 ms", "good"],
              ["Lap Timing", "Fresh", "121 ms", "good"],
              ["Track Data", "Fresh", "130 ms", "good"],
              ["Tyre & Wear", "Fresh", "128 ms", "good"],
              ["Position & Rivals", "Fresh", "135 ms", "good"],
              ["Weather Data", "Stale", "842 ms", "warning"],
            ].map(([item, stat, ms, tone]) => (
              <div key={item} className="ref-status-item-row">
                <div className="ref-status-item-left">
                  <span className={tone === "good" ? "dot-green" : "dot-orange"} />
                  <span>{item}</span>
                </div>
                <div className="ref-status-item-right">
                  <Badge tone={tone}>{stat}</Badge>
                  <small className="ref-ms-label">{ms}</small>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      {/* Middle Row */}
      <div className="ref-grid live-middle">
        {/* Panel 4: Live Lap Timing */}
        <Panel title="Live Lap Timing" icon={<StopwatchIcon size={14} />} action={<Badge>Lap 12 / 18</Badge>}>
          <div className="ref-lap-hero">
            <span>Current Lap</span>
            <strong>1:20.412</strong>
            <span className="badge-delta-good">-0.317 vs Personal Best</span>
          </div>
          <div className="ref-metric-grid five">
            <Metric label="Best Lap" value="1:20.412" detail="Lap 12" tone="blue" />
            <Metric label="Last Lap" value="1:20.941" detail="+0.529" tone="bad" />
            <Metric label="Sector 1" value="27.321" detail="-0.123" tone="good" />
            <Metric label="Sector 2" value="28.547" detail="-0.041" tone="good" />
            <Metric label="Sector 3" value="24.544" detail="-0.153" tone="good" />
          </div>
        </Panel>

        {/* Panel 5: Car Status (Live) */}
        <Panel
          title="Car Status (Live)"
          icon={
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
              <path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.5 3C2 11.2 2 11.6 2 12v4c0 .6.4 1 1 1h2"/>
              <circle cx="7" cy="17" r="2"/><circle cx="17" cy="17" r="2"/>
            </svg>
          }
        >
          <div className="ref-car-status-grid">
            <div className="ref-facts-col">
              <div><span>Fuel Load</span> <strong><FuelIcon size={12} style={{ marginRight: 3 }} /> 22.4 L <small>Est. 4.2 laps</small></strong></div>
              <div><span>ERS Mode</span> <strong className="text-blue"><ZapIcon size={12} style={{ marginRight: 3 }} /> Deploy</strong></div>
              <div><span>ERS Battery</span> <strong><ZapIcon size={12} style={{ marginRight: 3 }} /> 78%</strong></div>
              <div><span>Engine Mode</span> <strong><GearIcon size={12} style={{ marginRight: 3 }} /> Standard</strong></div>
              <div><span>Brake Bias</span> <strong><GearIcon size={12} style={{ marginRight: 3 }} /> 56.0%</strong></div>
            </div>
            <div className="ref-facts-col">
              <div><span>Tyre Compound</span> <strong><span className="badge-tyre-m">M</span> Medium</strong></div>
              <div className="tyre-wear-block">
                <span>Tyre Wear</span>
                <div className="tyre-quad-box">
                  <div><span>FL</span> <strong>62%</strong></div>
                  <div><span>FR</span> <strong>65%</strong></div>
                  <div><span>RL</span> <strong>58%</strong></div>
                  <div><span>RR</span> <strong>61%</strong></div>
                </div>
              </div>
              <div className="tyre-temp-block">
                <span>Tyre Temp (°C)</span>
                <div className="tyre-temp-row">
                  <span><ThermometerIcon size={12} style={{ marginRight: 2 }} /> FL 96</span> <span>FR 98</span> <span>RL 93</span> <span>RR 95</span>
                </div>
              </div>
            </div>
          </div>
        </Panel>

        {/* Panel 6: Track Map */}
        <Panel
          title="Track Map"
          icon={
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
              <path d="M7 18c-2.5 0-4-1.5-4-3.5 0-2.5 2-4.5 4.5-4.5h2c2.5 0 3.5-1.5 4.5-3 1.2-1.8 3-2 5-1 2.2 1.1 3 3.5 1.5 5.8l-2.5 4c-1.2 2-3.2 3.2-5.5 2.2H7z" />
            </svg>
          }
          action={<button className="ref-dropdown-btn">Monza <ChevronDownIcon size={11} /></button>}
        >
          <div className="ref-track-map-wrapper">
            <img src="/track-live-monza.png" alt="Monza Track Map" className="ref-monza-img" />
          </div>
        </Panel>
      </div>

      {/* Bottom Row */}
      <div className="ref-grid live-bottom">
        {/* Panel 7: Recent Laps */}
        <Panel title="Recent Laps" icon={<TableIcon size={14} />} action={<ActionLink screen="sessions" preservedQuery={preservedQuery}>View All Laps →</ActionLink>}>
          <div className="ref-table-wrap is-compact">
            <table className="ref-table">
              <thead>
                <tr><th>Lap</th><th>Lap Time</th><th>S1</th><th>S2</th><th>S3</th><th>Delta</th><th>Tyre</th><th>Valid</th></tr>
              </thead>
              <tbody>
                <tr className="is-selected">
                  <td><strong>12</strong></td><td><strong>1:20.412</strong></td><td>27.321</td><td>28.547</td><td>24.544</td>
                  <td><span className="badge-delta-good">-0.317</span></td><td><span className="badge-tyre-m">M</span></td><td><span className="tick-green"><CheckIcon size={11} /></span></td>
                </tr>
                <tr><td>11</td><td>1:20.941</td><td>27.682</td><td>28.691</td><td>24.568</td><td><span className="badge-delta-bad">+0.212</span></td><td><span className="badge-tyre-m">M</span></td><td><span className="tick-green"><CheckIcon size={11} /></span></td></tr>
                <tr><td>10</td><td>1:21.003</td><td>27.804</td><td>28.920</td><td>24.279</td><td><span className="badge-delta-bad">+0.274</span></td><td><span className="badge-tyre-m">M</span></td><td><span className="tick-green"><CheckIcon size={11} /></span></td></tr>
                <tr><td>9</td><td>1:22.137</td><td>28.341</td><td>29.011</td><td>24.785</td><td><span className="badge-delta-bad">+1.408</span></td><td><span className="badge-tyre-m">M</span></td><td><span className="tick-green"><CheckIcon size={11} /></span></td></tr>
                <tr><td>8</td><td>1:21.998</td><td>28.005</td><td>29.103</td><td>24.890</td><td><span className="badge-delta-bad">+1.269</span></td><td><span className="badge-tyre-m">M</span></td><td><span className="tick-green"><CheckIcon size={11} /></span></td></tr>
                <tr><td>7</td><td>1:22.491</td><td>28.444</td><td>29.337</td><td>24.710</td><td><span className="badge-delta-bad">+1.762</span></td><td><span className="badge-tyre-s">S</span></td><td><span className="tick-green"><CheckIcon size={11} /></span></td></tr>
              </tbody>
            </table>
          </div>
        </Panel>

        {/* Panel 8: Live Telemetry Graphs */}
        <Panel
          title="Live Telemetry Graphs"
          icon={<WaveIcon size={14} />}
          action={
            <div className="ref-chart-header-controls">
              <div className="ref-tab-pills small">
                <button className="tab-pill active">Distance</button>
                <button className="tab-pill">Time</button>
              </div>
              <span className="ref-legend-item"><span className="dot-blue" /> Speed</span>
              <span className="ref-legend-item"><span className="dot-green" /> Throttle</span>
              <span className="ref-legend-item"><span className="dot-red" /> Brake</span>
              <span className="ref-legend-item"><span className="dot-orange" /> Gear</span>
              <button className="ref-dropdown-btn">Current Lap (12) <ChevronDownIcon size={11} /></button>
              <button className="ref-btn-icon"><GearIcon size={12} /></button>
            </div>
          }
        >
          <div className="ref-chart-img-wrapper">
            <img src="/live-graph-full.png" alt="Live Telemetry Graphs" className="ref-live-graph-img" />
          </div>
        </Panel>
      </div>
    </>
  );
}

/* =========================================================================
   3. SESSIONS (Mock Image 8)
   ========================================================================= */
function Sessions({ preservedQuery }: { preservedQuery: string }) {
  return (
    <>
      <div className="sessions-toolbar">
        <PageHeading
          title="Sessions"
          subtitle="Manage imported sessions and processing runs"
          icon={
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#0260e8" strokeWidth="2">
              <ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/><path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/>
            </svg>
          }
        />
        <div className="ref-session-filters">
          <label><span>Track</span><button type="button">All Tracks <ChevronDownIcon size={11} /></button></label>
          <label><span>Session Type</span><button type="button">All Types <ChevronDownIcon size={11} /></button></label>
          <label><span>Date Range</span><button type="button"><CalendarIcon size={13} style={{ marginRight: 4 }} /> 01 Aug 2026 - 30 Sep 2026</button></label>
          <div className="ref-search-box">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="#94a3b8" strokeWidth="2" className="ref-search-icon">
              <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
            </svg>
            <input placeholder="Search sessions..." readOnly />
            <button className="ref-search-clear" type="button"><CrossIcon size={11} /></button>
          </div>
          <ActionLink screen="recordings" preservedQuery={preservedQuery} primary>
            <UploadIcon size={13} style={{ marginRight: 4 }} /> Import Session
          </ActionLink>
        </div>
      </div>

      <div className="ref-grid sessions-main">
        {/* Left Column Stack */}
        <div className="ref-stack">
          <Panel title="Imported Sessions" icon={<TableIcon size={14} />} action={<ActionLink screen="sessions" preservedQuery={preservedQuery}>View all sessions →</ActionLink>}>
            <div className="ref-table-wrap is-compact">
              <table className="ref-table">
                <thead>
                  <tr><th>#</th><th>Track</th><th>Session</th><th>Date <ChevronDownIcon size={10} /></th><th>Mode</th><th>Best Lap</th><th>Laps</th><th>Status</th></tr>
                </thead>
                <tbody>
                  <tr className="is-selected">
                    <td>1</td><td><strong>Monza</strong></td><td>Practice 1</td><td><strong>08 Sep 2026</strong></td><td>Practice</td><td><strong>1:20.412</strong></td><td>58</td><td><Badge tone="good"><span className="dot-green"/> Processed</Badge></td>
                  </tr>
                  <tr><td>2</td><td>Zandvoort</td><td>Race</td><td>01 Sep 2026</td><td>Race</td><td>1:12.904</td><td>72</td><td><Badge tone="good"><span className="dot-green"/> Processed</Badge></td></tr>
                  <tr><td>3</td><td>Spa</td><td>Qualifying</td><td>24 Aug 2026</td><td>Qualifying</td><td>1:43.227</td><td>12</td><td><Badge tone="good"><span className="dot-green"/> Processed</Badge></td></tr>
                  <tr><td>4</td><td>Silverstone</td><td>Practice 2</td><td>17 Aug 2026</td><td>Practice</td><td>1:27.315</td><td>46</td><td><Badge tone="good"><span className="dot-green"/> Processed</Badge></td></tr>
                  <tr><td>5</td><td>Imola</td><td>Race</td><td>10 Aug 2026</td><td>Race</td><td>1:19.882</td><td>63</td><td><Badge tone="good"><span className="dot-green"/> Processed</Badge></td></tr>
                  <tr><td>6</td><td>Barcelona</td><td>Practice 1</td><td>28 Jul 2026</td><td>Practice</td><td>1:15.703</td><td>54</td><td><Badge tone="blue"><span className="dot-blue-spin"/> Processing</Badge></td></tr>
                  <tr><td>7</td><td>Red Bull Ring</td><td>Qualifying</td><td>21 Jul 2026</td><td>Qualifying</td><td>1:04.991</td><td>14</td><td><Badge tone="good"><span className="dot-green"/> Processed</Badge></td></tr>
                  <tr><td>8</td><td>Hungaroring</td><td>Practice 1</td><td>14 Jul 2026</td><td>Practice</td><td>1:16.441</td><td>48</td><td><Badge tone="warning"><span className="dot-orange"/> Imported</Badge></td></tr>
                </tbody>
              </table>
            </div>
          </Panel>

          <Panel title="Processing Runs" icon={<GearIcon size={14} />} action={<button className="ref-dropdown-btn">All Status <ChevronDownIcon size={11} /></button>}>
            <div className="ref-table-wrap is-compact">
              <table className="ref-table">
                <thead>
                  <tr><th>Run ID</th><th>Session</th><th>Started</th><th>Finished</th><th>Capture Source</th><th>Status</th></tr>
                </thead>
                <tbody>
                  <tr className="is-selected">
                    <td><span className="dot-circle-blue" /> RUN-20260908-1423</td><td>Monza - P1</td><td>08 Sep 14:02</td><td>08 Sep 14:12</td><td>UDP (Live)</td><td><Badge tone="good"><span className="dot-green"/> Completed</Badge></td>
                  </tr>
                  <tr><td><span className="dot-circle-gray" /> RUN-20260908-1011</td><td>Monza - P1</td><td>08 Sep 10:58</td><td>08 Sep 11:04</td><td>File Import</td><td><Badge tone="good"><span className="dot-green"/> Completed</Badge></td></tr>
                  <tr><td><span className="dot-circle-gray" /> RUN-20260901-1530</td><td>Zandvoort - R</td><td>01 Sep 15:30</td><td>01 Sep 15:46</td><td>File Import</td><td><Badge tone="good"><span className="dot-green"/> Completed</Badge></td></tr>
                  <tr><td><span className="dot-circle-gray" /> RUN-20260824-0932</td><td>Spa - Q</td><td>24 Aug 09:32</td><td>24 Aug 09:41</td><td>UDP (Live)</td><td><Badge tone="good"><span className="dot-green"/> Completed</Badge></td></tr>
                  <tr><td><span className="dot-circle-gray" /> RUN-20260817-1120</td><td>Silverstone - P2</td><td>17 Aug 11:20</td><td>17 Aug 11:33</td><td>File Import</td><td><Badge tone="good"><span className="dot-green"/> Completed</Badge></td></tr>
                  <tr><td><span className="dot-circle-gray" /> RUN-20260810-1405</td><td>Imola - R</td><td>10 Aug 14:05</td><td>10 Aug 14:28</td><td>File Import</td><td><Badge tone="good"><span className="dot-green"/> Completed</Badge></td></tr>
                  <tr><td><span className="dot-circle-gray" /> RUN-20260728-0901</td><td>Barcelona - P1</td><td>28 Jul 09:01</td><td>28 Jul 09:08</td><td>UDP (Live)</td><td><Badge tone="blue"><span className="dot-blue-spin"/> Processing</Badge></td></tr>
                  <tr><td><span className="dot-circle-gray" /> RUN-20260721-1035</td><td>Red Bull Ring - Q</td><td>21 Jul 10:35</td><td>21 Jul 10:46</td><td>File Import</td><td><Badge tone="good"><span className="dot-green"/> Completed</Badge></td></tr>
                </tbody>
              </table>
            </div>
          </Panel>
        </div>

        {/* Middle Column Stack */}
        <div className="ref-stack">
          <Panel
            title="Run Details"
            icon={<ChatBubbleIcon size={14} />}
            action={
              <div className="ref-run-details-actions">
                <Badge tone="good"><span className="dot-green" /> Completed</Badge>
                <span className="lap-nav-arrows">‹ 1 of 12 ›</span>
              </div>
            }
          >
            <div className="ref-run-id-header">
              <div>
                <h2>RUN-20260908-1423</h2>
                <div className="ref-run-sub"><FlagIT /> Monza - Practice 1</div>
              </div>
              <div className="ref-run-header-btns">
                <button className="ref-btn-subtle">View Raw Data</button>
                <button className="ref-btn-icon">•••</button>
              </div>
            </div>
            <div className="ref-run-facts-grid">
              <div><span>Track</span> <strong>Monza</strong></div>
              <div><span>Best Lap</span> <strong>1:20.412</strong></div>
              <div><span>Session Type</span> <strong>Practice 1</strong></div>
              <div><span>Total Laps</span> <strong>58</strong></div>
              <div><span>Date</span> <strong>08 Sep 2026 14:02 - 14:12</strong></div>
              <div><span>Capture Source</span> <strong>UDP (Live)</strong></div>
              <div><span>Mode</span> <strong>Practice (Local)</strong></div>
              <div><span>Data Size</span> <strong>1.8 GB</strong></div>
            </div>

            {/* Lifecycle Stages */}
            <div className="ref-lifecycle-card">
              <span className="ref-lifecycle-title"><GearIcon size={13} style={{ marginRight: 4 }} /> Processing Lifecycle</span>
              <div className="ref-lifecycle-stepper">
                {[
                  ["Imported", "14:02:17"],
                  ["Parsing", "14:03:05"],
                  ["Processing", "14:08:21"],
                  ["Generating Attempts", "14:10:33"],
                  ["Completed", "14:12:11"],
                ].map(([step, time]) => (
                  <div className="stepper-step" key={step}>
                    <span className="stepper-dot"><CheckIcon size={10} /></span>
                    <strong>{step}</strong>
                    <small>{time}</small>
                  </div>
                ))}
              </div>
            </div>

            <div className="ref-metric-grid three">
              <Metric label="Attempts Generated" value="58" detail="Valid: 52  Rejected: 6" icon={<FileIcon size={13} />} />
              <Metric label="Processing Time" value="9m 54s" icon={<StopwatchIcon size={13} />} />
              <Metric label="Evidence Files" value="12" detail="~420 MB" icon={<DatabaseIcon size={13} />} />
            </div>
          </Panel>

          <Panel title="Attempts (Laps)" icon={<TableIcon size={14} />} action={<><Badge>58 attempts</Badge><button className="ref-dropdown-btn">All Laps <ChevronDownIcon size={11} /></button></>}>
            <div className="ref-table-wrap is-compact">
              <table className="ref-table">
                <thead>
                  <tr><th>Lap</th><th>Lap Time</th><th>Sector 1</th><th>Sector 2</th><th>Sector 3</th><th>Disposition</th><th>Valid</th><th>Notes</th></tr>
                </thead>
                <tbody>
                  <tr className="is-selected">
                    <td><strong>8</strong></td><td><strong>1:20.412</strong></td><td>27.321</td><td>28.547</td><td>24.544</td>
                    <td><Badge tone="blue">Reference</Badge></td><td><span className="tick-green"><CheckIcon size={11} /></span></td><td><strong>Best lap</strong></td>
                  </tr>
                  <tr><td>9</td><td>1:20.941</td><td>27.682</td><td>28.691</td><td>24.568</td><td><Badge tone="neutral">Push</Badge></td><td><span className="tick-green"><CheckIcon size={11} /></span></td><td>Clean lap</td></tr>
                  <tr><td>10</td><td>1:21.003</td><td>27.804</td><td>28.920</td><td>24.279</td><td><Badge tone="neutral">Push</Badge></td><td><span className="tick-green"><CheckIcon size={11} /></span></td><td>Clean lap</td></tr>
                  <tr><td>11</td><td>1:22.137</td><td>28.341</td><td>29.011</td><td>24.785</td><td><Badge tone="neutral">Push</Badge></td><td><span className="tick-green"><CheckIcon size={11} /></span></td><td>Clean lap</td></tr>
                  <tr><td>12</td><td>1:21.998</td><td>28.005</td><td>29.103</td><td>24.890</td><td><Badge tone="warning">Out Lap</Badge></td><td><span className="badge-warn"><WarningIcon size={11} /></span></td><td>Track limits</td></tr>
                  <tr><td>13</td><td>1:22.491</td><td>28.444</td><td>29.337</td><td>24.710</td><td><Badge tone="bad">Invalid</Badge></td><td><span className="badge-cross"><CrossIcon size={11} /></span></td><td>Cut track</td></tr>
                  <tr><td>14</td><td>1:23.004</td><td>28.889</td><td>29.512</td><td>24.603</td><td><Badge tone="neutral">Push</Badge></td><td><span className="tick-green"><CheckIcon size={11} /></span></td><td>Clean lap</td></tr>
                  <tr><td>15</td><td>1:25.632</td><td>29.440</td><td>30.221</td><td>25.971</td><td><Badge tone="neutral">In Lap</Badge></td><td><span className="tick-green"><CheckIcon size={11} /></span></td><td>Cooldown</td></tr>
                </tbody>
              </table>
            </div>
          </Panel>
        </div>

        {/* Right Column Stack */}
        <div className="ref-stack">
          <Panel title="Session Summary" icon={<FileIcon size={14} />}>
            <div className="ref-session-card">
              <div className="ref-session-info">
                <div className="ref-track-title"><FlagIT /> Monza</div>
                <div className="ref-gp-name">Practice 1</div>
                <div className="ref-session-date">08 Sep 2026 14:02 - 14:12</div>
              </div>
              <img src="/monza-thumb.png" alt="Monza" className="ref-session-thumb" />
            </div>
            <div className="ref-facts-box compact">
              <div><span>Best Lap</span> <strong>1:20.412</strong></div>
              <div><span>Total Laps</span> <strong>58</strong></div>
              <div><span>Track Length</span> <strong>5.793 km</strong></div>
              <div><span>Weather</span> <strong><SunIcon size={12} style={{ marginRight: 3 }} /> Dry, 27°C</strong></div>
              <div><span>Air Temp</span> <strong>27°C</strong></div>
              <div><span>Track Temp</span> <strong><SunIcon size={12} style={{ marginRight: 3 }} /> 32°C</strong></div>
            </div>
            <button className="ref-button is-primary full-width"><FolderIcon size={13} style={{ marginRight: 5 }} /> Open Session</button>
            <div className="ref-session-actions-row">
              <button className="ref-btn-subtle"><BarChartIcon size={12} style={{ marginRight: 4 }} /> View Attempts</button>
              <button className="ref-btn-subtle"><LineChartIcon size={12} style={{ marginRight: 4 }} /> Compare Lap</button>
            </div>
          </Panel>

          <Panel title="Observation Inventory" icon={<FileIcon size={14} />} action={<Badge>View All →</Badge>}>
            <div className="ref-inventory-list">
              {[
                ["Lap Charts", "12 files"],
                ["Telemetry Traces", "58 files"],
                ["Track Map Data", "2 files"],
                ["Tyre & Strategy", "6 files"],
                ["Event Logs", "1 file"],
                ["AI Analysis Notes", "3 files"],
              ].map(([name, count]) => (
                <div key={name}>
                  <span><FileIcon size={12} style={{ marginRight: 4 }} /> {name}</span>
                  <b>{count}</b>
                  <i className="tick-green"><CheckIcon size={11} /></i>
                </div>
              ))}
            </div>
          </Panel>

          <Panel title="Archive Status" icon={<DatabaseIcon size={14} />}>
            <div className="ref-archive-status-box">
              <div><span>Session Folder</span> <strong>sessions/2026/09/08/monza_p1 <FolderIcon size={12} style={{ marginLeft: 3 }} /></strong></div>
              <div><span>Size on Disk</span> <strong>1.8 GB</strong></div>
              <div><span>Compressed Archive</span> <strong>monza_p1_20260908.zip (420 MB) <DownloadIcon size={12} style={{ marginLeft: 3 }} /></strong></div>
              <div><span>Archive Status</span> <strong><span className="dot-green"/> Archived</strong></div>
              <div><span>Last Updated</span> <strong>08 Sep 2026 14:12:11</strong></div>
            </div>
          </Panel>
        </div>
      </div>
    </>
  );
}

/* =========================================================================
   4. LAP COMPARISON (Mock Image 4)
   ========================================================================= */
function Compare({ preservedQuery }: { preservedQuery: string }) {
  return (
    <>
      <PageHeading
        title="Lap Comparison"
        subtitle="Compare laps and understand where time is gained or lost"
        icon={
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#0260e8" strokeWidth="2">
            <rect x="3" y="11" width="4" height="10" rx="1.5"/><rect x="10" y="7" width="4" height="14" rx="1.5"/><rect x="17" y="3" width="4" height="18" rx="1.5"/>
          </svg>
        }
      />
      <div className="ref-compare-controls ref-panel">
        <label>
          <span>Target Lap</span>
          <button type="button"><span className="dot-blue"/> Lap 12 - 1:20.412 ‹ ›</button>
        </label>
        <label>
          <span>Reference Lap</span>
          <button type="button"><span className="dot-red"/> Session Best - 1:20.095 ‹ ›</button>
        </label>
        <label>
          <span>Comparison Policy</span>
          <button type="button">Delta to Reference <ChevronDownIcon size={11} /></button>
        </label>
        <label>
          <span>Distance Window (Optional)</span>
          <div className="ref-dist-window-inputs">
            <small>Start</small> <strong>0.0 km</strong>
            <small>End</small> <strong>4.3 km</strong>
          </div>
        </label>
        <label>
          <span>Presets</span>
          <button type="button" className="is-primary-choice">Full Lap <ChevronDownIcon size={11} /></button>
        </label>
      </div>

      <div className="ref-grid compare-summary">
        <Metric label="Target Lap (Lap 12)" value="1:20.412" detail="+0.317 vs reference" icon={<span className="dot-blue"/>} />
        <Metric label="Reference Lap (Session Best)" value="1:20.095" detail="Lap 8 · Practice 1" icon={<span className="dot-red"/>} />
        <Metric label="Delta to Reference" value="+0.317" detail="Slower" tone="bad" />
        <Metric label="Top Speed" value="339 km/h" detail="Reference: 342 km/h" icon={<StopwatchIcon size={13} />} />
        <Metric label="Sector 1" value="+0.123" detail="Slower" tone="bad" />
        <Metric label="Sector 2" value="-0.041" detail="Faster" tone="good" />
        <Metric label="Sector 3" value="+0.153" detail="Slower" tone="bad" />
      </div>

      <div className="ref-grid compare-middle">
        {/* Telemetry Comparison Chart */}
        <Panel
          title="Telemetry Comparison"
          icon={<BarChartIcon size={14} />}
          action={
            <div className="ref-chart-header-controls">
              <span className="ref-legend-item"><span className="dot-blue" /> Target Lap (1:20.412)</span>
              <span className="ref-legend-item"><span className="dot-red" /> Reference Lap (1:20.095)</span>
              <span className="badge-sec-light">S1</span>
              <span className="badge-sec-light">S2</span>
              <span className="badge-sec-light">S3</span>
              <div className="ref-tab-pills small">
                <button className="tab-pill active">Speed</button>
                <button className="tab-pill">Inputs</button>
                <button className="tab-pill">Steering</button>
                <button className="tab-pill">Gear</button>
              </div>
            </div>
          }
        >
          <div className="ref-chart-img-wrapper">
            <img src="/comp-telemetry-full.png" alt="Telemetry Comparison" className="ref-telemetry-comp-img" />
          </div>
        </Panel>

        {/* Right Stack */}
        <div className="ref-stack">
          <Panel title="Track Map – Delta to Reference" icon={<CircuitIcon size={14} />} action={<button className="ref-dropdown-btn">Monza <ChevronDownIcon size={11} /></button>}>
            <div className="ref-track-map-wrapper">
              <img src="/track-compare-monza.png" alt="Track Map Delta" className="ref-monza-img" />
            </div>
          </Panel>

          <Panel title="Reference Selection" icon={<TargetIcon size={13} />}>
            <div className="ref-reference-card-rich">
              <div className="ref-ref-head">
                <span className="dot-red" />
                <div>
                  <strong>Session Best (Lap 8)</strong>
                  <div className="ref-ref-time">1:20.095</div>
                  <small>Practice 1 • 08 Sep 2026 14:02</small>
                </div>
                <img src="/monza-thumb.png" alt="Monza" className="ref-session-thumb small" />
                <button className="ref-btn-subtle">Change</button>
              </div>
              <div className="ref-ref-banner">
                <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><StarIcon size={13} /> This is the fastest lap of the session and is used as the reference for comparison.</span>
              </div>
            </div>
          </Panel>
        </div>
      </div>

      <div className="ref-grid compare-bottom">
        <Panel title="Sector Comparison" icon={<TableIcon size={14} />}>
          <div className="ref-table-wrap is-compact">
            <table className="ref-table">
              <thead>
                <tr><th>Sector</th><th>Target Lap</th><th>Reference Lap</th><th>Delta</th><th>Delta %</th><th>Status</th></tr>
              </thead>
              <tbody>
                <tr><td>Sector 1</td><td>27.321</td><td>27.198</td><td><span className="badge-delta-bad">+0.123</span></td><td>+0.45%</td><td><strong className="text-red">Slower</strong></td></tr>
                <tr><td>Sector 2</td><td>28.547</td><td>28.588</td><td><span className="badge-delta-good">-0.041</span></td><td>-0.14%</td><td><strong className="text-green">Faster</strong></td></tr>
                <tr><td>Sector 3</td><td>24.544</td><td>24.391</td><td><span className="badge-delta-bad">+0.153</span></td><td>+0.63%</td><td><strong className="text-red">Slower</strong></td></tr>
                <tr className="is-selected"><td><strong>Full Lap</strong></td><td><strong>1:20.412</strong></td><td><strong>1:20.095</strong></td><td><strong className="text-red">+0.317</strong></td><td>+0.40%</td><td><strong className="text-red">Slower</strong></td></tr>
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="Key Findings & Evidence" icon={<LightbulbIcon size={14} />}>
          <div className="ref-findings-list">
            <div className="finding-row">
              <span className="badge-arrow-red"><ArrowUpIcon size={11} /></span>
              <div className="finding-text">
                <strong className="text-red">Time lost in Sector 1 (+0.123s)</strong>
                <p>Lower minimum speed at Turn 4 and later throttle application compared to the reference lap.</p>
              </div>
              <img src="/sparkline-s1.png" alt="S1 chart" className="finding-sparkline" />
              <span className="finding-arrow">›</span>
            </div>
            <div className="finding-row">
              <span className="badge-arrow-green"><ArrowDownIcon size={11} /></span>
              <div className="finding-text">
                <strong className="text-green">Time gained in Sector 2 (-0.041s)</strong>
                <p>Slightly better exit at Turn 7 with earlier throttle application.</p>
              </div>
              <img src="/sparkline-s2.png" alt="S2 chart" className="finding-sparkline" />
              <span className="finding-arrow">›</span>
            </div>
            <div className="finding-row">
              <span className="badge-arrow-red"><ArrowUpIcon size={11} /></span>
              <div className="finding-text">
                <strong className="text-red">Time lost in Sector 3 (+0.153s)</strong>
                <p>Lower top speed on the main straight and later braking into Turn 11.</p>
              </div>
              <img src="/sparkline-s3.png" alt="S3 chart" className="finding-sparkline" />
              <span className="finding-arrow">›</span>
            </div>
          </div>
        </Panel>
      </div>
    </>
  );
}

/* =========================================================================
   5. TRACK ANALYSIS (Mock Image 7)
   ========================================================================= */
function Track({ preservedQuery: _preservedQuery }: { preservedQuery: string }) {
  return (
    <>
      <div className="ref-track-config ref-panel">
        <div className="ref-track-config-title">
          <span className="ref-panel-icon">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
              <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>
            </svg>
          </span>
          <b>Track & Analysis Configuration</b>
        </div>
        <div className="ref-track-card-top">
          <img src="/monza-thumb.png" alt="Monza" className="ref-session-thumb" />
          <div className="ref-track-meta">
            <div className="ref-track-title"><FlagIT /> Monza</div>
            <div className="ref-gp-name">Italian Grand Prix</div>
            <small>Monza, Italy</small>
            <div className="ref-track-badges">
              <span>5.793 km</span> <span>11 Turns</span>
            </div>
          </div>
          <button className="ref-btn-subtle">Change Track</button>
        </div>

        <label className="ref-field">
          <span>Track Model</span>
          <div className="ref-field-input-row">
            <button type="button">Current Session (AI Model) <ChevronDownIcon size={11} /></button>
            <button type="button" className="ref-btn-icon" title="Configure Model"><GearIcon size={12} /></button>
          </div>
          <small>Uses your session data to build track performance model</small>
        </label>
        <label className="ref-field">
          <span>Region Set</span>
          <div className="ref-field-input-row">
            <button type="button">Corners & Key Zones <ChevronDownIcon size={11} /></button>
            <button type="button" className="ref-btn-icon" title="Edit Regions"><MapPinIcon size={12} /></button>
          </div>
          <small>Standard corner-based regions with DRS zones</small>
        </label>
        <button className="ref-button is-primary"><RefreshIcon size={13} style={{ marginRight: 5 }} /> Analyze Another Lap</button>
      </div>

      <div className="ref-grid track-main">
        {/* Track Map */}
        <Panel title="Track Map & Region Analysis" icon={<CircuitIcon size={14} />}>
          <div className="ref-track-map-wrapper">
            <img src="/track-analysis-map.png" alt="Monza Track Map" className="ref-analysis-map-img" />
          </div>
        </Panel>

        {/* Right Stack */}
        <div className="ref-stack">
          <Panel
            title="Region Metrics – Turn 4 (Lesmo 1)"
            icon={<LightbulbIcon size={14} />}
            action={<span className="lap-nav-arrows">‹ Region 4 of 11 ›</span>}
          >
            <div className="ref-metric-grid five">
              <Metric label="Minimum Speed" value="168" unit=" km/h" detail="-12 km/h · Ref: 180 km/h" tone="bad" icon={<StopwatchIcon size={13} />} />
              <Metric label="Apex Speed" value="212" unit=" km/h" detail="-8 km/h · Ref: 220 km/h" tone="bad" icon={<StopwatchIcon size={13} />} />
              <Metric label="Entry Speed" value="294" unit=" km/h" detail="-6 km/h · Ref: 300 km/h" tone="bad" icon={<ArrowUpRightIcon size={13} />} />
              <Metric label="Throttle On Dist." value="310" unit=" m" detail="+24 m · Ref: 286 m" tone="good" icon={<ZapIcon size={13} />} />
              <Metric label="Time Delta" value="+0.245" unit=" s" detail="Slower vs Reference" tone="bad" icon={<StopwatchIcon size={13} />} />
            </div>
          </Panel>

          <div className="ref-grid track-insights">
            <Panel title="Key Insights – Turn 4 (Lesmo 1)" icon={<LightbulbIcon size={14} />}>
              <ul className="ref-insights-bullet-list">
                <li><span className="dot-red" /> Minimum speed is 12 km/h lower than reference, costing ~0.145s.</li>
                <li><span className="dot-red" /> Later throttle application by 24 m compared to reference.</li>
                <li><span className="dot-orange" /> Braking phase is 3 m shorter, leading to lower mid-corner speed.</li>
                <li><span className="dot-blue" /> Maintain a tighter line on entry to improve minimum speed.</li>
                <li><span className="dot-blue" /> Focus on earlier throttle application on corner exit to reduce time loss.</li>
              </ul>
            </Panel>

            <Panel title="Region List – Biggest Time Losses" icon={<TableIcon size={14} />} action={<button className="ref-dropdown-btn">Sort by: Time Delta <ChevronDownIcon size={11} /></button>}>
              <div className="ref-table-wrap is-compact">
                <table className="ref-table">
                  <thead>
                    <tr><th>#</th><th>Turn / Region</th><th>Time Delta</th><th>Status</th></tr>
                  </thead>
                  <tbody>
                    <tr><td>1</td><td>Turn 1 – Rettifilo</td><td className="text-red">+0.498s</td><td><span className="dot-red"/> Slower</td></tr>
                    <tr className="is-selected"><td>2</td><td><strong>Turn 4 – Lesmo 1</strong></td><td className="text-red"><strong>+0.245s</strong></td><td><span className="dot-red"/> Slower</td></tr>
                    <tr><td>3</td><td>Turn 6 – Lesmo 2</td><td className="text-red">+0.198s</td><td><span className="dot-red"/> Slower</td></tr>
                    <tr><td>4</td><td>Turn 7 – Ascari</td><td className="text-red">+0.161s</td><td><span className="dot-red"/> Slower</td></tr>
                    <tr><td>5</td><td>Turn 11 – Parabolica</td><td className="text-red">+0.142s</td><td><span className="dot-red"/> Slower</td></tr>
                    <tr><td>6</td><td>Turn 3 – Curva Grande</td><td className="text-orange">+0.062s</td><td><span className="dot-orange"/> Similar</td></tr>
                    <tr><td>7</td><td>Turn 8 – Variante Ascari Exit</td><td className="text-green">-0.036s</td><td><span className="dot-green"/> Faster</td></tr>
                    <tr><td>8</td><td>Turn 5 – Lesmo 2 Entry</td><td className="text-green">-0.041s</td><td><span className="dot-green"/> Faster</td></tr>
                  </tbody>
                </table>
              </div>
            </Panel>
          </div>
        </div>
      </div>

      {/* Bottom Row */}
      <div className="ref-grid track-bottom">
        <Panel
          title="Region Comparison – Turn 4 (Lesmo 1)"
          icon={<TableIcon size={14} />}
          action={
            <div className="ref-chart-header-controls">
              <span className="ref-legend-item"><span className="dot-red"/> Current Lap (1:20.412)</span>
              <span className="ref-legend-item"><span className="dot-blue"/> Reference Lap (1:20.095)</span>
              <div className="ref-tab-pills small">
                <button className="tab-pill active">Speed</button>
                <button className="tab-pill">Throttle</button>
                <button className="tab-pill">Brake</button>
                <button className="tab-pill">Gear</button>
              </div>
            </div>
          }
        >
          <div className="ref-chart-img-wrapper">
            <img src="/track-region-chart-full.png" alt="Region Comparison" className="ref-region-chart-img" />
          </div>
        </Panel>

        <Panel
          title="Observed vs Reference Trajectory"
          icon={<BarChartIcon size={14} />}
          action={
            <div className="ref-chart-header-controls">
              <span className="ref-legend-item"><span className="dot-red"/> Current Lap</span>
              <span className="ref-legend-item"><span className="dot-blue"/> Reference Lap</span>
              <span className="ref-legend-item"><span className="dot-gray"/> Track Limits</span>
            </div>
          }
        >
          <div className="ref-chart-img-wrapper">
            <img src="/track-trajectory-duo.png" alt="Trajectory" className="ref-trajectory-img" />
          </div>
        </Panel>
      </div>

      <div className="ref-grid track-observations">
        <Panel title="Driving Pattern Observations" icon={<CarIcon size={14} />}>
          <ul className="ref-insights-bullet-list">
            <li><span className="dot-red"/> Later turn-in compared to reference, resulting in a wider entry line.</li>
            <li><span className="dot-red"/> Lower minimum speed indicates reduced confidence or over-braking.</li>
            <li><span className="dot-orange"/> Steering input is more abrupt, which can unsettle the car mid-corner.</li>
            <li><span className="dot-blue"/> Aim for a smoother, earlier turn-in to maintain higher minimum speed.</li>
          </ul>
        </Panel>
        <Panel title="Throttle & Exit Pattern Observations" icon={<BarChartIcon size={14} />}>
          <ul className="ref-insights-bullet-list">
            <li><span className="dot-red"/> Throttle application is 24 m later than reference.</li>
            <li><span className="dot-red"/> Progressive throttle is good, but overall exit speed is lower by 8 km/h.</li>
            <li><span className="dot-orange"/> Consider earlier throttle application while maintaining a stable rear end.</li>
            <li><span className="dot-blue"/> A cleaner exit will improve speed down the following straight and overall lap time.</li>
          </ul>
        </Panel>
      </div>
    </>
  );
}

/* =========================================================================
   6. AI ENGINEER (Mock Image 3)
   ========================================================================= */
function Engineer({ preservedQuery: _preservedQuery }: { preservedQuery: string }) {
  return (
    <>
      <PageHeading
        title="AI Race Engineer"
        subtitle="Your local AI race engineer. Ask about pace, braking, traction, strategy or anything about your driving."
        icon={
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#0260e8" strokeWidth="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
        }
        status={<Badge tone="neutral"><span className="dot-green" /> Local AI (Offline) <small className="text-gray">· Powered by local model · All data stays private</small></Badge>}
      />

      <div className="ref-panel ref-intent-panel">
        <span className="ref-intent-label">Engineer Intent</span>
        <div className="ref-intents">
          <div className="is-selected">
            <span className="intent-icon-sq">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#0260e8" strokeWidth="2">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
              </svg>
            </span>
            <div className="intent-text">
              <b>General Debrief</b>
              <span>Get analysis and explanations</span>
            </div>
          </div>
          <div>
            <span className="intent-icon-sq">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#64748b" strokeWidth="2">
                <rect x="3" y="11" width="4" height="10" rx="1"/><rect x="10" y="7" width="4" height="14" rx="1"/><rect x="17" y="3" width="4" height="18" rx="1"/>
              </svg>
            </span>
            <div className="intent-text">
              <b>Region Comparison</b>
              <span>Compare laps or sectors</span>
            </div>
          </div>
          <div>
            <span className="intent-icon-sq">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#64748b" strokeWidth="2">
                <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/>
              </svg>
            </span>
            <div className="intent-text">
              <b>Strategy Advice</b>
              <span>Race strategy and timing</span>
            </div>
          </div>
          <div>
            <span className="intent-icon-sq">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#64748b" strokeWidth="2">
                <circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1"/>
              </svg>
            </span>
            <div className="intent-text">
              <b>Tyre Analysis</b>
              <span>Tyre wear and performance</span>
            </div>
          </div>
        </div>
      </div>

      <div className="ref-grid engineer-main">
        {/* Chat Stream Card */}
        <section className="ref-panel ref-chat-panel">
          <div className="ref-chat-stream">
            {/* User message */}
            <div className="chat-bubble user-bubble">
              <div className="bubble-body">Why am I losing time in Sector 2 compared to my best lap?</div>
              <div className="bubble-meta">
                <span>14:03</span>
                <span className="avatar-user">U</span>
              </div>
            </div>

            {/* AI message */}
            <div className="chat-bubble ai-bubble">
              <div className="avatar-bot">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#fff" strokeWidth="2">
                  <rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><line x1="8" y1="16" x2="8.01" y2="16"/><line x1="16" y1="16" x2="16.01" y2="16"/>
                </svg>
              </div>
              <div className="bubble-content">
                <p>
                  You're losing <strong>0.317s</strong> in Sector 2 compared to your best lap, mainly due to a slower Turn 4 and lower minimum speed through Turns 4–5. Here's the breakdown:
                </p>
                <div className="ai-step-card">
                  <span className="step-num">1</span>
                  <div>
                    <strong>Turn 4 – Brake earlier and lower minimum speed</strong>
                    <p>You're braking ~6 meters earlier and carrying 8 km/h less minimum speed (112 km/h vs 120 km/h). This costs <strong className="text-red">~0.245s</strong> at Turn 4.</p>
                  </div>
                </div>
                <div className="ai-step-card">
                  <span className="step-num">2</span>
                  <div>
                    <strong>Turn 5 – Reduced mid-corner speed</strong>
                    <p>You're carrying 6 km/h less through Turn 5, costing <strong className="text-red">~0.072s</strong>.</p>
                  </div>
                </div>
                <div className="ai-step-card">
                  <span className="step-num">3</span>
                  <div>
                    <strong>Traction on exit</strong>
                    <p>Slightly higher wheelspin on exit of Turn 5, losing <strong className="text-red">~0.030s</strong> onto the straight.</p>
                  </div>
                </div>

                <div className="ai-key-takeaway">
                  <span className="takeaway-icon"><LightbulbIcon size={14} /></span>
                  <div>
                    <strong>Key takeaway</strong>
                    <p>Focus on a later brake and smoother trail braking into Turn 4. Carry more speed through the mid-corner (Turn 5) and be cleaner on throttle application at the exit.</p>
                  </div>
                </div>

                <div className="bubble-actions-row">
                  <span className="time-stamp">14:03</span>
                  <div className="action-icons">
                    <button aria-label="Helpful"><ThumbUpIcon size={13} /></button>
                    <button aria-label="Not helpful"><ThumbDownIcon size={13} /></button>
                    <button aria-label="Copy message"><CopyClipboardIcon size={13} /></button>
                    <button>•••</button>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="ref-suggest-section">
            <span className="ref-suggest-label">Try asking about:</span>
            <div className="ref-chip-row">
              <button className="ref-chip-btn"><TargetIcon size={12} style={{ marginRight: 4 }} /> Tyre wear analysis</button>
              <button className="ref-chip-btn"><TargetIcon size={12} style={{ marginRight: 4 }} /> Braking points</button>
              <button className="ref-chip-btn"><FlagOutlineIcon size={12} style={{ marginRight: 4 }} /> Strategy options</button>
              <button className="ref-chip-btn"><BarChartIcon size={12} style={{ marginRight: 4 }} /> Compare to best lap</button>
              <button className="ref-chip-btn"><LightbulbIcon size={12} style={{ marginRight: 4 }} /> Focus areas</button>
            </div>
          </div>

          <div className="ref-composer">
            <input placeholder="Ask your engineer..." readOnly />
            <button type="button" aria-label="Send">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
                <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
              </svg>
            </button>
          </div>
        </section>

        {/* Right Stack */}
        <div className="ref-stack">
          {/* Context & Data Source */}
          <Panel title="Context & Data Source" icon={<DatabaseIcon size={14} />} action={<button className="ref-btn-subtle">Change Context</button>}>
            <div className="ref-context-card">
              <img src="/monza-thumb.png" alt="Monza" className="ref-session-thumb" />
              <div className="ref-context-info">
                <div className="ref-track-title"><FlagIT /> Monza</div>
                <div className="ref-gp-name">Italian Grand Prix</div>
                <div className="ref-session-type">Practice 1</div>
                <div className="ref-session-date">08 Sep 2026 14:02</div>
              </div>
              <div className="ref-context-selectors">
                <div><span>Session</span> <button>Practice 1 <ChevronDownIcon size={11} /></button></div>
                <div><span>Lap</span> <button>‹ 12 / 18 ›</button></div>
                <div><span>Reference</span> <button>Best Lap (1:20.095) <ChevronDownIcon size={11} /></button></div>
                <div><span>Focus</span> <button>Sector 2 <ChevronDownIcon size={11} /></button></div>
              </div>
            </div>
          </Panel>

          {/* Voice Response */}
          <Panel
            title="Voice Response (Local)"
            icon={<WaveIcon size={14} />}
            action={<div className="ref-voice-badges"><Badge tone="neutral">Piper (local)</Badge><Badge tone="good"><span className="dot-green"/> Ready</Badge></div>}
          >
            <div className="ref-voice-controls-rich">
              <button className="play-circle-btn"><PlayIcon size={11} /></button>
              <button className="stop-square-btn"><StopIcon size={10} /></button>
              <div className="waveform-bar-area">
                <img src="/ai-voice-waveform.png" alt="Waveform" className="ref-waveform-img" />
              </div>
              <span className="waveform-time">0:00 / 0:18</span>
              <button className="ref-dropdown-btn">1.0x <ChevronDownIcon size={11} /></button>
            </div>
          </Panel>

          {/* Key Evidence */}
          <Panel title="Key Evidence" icon={<BarChartIcon size={14} />}>
            <div className="ref-metric-grid three">
              <Metric label="Sector 1" value="27.321" detail="-0.123 vs Best" tone="good" />
              <Metric label="Sector 2" value="28.547" detail="+0.317 vs Best" tone="bad" />
              <Metric label="Sector 3" value="24.544" detail="-0.153 vs Best" tone="good" />
            </div>
            <div className="ref-grid engineer-evidence-lower">
              <div className="evidence-sub-card">
                <div className="evidence-sub-header">
                  <span>Track View – Sector 2</span>
                  <button className="ref-dropdown-btn">Sector 2 <ChevronDownIcon size={11} /></button>
                </div>
                <img src="/ai-s2-track-clean.png" alt="Sector 2 Track" className="ref-s2-img" />
              </div>
              <div className="evidence-sub-card">
                <div className="evidence-sub-header">
                  <span>Speed Comparison – Sector 2</span>
                  <button className="ref-dropdown-btn">Turn 4-5 <ChevronDownIcon size={11} /></button>
                </div>
                <img src="/ai-s2-speed-clean.png" alt="Sector 2 Speed Chart" className="ref-s2-chart-img" />
              </div>
            </div>
          </Panel>
        </div>
      </div>
    </>
  );
}

/* =========================================================================
   7. RECORDINGS (Mock Image 6)
   ========================================================================= */
function Recordings({ preservedQuery }: { preservedQuery: string }) {
  return (
    <>
      <Panel
        title="Recording & Capture (UDP)"
        icon={<span className="dot-red"/>}
        className="recording-top-panel"
        action={
          <div className="ref-capture-status-row">
            <span className="ref-listening-text">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="#10b981" strokeWidth="2" style={{ display: "inline-block", verticalAlign: "middle", marginRight: "4px" }}>
                <path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/>
              </svg>
              Listening on UDP port 20777
            </span>
            <ActionLink screen="settings" preservedQuery={preservedQuery}>
              <span style={{ marginRight: "3px" }}><GearIcon size={12} /></span> Configure...
            </ActionLink>
          </div>
        }
      >
        <div className="ref-recording-top-inner">
          <div className="ref-recording-primary-box">
            <div className="ref-rec-badge-row">
              <Badge tone="good"><span className="dot-green"/> RECORDING (UDP)</Badge>
              <span className="ref-rec-sub">Receiving telemetry on port 20777</span>
            </div>
            <div className="ref-rec-big-row">
              <strong>00:18:42</strong>
              <div className="ref-rec-btns">
                <button className="ref-btn-subtle"><PauseIcon size={12} style={{ marginRight: 4 }} /> Pause Recording</button>
                <button className="ref-stop-btn-red"><StopIcon size={11} style={{ marginRight: 4 }} /> Stop Recording</button>
              </div>
            </div>
            <div className="ref-lap-capture-sub">
              <span>Lap 12</span> <span className="sep-pipe">|</span> <span>2.4 GB captured</span>
            </div>
          </div>

          <div className="ref-stats-cards-row">
            <div className="ref-stat-card">
              <span className="stat-label">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#64748b" strokeWidth="2">
                  <ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/><path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/>
                </svg>
                Packets Received
              </span>
              <strong>1,248,592</strong>
              <small>124 pkt/s</small>
            </div>
            <div className="ref-stat-card">
              <span className="stat-label">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#64748b" strokeWidth="2">
                  <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                </svg>
                Queue Drops
              </span>
              <strong>0</strong>
              <small>0.00%</small>
            </div>
            <div className="ref-stat-card">
              <span className="stat-label">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#64748b" strokeWidth="2">
                  <rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>
                </svg>
                Current File Size
              </span>
              <strong>2.4 GB</strong>
              <small>~ 18 min</small>
            </div>
          </div>
        </div>
      </Panel>

      <div className="ref-grid recordings-main">
        {/* Recorded Captures Table */}
        <Panel
          title="Recorded Captures"
          icon={
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
              <ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/><path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/>
            </svg>
          }
          action={
            <div className="ref-captures-header-actions">
              <div className="ref-search-input-pill">
                <span><SearchIcon size={13} /></span>
                <input placeholder="Search recordings..." readOnly />
              </div>
              <button className="ref-btn-subtle"><UploadIcon size={12} style={{ marginRight: 4 }} /> Import Files</button>
              <button className="ref-btn-icon">•••</button>
            </div>
          }
        >
          <div className="ref-tab-pills">
            <button className="tab-pill active">Local Recordings</button>
            <button className="tab-pill">Imported Sessions</button>
            <button className="tab-pill">All Files</button>
          </div>
          <div className="ref-table-wrap is-compact">
            <table className="ref-table">
              <thead>
                <tr><th>Name</th><th>Track <ChevronDownIcon size={10} /></th><th>Mode</th><th>Date / Time</th><th>Laps</th><th>Size</th><th>Status</th><th></th></tr>
              </thead>
              <tbody>
                <tr className="is-selected">
                  <td><span className="dot-circle-blue"/> Monza_Practice_2026_09_08_1402</td><td>Monza</td><td>Practice</td><td>Today, 14:02</td><td><strong>58</strong></td><td>1.8 GB</td><td><Badge tone="good"><span className="dot-green"/> Completed</Badge></td><td>•••</td>
                </tr>
                <tr><td><span className="dot-circle-gray"/> Zandvoort_Race_2026_09_01</td><td>Zandvoort</td><td>Race</td><td>01 Sep 2026</td><td>72</td><td>2.1 GB</td><td><Badge tone="good"><span className="dot-green"/> Completed</Badge></td><td>•••</td></tr>
                <tr><td><span className="dot-circle-gray"/> Spa_Qualifying_2026_08_24</td><td>Spa</td><td>Qualifying</td><td>24 Aug 2026</td><td>12</td><td>420 MB</td><td><Badge tone="blue"><span className="dot-blue"/> Imported</Badge></td><td>•••</td></tr>
                <tr><td><span className="dot-circle-gray"/> Silverstone_Practice_2026_08_17</td><td>Silverstone</td><td>Practice</td><td>17 Aug 2026</td><td>46</td><td>1.4 GB</td><td><Badge tone="good"><span className="dot-green"/> Completed</Badge></td><td>•••</td></tr>
                <tr><td><span className="dot-circle-gray"/> Imola_Race_2026_08_10</td><td>Imola</td><td>Race</td><td>10 Aug 2026</td><td>63</td><td>2.2 GB</td><td><Badge tone="good"><span className="dot-green"/> Completed</Badge></td><td>•••</td></tr>
                <tr><td><span className="dot-circle-gray"/> Barcelona_Practice_2026_08_03</td><td>Barcelona</td><td>Practice</td><td>03 Aug 2026</td><td>41</td><td>1.6 GB</td><td><Badge tone="blue"><span className="dot-blue"/> Imported</Badge></td><td>•••</td></tr>
                <tr><td><span className="dot-circle-gray"/> Suzuka_Qualifying_2026_07_26</td><td>Suzuka</td><td>Qualifying</td><td>26 Jul 2026</td><td>14</td><td>380 MB</td><td><Badge tone="bad"><span className="dot-red"/> Failed</Badge></td><td>•••</td></tr>
                <tr><td><span className="dot-circle-gray"/> Baku_Practice_2026_07_19</td><td>Baku</td><td>Practice</td><td>19 Jul 2026</td><td>39</td><td>1.3 GB</td><td><Badge tone="good"><span className="dot-green"/> Completed</Badge></td><td>•••</td></tr>
              </tbody>
            </table>
          </div>
        </Panel>

        {/* Replay Player */}
        <Panel
          title="Replay – Monza_Practice_2026_09_08_1402"
          icon={
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
              <path d="m22 8-6 4 6 4V8Z"/><rect x="2" y="6" width="14" height="12" rx="2"/>
            </svg>
          }
          action={<Badge tone="neutral">Read Only</Badge>}
        >
          <div className="ref-replay-video-container">
            <img src="/cockpit-replay-full.png" alt="Cockpit Video Replay" className="ref-cockpit-img" />
          </div>
          <div className="ref-replay-timeline-bar">
            <span>0:08:32</span>
            <div className="scrubber-track"><div className="scrubber-fill" style={{ width: "12%" }} /></div>
            <span>1:20:41</span>
          </div>
          <div className="ref-replay-controls-row">
            <button className="ctrl-btn"><SkipBackIcon size={13} /></button>
            <button className="play-circle-btn"><PlayIcon size={12} /></button>
            <button className="ctrl-btn"><SkipForwardIcon size={13} /></button>
            <div className="ref-tab-pills small">
              <button className="tab-pill">0.5x</button>
              <button className="tab-pill active">1x</button>
              <button className="tab-pill">2x</button>
              <button className="tab-pill">4x</button>
            </div>
          </div>
          <p className="ref-replay-note">ⓘ Replay is temporary and read-only. Telemetry is played from the selected file and is not live.</p>
        </Panel>

        {/* Import Jobs */}
        <Panel title="Import Jobs" icon={<UploadIcon size={14} />} action={<div className="ref-import-actions"><button className="ref-btn-subtle"><UploadIcon size={12} style={{ marginRight: 4 }} /> Import from file...</button><button className="ref-btn-icon">•••</button></div>}>
          <div className="ref-table-wrap is-compact">
            <table className="ref-table">
              <thead>
                <tr><th>File Name</th><th>Track</th><th>Mode</th><th>Progress</th><th>Status</th><th>Started</th><th></th></tr>
              </thead>
              <tbody>
                <tr>
                  <td><FileIcon size={12} style={{ marginRight: 4 }} /> Monza_Race_2026_09_05.f1r</td><td>Monza</td><td>Race</td>
                  <td><div className="bar-track small"><div className="bar-fill fill-blue" style={{ width: "65%" }} /></div> 65%</td>
                  <td><Badge tone="blue"><span className="dot-blue-spin"/> Importing</Badge></td><td>Today, 15:21</td><td>•••</td>
                </tr>
                <tr>
                  <td><FileIcon size={12} style={{ marginRight: 4 }} /> Hungaroring_Practice_2026_08_31.f1r</td><td>Hungaroring</td><td>Practice</td>
                  <td><div className="bar-track small"><div className="bar-fill fill-green" style={{ width: "100%" }} /></div> 100%</td>
                  <td><Badge tone="good"><span className="dot-green"/> Complete</Badge></td><td>Today, 14:03</td><td>•••</td>
                </tr>
                <tr>
                  <td><FileIcon size={12} style={{ marginRight: 4 }} /> Spa_Race_2026_08_24.f1r</td><td>Spa</td><td>Race</td>
                  <td><div className="bar-track small"><div className="bar-fill" style={{ width: "0%" }} /></div> 0%</td>
                  <td><Badge tone="neutral"><span className="dot-gray"/> Queued</Badge></td><td>Today, 13:12</td><td>•••</td>
                </tr>
                <tr>
                  <td><FileIcon size={12} style={{ marginRight: 4 }} /> Austin_Practice_2026_08_18.f1r</td><td>Austin</td><td>Practice</td>
                  <td><div className="bar-track small"><div className="bar-fill fill-red" style={{ width: "0%" }} /></div> 0%</td>
                  <td><Badge tone="bad"><span className="dot-red"/> Failed</Badge></td><td>Today, 11:46</td><td>•••</td>
                </tr>
              </tbody>
            </table>
          </div>
        </Panel>

        {/* Selected Recording Details */}
        <Panel title="Selected Recording Details" icon={<DatabaseIcon size={14} />} action={<button className="ref-btn-icon">•••</button>}>
          <div className="ref-recording-details-body">
            <div className="ref-rec-info-col">
              <div className="ref-rec-thumb-row">
                <img src="/monza-thumb.png" alt="Monza" />
                <div className="ref-rec-title-meta">
                  <strong>Monza_Practice_2026_09_08_1402</strong>
                  <div className="meta-sub"><FlagIT /> Monza</div>
                  <div className="meta-sub">Practice | 08 Sep 2026 14:02</div>
                </div>
              </div>
              <div className="ref-rec-facts-2col">
                <span>Laps</span> <strong>58</strong>
                <span>Duration</span> <strong>1:20:41</strong>
                <span>File Size</span> <strong>1.8 GB</strong>
                <span>Data Rate</span> <strong>124 pkt/s (avg)</strong>
              </div>
            </div>

            <div className="ref-rec-metrics-col">
              <div className="ref-capture-qual-box">
                <div className="qual-header">
                  <span>Capture Quality</span>
                  <Badge tone="good">Good</Badge>
                </div>
                <div className="bar-track" style={{ height: "6px", background: "#e2e8f0", borderRadius: "3px" }}>
                  <div className="bar-fill" style={{ width: "95%", height: "100%", background: "#10b981", borderRadius: "3px" }} />
                </div>
              </div>

              <div className="ref-net-metrics-list">
                <div className="ref-net-metric-row">
                  <span className="metric-left">
                    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="#64748b" strokeWidth="2"><path d="M2 20h.01"/><path d="M7 20v-4"/><path d="M12 20v-8"/><path d="M17 20V4"/></svg>
                    Packet Loss
                  </span>
                  <span style={{ color: "#10b981", marginRight: "6px" }}>●</span>
                  <strong>0.3%</strong>
                </div>
                <div className="ref-net-metric-row">
                  <span className="metric-left">
                    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="#64748b" strokeWidth="2"><path d="m16 3 4 4-4 4"/><path d="M20 7H4"/><path d="m8 21-4-4 4-4"/><path d="M4 17h16"/></svg>
                    Out of Order
                  </span>
                  <span style={{ color: "#10b981", marginRight: "6px" }}>●</span>
                  <strong>0.1%</strong>
                </div>
                <div className="ref-net-metric-row">
                  <span className="metric-left">
                    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="#64748b" strokeWidth="2"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                    Queue Drops
                  </span>
                  <span style={{ color: "#94a3b8", marginRight: "6px" }}>●</span>
                  <strong>0 (0.00%)</strong>
                </div>
              </div>
            </div>
          </div>

          <div className="ref-rec-quick-actions">
            <span className="qa-label">Quick Actions</span>
            <button className="ref-btn-blue-action"><DownloadIcon size={12} style={{ marginRight: 4 }} /> Import</button>
            <button className="ref-btn-subtle"><SearchIcon size={12} style={{ marginRight: 4 }} /> Inspect</button>
            <button className="ref-btn-subtle"><PlayIcon size={11} style={{ marginRight: 4 }} /> Replay</button>
            <button className="ref-btn-danger"><TrashIcon size={12} style={{ marginRight: 4 }} /> Delete</button>
          </div>
        </Panel>
      </div>
    </>
  );
}

/* =========================================================================
   8. SETTINGS (Mock Image 5)
   ========================================================================= */
function Settings(_props: { preservedQuery: string }) {
  return (
    <>
      <PageHeading
        title="Settings"
        subtitle="Configure your AI race engineer, devices, telemetry and more"
        icon={<GearIcon size={20} />}
      />
      <div className="ref-privacy-banner">
        <span className="icon-info"><InfoIcon size={14} /></span>
        <strong>All processing is local on your computer.</strong>
        <span>Your data, voice, and telemetry never leave this device.</span>
        <button className="close-banner-btn"><CrossIcon size={11} /></button>
      </div>

      <div className="ref-grid settings-main">
        {/* Left Nav */}
        <nav className="ref-settings-nav" aria-label="Settings categories">
          {[
            { title: "General", subtitle: "App behaviour & preferences", icon: <GearIcon size={14} />, active: true },
            { title: "AI Settings", subtitle: "Model & personality", icon: <BrainIcon size={14} />, active: false },
            { title: "Voice & Audio", subtitle: "Speech input & output", icon: <SpeakerIcon size={14} />, active: false },
            { title: "Telemetry", subtitle: "UDP & data settings", icon: <WaveIcon size={14} />, active: false },
            { title: "Devices", subtitle: "Wheel, buttons & PTT", icon: <GamepadIcon size={14} />, active: false },
            { title: "Overlay", subtitle: "HUD & in-game display", icon: <MonitorIcon size={14} />, active: false },
            { title: "Storage", subtitle: "Recordings & data management", icon: <DatabaseIcon size={14} />, active: false },
          ].map(({ title, subtitle, icon, active }) => (
            <button className={`ref-settings-nav-item ${active ? "is-active" : ""}`} key={title}>
              <span className="settings-nav-icon">{icon}</span>
              <div className="settings-nav-text">
                <b>{title}</b>
                <small>{subtitle}</small>
              </div>
            </button>
          ))}
        </nav>

        {/* Column 1 */}
        <div className="ref-settings-column">
          {/* AI Settings */}
          <Panel
            title="AI Settings"
            subtitle="Configure the local AI model and behaviour"
            icon={
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
                <rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="2" x2="9" y2="4"/><line x1="15" y1="2" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="22"/><line x1="15" y1="20" x2="15" y2="22"/><line x1="20" y1="9" x2="22" y2="9"/><line x1="20" y1="14" x2="22" y2="14"/><line x1="2" y1="9" x2="4" y2="9"/><line x1="2" y1="14" x2="4" y2="14"/>
              </svg>
            }
          >
            <div className="ref-settings-card-2col">
              <div className="card-col">
                <div className="ref-field">
                  <span>Local Model</span>
                  <button className="ref-select-full"><span style={{ display: "flex", alignItems: "center", gap: "6px" }}><span className="dot-green"/> Llama 3.1 8B Instruct (Recommended)</span> <span><ChevronDownIcon size={11} /></span></button>
                </div>
                <div className="ref-field">
                  <span>Personality & Focus</span>
                  <button className="ref-select-full"><span style={{ display: "flex", alignItems: "center", gap: "6px" }}><FlagCheckeredIcon size={13} /> Race Engineer (Balanced)</span> <span><ChevronDownIcon size={11} /></span></button>
                  <small>Balanced advice on pace, strategy, tyre management and safety.</small>
                </div>
                <div className="ref-field">
                  <span>Response Length</span>
                  <button className="ref-select-full"><span>Medium</span> <span><ChevronDownIcon size={11} /></span></button>
                  <small>Concise and actionable responses.</small>
                </div>
              </div>
              <div className="card-col">
                <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "8px" }}>
                  <button className="ref-btn-subtle" style={{ color: "#0260e8", border: "1px solid #bfdbfe", background: "#f8fafc", padding: "4px 10px", fontSize: "11px", borderRadius: "6px" }}>
                    <><DatabaseIcon size={12} style={{ marginRight: 4 }} /> Manage Models</>
                  </button>
                </div>
                <div className="ref-toggle-list">
                  <label>
                    <div className="switch active"><span className="switch-knob"/></div>
                    <div><b>Use session context</b><small>Include live timing, laps and telemetry</small></div>
                  </label>
                  <label>
                    <div className="switch active"><span className="switch-knob"/></div>
                    <div><b>Proactive suggestions</b><small>Offer advice without being asked</small></div>
                  </label>
                  <label>
                    <div className="switch active"><span className="switch-knob"/></div>
                    <div><b>Safety focus</b><small>Prioritise safety and risk management</small></div>
                  </label>
                  <label>
                    <div className="switch"><span className="switch-knob"/></div>
                    <div><b>Use track/tyre knowledge</b><small>Include circuit and tyre compound knowledge</small></div>
                  </label>
                </div>
              </div>
            </div>
          </Panel>

          {/* Telemetry */}
          <Panel
            title="Telemetry"
            subtitle="Configure UDP telemetry input and data handling"
            icon={
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
                <path d="M2 12h5l3-7 4 14 3-7h5"/>
              </svg>
            }
            action={<Badge tone="good"><span className="dot-green"/> Connected</Badge>}
          >
            <div className="ref-grid-2x2">
              <label className="ref-field">
                <span>UDP Host</span>
                <input className="ref-input" value="127.0.0.1" readOnly />
              </label>
              <label className="ref-field">
                <span>UDP Port</span>
                <input className="ref-input" value="20777" readOnly />
              </label>
              <label className="ref-field">
                <span>Queue Size</span>
                <input className="ref-input" value="1000" readOnly />
                <small>Number of telemetry packets to buffer</small>
              </label>
              <label className="ref-field">
                <span>Data Freshness</span>
                <button className="ref-select-full"><span>Normal (100 ms)</span> <span><ChevronDownIcon size={11} /></span></button>
                <small>How quickly to process incoming data</small>
              </label>
            </div>
          </Panel>

          {/* Overlay */}
          <Panel
            title="Overlay"
            subtitle="In-game overlay and HUD settings"
            icon={
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
                <rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>
              </svg>
            }
          >
            <div className="ref-settings-card-2col">
              <div className="card-col">
                <div className="ref-toggle-list">
                  <label><div className="switch active"><span className="switch-knob"/></div><div><b>Enable In-Game Overlay (HUD)</b><small>Show live advice and alerts in-game</small></div></label>
                  <label><div className="switch active"><span className="switch-knob"/></div><div><b>Show Delta & Sector Info</b><small>Display current delta and sector times</small></div></label>
                  <label><div className="switch active"><span className="switch-knob"/></div><div><b>Show Tyre & Fuel Info</b><small>Show tyre age, compound and fuel load</small></div></label>
                  <label><div className="switch active"><span className="switch-knob"/></div><div><b>Show AI Messages</b><small>Display voice messages as text</small></div></label>
                </div>
              </div>
              <div className="card-col">
                <div className="ref-field"><span>Overlay Position</span><button className="ref-select-full"><span>Top Right</span> <span><ChevronDownIcon size={11} /></span></button></div>
                <div className="ref-slider-field" style={{ margin: "4px 0" }}>
                  <div className="slider-header-label"><span>Opacity</span><strong>80%</strong></div>
                  <div className="slider-row"><div className="slider-track"><div className="slider-fill" style={{ width: "80%" }}><div className="slider-thumb"/></div></div></div>
                </div>
                <div className="ref-field"><span>Text Size</span><button className="ref-select-full"><span>Medium</span> <span><ChevronDownIcon size={11} /></span></button></div>
                <div className="ref-field"><span>Theme</span><button className="ref-select-full"><span>Auto (Light/Dark)</span> <span><ChevronDownIcon size={11} /></span></button></div>
              </div>
            </div>
          </Panel>
        </div>

        {/* Column 2 */}
        <div className="ref-settings-column">
          {/* Voice & Audio */}
          <Panel
            title="Voice & Audio"
            subtitle="Configure speech-to-text and text-to-speech"
            icon={
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
                <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
              </svg>
            }
          >
            <div className="ref-settings-card-2col">
              <div className="card-col">
                <div className="ref-field">
                  <span>Speech-to-Text (Microphone)</span>
                  <button className="ref-select-full"><span style={{ display: "flex", alignItems: "center", gap: "6px" }}><MicrophoneIcon size={13} /> Windows Default Device</span> <span><ChevronDownIcon size={11} /></span></button>
                  <div className="vu-meter">
                    {Array.from({ length: 32 }, (_, i) => (
                      <span key={i} className={i < 17 ? "lit" : ""} />
                    ))}
                  </div>
                </div>
                <div className="ref-field">
                  <span>Push-to-Talk</span>
                  <button className="ref-select-full"><span style={{ display: "flex", alignItems: "center", gap: "6px" }}><KeyboardIcon size={13} /> Mouse Button 4 (Side)</span> <span><ChevronDownIcon size={11} /></span></button>
                  <small>Hold to talk to the AI engineer</small>
                </div>
                <div className="ref-field">
                  <span>Text-to-Speech (Output)</span>
                  <button className="ref-select-full"><span style={{ display: "flex", alignItems: "center", gap: "6px" }}><SpeakerIcon size={13} /> Windows Default Device</span> <span><ChevronDownIcon size={11} /></span></button>
                </div>
              </div>
              <div className="card-col">
                <div className="ref-field">
                  <span>AI Voice</span>
                  <button className="ref-select-full"><span style={{ display: "flex", alignItems: "center", gap: "6px" }}><UserIcon size={13} /> Brian (British, Calm)</span> <span><ChevronDownIcon size={11} /></span></button>
                </div>
                <button className="ref-btn-play-voice">
                  <PlayIcon size={11} style={{ marginRight: 5 }} /> Play Test Voice
                </button>
                <div className="ref-slider-field">
                  <div className="slider-header-label">
                    <span>Voice Speed</span>
                    <strong>1.0x</strong>
                  </div>
                  <div className="slider-row">
                    <div className="slider-track"><div className="slider-fill" style={{ width: "50%" }}><div className="slider-thumb"/></div></div>
                  </div>
                </div>
                <div className="ref-slider-field">
                  <div className="slider-header-label">
                    <span>Voice Volume</span>
                    <strong>80%</strong>
                  </div>
                  <div className="slider-row">
                    <div className="slider-track"><div className="slider-fill" style={{ width: "80%" }}><div className="slider-thumb"/></div></div>
                  </div>
                </div>
              </div>
            </div>
          </Panel>

          {/* Devices */}
          <Panel
            title="Devices"
            subtitle="Configure wheel, buttons and other input devices"
            icon={
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
                <rect x="2" y="6" width="20" height="12" rx="4"/><line x1="6" y1="12" x2="10" y2="12"/><line x1="8" y1="10" x2="8" y2="14"/><line x1="15" y1="13" x2="15.01" y2="13"/><line x1="18" y1="11" x2="18.01" y2="11"/>
              </svg>
            }
            action={<button className="ref-btn-subtle" style={{ fontSize: "11px", padding: "4px 8px" }}><RefreshIcon size={11} style={{ marginRight: 4 }} /> Detect Devices</button>}
          >
            <div className="ref-settings-card-2col">
              <div className="card-col">
                <div className="ref-field">
                  <span>Selected Device</span>
                  <button className="ref-select-full"><span style={{ display: "flex", alignItems: "center", gap: "6px" }}><GamepadIcon size={13} /> Logitech G Pro Racing Wheel</span> <span><ChevronDownIcon size={11} /></span></button>
                </div>
                <div>
                  <span style={{ fontSize: "11px", fontWeight: "600", color: "#1e293b", display: "block", marginBottom: "6px" }}>Button Mapping</span>
                  <div className="btn-map-row"><span>Button 1</span><button className="ref-select-full"><span>Request Tyre Info</span> <span><ChevronDownIcon size={11} /></span></button></div>
                  <div className="btn-map-row"><span>Button 2</span><button className="ref-select-full"><span>Ask About Pace</span> <span><ChevronDownIcon size={11} /></span></button></div>
                  <div className="btn-map-row"><span>Button 3</span><button className="ref-select-full"><span>Toggle Overlay</span> <span><ChevronDownIcon size={11} /></span></button></div>
                  <div className="btn-map-row"><span>Button 4</span><button className="ref-select-full"><span>Cycle AI Focus</span> <span><ChevronDownIcon size={11} /></span></button></div>
                </div>
              </div>
              <div className="card-col">
                <div className="ref-field">
                  <span>Push-to-Talk Assignment</span>
                  <button className="ref-select-full"><span>Mouse Button 4 (Side)</span> <span><ChevronDownIcon size={11} /></span></button>
                  <small>Hold to talk to the AI engineer</small>
                </div>
                <div className="ref-field" style={{ marginTop: "12px" }}>
                  <span>Secondary Device (Optional)</span>
                  <button className="ref-select-full"><span style={{ display: "flex", alignItems: "center", gap: "6px" }}><GamepadIcon size={13} /> No device selected</span> <span><ChevronDownIcon size={11} /></span></button>
                  <small>e.g. Stream Deck, button box</small>
                </div>
              </div>
            </div>
          </Panel>

          {/* Storage */}
          <Panel
            title="Storage"
            subtitle="Manage recordings, database and disk usage"
            icon={
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0260e8" strokeWidth="2">
                <ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/><path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/>
              </svg>
            }
          >
            <div className="ref-settings-card-2col">
              <div className="card-col">
                <div className="ref-field">
                  <span>Database Path</span>
                  <div style={{ display: "flex", gap: "6px" }}>
                    <input className="ref-input" value="C:\Users\Alex\GP_T\data" readOnly />
                    <button className="ref-btn-subtle" style={{ width: "32px", height: "28px", display: "grid", placeItems: "center", flex: "none", borderRadius: "6px" }}>
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#1e293b" strokeWidth="2"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/></svg>
                    </button>
                  </div>
                </div>
                <div className="ref-field">
                  <span>Recordings Root</span>
                  <div style={{ display: "flex", gap: "6px" }}>
                    <input className="ref-input" value="C:\Users\Alex\GP_T\recordings" readOnly />
                    <button className="ref-btn-subtle" style={{ width: "32px", height: "28px", display: "grid", placeItems: "center", flex: "none", borderRadius: "6px" }}>
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#1e293b" strokeWidth="2"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/></svg>
                    </button>
                  </div>
                </div>
              </div>
              <div className="card-col">
                <div className="ref-disk-usage-section">
                  <div className="disk-usage-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "4px" }}>
                    <strong style={{ fontSize: "11px", color: "#1e293b" }}>Disk Usage</strong>
                    <span style={{ fontSize: "10.5px", color: "#64748b" }}>42.6 GB / 200 GB <b style={{ color: "#0f172a", marginLeft: "4px" }}>21%</b></span>
                  </div>
                  <div className="disk-bar-multi">
                    <span className="bar-rec" style={{ width: "14.2%" }} />
                    <span className="bar-db" style={{ width: "4%" }} />
                    <span className="bar-logs" style={{ width: "1.6%" }} />
                    <span className="bar-temp" style={{ width: "0.5%" }} />
                  </div>
                  <div className="disk-legend-grid">
                    <div><span style={{ color: "#0260e8", marginRight: "4px" }}>●</span> Recordings <b>28.4 GB</b></div>
                    <div><span style={{ color: "#10b981", marginRight: "4px" }}>●</span> Database <b>8.1 GB</b></div>
                    <div><span style={{ color: "#f59e0b", marginRight: "4px" }}>●</span> Logs <b>3.2 GB</b></div>
                    <div><span style={{ color: "#94a3b8", marginRight: "4px" }}>●</span> Temporary Files <b>0.9 GB</b></div>
                  </div>
                  <div className="storage-action-row" style={{ display: "flex", gap: "8px", marginTop: "10px" }}>
                    <button className="ref-btn-danger"><TrashIcon size={12} style={{ marginRight: 4 }} /> Clean Old Recordings...</button>
                    <button className="ref-btn-subtle" style={{ fontSize: "11px", padding: "5px 10px" }}><FolderIcon size={12} style={{ marginRight: 4 }} /> Open Folder</button>
                  </div>
                </div>
              </div>
            </div>
          </Panel>
        </div>
      </div>

      <footer className="ref-settings-footer-actions">
        <button className="ref-btn-subtle"><RefreshIcon size={12} style={{ marginRight: 4 }} /> Reset to Defaults</button>
        <button className="ref-btn-subtle">Cancel</button>
        <button className="ref-btn-blue-action" style={{ padding: "7px 18px" }}><CheckIcon size={12} style={{ marginRight: 4 }} /> Apply Settings</button>
      </footer>
    </>
  );
}
