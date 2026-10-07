import {
  ChevronDownIcon,
  FuelIcon,
  ZapIcon,
  ThermometerIcon,
  StopwatchIcon,
  GearIcon,
  CheckIcon,
  WaveIcon,
  TableIcon,
} from "../Icons";
import { FlagIT, Panel, Badge, ActionLink, Metric } from "./ScreenPrimitives";

export default function Live({ preservedQuery }: { preservedQuery: string }) {
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
