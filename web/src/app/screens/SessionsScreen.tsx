import {
  ChevronDownIcon,
  SunIcon,
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
  ChatBubbleIcon,
  TableIcon,
  UploadIcon,
  DownloadIcon,
} from "../Icons";
import { FlagIT, Panel, PageHeading, Badge, ActionLink, Metric } from "./ScreenPrimitives";

export default function Sessions({ preservedQuery }: { preservedQuery: string }) {
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
