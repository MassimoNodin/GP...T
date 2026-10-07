"use client";

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { ApiResponse } from "@/lib/api";
import { parseAnalysisInput, REGION_COLOURS } from "@/lib/analysis-display";
import { appScreenPath, type AppScreen } from "@/lib/navigation";
import {
  isTestDataScenario,
  TEST_DATA_SCENARIOS,
  type TestDataSnapshot,
  type TestLap,
} from "@/lib/test-data";
import {
  AnalysisPanel,
  LapComparisonTrace,
  signed,
  timeMs,
  TrackAnalysisMap,
} from "./AnalysisWidgets";
import {
  BarChartIcon,
  BrainIcon,
  CircuitIcon,
  DatabaseIcon,
  GearIcon,
  StopwatchIcon,
  WaveIcon,
} from "./Icons";

type Selection = { session: string; target: string; reference: string };
const titles: Partial<Record<AppScreen, string>> = {
  dashboard: "Dashboard",
  live: "Live Telemetry",
  sessions: "Sessions",
  compare: "Lap Comparison",
  track: "Track Analysis",
  engineer: "AI Engineer",
  recordings: "Recordings",
  settings: "Settings",
};
const number = (value: number | null | undefined, suffix = "", decimals = 0) =>
  value == null
    ? "—"
    : `${value.toLocaleString("en-US", { maximumFractionDigits: decimals })}${suffix}`;
const bytes = (value: number | null | undefined) =>
  value == null ? "—" : `${(value / 1e9).toFixed(2)} GB`;

