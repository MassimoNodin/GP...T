"use client";

import type { LiveSource, PinnedLiveSnapshot } from "@/lib/live";
import { isPinnedLiveActive } from "@/lib/live";
import {
  usePinnedLiveCurrent,
  type PinnedLiveViewStatus,
} from "@/lib/use-pinned-live-current";
import { AnalysisPanel, timeMs } from "./AnalysisWidgets";
import {
  CarIcon,
  CircuitIcon,
  DatabaseIcon,
  StopwatchIcon,
  TableIcon,
  WaveIcon,
} from "./Icons";
import LiveTelemetryChart from "./LiveTelemetryChart";

export default function LiveTelemetryScreen({
  initialStatus,
  initialSnapshot,
  source,
  operationId,
  recordingsHref,
}: {
  initialStatus: PinnedLiveViewStatus;
  initialSnapshot: PinnedLiveSnapshot | null;
  source: LiveSource | null;
  operationId: string | null;
  recordingsHref: string | null;
}) {
  const { status, snapshot } = usePinnedLiveCurrent({
    initialStatus,
    initialSnapshot,
    source,
    operationId,
  });
  const active =
    status === "ready" && snapshot !== null && isPinnedLiveActive(snapshot);
  const fresh = <T extends { status: string }>(
    monitor: T | null | undefined,
  ): T | null => (active && monitor?.status === "fresh" ? monitor : null);
  const telemetry = fresh(snapshot?.live_telemetry),
    car = fresh(snapshot?.live_car_status),
    damage = fresh(snapshot?.live_car_damage),
    timing = fresh(snapshot?.live_lap_timing),
    conditions = fresh(snapshot?.live_session_conditions),
    history = fresh(snapshot?.live_session_history);
  const rows = history?.rows ?? [];
  const best = rows
    .filter((r) => r.lap_valid && r.lap_time_available && r.lap_time_ms > 0)
    .reduce<(typeof rows)[number] | null>(
      (previous, row) =>
        !previous || row.lap_time_ms < previous.lap_time_ms ? row : previous,
      null,
    );
  const tyres = [
    { label: "FL", index: 2 },
    { label: "FR", index: 3 },
    { label: "RL", index: 0 },
    { label: "RR", index: 1 },
  ];
  const monitors = [
    { label: "Player Telemetry", monitor: snapshot?.live_telemetry },
    { label: "Car Status", monitor: snapshot?.live_car_status },
    { label: "Lap Timing", monitor: snapshot?.live_lap_timing },
    { label: "Track Data", monitor: snapshot?.live_motion },
    { label: "Tyre & Wear", monitor: snapshot?.live_car_damage },
    { label: "Session History", monitor: snapshot?.live_session_history },
    { label: "Weather Data", monitor: snapshot?.live_session_conditions },
  ];
  return (
    <section
      className="reference-screen reference-live dynamic-live"
      aria-label="Live telemetry screen"
    >
      {!active ? (
        <div className="analysis-live-notice" role="status">
          <span>
            {status === "unselected"
              ? "Start a recording or replay, then open its live telemetry."
              : status === "replaced"
                ? "The selected operation was replaced. Its telemetry has been cleared."
                : status === "error"
                  ? "The selected source could not be refreshed. Retrying."
                  : status === "ready"
                    ? "This operation has ended. Live values have been cleared."
                    : "The selected live source is unavailable."}
          </span>
          {recordingsHref ? (
            <a className="ref-button" href={recordingsHref}>
              Open Recordings
            </a>
          ) : null}
        </div>
      ) : null}
      <div className="ref-grid live-top">
        <AnalysisPanel
          title="Session Information"
          icon={<span className="dot-red" />}
        >
          <div className="live-session-summary">
            <CircuitIcon size={66} />
            <div>
              <strong>
                {active
                  ? (snapshot?.context?.track_name ?? "Unknown track")
                  : "No live track"}
              </strong>
              <span>
                {active
                  ? (snapshot?.context?.session_type?.replaceAll("_", " ") ??
                    "—")
                  : "—"}
              </span>
              <span>
                {active
                  ? (snapshot?.context?.game_mode?.replaceAll("_", " ") ?? "—")
                  : "—"}
              </span>
            </div>
          </div>
          <div className="ref-facts-box">
            <Fact
              label="Session Time"
              value={sessionTime(telemetry?.session_time_s)}
            />
            <Fact
              label="Source"
              value={
                active
                  ? snapshot?.source === "replay"
                    ? "Recorded replay"
                    : "UDP recording"
                  : "—"
              }
            />
            <Fact
              label="Track Temperature"
              value={number(conditions?.track_temperature_c, "°C")}
            />
            <Fact
              label="Air Temperature"
              value={number(conditions?.air_temperature_c, "°C")}
            />
          </div>
        </AnalysisPanel>
        <LiveTelemetryCard snapshot={snapshot} status={status} />
        <AnalysisPanel
          title="Telemetry Data Status"
          icon={<DatabaseIcon size={24} />}
        >
          <div className="ref-status-list">
            {monitors.map(({ label, monitor }) => {
              const state = active
                  ? (monitor?.status ?? "waiting")
                  : "unavailable",
                good = state === "fresh";
              return (
                <div key={label} className="ref-status-item-row">
                  <div className="ref-status-item-left">
                    <span
                      className={
                        good
                          ? "dot-green"
                          : state === "stale"
                            ? "dot-orange"
                            : "dot-gray"
                      }
                    />
                    <span>{label}</span>
                  </div>
                  <div className="ref-status-item-right">
                    <span
                      className={`ref-badge ${good ? "ref-badge-good" : state === "stale" ? "ref-badge-warning" : ""}`}
                    >
                      {state}
                    </span>
                    <small className="ref-ms-label">
                      {active && monitor?.age_ms != null
                        ? `${Math.round(monitor.age_ms)} ms`
                        : "—"}
                    </small>
                  </div>
                </div>
              );
            })}
          </div>
        </AnalysisPanel>
      </div>
      <div className="ref-grid live-middle">
        <AnalysisPanel
          title="Live Lap Timing"
          icon={<StopwatchIcon size={25} />}
          action={
            <span className="ref-badge">Lap {timing?.lap_number ?? "—"}</span>
          }
        >
          <div className="live-current-lap">
            <span>Current Lap</span>
            <strong>{timeMs(timing?.current_lap_time_ms)}</strong>
          </div>
          <div className="live-timing-metrics">
            <TimingMetric label="Recent Best" value={timeMs(best?.lap_time_ms)} />
            <TimingMetric
              label="Last Lap"
              value={timeMs(timing?.previous_lap_time_ms)}
            />
            <TimingMetric
              label="Sector 1"
              value={sector(timing?.sector1_time_ms)}
            />
            <TimingMetric
              label="Sector 2"
              value={sector(timing?.sector2_time_ms)}
            />
            <TimingMetric label="Sector 3" value="—" />
          </div>
        </AnalysisPanel>
        <AnalysisPanel title="Car Status (Live)" icon={<CarIcon size={25} />}>
          <div className="ref-car-status-grid">
            <div className="ref-facts-col">
              <Fact
                label="Fuel Load"
                value={number(car?.fuel_in_tank_reported, "reported", 1)}
              />
              <Fact
                label="Fuel Remaining"
                value={number(car?.fuel_remaining_laps, "laps", 1)}
              />
              <Fact label="ERS Mode" value="—" />
              <Fact label="Engine Mode" value="—" />
              <Fact
                label="Brake Bias"
                value={number(car?.front_brake_bias_percent, "%", 1)}
              />
            </div>
            <div className="ref-facts-col">
              <Fact
                label="Tyre Compound"
                value={compound(car?.visual_tyre_compound)}
              />
              {tyres.map((tyre) => (
                <Fact
                  key={tyre.label}
                  label={`${tyre.label} Wear`}
                  value={number(damage?.tyre_wear_percent?.[tyre.index], "%")}
                />
              ))}
              <div className="live-tyre-grid">
                <span>Tyre Temp (°C)</span>
                <div>
                  {tyres.map((tyre) => (
                    <span key={tyre.label}>
                      {tyre.label}{" "}
                      <b>
                        {number(
                          telemetry?.tyre_surface_temperature_c?.[tyre.index],
                        )}
                      </b>
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </AnalysisPanel>
        <AnalysisPanel title="Track Map" icon={<CircuitIcon size={25} />}>
          <div className="analysis-empty">
            <CircuitIcon size={40} />
            <strong>
              {active
                ? (snapshot?.context?.track_name ?? "Track")
                : "No track selected"}
            </strong>
            <span>
              A complete recorded path is available on Track Analysis. Live
              motion supplies the current position only.
            </span>
          </div>
        </AnalysisPanel>
      </div>
      <div className="ref-grid live-bottom">
        <AnalysisPanel title="Recent Laps" icon={<TableIcon size={25} />}>
          <div className="ref-table-wrap">
            <table className="ref-table">
              <thead>
                <tr>
                  <th>Lap</th>
                  <th>Lap Time</th>
                  <th>S1</th>
                  <th>S2</th>
                  <th>S3</th>
                  <th>Valid</th>
                </tr>
              </thead>
              <tbody>
                {rows
                  .slice(-6)
                  .reverse()
                  .map((row) => (
                    <tr key={row.lap_number}>
                      <td>{row.lap_number}</td>
                      <td>
                        {row.lap_time_available ? timeMs(row.lap_time_ms) : "—"}
                      </td>
                      <td>
                        {row.sector1_time_available
                          ? sector(row.sector1_time_ms)
                          : "—"}
                      </td>
                      <td>
                        {row.sector2_time_available
                          ? sector(row.sector2_time_ms)
                          : "—"}
                      </td>
                      <td>
                        {row.sector3_time_available
                          ? sector(row.sector3_time_ms)
                          : "—"}
                      </td>
                      <td>{row.lap_valid ? "Yes" : "No"}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {!rows.length ? (
              <p className="analysis-source-note">
                Waiting for reported session history.
              </p>
            ) : null}
          </div>
        </AnalysisPanel>
        <AnalysisPanel
          title="Live Telemetry Graphs"
          icon={<WaveIcon size={25} />}
        >
          {active && snapshot?.live_telemetry ? (
            <LiveTelemetryChart
              telemetry={snapshot.live_telemetry}
              sourceState={snapshot.state}
            />
          ) : (
            <div className="analysis-empty">
              <WaveIcon size={36} />
              <strong>Waiting for live telemetry</strong>
              <span>
                Fresh speed, throttle, brake and gear observations will appear
                here.
              </span>
            </div>
          )}
        </AnalysisPanel>
      </div>
    </section>
  );
}
export function LiveTelemetryCard({
  snapshot,
  status,
}: {
  snapshot: PinnedLiveSnapshot | null;
  status: PinnedLiveViewStatus;
}) {
  const active =
    status === "ready" && snapshot !== null && isPinnedLiveActive(snapshot);
  const telemetry =
    active && snapshot?.live_telemetry?.status === "fresh"
      ? snapshot.live_telemetry
      : null;
  const car =
    active && snapshot?.live_car_status?.status === "fresh"
      ? snapshot.live_car_status
      : null;
  const damage =
    active && snapshot?.live_car_damage?.status === "fresh"
      ? snapshot.live_car_damage
      : null;
  const tyres = [
    { label: "FL", index: 2 },
    { label: "FR", index: 3 },
    { label: "RL", index: 0 },
    { label: "RR", index: 1 },
  ];
  const statusLabel =
    status === "ready"
      ? active
        ? snapshot?.source === "replay"
          ? "Replay"
          : "Live"
        : "Ended"
      : status === "unselected"
        ? "Choose a live source"
        : status;
  return (
    <AnalysisPanel
      title="Live Telemetry"
      icon={<WaveIcon size={25} />}
      action={
        <div className="ref-live-tags">
          <span className={`ref-badge ${active ? "ref-badge-good" : ""}`}>
            {statusLabel}
          </span>
          <span className={`ref-badge ${telemetry ? "ref-badge-good" : ""}`}>
            {active
              ? (snapshot?.live_telemetry?.status ?? "Waiting")
              : "Waiting"}{" "}
            {active && snapshot?.live_telemetry?.age_ms != null
              ? `${Math.round(snapshot.live_telemetry.age_ms)} ms`
              : ""}
          </span>
        </div>
      }
    >
      <div className="live-mock-gauge">
        <svg viewBox="0 0 500 125" aria-hidden="true">
          {Array.from({ length: 26 }, (_, i) => {
            const fraction = i / 26,
              next = (i + 0.65) / 26,
              rpm = telemetry?.engine_rpm;
            const colour =
              rpm == null || fraction > rpm / 15000
                ? "#e5ebf3"
                : fraction > 0.62
                  ? "#ff172b"
                  : "#0068ff";
            return (
              <path
                key={i}
                d={`M${22 + fraction * 456},${67 - Math.sin(fraction * Math.PI) * 52}L${22 + next * 456},${67 - Math.sin(next * Math.PI) * 52}`}
                stroke={colour}
                strokeWidth="5"
                strokeLinecap="round"
              />
            );
          })}
        </svg>
        <div className="live-mock-readouts">
          <div>
            <strong>{number(telemetry?.speed_kph)}</strong>
            <span>KM/H</span>
          </div>
          <div>
            <span>Gear</span>
            <strong className="live-gear">
              {telemetry?.gear === -1
                ? "R"
                : telemetry?.gear === 0
                  ? "N"
                  : number(telemetry?.gear)}
            </strong>
          </div>
          <div>
            <strong className="live-rpm">
              {telemetry?.engine_rpm == null
                ? "—"
                : telemetry.engine_rpm.toLocaleString("en-US")}
            </strong>
            <span>RPM</span>
          </div>
        </div>
      </div>
      <div className="live-mock-lower">
        <div className="live-input-bars">
          <InputBar
            label="Throttle"
            value={telemetry?.throttle}
            colour="#00c65b"
          />
          <InputBar label="Brake" value={telemetry?.brake} colour="#ff172b" />
          <InputBar label="DRS" value={null} colour="#0068ff" />
        </div>
        <div className="ref-facts-col">
          <Fact
            label="Fuel"
            value={number(car?.fuel_in_tank_reported, "reported", 1)}
          />
          <Fact label="ERS" value="—" />
          <Fact label="Tyres" value={compound(car?.visual_tyre_compound)} />
          <div className="live-tyre-grid">
            <span>Tyre Wear</span>
            <div>
              {tyres.map((tyre) => (
                <span key={tyre.label}>
                  {tyre.label}{" "}
                  <b>{number(damage?.tyre_wear_percent?.[tyre.index], "%")}</b>
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </AnalysisPanel>
  );
}
function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
function number(value: number | null | undefined, unit = "", precision = 0) {
  return value == null || !Number.isFinite(value)
    ? "—"
    : `${value.toFixed(precision)}${unit ? ` ${unit}` : ""}`;
}
function sector(value: number | null | undefined) {
  return value == null || value <= 0 ? "—" : (value / 1000).toFixed(3);
}
function sessionTime(value: number | null | undefined) {
  if (value == null) return "—";
  return [
    Math.floor(value / 3600),
    Math.floor(value / 60) % 60,
    Math.floor(value) % 60,
  ]
    .map((n) => n.toString().padStart(2, "0"))
    .join(":");
}
function compound(id: number | null | undefined) {
  return id == null
    ? "—"
    : ((
        {
          16: "Soft",
          17: "Medium",
          18: "Hard",
          7: "Intermediate",
          8: "Wet",
        } as Record<number, string>
      )[id] ?? `Compound ${id}`);
}
function TimingMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
function InputBar({
  label,
  value,
  colour,
}: {
  label: string;
  value?: number | null;
  colour: string;
}) {
  const available = value != null && Number.isFinite(value);
  return (
    <div>
      <span>{label}</span>
      <div className="bar-track">
        <div
          style={{
            width: available
              ? `${Math.max(0, Math.min(1, value)) * 100}%`
              : "0%",
            background: colour,
          }}
        />
      </div>
      <strong>{available ? `${Math.round(value * 100)}%` : "—"}</strong>
    </div>
  );
}
