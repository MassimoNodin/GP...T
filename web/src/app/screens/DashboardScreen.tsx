import DashboardLiveTelemetry from "../DashboardLiveTelemetry";
import {
  ChevronDownIcon,
  FuelIcon,
  ZapIcon,
  SunIcon,
  GearIcon,
  CarIcon,
  ChatBubbleIcon,
  SendIcon,
  PlayIcon,
  SignalIcon,
} from "../Icons";
import { FlagIT, Panel, Badge, ActionLink, Metric } from "./ScreenPrimitives";

export default function Dashboard({ preservedQuery }: { preservedQuery: string }) {
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

        <DashboardLiveTelemetry preservedQuery={preservedQuery} />

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
