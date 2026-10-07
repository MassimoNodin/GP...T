import {
  ChevronDownIcon,
  GearIcon,
  CheckIcon,
  CrossIcon,
  FolderIcon,
  DatabaseIcon,
  RefreshIcon,
  PlayIcon,
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
  InfoIcon,
} from "../Icons";
import { Panel, PageHeading, Badge } from "./ScreenPrimitives";

export default function Settings(_props: { preservedQuery: string }) {
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
