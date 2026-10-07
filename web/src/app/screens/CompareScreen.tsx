import {
  ChevronDownIcon,
  StopwatchIcon,
  BarChartIcon,
  StarIcon,
  LightbulbIcon,
  TargetIcon,
  CircuitIcon,
  TableIcon,
  ArrowUpIcon,
  ArrowDownIcon,
} from "../Icons";
import { Panel, PageHeading, Metric } from "./ScreenPrimitives";

export default function Compare({ preservedQuery }: { preservedQuery: string }) {
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