export default function TestDataWorkspace({
  screen,
  scenario,
  initialSelection,
}: {
  screen: AppScreen;
  scenario: string;
  initialSelection: Selection;
}) {
  const selection = initialSelection;
  const [tick, setTick] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [result, setResult] = useState<{
    identity: string;
    data: TestDataSnapshot;
  } | null>(null);
  const [failure, setFailure] = useState<{
    key: string;
    message: string;
  } | null>(null);
  const requestSequence = useRef(0);
  const identity = JSON.stringify([scenario, selection]);
  const requestKey = JSON.stringify([identity, tick, refresh]);
  const validScenario = isTestDataScenario(scenario);

  useEffect(() => {
    if (!validScenario) return;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8000);
    const sequence = ++requestSequence.current;
    const query = new URLSearchParams({ scenario, tick: String(tick) });
    for (const [key, value] of Object.entries(selection))
      if (value) query.set(key, value);
    async function load() {
      try {
        const response = await fetch(`/api/test-data?${query}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload: ApiResponse<TestDataSnapshot> = await response.json();
        if (
          !response.ok ||
          payload.api_version !== "v1" ||
          payload.status !== "ok" ||
          !payload.data
        )
          throw new Error(payload.reason ?? "Test API unavailable.");
        const data = payload.data;
        if (
          data.schema_version !== 1 ||
          data.source.kind !== "synthetic" ||
          data.source.fixture_version !== "ui-test-v1" ||
          data.source.scenario !== scenario ||
          data.source.tick !== tick ||
          data.diagnostic_only !== true ||
          data.coaching_eligible !== false ||
          data.ranking_eligible !== false ||
          data.sessions.length > 3 ||
          data.laps.length > 12 ||
          (selection.session &&
            data.selected.sessionId !== selection.session) ||
          (selection.target && data.selected.targetId !== selection.target) ||
          (selection.reference &&
            data.selected.referenceId !== selection.reference)
        )
          throw new Error("Test API selection or source did not match.");
        data.analysis =
          scenario === "empty"
            ? { version: 1 }
            : parseAnalysisInput(data.analysis);
        if (
          sequence === requestSequence.current &&
          !controller.signal.aborted
        ) {
          setResult({ identity, data });
          setFailure(null);
        }
      } catch (cause) {
        if (sequence === requestSequence.current) {
          setResult(null);
          setPlaying(false);
          setFailure({
            key: requestKey,
            message: controller.signal.aborted
              ? "Test API request timed out."
              : cause instanceof Error
                ? cause.message
                : "Test API unavailable.",
          });
        }
      } finally {
        window.clearTimeout(timeout);
      }
    }
    void load();
    return () => {
      requestSequence.current++;
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, [scenario, validScenario, selection, tick, refresh, requestKey, identity]);

  useEffect(() => {
    if (
      !playing ||
      !validScenario ||
      tick === 3600 ||
      scenario === "unavailable" ||
      scenario === "empty" ||
      scenario === "stale"
    )
      return;
    if (
      result?.identity !== identity ||
      result.data.source.tick !== tick ||
      failure?.key === requestKey
    )
      return;
    const timeout = window.setTimeout(
      () => setTick((value) => Math.min(3600, value + 1)),
      1000,
    );
    return () => window.clearTimeout(timeout);
  }, [
    playing,
    scenario,
    validScenario,
    tick,
    result,
    identity,
    failure,
    requestKey,
  ]);

  const data = result?.identity === identity ? result.data : null;
  const error = !validScenario
    ? "Choose a valid test-data scenario."
    : failure?.key === requestKey
      ? failure.message
      : null;
  const stateQuery = (next: Selection) =>
    new URLSearchParams({
      test_data: scenario,
      ...(next.session ? { test_session: next.session } : {}),
      ...(next.target ? { test_target: next.target } : {}),
      ...(next.reference ? { test_reference: next.reference } : {}),
    });
  const href = (destination: AppScreen) =>
    `${appScreenPath(destination)}?${stateQuery(selection)}`;
  function choose(next: Partial<Selection>) {
    setPlaying(false);
    window.location.assign(
      `${appScreenPath(screen)}?${stateQuery({ ...selection, ...next })}`,
    );
  }
  function reset() {
    setPlaying(false);
    setResult(null);
    setFailure(null);
    setTick(0);
    if (Object.values(selection).some(Boolean)) {
      window.location.assign(
        `${appScreenPath(screen)}?test_data=${encodeURIComponent(scenario)}`,
      );
      return;
    }
    setRefresh((value) => value + 1);
  }

  return (
    <>
      <section
        className="test-data-controls"
        aria-label="API test data controls"
      >
        <div>
          <strong>Synthetic API test data</strong>
          <small>
            Examples only · no real capture, import, model inference or saved
            settings
          </small>
        </div>
        <label>
          Scenario
          <select
            value={validScenario ? scenario : ""}
            onChange={(event) => {
              window.location.assign(
                `${appScreenPath(screen)}?test_data=${event.target.value}`,
              );
            }}
          >
            {!validScenario ? <option value="">Choose scenario</option> : null}
            {TEST_DATA_SCENARIOS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <span>
          Tick <b>{tick}</b>
        </span>
        <button
          className="ref-button"
          disabled={
            !validScenario ||
            ["empty", "stale", "unavailable"].includes(scenario) ||
            tick === 3600
          }
          onClick={() => setPlaying((value) => !value)}
        >
          {playing ? "Pause test feed" : "Play test feed"}
        </button>
        <button
          className="ref-button"
          disabled={!validScenario || tick === 3600}
          onClick={() => {
            setPlaying(false);
            setTick((value) => Math.min(3600, value + 1));
          }}
        >
          Step test feed
        </button>
        <button className="ref-button" onClick={reset}>
          Reset test data
        </button>
        <a className="ref-button" href={appScreenPath(screen)}>
          Exit test mode
        </a>
      </section>
      {error ? (
        <div className="test-data-message" role="alert">
          <strong>Test data unavailable</strong>
          <span>{error}</span>
          <button
            className="ref-button"
            onClick={() => setRefresh((value) => value + 1)}
          >
            Retry API
          </button>
        </div>
      ) : !data ? (
        <div className="test-data-message" role="status">
          Loading selected data from /api/test-data…
        </div>
      ) : (
        <>
          <div
            className="test-data-selection"
            aria-label="Test session and lap selection"
          >
            <label>
              Session
              <select
                value={data.selected.sessionId ?? ""}
                disabled={!data.sessions.length}
                onChange={(e) =>
                  choose({ session: e.target.value, target: "", reference: "" })
                }
              >
                {!data.sessions.length ? (
                  <option value="">No test sessions</option>
                ) : null}
                {data.sessions.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.track} · {s.type}
                  </option>
                ))}
              </select>
            </label>
            <LapSelect
              label="Target lap"
              value={data.selected.targetId}
              laps={data.laps}
              onChange={(target) => choose({ target })}
            />
            <LapSelect
              label="Reference lap"
              value={data.selected.referenceId}
              laps={data.laps}
              onChange={(reference) => choose({ reference })}
            />
            <small>
              {data.source.fixture_version} · {data.source.at_utc}
            </small>
            <a
              className="ref-button"
              href={`/api/test-data?${new URLSearchParams({ scenario, tick: String(tick), ...(data.selected.sessionId ? { session: data.selected.sessionId } : {}), ...(data.selected.targetId ? { target: data.selected.targetId } : {}), ...(data.selected.referenceId ? { reference: data.selected.referenceId } : {}) })}`}
              target="_blank"
              rel="noreferrer"
            >
              View API JSON
            </a>
          </div>
          {scenario === "empty" ? (
            <div className="test-data-empty" role="status">
              Empty scenario: no sessions, recordings, telemetry or analysis are
              supplied.
            </div>
          ) : null}
          <TestDataPage
          key={`${identity}:${refresh}`}
            screen={screen}
            data={data}
            href={href}
            choose={choose}
          />
        </>
      )}
    </>
  );
}

function LapSelect({
  label,
  value,
  laps,
  onChange,
}: {
  label: string;
  value: string | null;
  laps: TestLap[];
  onChange: (value: string) => void;
}) {
  return (
    <label>
      {label}
      <select
        value={value ?? ""}
        disabled={!laps.length}
        onChange={(e) => onChange(e.target.value)}
      >
        {!laps.length ? <option value="">No laps</option> : null}
        {laps.map((lap) => (
          <option key={lap.id} value={lap.id}>
            Lap {lap.number} · {timeMs(lap.timeMs)}
            {lap.valid ? "" : " · invalid fixture"}
          </option>
        ))}
      </select>
    </label>
  );
}
function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="test-data-facts">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="test-data-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function TestDataPage({
  screen,
  data,
  href,
  choose,
}: {
  screen: AppScreen;
  data: TestDataSnapshot;
  href: (screen: AppScreen) => string;
  choose: (value: Partial<Selection>) => void;
}) {
  const [regionId, setRegionId] = useState<string | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const session = data.sessions.find((s) => s.id === data.selected.sessionId);
  const target = data.laps.find((lap) => lap.id === data.selected.targetId);
  const reference = data.laps.find(
    (lap) => lap.id === data.selected.referenceId,
  );
  const shape = data.analysis.track;
  const region =
    shape?.regions.find((r) => r.id === regionId) ?? shape?.regions[0];
  const selectRegion = (id: string) => {
    setRegionId(id);
    setCursor(null);
  };
  const map = (
    <AnalysisPanel
      title="Track Map & Region Analysis"
      icon={<CircuitIcon size={22} />}
      action={
        <a className="ref-button" href={href("track")}>
          Track analysis
        </a>
      }
    >
      {shape ? (
        <TrackAnalysisMap
          shape={shape}
          selectedRegionId={region?.id}
          onSelectRegion={selectRegion}
          cursorDistanceM={cursor}
        />
      ) : (
        <Empty label="No track shape supplied" />
      )}
      <p className="analysis-source-note">
        Supplied synthetic geometry and classifications from the test API.
      </p>
    </AnalysisPanel>
  );
  const summary = (
    <AnalysisPanel
      title="Selected Lap Summary"
      icon={<StopwatchIcon size={22} />}
    >
      <div className="test-data-metrics">
        <Metric
          label={`Target lap ${target?.number ?? "—"}`}
          value={timeMs(target?.timeMs)}
        />
        <Metric
          label={`Reference lap ${reference?.number ?? "—"}`}
          value={timeMs(reference?.timeMs)}
        />
        <Metric
          label="Lap delta"
          value={
            target && reference
              ? `${signed((target.timeMs - reference.timeMs) / 1000)} s`
              : "—"
          }
        />
      </div>
      <Facts
        rows={[
          ["Track", session?.track ?? "—"],
          ["Tyre", target?.tyre ?? "—"],
          [
            "Validity",
            target ? (target.valid ? "Valid fixture" : "Invalid fixture") : "—",
          ],
          ...[0, 1, 2].map((i): [string, ReactNode] => [
            `Sector ${i + 1}`,
            timeMs(target?.sectorsMs[i]),
          ]),
        ]}
      />
    </AnalysisPanel>
  );
  const lapTable = (
    <AnalysisPanel
      title="Attempts (Laps)"
      icon={<DatabaseIcon size={22} />}
      action={
        <a className="ref-button" href={href("compare")}>
          Compare selected laps
        </a>
      }
    >
      <div className="ref-table-wrap">
        <table className="ref-table">
          <thead>
            <tr>
              <th>Lap</th>
              <th>Time</th>
              <th>S1</th>
              <th>S2</th>
              <th>S3</th>
              <th>Disposition</th>
              <th>Validity</th>
            </tr>
          </thead>
          <tbody>
            {data.laps.map((lap) => (
              <tr
                key={lap.id}
                className={lap.id === target?.id ? "is-selected" : ""}
              >
                <td>
                  <button
                    className="test-data-row-button"
                    aria-pressed={lap.id === target?.id}
                    onClick={() => choose({ target: lap.id })}
                  >
                    Lap {lap.number}
                  </button>
                </td>
                <td>{timeMs(lap.timeMs)}</td>
                {lap.sectorsMs.map((ms, i) => (
                  <td key={i}>{timeMs(ms)}</td>
                ))}
                <td>{lap.disposition}</td>
                <td>{lap.valid ? "Valid" : "Invalid"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </AnalysisPanel>
  );
  const captures = (
    <CaptureCatalog
      data={data}
      onSelect={(sessionId) =>
        choose({ session: sessionId, target: "", reference: "" })
      }
    />
  );
  const trace = (
    <AnalysisPanel
      title={
        screen === "track"
          ? `Region Comparison – ${region?.label ?? "—"}`
          : "Lap Comparison Trace"
      }
      icon={<BarChartIcon size={22} />}
      action={
        <a className="ref-button" href={href("compare")}>
          Lap comparison
        </a>
      }
    >
      <LapComparisonTrace
        input={data.analysis.comparison ?? null}
        windowM={
          screen === "track" && region ? [region.startM, region.endM] : null
        }
        onCursor={setCursor}
        compact={screen === "dashboard"}
      />
      <p className="analysis-source-note">
        Generated API samples · values and gaps are synthetic.
      </p>
    </AnalysisPanel>
  );
  const recording = <RecordingSummary data={data} />;
  const runtime = (
    <AnalysisPanel
      title="AI Race Engineer"
      icon={<BrainIcon size={22} />}
      action={
        <a className="ref-button" href={href("engineer")}>
          Open AI Engineer
        </a>
      }
    >
      <Facts
        rows={[
          ["Provider", data.runtime?.provider ?? "—"],
          ["Model", data.runtime?.model ?? "—"],
          ["Status", data.runtime?.status ?? "—"],
        ]}
      />
      <p className="test-data-answer">
        {data.engineer?.answer ?? "No Engineer example supplied."}
      </p>
    </AnalysisPanel>
  );

  if (screen === "settings") return <TestSettings data={data} />;
  return (
    <section
      className={`reference-screen test-data-screen test-data-${screen}`}
      aria-label={`${titles[screen]} test data screen`}
    >
      {screen !== "dashboard" ? (
        <div className="ref-page-title">
          <h1>{titles[screen]}</h1>
          <p>Populated from the synthetic test API</p>
        </div>
      ) : null}
      {screen === "dashboard" ? (
        <>
          <div className="ref-grid test-data-three">
            {recording}
            <Telemetry data={data} />
            {runtime}
          </div>
          <div className="ref-grid test-data-three">
            {lapTable}
            {summary}
            {map}
          </div>
          <div className="ref-grid test-data-two">
            {captures}
            {trace}
          </div>
        </>
      ) : null}
      {screen === "live" ? (
        <>
          <div className="ref-grid test-data-three">
            <AnalysisPanel
              title="Session Information"
              icon={<CircuitIcon size={22} />}
            >
              <Facts
                rows={[
                  ["Track", session?.track ?? "—"],
                  ["Session", session?.type ?? "—"],
                  ["Track length", number(session?.lengthM, " m")],
                  ["Air temperature", number(data.live?.airC, "°C")],
                  ["Track temperature", number(data.live?.trackC, "°C")],
                  ["Source", "Synthetic API feed"],
                ]}
              />
            </AnalysisPanel>
            <Telemetry data={data} />
            <AnalysisPanel
              title="Telemetry Data Status"
              icon={<DatabaseIcon size={22} />}
            >
              <Facts
                rows={[
                  ["State", data.live?.status ?? "Unavailable"],
                  ["Age", number(data.live?.ageMs, " ms")],
                  [
                    "Samples",
                    number(data.analysis.comparison?.distanceM.length),
                  ],
                  [
                    "Gaps",
                    data.source.scenario === "partial"
                      ? "Deliberately missing intervals"
                      : "No fixture gaps",
                  ],
                  ["Frame", number(data.source.tick)],
                ]}
              />
            </AnalysisPanel>
          </div>
          <div className="ref-grid test-data-three">
            {summary}
            <AnalysisPanel title="Tyres & Car Status">
              <Facts
                rows={(
                  data.live?.tyreWearPercent ?? [null, null, null, null]
                ).map(
                  (value, i) =>
                    [
                      `${["FL", "FR", "RL", "RR"][i]} wear`,
                      data.live?.status === "fresh"
                        ? number(value, "%", 1)
                        : "—",
                    ] as [string, ReactNode],
                )}
              />
              <Facts
                rows={(
                  data.live?.tyreTemperatureC ?? [null, null, null, null]
                ).map(
                  (value, i) =>
                    [
                      `${["FL", "FR", "RL", "RR"][i]} temperature`,
                      data.live?.status === "fresh" ? number(value, "°C") : "—",
                    ] as [string, ReactNode],
                )}
              />
            </AnalysisPanel>
            {map}
          </div>
          <div className="ref-grid test-data-two">
            {trace}
            {lapTable}
          </div>
        </>
      ) : null}
      {screen === "sessions" ? (
        <div className="ref-grid test-data-sessions">
          <div className="ref-stack">
            <SessionTable data={data} choose={choose} />
            <AnalysisPanel title="Processing Runs">
              <Facts
                rows={data.sessions.map((s) => [
                  s.runId,
                  `${s.track} · ${s.status}`,
                ])}
              />
            </AnalysisPanel>
          </div>
          <div className="ref-stack">
            <AnalysisPanel title="Run Details">
              <Facts
                rows={[
                  ["Run", session?.runId ?? "—"],
                  ["Track", session?.track ?? "—"],
                  ["Started", session?.startedAt ?? "—"],
                  ["Best lap", timeMs(session?.bestTimeMs)],
                  ["Attempts", number(session?.lapCount)],
                ]}
              />
              <ol className="test-data-lifecycle">
                {data.lifecycle.map((step) => (
                  <li key={step.label}>
                    <b>{step.label}</b>
                    <span>
                      {step.at} · {step.status}
                    </span>
                  </li>
                ))}
              </ol>
            </AnalysisPanel>
            {lapTable}
          </div>
          <div className="ref-stack">
            {summary}
            <AnalysisPanel title="Observation Inventory">
              <Facts
                rows={data.inventory.map((item) => [
                  item.label,
                  item.count === null
                    ? item.status
                    : `${item.count} files · ${item.status}`,
                ])}
              />
            </AnalysisPanel>
            <AnalysisPanel title="Archive Status">
              <Facts
                rows={[
                  ["Namespace", session?.id ?? "—"],
                  ["Archive", "In-memory fixture only"],
                  ["Stored files", "None created"],
                ]}
              />
            </AnalysisPanel>
          </div>
        </div>
      ) : null}
      {screen === "recordings" ? (
        <>
          {recording}
          <div className="ref-grid test-data-two">
            {captures}
            <AnalysisPanel title="Replay Preview" icon={<WaveIcon size={22} />}>
              <div className="test-data-replay-map">
                {shape ? (
                  <TrackAnalysisMap
                    shape={shape}
                    cursorDistanceM={data.live?.distanceM}
                  />
                ) : (
                  <Empty label="No playback shape" />
                )}
              </div>
              <Facts
                rows={[
                  [
                    "Capture",
                    data.captures.find((c) => c.sessionId === session?.id)
                      ?.name ?? "—",
                  ],
                  ["Distance", number(data.live?.distanceM, " m")],
                  [
                    "Speed",
                    data.live?.status === "fresh"
                      ? number(data.live.speedKph, " km/h")
                      : "—",
                  ],
                  ["Playback", "Use Play / Pause / Step test feed above"],
                ]}
              />
              <p className="analysis-source-note">
                Synthetic telemetry playback; no game video or real replay
                controller.
              </p>
            </AnalysisPanel>
          </div>
          <div className="ref-grid test-data-two">
            <AnalysisPanel title="Import Jobs">
              <Facts
                rows={data.imports.map((job) => [
                  job.name,
                  `${job.status} · ${number(job.progress, "%")}`,
                ])}
              />
              {data.imports.map((job) => (
                <progress
                  key={job.id}
                  aria-label={`${job.name} simulated progress`}
                  value={job.progress ?? undefined}
                  max={100}
                />
              ))}
            </AnalysisPanel>
            <AnalysisPanel title="Selected Recording Details">
              <Facts
                rows={[
                  [
                    "Capture",
                    data.captures.find((c) => c.sessionId === session?.id)
                      ?.name ?? "—",
                  ],
                  [
                    "Size",
                    bytes(
                      data.captures.find((c) => c.sessionId === session?.id)
                        ?.bytes,
                    ),
                  ],
                  ["Laps", number(session?.lapCount)],
                  [
                    "Packet loss",
                    number(
                      data.captures.find((c) => c.sessionId === session?.id)
                        ?.packetLossPercent,
                      "%",
                      1,
                    ),
                  ],
                  [
                    "Quality",
                    data.source.scenario === "partial"
                      ? "Partial fixture"
                      : "Synthetic fixture",
                  ],
                ]}
              />
              <a className="ref-button" href={href("sessions")}>
                Inspect test session
              </a>
            </AnalysisPanel>
          </div>
        </>
      ) : null}
      {screen === "engineer" ? (
        <>
          <div className="test-data-intents">
            {[
              "General debrief",
              "Region comparison",
              "Strategy unavailable",
              "Tyre coaching unavailable",
            ].map((label) => (
              <span key={label}>{label}</span>
            ))}
          </div>
          <div className="ref-grid test-data-two">
            <AnalysisPanel
              title="Example Conversation"
              icon={<BrainIcon size={22} />}
            >
              <div className="test-data-question">
                {data.engineer?.question ?? "No example question"}
              </div>
              <p className="test-data-answer">
                {data.engineer?.answer ?? "No example answer"}
              </p>
              {region ? (
                <Facts
                  rows={[
                    ["Selected region", region.label],
                    [
                      "Supplied region delta",
                      region.deltaS == null
                        ? "—"
                        : `${signed(region.deltaS)} s`,
                    ],
                    [
                      "Minimum speed",
                      number(region.minimumSpeedKph, " km/h", 1),
                    ],
                  ]}
                />
              ) : null}
              <ul className="analysis-evidence">
                {data.engineer?.limitations.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
              <p className="analysis-source-note">
                These are deterministic API examples. Free-text Ask and
                microphone are available in real-data mode.
              </p>
            </AnalysisPanel>
            <div className="ref-stack">
              {runtime}
              <AnalysisPanel title="Voice Response">
                <Facts
                  rows={[
                    ["Profile", data.runtime?.voice ?? "—"],
                    ["Playback", "No audio generated by fixture API"],
                  ]}
                />
              </AnalysisPanel>
              {map}
            </div>
          </div>
          {trace}
        </>
      ) : null}
      {screen === "compare" ? (
        <>
          <div className="ref-grid test-data-two">
            {summary}
            <AnalysisPanel title="Reference Selection">
              <Facts
                rows={[
                  ["Target", target ? `Lap ${target.number}` : "—"],
                  ["Reference", reference ? `Lap ${reference.number}` : "—"],
                  [
                    "Selection",
                    "Choose target/reference in API controls above",
                  ],
                  ["Authority", "Synthetic diagnostic data only"],
                ]}
              />
            </AnalysisPanel>
          </div>
          <div className="ref-grid test-data-compare">
            {trace}
            {map}
          </div>
          <div className="ref-grid test-data-two">
            {lapTable}
            <RegionTable
              data={data}
              selected={region?.id}
              onSelect={selectRegion}
            />
          </div>
        </>
      ) : null}
      {screen === "track" ? (
        <>
          <div className="ref-grid test-data-compare">
            {map}
            <div className="ref-stack">
              <AnalysisPanel title={`Region Metrics – ${region?.label ?? "—"}`}>
                <Facts
                  rows={[
                    [
                      "Delta",
                      region?.deltaS == null
                        ? "—"
                        : `${signed(region.deltaS)} s`,
                    ],
                    ["Classification", region?.status ?? "—"],
                    [
                      "Minimum speed",
                      number(region?.minimumSpeedKph, " km/h", 1),
                    ],
                    [
                      "Reference speed",
                      number(region?.referenceMinimumSpeedKph, " km/h", 1),
                    ],
                    [
                      "Throttle pickup",
                      number(region?.throttlePickupM, " m", 1),
                    ],
                    [
                      "Window",
                      region
                        ? `${number(region.startM)}–${number(region.endM)} m`
                        : "—",
                    ],
                  ]}
                />
              </AnalysisPanel>
              <RegionTable
                data={data}
                selected={region?.id}
                onSelect={selectRegion}
              />
            </div>
          </div>
          {trace}
        </>
      ) : null}
    </section>
  );
}

function Empty({ label }: { label: string }) {
  return (
    <div className="analysis-empty">
      <strong>{label}</strong>
      <span>Choose another API scenario to supply data.</span>
    </div>
  );
}
function RegionTable({
  data,
  selected,
  onSelect,
}: {
  data: TestDataSnapshot;
  selected?: string;
  onSelect: (id: string) => void;
}) {
  return (
    <AnalysisPanel title="Region List – Comparisons">
      <div className="ref-table-wrap">
        <table className="ref-table">
          <thead>
            <tr>
              <th>Turn / Region</th>
              <th>Delta</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {data.analysis.track?.regions.map((region) => (
              <tr
                key={region.id}
                className={selected === region.id ? "is-selected" : ""}
              >
                <td>
                  <button
                    className="test-data-row-button"
                    aria-pressed={selected === region.id}
                    onClick={() => onSelect(region.id)}
                  >
                    {region.label}
                  </button>
                </td>
                <td>
                  {region.deltaS == null ? "—" : `${signed(region.deltaS)} s`}
                </td>
                <td>
                  <i
                    className="analysis-status-dot"
                    style={{ background: REGION_COLOURS[region.status] }}
                  />
                  {region.status}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </AnalysisPanel>
  );
}
function SessionTable({
  data,
  choose,
}: {
  data: TestDataSnapshot;
  choose: (next: Partial<Selection>) => void;
}) {
  const [query, setQuery] = useState("");
  const sessions = data.sessions.filter((session) =>
    session.track.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <AnalysisPanel title="Imported Sessions">
      <label className="test-data-search">
        Search test sessions
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Track name"
        />
      </label>
      <div className="ref-table-wrap">
        <table className="ref-table">
          <thead>
            <tr>
              <th>Track</th>
              <th>Mode</th>
              <th>Best Lap</th>
              <th>Laps</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((session) => (
              <tr
                key={session.id}
                className={
                  session.id === data.selected.sessionId ? "is-selected" : ""
                }
              >
                <td>
                  <button
                    className="test-data-row-button"
                    onClick={() =>
                      choose({ session: session.id, target: "", reference: "" })
                    }
                    aria-pressed={session.id === data.selected.sessionId}
                  >
                    {session.track}
                  </button>
                </td>
                <td>{session.type}</td>
                <td>{timeMs(session.bestTimeMs)}</td>
                <td>{session.lapCount}</td>
                <td>{session.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </AnalysisPanel>
  );
}
function CaptureCatalog({
  data,
  onSelect,
}: {
  data: TestDataSnapshot;
  onSelect: (sessionId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const captures = data.captures.filter((capture) =>
    capture.name.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <AnalysisPanel title="Recorded Captures" icon={<DatabaseIcon size={22} />}>
      <label className="test-data-search">
        Search test recordings
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Capture name"
        />
      </label>
      <div className="ref-table-wrap">
        <table className="ref-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Laps</th>
              <th>Size</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {captures.map((capture) => (
              <tr
                key={capture.id}
                className={
                  capture.sessionId === data.selected.sessionId
                    ? "is-selected"
                    : ""
                }
              >
                <td>
                  <button
                    className="test-data-row-button"
                    onClick={() => onSelect(capture.sessionId)}
                    aria-pressed={capture.sessionId === data.selected.sessionId}
                  >
                    {capture.name}
                  </button>
                </td>
                <td>{capture.laps}</td>
                <td>{bytes(capture.bytes)}</td>
                <td>{capture.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </AnalysisPanel>
  );
}
function RecordingSummary({ data }: { data: TestDataSnapshot }) {
  return (
    <AnalysisPanel
      title="Recording & Capture (Simulated)"
      icon={<span className="dot-red" />}
    >
      <div className="test-data-recording">
        <Metric
          label={data.recording?.status ?? "No fixture capture"}
          value={timeMs(data.recording?.elapsedMs)}
        />
        <Metric
          label="Packets received"
          value={number(data.recording?.packets)}
        />
        <Metric
          label="Queue drops"
          value={number(data.recording?.queueDrops)}
        />
        <Metric label="File size" value={bytes(data.recording?.bytes)} />
        <Metric
          label="Packet rate"
          value={number(data.recording?.rate, " pkt/s")}
        />
      </div>
      <p className="analysis-source-note">
        The fixture clock changes these API counters. No UDP socket or recording
        file is opened.
      </p>
    </AnalysisPanel>
  );
}
function Telemetry({ data }: { data: TestDataSnapshot }) {
  const live = data.live?.status === "fresh" ? data.live : null;
  return (
    <AnalysisPanel
      title="Live Telemetry"
      icon={<WaveIcon size={24} />}
      action={
        <span className="ref-badge">
          Synthetic · {data.live?.status ?? "unavailable"}
        </span>
      }
    >
      <div className="live-mock-gauge">
        <svg viewBox="0 0 500 125" aria-hidden="true">
          {Array.from({ length: 26 }, (_, i) => {
            const a = i / 26,
              b = (i + 0.65) / 26;
            return (
              <path
                key={i}
                d={`M${22 + a * 456},${67 - Math.sin(a * Math.PI) * 52}L${22 + b * 456},${67 - Math.sin(b * Math.PI) * 52}`}
                stroke={
                  live?.rpm != null && a <= live.rpm / 15000
                    ? a > 0.62
                      ? "#ff172b"
                      : "#0068ff"
                    : "#e5ebf3"
                }
                strokeWidth="5"
                strokeLinecap="round"
              />
            );
          })}
        </svg>
        <div className="live-mock-readouts">
          <div>
            <strong>{number(live?.speedKph)}</strong>
            <span>KM/H</span>
          </div>
          <div>
            <span>Gear</span>
            <strong className="live-gear">{number(live?.gear)}</strong>
          </div>
          <div>
            <strong className="live-rpm">{number(live?.rpm)}</strong>
            <span>RPM</span>
          </div>
        </div>
      </div>
      <div className="live-mock-lower">
        <div className="live-input-bars">
          {(
            [
              ["Throttle", live?.throttle, "#00c65b"],
              ["Brake", live?.brake, "#ff172b"],
            ] as const
          ).map(([label, value, color]) => (
            <div className="test-data-pedal" key={label}>
              <span>{label}</span>
              <div>
                <i
                  style={{ width: `${(value ?? 0) * 100}%`, background: color }}
                />
              </div>
              <b>{number(value == null ? null : value * 100, "%")}</b>
            </div>
          ))}
        </div>
        <Facts
          rows={[
            ["Fuel", number(live?.fuelReported, " reported", 1)],
            ["ERS", number(live?.ersPercent, "%")],
            [
              "DRS",
              live?.drs == null
                ? "—"
                : live.drs
                  ? "Active fixture"
                  : "Inactive fixture",
            ],
            ["Age", number(data.live?.ageMs, " ms")],
          ]}
        />
      </div>
      {data.live?.status === "stale" ? (
        <p className="analysis-source-note">
          Stale API scenario: telemetry values are hidden.
        </p>
      ) : null}
    </AnalysisPanel>
  );
}
function TestSettings({ data }: { data: TestDataSnapshot }) {
  return (
    <section
      className="reference-screen test-data-screen test-data-settings"
      aria-label="Settings test data screen"
    >
      <div className="ref-page-title">
        <h1>Settings</h1>
        <p>Read-only configuration examples supplied by the API</p>
      </div>
      <div className="ref-grid test-data-two">
        <AnalysisPanel title="AI Settings" icon={<BrainIcon size={22} />}>
          <Facts
            rows={[
              ["Local model", data.runtime?.model ?? "—"],
              ["Provider", data.runtime?.provider ?? "—"],
              ["Runtime state", data.runtime?.status ?? "—"],
              ["Inference", "No model is called by test mode"],
            ]}
          />
        </AnalysisPanel>
        <AnalysisPanel title="Voice & Audio">
          <Facts
            rows={[
              ["Profile", data.runtime?.voice ?? "—"],
              ["Speed", number(data.settings?.voiceSpeed, "×", 1)],
              ["Volume", number(data.settings?.voiceVolume, "%")],
              ["Microphone", "Not requested in test mode"],
            ]}
          />
        </AnalysisPanel>
        <AnalysisPanel title="Telemetry" icon={<WaveIcon size={22} />}>
          <Facts
            rows={[
              ["UDP host", data.settings?.udpHost ?? "—"],
              ["Port", number(data.settings?.udpPort)],
              ["Queue", number(data.settings?.queueSize)],
              ["State", "Fixture values; no listener started"],
            ]}
          />
        </AnalysisPanel>
        <AnalysisPanel title="Devices" icon={<GearIcon size={22} />}>
          <Facts
            rows={[
              ["Device examples", data.settings?.devices.join(", ") || "—"],
              ["Discovery", "No hardware queried"],
              ["Assignments", "Read-only examples"],
            ]}
          />
        </AnalysisPanel>
        <AnalysisPanel title="Browser HUD">
          <Facts
            rows={[
              ["Position", data.settings?.hudPosition ?? "—"],
              ["Opacity", number(data.settings?.hudOpacity, "%")],
              ["Theme", data.settings?.hudTheme ?? "—"],
              ["Persistence", "Real HUD preferences are not modified"],
            ]}
          />
        </AnalysisPanel>
        <AnalysisPanel title="Storage" icon={<DatabaseIcon size={22} />}>
          <Facts
            rows={[
              ["Synthetic managed usage", bytes(data.storage?.usedBytes)],
              ["Test budget", bytes(data.storage?.budgetBytes)],
              ...(data.storage?.categories.map(
                (category): [string, ReactNode] => [
                  category.label,
                  bytes(category.bytes),
                ],
              ) ?? []),
            ]}
          />
          <p className="analysis-source-note">
            API-generated budget and sizes, not a measurement of this computer.
          </p>
        </AnalysisPanel>
        <AnalysisPanel title="General">
          <Facts
            rows={[
              ["Data source", data.source.kind],
              ["Fixture version", data.source.fixture_version],
              ["Scenario", data.source.scenario],
              [
                "Capabilities",
                "Read-only examples and simulated feed controls",
              ],
            ]}
          />
        </AnalysisPanel>
      </div>
    </section>
  );
}
