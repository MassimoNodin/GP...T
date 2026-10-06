import type { ReactNode } from "react";
import {
  appScreenHref,
  isSelectionTransferBlocked,
  type AppScreen,
} from "@/lib/navigation";

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
  icon?: string;
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
        <div className="ref-panel-title"><h2>{title}</h2>{subtitle ? <small>{subtitle}</small> : null}</div>
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
  icon?: string;
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
  const blocked = screen !== undefined &&
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
        title={blocked ? "Selection is too large to carry safely." : "This action is unavailable."}
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
  icon?: string;
}) {
  return (
    <div className={`ref-metric ${tone ? `ref-metric-${tone}` : ""}`}>
      <span className="ref-metric-label">{icon ? <i aria-hidden="true">{icon}</i> : null}{label}</span>
      <strong>{value}{unit ? <small>{unit}</small> : null}</strong>
      {detail ? <span className="ref-metric-detail">{detail}</span> : null}
    </div>
  );
}

function Facts({ items, columns = 2 }: { items: Array<[string, string]>; columns?: number }) {
  return (
    <dl className={`ref-facts ref-cols-${columns}`}>
      {items.map(([label, value]) => (
        <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  );
}

function PlaceholderTable({
  headings,
  rows = 5,
  selectedRow = false,
  compact = false,
}: {
  headings: string[];
  rows?: number;
  selectedRow?: boolean;
  compact?: boolean;
}) {
  return (
    <div className={`ref-table-wrap ${compact ? "is-compact" : ""}`}>
      <table className="ref-table">
        <thead><tr>{headings.map((heading) => <th key={heading}>{heading}</th>)}</tr></thead>
        <tbody>
          {Array.from({ length: rows }, (_, index) => (
            <tr className={selectedRow && index === 0 ? "is-selected" : ""} key={index}>
              {headings.map((heading, column) => <td key={`${index}-${heading}`}>{column === 0 && !compact ? "—" : "—"}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChartPlaceholder({ channels = ["Speed", "Throttle", "Brake"] }: { channels?: string[] }) {
  return (
    <div className="ref-chart" aria-label="Chart area; telemetry is unavailable">
      {channels.map((channel, index) => (
        <div className="ref-chart-row" key={channel}>
          <span>{channel}</span>
          <svg viewBox="0 0 520 44" preserveAspectRatio="none" role="img" aria-label={`${channel} chart has no data`}>
            {[0, 1, 2, 3].map((tick) => <line key={tick} x1="0" x2="520" y1={tick * 14 + 1} y2={tick * 14 + 1} />)}
            <line className="ref-chart-axis" x1="0" x2="520" y1="43" y2="43" />
            {index === 0 ? <path className="ref-chart-baseline" d="M0 22H520" /> : null}
          </svg>
        </div>
      ))}
      <div className="ref-chart-distance"><span>0.0</span><span>Distance</span><span>—</span></div>
    </div>
  );
}

function TrackPlaceholder({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`ref-track-placeholder ${compact ? "is-compact" : ""}`}>
      <svg viewBox="0 0 520 230" role="img" aria-label="Track map unavailable until a validated track model is selected">
        <path className="ref-track-shadow" d="M72 162 C49 119 81 84 119 92 L175 101 C207 105 211 47 259 55 L327 65 C374 71 395 107 441 92 C490 77 498 124 460 148 C428 169 375 151 333 158 C279 166 275 198 221 187 L158 175 C123 169 92 194 72 162Z" />
        <path className="ref-track-line" d="M72 162 C49 119 81 84 119 92 L175 101 C207 105 211 47 259 55 L327 65 C374 71 395 107 441 92 C490 77 498 124 460 148 C428 169 375 151 333 158 C279 166 275 198 221 187 L158 175 C123 169 92 194 72 162Z" />
        <text x="260" y="218" textAnchor="middle">TRACK GEOMETRY UNAVAILABLE</text>
      </svg>
      {!compact ? <span className="ref-map-legend"><i /> Faster <i /> Similar <i /> Slower</span> : null}
    </div>
  );
}

function PhotoPlaceholder({ label = "Track image unavailable" }: { label?: string }) {
  return <div className="ref-photo-placeholder" role="img" aria-label={label}><span>GP…T</span><small>{label}</small></div>;
}

function ChipRow({ children }: { children: ReactNode }) {
  return <div className="ref-chip-row">{children}</div>;
}

function SkeletonList({ rows = 4 }: { rows?: number }) {
  return <div className="ref-skeleton-list">{Array.from({ length: rows }, (_, i) => <span key={i}><i /><b /><em /></span>)}</div>;
}

function Dashboard({ preservedQuery }: { preservedQuery: string }) {
  return (
    <>
      <div className="ref-grid dash-top">
        <Panel title="Recording & Session" icon="◉" action={<Badge>Recorder status unavailable</Badge>}>
          <div className="ref-recording-summary">
            <div className="ref-recording-state"><Badge tone="neutral">RECORDING STATUS UNAVAILABLE</Badge><span>Local UDP source status unavailable</span></div>
            <div className="ref-recording-big"><strong>—:—:—</strong><ActionLink screen="recordings" preservedQuery={preservedQuery}>Open recordings</ActionLink></div>
            <ChipRow><span>Lap —</span><span>— captured</span></ChipRow>
          </div>
          <div className="ref-session-mini"><PhotoPlaceholder /><div><b>—  Track unavailable</b><span>Session details unavailable</span><small>—</small></div><ActionLink screen="sessions" preservedQuery={preservedQuery}>Change</ActionLink></div>
        </Panel>
        <Panel title="Live Telemetry" icon="∿" action={<Badge>Telemetry unavailable</Badge>}>
          <div className="ref-dash-gauge"><div className="ref-gauge-ring"><span>—<small>KM/H</small></span></div><div><small>Gear</small><strong>—</strong></div><div><small>Engine speed</small><strong>— <small>RPM</small></strong></div></div>
          <div className="ref-telemetry-bars">{[["Throttle", "—"], ["Brake", "—"], ["DRS", "—"]].map(([label, value]) => <div key={label}><span>{label}</span><i><b /></i><strong>{value}</strong></div>)}</div>
          <Facts items={[["Tyres", "—"], ["Fuel", "—"], ["ERS", "—"]]} columns={1} />
        </Panel>
        <Panel title="AI Race Engineer" icon="▣" action={<Badge>Engineer status unavailable</Badge>}>
          <div className="ref-engineer-welcome"><span>●</span><p>Ask about recorded telemetry and lap evidence.</p></div>
          <div className="ref-prompt-list">{["Summarise my last lap", "Compare two recorded laps", "Explain a track region", "What data is available?"].map((prompt) => <button type="button" key={prompt} disabled>{prompt}<i>→</i></button>)}</div>
          <div className="ref-composer"><span>Ask your engineer…</span><button type="button" disabled aria-label="Send question">➤</button></div>
        </Panel>
      </div>
      <div className="ref-grid dash-middle">
        <Panel title="Lap & Session Selector" icon="⌕" action={<ActionLink screen="sessions" preservedQuery={preservedQuery}>View sessions →</ActionLink>}>
          <ChipRow><Badge tone="blue">Current session</Badge><Badge>Imported sessions</Badge></ChipRow>
          <div className="ref-select-row"><button type="button" disabled>Session type　⌄</button><button type="button" disabled>Track　⌄</button></div>
          <PlaceholderTable headings={["Lap", "Lap Time", "Δ Best", "S1", "S2", "S3"]} rows={7} selectedRow compact />
        </Panel>
        <Panel title="Selected Lap Summary" icon="▰" action={<Badge>Lap —</Badge>}>
          <div className="ref-lap-summary"><Metric label="Lap time" value="—:—. —" detail="Attempt data unavailable" /><Metric label="Position" value="—" /><Metric label="Track" value="—" /></div>
          <div className="ref-metric-grid four"><Metric label="Sector 1" /><Metric label="Sector 2" /><Metric label="Sector 3" /><Metric label="Top speed" /></div>
          <div className="ref-metric-grid five"><Metric label="Tyre" /><Metric label="Fuel" /><Metric label="ERS" /><Metric label="Track temp" /><Metric label="Air temp" /></div>
        </Panel>
        <Panel title="Track & Region Analysis" icon="⌁" action={<Badge>—</Badge>}>
          <TrackPlaceholder compact />
          <div className="ref-track-note"><b>Track model unavailable</b><span>Validated geometry and region metrics will appear here.</span></div>
        </Panel>
      </div>
      <div className="ref-grid dash-bottom">
        <Panel title="Recent Recordings" icon="▤" action={<ActionLink screen="recordings" preservedQuery={preservedQuery}>View files →</ActionLink>}>
          <PlaceholderTable headings={["Name", "Track", "Type", "Date", "Laps", "Size"]} rows={5} compact />
        </Panel>
        <Panel title="Lap Comparison" icon="▥" action={<ActionLink screen="compare" preservedQuery={preservedQuery}>Compare laps →</ActionLink>}>
          <div className="ref-chart-tabs"><span>Current lap —</span><span>Reference —</span><Badge>Full lap</Badge></div>
          <ChartPlaceholder />
        </Panel>
      </div>
    </>
  );
}

function Live({ preservedQuery }: { preservedQuery: string }) {
  return (
    <>
      <div className="ref-grid live-top">
        <Panel title="Session Information" icon="◉" action={<Badge>Source unavailable</Badge>}>
          <div className="ref-session-feature"><PhotoPlaceholder /><div><b>—  Track unavailable</b><span>Session details unavailable</span><span>—</span></div></div>
          <Facts items={[["Session time", "—"], ["Local time", "—"], ["Track temperature", "—"], ["Air temperature", "—"]]} />
        </Panel>
        <Panel title="Live Telemetry" icon="∿" action={<Badge tone="neutral">Connection status unavailable</Badge>}>
          <div className="ref-live-gauge"><div className="ref-gauge-ring"><span>—<small>KM/H</small></span></div><div><small>Gear</small><strong>—</strong></div><div><strong>—</strong><small>RPM</small></div></div>
          <div className="ref-live-lower"><div className="ref-telemetry-bars">{[["Throttle", "—"], ["Brake", "—"], ["DRS", "—"]].map(([label, value]) => <div key={label}><span>{label}</span><i><b /></i><strong>{value}</strong></div>)}</div><Facts items={[["Fuel", "—"], ["ERS", "—"], ["Tyres", "—"], ["Wear", "—"]]} /></div>
        </Panel>
        <Panel title="Telemetry Data Status" icon="▤">
          <div className="ref-status-list">{["Player Telemetry", "Car Status", "Lap Timing", "Track Data", "Tyre & Wear", "Position & Rivals", "Weather Data"].map((item) => <div key={item}><i /><span>{item}</span><Badge>Unavailable</Badge><small>—</small></div>)}</div>
        </Panel>
      </div>
      <div className="ref-grid live-middle">
        <Panel title="Live Lap Timing" icon="◷" action={<Badge>Lap — / —</Badge>}>
          <div className="ref-lap-hero"><span>Current lap</span><strong>—:—. —</strong><Badge>— vs best</Badge></div>
          <div className="ref-metric-grid five"><Metric label="Best lap" /><Metric label="Last lap" /><Metric label="Sector 1" /><Metric label="Sector 2" /><Metric label="Sector 3" /></div>
        </Panel>
        <Panel title="Car Status (Live)" icon="▣">
          <div className="ref-status-columns"><Facts items={[["Fuel load", "—"], ["ERS mode", "—"], ["ERS battery", "—"], ["Engine mode", "—"], ["Brake bias", "—"]]} /><Facts items={[["Tyre compound", "—"], ["Tyre wear", "—"], ["Tyre temperature", "—"]]} /></div>
        </Panel>
        <Panel title="Track Map" icon="⌁" action={<Badge>—</Badge>}>
          <TrackPlaceholder compact />
          <ChipRow><Badge>Current car —</Badge><Badge>Track model unavailable</Badge></ChipRow>
        </Panel>
      </div>
      <div className="ref-grid live-bottom">
        <Panel title="Recent Laps" icon="▤" action={<ActionLink screen="sessions" preservedQuery={preservedQuery}>View all laps →</ActionLink>}>
          <PlaceholderTable headings={["Lap", "Lap Time", "S1", "S2", "S3", "Delta", "Tyre"]} rows={6} compact />
        </Panel>
        <Panel title="Live Telemetry Graphs" icon="∿" action={<Badge>Current lap —</Badge>}>
          <div className="ref-chart-tabs"><Badge tone="blue">Distance</Badge><Badge>Time</Badge><span>● Speed　 ● Throttle　 ● Brake　 ● Gear</span></div>
          <ChartPlaceholder channels={["Speed", "Throttle", "Brake", "Gear"]} />
        </Panel>
      </div>
    </>
  );
}

function Engineer({ preservedQuery: _preservedQuery }: { preservedQuery: string }) {
  return (
    <>
      <PageHeading title="AI Race Engineer" subtitle="Your local race engineer. Ask about pace, braking, traction, or recorded telemetry." icon="▣" status={<Badge>Local AI status unavailable</Badge>} />
      <Panel title="Engineer Intent" icon="◈" className="ref-intent-panel">
        <div className="ref-intents">{[["General Debrief", "Recorded evidence and summaries"], ["Region Comparison", "Compare a pair of laps or regions"], ["Strategy Advice", "Not available in this build"], ["Tyre Analysis", "Recorded tyre evidence"]].map(([title, subtitle], index) => <div className={index === 0 ? "is-selected" : ""} key={title}><b>{title}</b><span>{subtitle}</span></div>)}</div>
      </Panel>
      <div className="ref-grid engineer-main">
        <Panel title="Engineer Conversation" icon="▣" className="ref-chat-panel">
          <div className="ref-chat-empty"><span>GP…T</span><b>Ask about your recorded evidence</b><p>Conversation data is unavailable. Choose recorded evidence in the detailed workflow below.</p></div>
          <div className="ref-suggest-label">Try asking about</div>
          <ChipRow><Badge>Tyre wear evidence</Badge><Badge>Recorded braking</Badge><Badge>Compare two laps</Badge></ChipRow>
          <div className="ref-composer"><span>Ask your engineer…</span><button type="button" disabled aria-label="Send question">➤</button></div>
        </Panel>
        <div className="ref-stack">
          <Panel title="Context & Data Source" icon="▤" action={<Badge>Change context</Badge>}>
            <div className="ref-context-card"><PhotoPlaceholder /><div><b>—  Session unavailable</b><span>Track and mode unavailable</span><span>—</span></div><Facts items={[["Session", "—"], ["Lap", "—"], ["Reference", "—"], ["Focus", "—"]]} /></div>
          </Panel>
          <Panel title="Voice Response (Local)" icon="∿" action={<Badge>Voice unavailable</Badge>}>
            <div className="ref-voice-controls"><button type="button" disabled>▶</button><button type="button" disabled>■</button><div className="ref-waveform">{Array.from({ length: 40 }, (_, i) => <i key={i} />)}</div><span>— / —</span></div>
          </Panel>
          <Panel title="Key Evidence" icon="▥">
            <div className="ref-metric-grid three"><Metric label="Sector 1" /><Metric label="Sector 2" /><Metric label="Sector 3" /></div>
            <div className="ref-grid engineer-evidence-lower"><TrackPlaceholder compact /><ChartPlaceholder channels={["Speed"]} /></div>
          </Panel>
        </div>
      </div>
    </>
  );
}

function Compare({ preservedQuery }: { preservedQuery: string }) {
  return (
    <>
      <PageHeading title="Lap Comparison" subtitle="Compare recorded laps and inspect the evidence behind their time difference." icon="▥" />
      <div className="ref-compare-controls ref-panel">
        {["Target lap", "Reference lap", "Comparison policy", "Distance window", "Preset"].map((label) => <label key={label}><span>{label}</span><button type="button" disabled>—　⌄</button></label>)}
      </div>
      <div className="ref-grid compare-summary">
        <Metric label="Target lap" value="—:—. —" detail="Attempt data unavailable" />
        <Metric label="Reference lap" value="—:—. —" detail="Reference data unavailable" />
        <Metric label="Delta to reference" value="—. —" detail="Unavailable" tone="bad" />
        <Metric label="Top speed" value="—" unit=" km/h" detail="No trace data" />
        <Metric label="Sector 1" value="—. —" detail="Unavailable" tone="bad" />
        <Metric label="Sector 2" value="—. —" detail="Unavailable" />
        <Metric label="Sector 3" value="—. —" detail="Unavailable" tone="bad" />
      </div>
      <div className="ref-grid compare-middle">
        <Panel title="Telemetry Comparison" icon="▤" action={<ChipRow><Badge>Speed</Badge><Badge>Inputs</Badge><Badge>Steering</Badge><Badge>Gear</Badge></ChipRow>}>
          <div className="ref-chart-tabs"><span>● Target lap —　 ● Reference lap —</span><span>S1　 S2　 S3</span></div>
          <ChartPlaceholder channels={["Speed", "Delta", "Throttle", "Brake", "Gear"]} />
        </Panel>
        <div className="ref-stack">
          <Panel title="Track Map — Delta to Reference" icon="⌁"><TrackPlaceholder compact /><ChipRow><Badge>Faster</Badge><Badge>Similar</Badge><Badge>Slower</Badge></ChipRow></Panel>
          <Panel title="Reference Selection" icon="◉"><div className="ref-reference-card"><Badge>Reference unavailable</Badge><span>Choose an eligible reference in the detailed comparison controls.</span><ActionLink screen="sessions" preservedQuery={preservedQuery}>Open sessions →</ActionLink></div></Panel>
        </div>
      </div>
      <div className="ref-grid compare-bottom">
        <Panel title="Sector Comparison" icon="▤"><PlaceholderTable headings={["Sector", "Target lap", "Reference lap", "Delta", "Delta %", "Status"]} rows={4} compact /></Panel>
        <Panel title="Key Findings & Evidence" icon="▣"><div className="ref-empty-findings"><span>—</span><b>No findings available</b><p>Select an exact target and reference with matching recorded evidence.</p></div></Panel>
      </div>
    </>
  );
}

function Settings(_props: { preservedQuery: string }) {
  const groups = [
    ["AI Settings", "Local model and response preferences"],
    ["Voice & Audio", "Speech input and output"],
    ["Telemetry", "UDP connection and freshness"],
    ["Devices", "Wheel and button controls"],
    ["Overlay", "HUD preferences"],
    ["Storage", "Recordings and local data"],
  ];
  return (
    <>
      <PageHeading title="Settings" subtitle="Configure the local race engineer, telemetry, devices, and display." icon="⚙" />
      <div className="ref-privacy-banner"><b>ⓘ　All processing stays on your computer.</b><span>Voice, telemetry, and stored data remain local to this device.</span></div>
      <div className="ref-grid settings-main">
        <nav className="ref-settings-nav" id="ref-setting-0" aria-label="Settings categories">
          {[["General", "App preferences"], ...groups].map(([title, subtitle], index) => <a className={index === 0 ? "is-current" : ""} href={`#ref-setting-${index}`} key={title}><i>{["⚙", "▦", "◖", "∿", "▣", "▤", "▤"][index]}</i><span><b>{title}</b><small>{subtitle}</small></span></a>)}
        </nav>
        <div className="ref-settings-column">
          <Panel id="ref-setting-1" title="AI Settings" subtitle="Configure the local AI model and behaviour" icon="▦" className="ref-settings-card">
            <div className="ref-ai-settings-grid">
              <div className="ref-ai-fields">
                <label className="ref-field">Local Model<button type="button" disabled><i className="ref-status-indicator" />Not available　⌄</button><small>Local model status unavailable</small></label>
                <label className="ref-field">Personality & Focus<button type="button" disabled>—　⌄</button><small>Preference status unavailable</small></label>
                <label className="ref-field">Response Length<button type="button" disabled>—　⌄</button><small>Preference status unavailable</small></label>
              </div>
              <div className="ref-switch-list">{[
                ["Use session context", "Include live timing, laps and telemetry"],
                ["Proactive suggestions", "Offer advice without being asked"],
                ["Safety focus", "Prioritise safety and risk management"],
                ["Use track/tyre knowledge", "Include circuit and tyre compound knowledge"],
              ].map(([label, description]) => <label key={label}><span>{label}<small>{description}</small><small>Preference status unavailable</small></span><i aria-label={`${label} preference unavailable`} /></label>)}</div>
            </div>
          </Panel>
          <Panel id="ref-setting-3" title="Telemetry" subtitle="Configure UDP telemetry input and data handling" icon="∿" action={<Badge>Connection status unavailable</Badge>} className="ref-settings-card">
            <div className="ref-grid settings-fields"><label className="ref-field">UDP host<input disabled value="—" readOnly /></label><label className="ref-field">UDP port<input disabled value="—" readOnly /></label><label className="ref-field">Queue size<input disabled value="—" readOnly /></label><label className="ref-field">Data freshness<button type="button" disabled>—　⌄</button></label></div>
          </Panel>
          <Panel id="ref-setting-5" title="Overlay" subtitle="In-game overlay and HUD settings" icon="▱" className="ref-settings-card"><div className="ref-grid settings-fields"><div className="ref-switch-list">{["Enable in-game overlay", "Show delta & sector info", "Show tyre & fuel info", "Show AI messages"].map((label) => <label key={label}><span>{label}</span><i aria-label={`${label} preference unavailable`} /></label>)}</div><div className="ref-switch-list"><label><span>Overlay position</span><i aria-label="Overlay position unavailable" /></label><label><span>Opacity</span><i aria-label="Opacity unavailable" /></label><label><span>Text size</span><i aria-label="Text size unavailable" /></label><label><span>Theme</span><i aria-label="Theme unavailable" /></label></div></div></Panel>
        </div>
        <div className="ref-settings-column">
          <Panel id="ref-setting-2" title="Voice & Audio" subtitle="Configure speech-to-text and text-to-speech" icon="◖" action={<Badge>Browser voice</Badge>} className="ref-settings-card">
            <div className="ref-voice-settings-grid">
              <div className="ref-voice-inputs">
                {[["◖", "Speech-to-Text (Microphone)"], ["⌨", "Push-to-Talk"], ["◖", "Text-to-Speech (Output)"]].map(([icon, label], index) => <div className="ref-device-field" key={label}><span aria-hidden="true">{icon}</span><label className="ref-field">{label}<button type="button" disabled>Device unavailable　⌄</button>{index === 0 ? <span className="ref-input-level" aria-label="Input level unavailable">{Array.from({ length: 20 }, (_, i) => <i key={i} />)}</span> : null}</label></div>)}
              </div>
              <div className="ref-voice-output">
                <label className="ref-field">AI Voice<button type="button" disabled>Not available　⌄</button></label>
                <button className="ref-test-voice" type="button" disabled>▶　Play Test Voice</button>
                <div className="ref-slider"><span>Voice Speed</span><strong>—</strong><i /></div>
                <div className="ref-slider"><span>Voice Volume</span><strong>—</strong><i /></div>
              </div>
            </div>
          </Panel>
          <Panel id="ref-setting-4" title="Devices" subtitle="Configure wheel, buttons and other input devices" icon="▣" action={<><Badge>Device status unavailable</Badge><button className="ref-button" type="button" disabled>Detect Devices</button></>} className="ref-settings-card">
            <label className="ref-field">Selected device<button type="button" disabled>Device selection unavailable　⌄</button></label>
            <div className="ref-grid settings-fields"><div><b>Button mapping</b><Facts items={[["Button 1", "—"], ["Button 2", "—"], ["Button 3", "—"], ["Button 4", "—"]]} /></div><div><label className="ref-field">Push-to-Talk<button type="button" disabled>—　⌄</button></label><label className="ref-field">Secondary device<button type="button" disabled>—　⌄</button></label></div></div>
          </Panel>
          <Panel id="ref-setting-6" title="Storage" subtitle="Manage recordings, database and disk usage" icon="▤" action={<Badge>Usage unavailable</Badge>} className="ref-settings-card">
            <div className="ref-grid settings-fields"><div><label className="ref-field">Database path<input disabled value="—" readOnly /></label><label className="ref-field">Recordings folder<input disabled value="—" readOnly /></label></div><div><b>Disk usage</b><div className="ref-storage-meter"><i /></div><span>— used</span></div></div>
          </Panel>
        </div>
      </div>
      <div className="ref-settings-actions"><button type="button" disabled>Reset to defaults</button><button type="button" disabled>Cancel</button><button type="button" disabled>Apply settings</button></div>
    </>
  );
}

function Recordings({ preservedQuery }: { preservedQuery: string }) {
  return (
    <>
      <PageHeading title="Recording & Capture (UDP)" icon="◉" status={<ChipRow><Badge>UDP status unavailable</Badge><ActionLink screen="settings" preservedQuery={preservedQuery}>Configure…</ActionLink></ChipRow>} />
      <div className="ref-recording-banner">
        <div className="ref-recording-primary"><Badge tone="neutral">RECORDING STATUS UNAVAILABLE</Badge><span>Current recorder state and capture details are unavailable.</span><strong>—:—:—</strong><ChipRow><span>Lap —</span><span>— captured</span></ChipRow></div>
        <ActionLink href="#recording-controls-trigger">Open controls</ActionLink>
        <div className="ref-grid ref-recording-stats"><Metric label="Packets received" /><Metric label="Queue drops" /><Metric label="Current file size" /></div>
      </div>
      <div className="ref-grid recordings-main">
        <Panel title="Recorded Captures" icon="▤" action={<ActionLink href="#recording-controls-trigger">Import files</ActionLink>}>
          <ChipRow><Badge tone="blue">Local recordings</Badge><Badge>Imported sessions</Badge><Badge>All files</Badge></ChipRow>
          <PlaceholderTable headings={["Name", "Track", "Mode", "Date / Time", "Laps", "Size", "Status"]} rows={8} selectedRow compact />
        </Panel>
        <Panel title="Replay" icon="▣" action={<Badge>Read only</Badge>}>
          <div className="ref-replay-frame"><span>Selected capture preview</span><b>—</b><small>Video unavailable</small></div>
          <div className="ref-replay-timeline"><span>—:—</span><i /><span>—:—</span></div>
          <div className="ref-replay-controls"><button type="button" disabled>◀</button><button type="button" disabled>▶</button><button type="button" disabled>▶|</button><Badge>1×</Badge></div>
          <p className="ref-note">Replay telemetry remains read-only and uses the selected capture.</p>
        </Panel>
        <Panel title="Import Jobs" icon="⇧" action={<ActionLink href="#recording-controls-trigger">Import from file…</ActionLink>}>
          <PlaceholderTable headings={["File name", "Track", "Mode", "Progress", "Status", "Started"]} rows={4} compact />
        </Panel>
        <Panel title="Selected Recording Details" icon="▤" action={<Badge>—</Badge>}>
          <div className="ref-grid recording-details"><PhotoPlaceholder /><Facts items={[["Track", "—"], ["Session", "—"], ["Laps", "—"], ["Duration", "—"], ["File size", "—"], ["Data rate", "—"]]} /><div className="ref-capture-quality"><b>Capture quality</b><Badge>Unavailable</Badge><SkeletonList rows={3} /></div></div>
          <ChipRow><ActionLink href="#recording-controls-trigger">Inspect</ActionLink><ActionLink href="#recording-controls-trigger">Replay</ActionLink><span className="ref-button is-disabled" aria-disabled="true" title="Select a capture in the detailed workflow before deleting it.">Delete</span></ChipRow>
        </Panel>
      </div>
    </>
  );
}

function Track({ preservedQuery: _preservedQuery }: { preservedQuery: string }) {
  return (
    <>
      <div className="ref-track-config ref-panel"><div className="ref-track-config-title"><span className="ref-panel-icon">▣</span><b>Track & Analysis Configuration</b></div><div className="ref-track-select"><PhotoPlaceholder /><b>—</b><span>Track model unavailable</span></div>{[["Track model", "Choose an exact attempt"], ["Region set", "Select a validated model"]].map(([label, text]) => <label className="ref-field" key={label}>{label}<button type="button" disabled>{text}　⌄</button></label>)}<ActionLink href="#track-evidence-trigger">Analyze selected lap</ActionLink></div>
      <div className="ref-grid track-main">
        <Panel title="Track Map & Region Analysis" icon="⌁"><TrackPlaceholder /><ChipRow><Badge>Faster</Badge><Badge>Similar</Badge><Badge>Slower</Badge></ChipRow></Panel>
        <div className="ref-stack">
          <Panel title="Region Metrics" icon="◉" action={<Badge>Region —</Badge>}><div className="ref-metric-grid five"><Metric label="Minimum speed" unit=" km/h" /><Metric label="Apex speed" unit=" km/h" /><Metric label="Entry speed" unit=" km/h" /><Metric label="Throttle on distance" unit=" m" /><Metric label="Time delta" unit=" s" /></div></Panel>
          <div className="ref-grid track-insights">
            <Panel title="Key Insights" icon="✦"><div className="ref-empty-findings"><span>—</span><b>No region evidence loaded</b><p>Select a recorded attempt and model revision.</p></div></Panel>
            <Panel title="Region List — Time Differences" icon="▤"><PlaceholderTable headings={["#", "Turn / Region", "Time delta", "Status"]} rows={6} compact /></Panel>
          </div>
        </div>
      </div>
      <div className="ref-grid track-bottom">
        <Panel title="Region Comparison" icon="▥" action={<ChipRow><Badge>Speed</Badge><Badge>Throttle</Badge><Badge>Brake</Badge></ChipRow>}><ChartPlaceholder channels={["Speed"]} /></Panel>
        <Panel title="Observed vs Reference Trajectory" icon="▤"><div className="ref-trajectory-placeholders"><TrackPlaceholder compact /><TrackPlaceholder compact /></div><ChipRow><Badge>Current attempt —</Badge><Badge>Reference —</Badge></ChipRow></Panel>
      </div>
      <div className="ref-grid track-observations"><Panel title="Driving Pattern Observations" icon="◉"><SkeletonList rows={4} /></Panel><Panel title="Throttle & Exit Pattern Observations" icon="▥"><SkeletonList rows={4} /></Panel></div>
    </>
  );
}

function Sessions({ preservedQuery }: { preservedQuery: string }) {
  return (
    <>
      <div className="sessions-toolbar">
        <PageHeading title="Sessions" subtitle="Manage imported sessions and processing runs." icon="▤" />
        <div className="ref-session-filters"><label>Track<button type="button" disabled>All tracks　⌄</button></label><label>Session type<button type="button" disabled>All types　⌄</button></label><label>Date range<button type="button" disabled>—　◷</button></label><label className="ref-search-label">Search sessions<input disabled placeholder="Search sessions…" /></label><ActionLink screen="recordings" href="#recording-controls-trigger" preservedQuery={preservedQuery} primary>Import Session</ActionLink></div>
      </div>
      <div className="ref-grid sessions-main">
        <div className="ref-stack">
          <Panel title="Imported Sessions" icon="▤" action={<Badge>—</Badge>}><PlaceholderTable headings={["#", "Track", "Session", "Date", "Mode", "Best lap", "Laps", "Status"]} rows={8} selectedRow compact /></Panel>
          <Panel title="Processing Runs" icon="⚙" action={<Badge>All statuses</Badge>}><PlaceholderTable headings={["Run ID", "Session", "Started", "Finished", "Source", "Status"]} rows={6} compact /></Panel>
        </div>
        <div className="ref-stack">
          <Panel title="Run Details" icon="▣" action={<Badge>—</Badge>}>
            <h3 className="ref-run-id">RUN —</h3><div className="ref-selected-track"><PhotoPlaceholder /><b>—  Session unavailable</b></div>
            <Facts items={[["Track", "—"], ["Best lap", "—"], ["Session type", "—"], ["Total laps", "—"], ["Date", "—"], ["Capture source", "—"], ["Mode", "—"], ["Data size", "—"]]} />
            <div className="ref-lifecycle"><b>Processing lifecycle</b><SkeletonList rows={1} /></div>
            <div className="ref-metric-grid three"><Metric label="Attempts generated" /><Metric label="Processing time" /><Metric label="Evidence files" /></div>
          </Panel>
          <Panel title="Attempts (Laps)" icon="☷" action={<Badge>All laps</Badge>}><PlaceholderTable headings={["Lap", "Lap time", "S1", "S2", "S3", "Disposition", "Valid"]} rows={7} selectedRow compact /></Panel>
        </div>
        <div className="ref-stack">
          <Panel title="Session Summary" icon="▣"><div className="ref-session-summary"><b>—  Track unavailable</b><span>Session details unavailable</span><span>—</span></div><Facts items={[["Best lap", "—"], ["Total laps", "—"], ["Track length", "—"], ["Weather", "—"], ["Air temperature", "—"], ["Track temperature", "—"]]} /><span className="ref-button is-disabled is-primary" aria-disabled="true" title="Select a session in the detailed workflow before opening it.">Open Session</span><ChipRow><ActionLink href="#sessions-workflow-trigger">View Attempts</ActionLink><ActionLink screen="compare" preservedQuery={preservedQuery}>Compare Lap</ActionLink></ChipRow></Panel>
          <Panel title="Observation Inventory" icon="▤" action={<Badge>View all →</Badge>}><div className="ref-inventory-list">{["Lap charts", "Telemetry traces", "Track map data", "Tyre & strategy", "Event logs", "Analysis notes"].map((item) => <div key={item}><span>{item}</span><b>—</b><i>—</i></div>)}</div></Panel>
          <Panel title="Archive Status" icon="▤"><Facts items={[["Session folder", "—"], ["Size on disk", "—"], ["Compressed archive", "—"], ["Archive status", "—"], ["Last updated", "—"]]} /></Panel>
        </div>
      </div>
    </>
  );
}
