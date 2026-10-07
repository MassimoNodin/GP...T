import {
  ChevronDownIcon,
  ZapIcon,
  StopwatchIcon,
  GearIcon,
  BarChartIcon,
  LightbulbIcon,
  CarIcon,
  MapPinIcon,
  RefreshIcon,
  CircuitIcon,
  TableIcon,
  ArrowUpRightIcon,
} from "../Icons";
import { FlagIT, Panel, Metric } from "./ScreenPrimitives";

export default function Track({ preservedQuery: _preservedQuery }: { preservedQuery: string }) {
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
