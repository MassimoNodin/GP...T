import {
  ChevronDownIcon,
  GearIcon,
  FileIcon,
  DatabaseIcon,
  SearchIcon,
  PauseIcon,
  PlayIcon,
  StopIcon,
  SkipBackIcon,
  SkipForwardIcon,
  TrashIcon,
  UploadIcon,
  DownloadIcon,
} from "../Icons";
import { FlagIT, Panel, Badge, ActionLink } from "./ScreenPrimitives";

export default function Recordings({ preservedQuery }: { preservedQuery: string }) {
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
