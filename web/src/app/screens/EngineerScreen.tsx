import {
  ChevronDownIcon,
  BarChartIcon,
  DatabaseIcon,
  LightbulbIcon,
  ThumbUpIcon,
  ThumbDownIcon,
  CopyClipboardIcon,
  TargetIcon,
  FlagOutlineIcon,
  PlayIcon,
  StopIcon,
  WaveIcon,
} from "../Icons";
import { FlagIT, Panel, PageHeading, Badge, Metric } from "./ScreenPrimitives";

export default function Engineer({ preservedQuery: _preservedQuery }: { preservedQuery: string }) {
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
