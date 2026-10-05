"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type {
  ApiResponse,
  LiveCarDamageRecord,
  LiveCarSetupRecord,
  LiveCarStatusRecord,
  LiveLapTimingRecord,
  LiveMotionRecord,
  LiveSessionConditionsRecord,
  LiveTelemetryRecord,
  RecordingJobRecord,
} from "@/lib/api";
import {
  readCarDamageMonitor,
  readCarSetupMonitor,
  readMotionMonitor,
  readSessionConditionsMonitor,
} from "@/lib/live";
import { appScreenHref, isSelectionTransferBlocked } from "@/lib/navigation";

const activeStatuses = new Set(["starting", "recording", "stopping"]);

export default function RecordingControls({
  initialRecording,
  initialError,
  preservedQuery = "",
}: {
  initialRecording: RecordingJobRecord | null;
  initialError: boolean;
  preservedQuery?: string;
}) {
  const [recording, setRecording] = useState(initialRecording);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();
  const active = recording ? activeStatuses.has(recording.status) : false;
  const liveHref =
    active && recording && !isSelectionTransferBlocked(preservedQuery)
      ? appScreenHref("live", preservedQuery, {
          live_source: "recording",
          live_operation_id: recording.recording_id,
        })
      : null;

  useEffect(() => {
    if (!active) return;
    let mounted = true;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      try {
        const response = await fetch("/api/recordings/current", {
          cache: "no-store",
        });
        const body = (await response.json()) as ApiResponse<RecordingJobRecord>;
        if (!response.ok || body.status !== "ok") {
          throw new Error(body.reason ?? "recording_status_unavailable");
        }
        if (!mounted) return;
        const next = body.data;
        setRecording(next);
        setError(null);
        if (next && !activeStatuses.has(next.status)) {
          router.refresh();
          return;
        }
      } catch {
        if (mounted) setError("Recording status is temporarily unavailable.");
      }
      if (mounted) timer = setTimeout(poll, 250);
    };

    timer = setTimeout(poll, 400);
    return () => {
      mounted = false;
      clearTimeout(timer);
    };
  }, [active, router]);

  async function start() {
    await send("/api/recordings/start");
  }

  async function stop() {
    if (!recording) return;
    await send(`/api/recordings/${recording.recording_id}/stop`);
  }

  async function send(url: string) {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(url, { method: "POST" });
      const body = (await response.json()) as ApiResponse<RecordingJobRecord>;
      if (!response.ok || !body.data) {
        const reason = body.reason ?? "recording_request_failed";
        throw new Error(
          reason === "another_local_operation_is_in_progress"
            ? "An import or recording operation is already running."
            : reason === "recording_controller_unavailable"
              ? "The local recording service is unavailable. Restart the API and refresh."
              : "The recording request could not be completed.",
        );
      }
      setRecording(body.data);
      if (body.data.status === "complete") router.refresh();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The recording request could not be completed.",
      );
    } finally {
      setBusy(false);
    }
  }

  const progress = recording?.progress;
  const summary = recording?.summary;
  const context = progress?.latest_context;
  const isReceiving = (progress?.received ?? 0) > 0;

  return (
    <div className="recording-controls">
      <div className="recording-control-copy">
        <div className="recording-state-line">
          <span
            className={`recording-light ${active ? "recording-light-active" : ""}`}
          />
          <strong aria-live="polite">
            {recordingStatus(recording, isReceiving)}
          </strong>
          {recording && (
            <span className="recording-port">UDP {recording.bind_port}</span>
          )}
        </div>
        <p>
          {active
            ? context
              ? `${context.track_name ?? "Unknown track"} · ${label(context.session_type)} · ${label(context.game_mode)}`
              : "Listening for telemetry. Time Trial, Race, and unknown modes are recorded the same way."
            : recording?.status === "complete" && recording.published
              ? "Capture finalized and available in the inbox below. Review receive losses before using it as analysis evidence."
              : recording?.status === "interrupted"
                ? "The previous recording was interrupted. Its staging file was retained; start a new capture to continue."
                : recording?.status === "failed"
                  ? "The capture could not be published. Its staging file, if present, was retained for inspection."
                  : "Start UDP capture to create a recording directly in the local inbox."}
        </p>
        {active && progress && (
          <div className="recording-metrics">
            <span>{formatDuration(progress.elapsed_ms)}</span>
            <span>{progress.recorded.toLocaleString()} written</span>
            <span>{progress.queue_dropped.toLocaleString()} queue drops</span>
            {progress.socket_errors > 0 && (
              <span>{progress.socket_errors} socket errors</span>
            )}
            {!isReceiving && recording.status === "recording" && (
              <span>waiting for first packet</span>
            )}
          </div>
        )}
        {!active && recording?.status === "complete" && summary && (
          <div className="recording-metrics">
            <span>
              {numeric(summary, "received").toLocaleString()} received
            </span>
            <span>{numeric(summary, "recorded").toLocaleString()} written</span>
            <span>
              {numeric(summary, "queue_dropped").toLocaleString()} queue drops
            </span>
          </div>
        )}
      </div>
      <div className="recording-control-actions">
        <button
          className="import-button"
          type="button"
          onClick={start}
          disabled={busy || active || initialError}
        >
          {recording?.status === "complete" || recording?.status === "failed"
            ? "Start another recording"
            : recording?.status === "interrupted"
              ? "Start new recording"
              : "Start recording"}
        </button>
        <button
          className="import-button stop-recording-button"
          type="button"
          onClick={stop}
          disabled={busy || !active || recording?.status === "stopping"}
        >
          {recording?.status === "stopping" ? "Finalizing…" : "Stop recording"}
        </button>
      </div>
      {error && (
        <p className="recording-control-alert" role="alert">
          {error}
        </p>
      )}
      {initialError && !active && (
        <p className="recording-control-alert" role="status">
          The local recording service is unavailable. Check the API and refresh
          this page.
        </p>
      )}
      {recording?.failure_reason && !active && (
        <p className="recording-control-alert" role="status">
          {recording.failure_reason.replaceAll("_", " ")}
        </p>
      )}
      {active && progress && (
        <>
          <LiveTelemetryPanel telemetry={progress.live_telemetry} />
          {progress.live_car_status ? (
            <LiveCarStatusPanel telemetry={progress.live_car_status} />
          ) : null}
          {progress.live_lap_timing ? (
            <LiveLapTimingPanel telemetry={progress.live_lap_timing} />
          ) : null}
          {progress.live_car_damage ? (
            <LiveCarDamagePanel telemetry={progress.live_car_damage} />
          ) : null}
          {progress.live_car_setup ? (
            <LiveCarSetupPanel telemetry={progress.live_car_setup} />
          ) : null}
          {progress.live_session_conditions ? (
            <LiveSessionConditionsPanel telemetry={progress.live_session_conditions} />
          ) : null}
          {progress.live_motion ? (
            <LiveMotionPanel telemetry={progress.live_motion} />
          ) : null}
        </>
      )}
      {liveHref && (
        <a className="live-open-link" href={liveHref}>
          Open live telemetry <span aria-hidden="true">↗</span>
        </a>
      )}
    </div>
  );
}

export function LiveTelemetryPanel({
  telemetry,
  sourceKind = "recording",
}: {
  telemetry: LiveTelemetryRecord;
  sourceKind?: "recording" | "replay";
}) {
  const temperatureWheels = [
    { label: "Rear left", index: 0 },
    { label: "Rear right", index: 1 },
    { label: "Front left", index: 2 },
    { label: "Front right", index: 3 },
  ] as const;
  const engineTemperatureMaximum =
    telemetry.packet_format === 2025
      ? 65_535
      : telemetry.packet_format === 2026
        ? 255
        : -1;
  const statusCopy: Record<LiveTelemetryRecord["status"], string> = {
    waiting: "Waiting for a synchronized player frame.",
    fresh:
      sourceKind === "replay"
        ? "Player telemetry is updating from replay delivery."
        : "Player telemetry is updating from the current recording.",
    stale:
      sourceKind === "replay"
        ? `Last playback-delivered player frame was ${formatAge(telemetry.age_ms)} ago.`
        : `Last player frame was ${formatAge(telemetry.age_ms)} ago.`,
    unsupported:
      sourceKind === "replay"
        ? "Replay continues. The live view does not support this packet format yet."
        : "Recording continues. The live view does not support this packet format yet.",
    unavailable: liveUnavailableReason(telemetry.reason),
  };

  return (
    <section
      className="live-telemetry"
      data-state={telemetry.status}
      aria-label="Live player telemetry"
    >
      <div className="live-telemetry-heading">
        <div>
          <div className="eyebrow">
            {sourceKind === "replay"
              ? "REPLAYED PLAYER TELEMETRY"
              : "LIVE PLAYER TELEMETRY"}
          </div>
          <p>{statusCopy[telemetry.status]}</p>
        </div>
        <span className={`live-telemetry-state state-${telemetry.status}`}>
          {telemetry.status.toUpperCase()}
        </span>
      </div>
      {telemetry.status !== "waiting" && (
        <div className="live-telemetry-grid">
          <LiveMetric label="LAP" value={display(telemetry.lap_number)} />
          <LiveMetric label="CLOCK" value={formatLapClock(telemetry.lap_time_ms)} />
          <LiveMetric
            label="VALIDITY"
            value={
              telemetry.game_invalid === null ||
              telemetry.game_invalid === undefined
                ? "—"
                : telemetry.game_invalid
                  ? "INVALID"
                  : "CLEAN"
            }
          />
          <LiveMetric
            label="SPEED"
            value={withUnit(telemetry.speed_kph, "km/h")}
          />
          <LiveMetric label="GEAR" value={display(telemetry.gear)} />
          <LiveMetric
            label="RPM"
            value={
              telemetry.engine_rpm === null ||
              telemetry.engine_rpm === undefined
                ? "—"
                : telemetry.engine_rpm.toLocaleString()
            }
          />
          <LiveMetric
            label="ENGINE TEMP"
            value={temperatureValue(
              telemetry.engine_temperature_c,
              engineTemperatureMaximum,
            )}
          />
          <LiveMetric
            label="THROTTLE"
            value={withUnit(telemetry.throttle, "%", 0, 100)}
          />
          <LiveMetric
            label="BRAKE"
            value={withUnit(telemetry.brake, "%", 0, 100)}
          />
          <LiveMetric label="PIT" value={pitStatus(telemetry.pit_status_id)} />
          <LiveMetric
            label="DRIVER"
            value={driverStatus(telemetry.driver_status_id)}
          />
        </div>
      )}
      {telemetry.status !== "waiting" && (
        <div
          className="live-temperature-scroll"
          tabIndex={0}
          aria-label="Wheel temperature readings; scroll horizontally if needed"
        >
          <table className="live-temperature-table">
            <caption>Source-reported wheel temperatures · °C</caption>
            <thead>
              <tr>
                <th scope="col">Wheel</th>
                <th scope="col">Brake</th>
                <th scope="col">Tyre surface</th>
                <th scope="col">Tyre inner</th>
              </tr>
            </thead>
            <tbody>
              {temperatureWheels.map(({ label, index }) => (
                <tr key={label}>
                  <th scope="row">{label}</th>
                  <td>
                    {wheelTemperature(telemetry.brake_temperature_c, index, 65_535)}
                  </td>
                  <td>
                    {wheelTemperature(
                      telemetry.tyre_surface_temperature_c,
                      index,
                      255,
                    )}
                  </td>
                  <td>
                    {wheelTemperature(
                      telemetry.tyre_inner_temperature_c,
                      index,
                      255,
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function temperatureValue(value: unknown, maximum: number): string {
  return isTemperature(value, maximum) ? `${value} °C` : "—";
}

function wheelTemperature(
  values: unknown,
  index: number,
  maximum: number,
): string {
  if (!isTemperatureArray(values, maximum)) return "—";
  const value: unknown = values[index];
  return isTemperature(value, maximum) ? `${value} °C` : "—";
}

function isTemperatureArray(
  value: unknown,
  maximum: number,
): value is readonly number[] {
  return (
    Array.isArray(value) &&
    value.length === 4 &&
    value.every((item) => isTemperature(item, maximum))
  );
}

function isTemperature(value: unknown, maximum: number): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 0 &&
    value <= maximum
  );
}

function LiveMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="live-telemetry-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function LiveCarStatusPanel({
  telemetry,
  sourceKind = "recording",
}: {
  telemetry: LiveCarStatusRecord;
  sourceKind?: "recording" | "replay";
}) {
  const statusCopy: Record<LiveCarStatusRecord["status"], string> = {
    waiting: "Waiting for same-frame player Lap Data and Car Status.",
    fresh:
      sourceKind === "replay"
        ? "Car Status is matched to the current player frame in the replay."
        : "Car Status is matched to the current player frame.",
    stale:
      sourceKind === "replay"
        ? `Last playback-delivered Car Status frame was ${formatAge(telemetry.age_ms)} ago.`
        : `Last matched Car Status frame was ${formatAge(telemetry.age_ms)} ago.`,
    unsupported:
      sourceKind === "replay"
        ? "Replay continues. This Car Status packet version is unsupported."
        : "Recording continues. This Car Status packet version is unsupported.",
    unavailable: liveCarStatusUnavailableReason(telemetry.reason),
  };
  const invalidFields = (telemetry.validation_flags ?? []).map((flag) =>
    flag.replace("invalid_car_status_", "").replaceAll("_", " "),
  );

  return (
    <section
      className="live-telemetry live-car-status"
      data-state={telemetry.status}
      aria-label="Live player car status"
    >
      <div className="live-telemetry-heading">
        <div>
          <div className="eyebrow">
            {sourceKind === "replay" ? "REPLAYED CAR STATUS" : "LIVE CAR STATUS"}
          </div>
          <p>{statusCopy[telemetry.status]}</p>
        </div>
        <span className={`live-telemetry-state state-${telemetry.status}`}>
          {telemetry.status.toUpperCase()}
        </span>
      </div>
      {telemetry.status !== "waiting" && (
        <>
          <div className="live-telemetry-grid">
            <LiveMetric
              label="FUEL REPORTED · UNIT UNSPECIFIED"
              value={fixed(telemetry.fuel_in_tank_reported, 2)}
            />
            <LiveMetric
              label="GAME-REPORTED REMAINING LAPS"
              value={fixed(telemetry.fuel_remaining_laps, 2)}
            />
            <LiveMetric
              label="ACTUAL TYRE COMPOUND CODE"
              value={display(telemetry.actual_tyre_compound)}
            />
            <LiveMetric
              label="VISUAL TYRE COMPOUND CODE"
              value={display(telemetry.visual_tyre_compound)}
            />
            <LiveMetric
              label="TYRE AGE"
              value={withUnit(telemetry.tyre_age_laps, "laps")}
            />
            <LiveMetric
              label="FRONT BRAKE BIAS"
              value={withUnit(telemetry.front_brake_bias_percent, "%")}
            />
            <LiveMetric
              label="PIT LIMITER"
              value={
                telemetry.pit_limiter_active == null
                  ? "—"
                  : telemetry.pit_limiter_active
                    ? "ON"
                    : "OFF"
              }
            />
          </div>
          <p className="live-car-status-note">
            Fuel quantity has no unit in the game feed; remaining laps is the
            reported value, not an app forecast.
          </p>
          {invalidFields.length ? (
            <p className="live-car-status-invalid" role="status">
              Invalid fields are unavailable: {invalidFields.join(", ")}.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

export function LiveLapTimingPanel({
  telemetry,
  sourceKind = "recording",
}: {
  telemetry: LiveLapTimingRecord;
  sourceKind?: "recording" | "replay";
}) {
  const statusCopy: Record<LiveLapTimingRecord["status"], string> = {
    waiting: "Waiting for selected-player Lap Data.",
    fresh:
      sourceKind === "replay"
        ? "Game-reported lap timing is updating from replay delivery."
        : "Game-reported lap timing is updating for the selected player.",
    stale:
      sourceKind === "replay"
        ? `Last playback-delivered Lap Data update was ${formatAge(telemetry.age_ms)} ago.`
        : `Last Lap Data timing update was ${formatAge(telemetry.age_ms)} ago.`,
    unsupported:
      sourceKind === "replay"
        ? "Replay continues. This Lap Data packet version is unsupported."
        : "Recording continues. This Lap Data packet version is unsupported.",
    unavailable: liveLapTimingUnavailableReason(telemetry.reason),
  };
  const invalidFields = (telemetry.validation_flags ?? []).map((flag) =>
    flag.replaceAll("_", " "),
  );

  return (
    <section
      className="live-telemetry live-lap-timing"
      data-state={telemetry.status}
      aria-label="Live reported lap timing"
    >
      <div className="live-telemetry-heading">
        <div>
          <div className="eyebrow">
            {sourceKind === "replay"
              ? "REPLAYED REPORTED LAP TIMING"
              : "LIVE REPORTED LAP TIMING"}
          </div>
          <p>{statusCopy[telemetry.status]}</p>
        </div>
        <span className={`live-telemetry-state state-${telemetry.status}`}>
          {telemetry.status.toUpperCase()}
        </span>
      </div>
      {telemetry.status !== "waiting" && (
        <>
          <div className="live-telemetry-grid">
            <LiveMetric label="LAP" value={display(telemetry.lap_number)} />
            <LiveMetric
              label="CURRENT LAP CLOCK"
              value={formatLapClock(telemetry.current_lap_time_ms)}
            />
            <LiveMetric
              label="CURRENT SECTOR"
              value={
                telemetry.current_sector == null
                  ? "—"
                  : `SECTOR ${telemetry.current_sector}`
              }
            />
            <LiveMetric
              label="GAME-REPORTED PREVIOUS LAP"
              value={formatLapClock(telemetry.previous_lap_time_ms)}
            />
            <LiveMetric
              label="REPORTED SECTOR 1"
              value={formatLapClock(telemetry.sector1_time_ms)}
            />
            <LiveMetric
              label="REPORTED SECTOR 2"
              value={formatLapClock(telemetry.sector2_time_ms)}
            />
          </div>
          <p className="live-car-status-note">
            Timing is shown as reported by the game; this panel does not assess
            lap validity or infer lap completion.
          </p>
          {invalidFields.length ? (
            <p className="live-car-status-invalid" role="status">
              Unavailable fields: {invalidFields.join(", ")}.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

export function LiveCarDamagePanel({
  telemetry: inputTelemetry,
  sourceKind = "recording",
}: {
  telemetry: LiveCarDamageRecord;
  sourceKind?: "recording" | "replay";
}) {
  const telemetry = readCarDamageMonitor(inputTelemetry) ?? {
    status: "unavailable" as const,
    reason: "malformed_optional_fields",
    age_ms: null,
    validation_flags: [],
  };
  const wheels = [
    { label: "Rear left", index: 0 },
    { label: "Rear right", index: 1 },
    { label: "Front left", index: 2 },
    { label: "Front right", index: 3 },
  ] as const;
  const copy: Record<LiveCarDamageRecord["status"], string> = {
    waiting: "Waiting for an admitted selected-player Car Damage packet.",
    fresh: "Recent sparse Car Damage observation; it updates only when that packet arrives.",
    stale:
      `Last Car Damage observation was ${formatAge(telemetry.age_ms)} ago; values may have changed since.`,
    unsupported:
      sourceKind === "replay"
        ? "Replay continues. This Car Damage packet version is unsupported."
        : "Recording continues. This Car Damage packet version is unsupported.",
    unavailable: liveCarDamageUnavailableReason(telemetry.reason),
  };
  const invalidFields = (telemetry.validation_flags ?? []).map((flag) =>
    flag.replace("invalid_car_damage_", "").replaceAll("_", " "),
  );
  const observationCount =
    Number.isInteger(telemetry.observation_count) &&
    (telemetry.observation_count ?? -1) >= 0
      ? telemetry.observation_count!.toLocaleString()
      : "—";

  return (
    <section
      className="live-telemetry live-car-damage"
      data-state={telemetry.status}
      aria-label="Sparse live player damage observation"
    >
      <div className="live-telemetry-heading">
        <div>
          <div className="eyebrow">
            {sourceKind === "replay" ? "REPLAYED CAR DAMAGE" : "LIVE CAR DAMAGE"}
          </div>
          <p>{copy[telemetry.status]}</p>
        </div>
        <span className={`live-telemetry-state state-${telemetry.status}`}>
          {telemetry.status.toUpperCase()}
        </span>
      </div>
      {telemetry.status !== "waiting" && (
        <>
          <div className="live-car-damage-meta">
            <span>{observationCount} admitted observations</span>
            <span>
              Source time {finiteNumber(telemetry.session_time_s)?.toFixed(2) ?? "—"} s
            </span>
            <span>Session {safeText(telemetry.session_uid)}</span>
            <span>Player {safeInteger(telemetry.player_car_index)}</span>
            <span>Format {safeInteger(telemetry.packet_format)}</span>
            <span>Frame {safeInteger(telemetry.frame_identifier)}</span>
          </div>
          <div className="live-temperature-scroll" tabIndex={0}>
            <table className="live-temperature-table">
              <caption>Source-reported tyre and brake damage · percent</caption>
              <thead>
                <tr>
                  <th scope="col">Wheel</th>
                  <th scope="col">Tyre wear</th>
                  <th scope="col">Tyre damage</th>
                  <th scope="col">Brake damage</th>
                </tr>
              </thead>
              <tbody>
                {wheels.map(({ label, index }) => (
                  <tr key={label}>
                    <th scope="row">{label}</th>
                    <td>{percentArrayValue(telemetry.tyre_wear_percent, index, false)}</td>
                    <td>{percentArrayValue(telemetry.tyre_damage_percent, index, true)}</td>
                    <td>{percentArrayValue(telemetry.brake_damage_percent, index, true)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="live-telemetry-grid">
            <LiveMetric label="FRONT LEFT WING" value={percentValue(telemetry.front_left_wing_damage_percent, true)} />
            <LiveMetric label="FRONT RIGHT WING" value={percentValue(telemetry.front_right_wing_damage_percent, true)} />
            <LiveMetric label="REAR WING" value={percentValue(telemetry.rear_wing_damage_percent, true)} />
            <LiveMetric label="ENGINE DAMAGE" value={percentValue(telemetry.engine_damage_percent, true)} />
          </div>
          <p className="live-car-status-note">
            These are packet-time observations. Sparse updates do not indicate packet loss or continuous car condition.
          </p>
          {invalidFields.length ? (
            <p className="live-car-status-invalid" role="status">
              Invalid fields are unavailable: {invalidFields.join(", ")}.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

export function LiveCarSetupPanel({
  telemetry: inputTelemetry,
  sourceKind = "recording",
}: {
  telemetry: LiveCarSetupRecord;
  sourceKind?: "recording" | "replay";
}) {
  const telemetry = readCarSetupMonitor(inputTelemetry) ?? {
    status: "unavailable" as const,
    reason: "malformed_optional_fields",
    age_ms: null,
    validation_flags: [],
  };
  const statusCopy: Record<LiveCarSetupRecord["status"], string> = {
    waiting: "Waiting for an admitted selected-player Car Setups packet.",
    fresh: "Recent sparse setup observation; it updates only when that packet arrives.",
    stale: `Last setup observation was ${formatAge(telemetry.age_ms)} ago; settings may have changed since.`,
    unsupported:
      sourceKind === "replay"
        ? "Replay continues. This Car Setups packet version is unsupported."
        : "Recording continues. This Car Setups packet version is unsupported.",
    unavailable: liveCarSetupUnavailableReason(telemetry.reason),
  };
  const invalidFields = (telemetry.validation_flags ?? []).map((flag) =>
    flag.replace("invalid_car_setup_", "").replaceAll("_", " "),
  );
  const observationCount =
    Number.isInteger(telemetry.observation_count) &&
    (telemetry.observation_count ?? -1) >= 0
      ? telemetry.observation_count!.toLocaleString()
      : "—";

  return (
    <section
      className="live-telemetry live-car-setup"
      data-state={telemetry.status}
      aria-label="Sparse live player setup observation"
    >
      <div className="live-telemetry-heading">
        <div>
          <div className="eyebrow">
            {sourceKind === "replay" ? "REPLAYED CAR SETUP" : "LIVE CAR SETUP"}
          </div>
          <p>{statusCopy[telemetry.status]}</p>
        </div>
        <span className={`live-telemetry-state state-${telemetry.status}`}>
          {telemetry.status.toUpperCase()}
        </span>
      </div>
      {telemetry.status !== "waiting" && (
        <>
          <div className="live-car-damage-meta">
            <span>{observationCount} admitted observations</span>
            <span>
              Source time {finiteNumber(telemetry.session_time_s)?.toFixed(2) ?? "—"} s
            </span>
            <span>Session {safeText(telemetry.session_uid)}</span>
            <span>Player {safeInteger(telemetry.player_car_index)}</span>
            <span>Format {safeInteger(telemetry.packet_format)}</span>
            <span>Frame {safeInteger(telemetry.frame_identifier)}</span>
          </div>
          <h3 className="live-subheading">Aero and differential</h3>
          <div className="live-telemetry-grid">
            <LiveMetric label="CURRENT FRONT WING" value={setupInteger(telemetry.front_wing)} />
            <LiveMetric label="REAR WING" value={setupInteger(telemetry.rear_wing)} />
            <LiveMetric label="ON-THROTTLE DIFFERENTIAL" value={setupPercent(telemetry.on_throttle_differential)} />
            <LiveMetric label="OFF-THROTTLE DIFFERENTIAL" value={setupPercent(telemetry.off_throttle_differential)} />
            <LiveMetric label="NEXT-PIT FRONT WING REQUEST" value={setupFloat(telemetry.next_front_wing_value)} />
          </div>
          <h3 className="live-subheading">Suspension and brakes</h3>
          <div className="live-telemetry-grid">
            <LiveMetric label="FRONT CAMBER" value={setupFloat(telemetry.front_camber)} />
            <LiveMetric label="REAR CAMBER" value={setupFloat(telemetry.rear_camber)} />
            <LiveMetric label="FRONT TOE" value={setupFloat(telemetry.front_toe)} />
            <LiveMetric label="REAR TOE" value={setupFloat(telemetry.rear_toe)} />
            <LiveMetric label="FRONT SUSPENSION" value={setupInteger(telemetry.front_suspension)} />
            <LiveMetric label="REAR SUSPENSION" value={setupInteger(telemetry.rear_suspension)} />
            <LiveMetric label="FRONT ANTI-ROLL BAR" value={setupInteger(telemetry.front_anti_roll_bar)} />
            <LiveMetric label="REAR ANTI-ROLL BAR" value={setupInteger(telemetry.rear_anti_roll_bar)} />
            <LiveMetric label="FRONT SUSPENSION HEIGHT" value={setupInteger(telemetry.front_suspension_height)} />
            <LiveMetric label="REAR SUSPENSION HEIGHT" value={setupInteger(telemetry.rear_suspension_height)} />
            <LiveMetric label="BRAKE PRESSURE" value={setupPercent(telemetry.brake_pressure_percent)} />
            <LiveMetric label="BRAKE BIAS" value={setupPercent(telemetry.brake_bias_percent)} />
            <LiveMetric label="ENGINE BRAKING" value={setupPercent(telemetry.engine_braking_percent)} />
          </div>
          <h3 className="live-subheading">Tyre setup and fuel</h3>
          <div className="live-telemetry-grid">
            <LiveMetric label="REAR LEFT PRESSURE" value={setupFloat(telemetry.rear_left_tyre_pressure_psi, " psi")} />
            <LiveMetric label="REAR RIGHT PRESSURE" value={setupFloat(telemetry.rear_right_tyre_pressure_psi, " psi")} />
            <LiveMetric label="FRONT LEFT PRESSURE" value={setupFloat(telemetry.front_left_tyre_pressure_psi, " psi")} />
            <LiveMetric label="FRONT RIGHT PRESSURE" value={setupFloat(telemetry.front_right_tyre_pressure_psi, " psi")} />
            <LiveMetric label="BALLAST" value={setupInteger(telemetry.ballast)} />
            <LiveMetric label="SETUP FUEL LOAD" value={setupFloat(telemetry.fuel_load)} />
          </div>
          <p className="live-car-status-note">
            Values are source-reported setup observations. The next-pit wing request is separate from current wing; fuel and tyre values are not live condition or setup advice.
          </p>
          {invalidFields.length ? (
            <p className="live-car-status-invalid" role="status">
              Invalid fields are unavailable: {invalidFields.join(", ")}.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

export function LiveSessionConditionsPanel({
  telemetry: inputTelemetry,
  sourceKind = "recording",
}: {
  telemetry: LiveSessionConditionsRecord;
  sourceKind?: "recording" | "replay";
}) {
  const telemetry = readSessionConditionsMonitor(inputTelemetry) ?? {
    status: "unavailable" as const,
    reason: "malformed_optional_fields",
    age_ms: null,
    validation_flags: [],
  };
  const statusCopy: Record<LiveSessionConditionsRecord["status"], string> = {
    waiting: "Waiting for an admitted Session packet.",
    fresh: "Recent game-reported session conditions; values update when a Session packet arrives.",
    stale: `Last Session packet was ${formatAge(telemetry.age_ms)} ago; conditions may have changed since.`,
    unsupported:
      sourceKind === "replay"
        ? "Replay continues. This Session packet version is unsupported."
        : "Recording continues. This Session packet version is unsupported.",
    unavailable: liveSessionConditionsUnavailableReason(telemetry.reason),
  };
  const observationCount =
    Number.isInteger(telemetry.observation_count) &&
    (telemetry.observation_count ?? -1) >= 0
      ? telemetry.observation_count!.toLocaleString()
      : "—";
  const weather =
    telemetry.weather_name ??
    (telemetry.weather_id == null
      ? "—"
      : `Unknown weather (ID ${telemetry.weather_id})`);
  const invalidFields = (telemetry.validation_flags ?? []).map((flag) =>
    flag.replaceAll("_", " "),
  );

  return (
    <section
      className="live-telemetry live-session-conditions"
      data-state={telemetry.status}
      aria-label="Live reported session conditions"
    >
      <div className="live-telemetry-heading">
        <div>
          <div className="eyebrow">
            {sourceKind === "replay" ? "REPLAYED SESSION CONDITIONS" : "LIVE SESSION CONDITIONS"}
          </div>
          <p>{statusCopy[telemetry.status]}</p>
        </div>
        <span className={`live-telemetry-state state-${telemetry.status}`}>
          {telemetry.status.toUpperCase()}
        </span>
      </div>
      {telemetry.status !== "waiting" && (
        <>
          <div className="live-car-damage-meta">
            <span>{observationCount} admitted observations</span>
            <span>Session {safeText(telemetry.session_uid)}</span>
            <span>Format {safeInteger(telemetry.packet_format)}</span>
            <span>Frame {safeInteger(telemetry.frame_identifier)}</span>
            <span>Source time {finiteNumber(telemetry.session_time_s)?.toFixed(2) ?? "—"} s</span>
          </div>
          <div className="live-telemetry-grid">
            <LiveMetric label="REPORTED WEATHER" value={weather.replaceAll("_", " ")} />
            <LiveMetric label="AIR TEMPERATURE" value={setupTemperature(telemetry.air_temperature_c)} />
            <LiveMetric label="TRACK TEMPERATURE" value={setupTemperature(telemetry.track_temperature_c)} />
          </div>
          <p className="live-car-status-note">
            These are session-wide values reported by the game. Age describes time since the last admitted Session observation.
          </p>
          {invalidFields.length ? (
            <p className="live-car-status-invalid" role="status">
              Unavailable fields: {invalidFields.join(", ")}.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

function liveSessionConditionsUnavailableReason(reason: string | null) {
  const reasons: Record<string, string> = {
    session_context_adapter_unsupported: "The game continues. This Session packet version is unsupported.",
    session_context_decode_failed: "Session conditions are hidden because this packet could not be decoded safely.",
    conflicting_session_context_packets: "Session conditions are hidden because this frame contains conflicting Session packets.",
    receive_provenance_unavailable: "Session conditions are hidden because receive-time evidence is incomplete.",
  };
  const message = reason && Object.hasOwn(reasons, reason) ? reasons[reason] : null;
  return typeof message === "string"
    ? message
    : "Session conditions are currently unavailable.";
}

function setupTemperature(value: unknown) {
  return typeof value === "number" && Number.isInteger(value) && value >= -128 && value <= 127
    ? `${value} °C`
    : "—";
}

export function LiveMotionPanel({
  telemetry: inputTelemetry,
  sourceKind = "recording",
}: {
  telemetry: LiveMotionRecord;
  sourceKind?: "recording" | "replay";
}) {
  const telemetry = readMotionMonitor(inputTelemetry) ?? {
    status: "unavailable" as const,
    reason: "malformed_optional_fields",
    age_ms: null,
    validation_flags: [],
  };
  const statusCopy: Record<LiveMotionRecord["status"], string> = {
    waiting: "Waiting for an admitted selected-player Motion packet.",
    fresh: "Recent selected-player world position and velocity from Motion.",
    stale: `Last Motion observation was ${formatAge(telemetry.age_ms)} ago; values may have changed since.`,
    unsupported:
      sourceKind === "replay"
        ? "Replay continues. This Motion packet version is unsupported."
        : "Recording continues. This Motion packet version is unsupported.",
    unavailable: liveMotionUnavailableReason(telemetry.reason),
  };
  const observationCount =
    Number.isInteger(telemetry.observation_count) &&
    (telemetry.observation_count ?? -1) >= 0
      ? telemetry.observation_count!.toLocaleString()
      : "—";
  const invalidFields = (telemetry.validation_flags ?? []).map((flag) =>
    flag.replace("invalid_motion_", "").replaceAll("_", " "),
  );
  const position = telemetry.world_position_m;
  const velocity = telemetry.world_velocity_mps;

  return (
    <section
      className="live-telemetry live-motion"
      data-state={telemetry.status}
      aria-label="Live selected-player world position and velocity"
    >
      <div className="live-telemetry-heading">
        <div>
          <div className="eyebrow">
            {sourceKind === "replay" ? "REPLAYED PLAYER MOTION" : "LIVE PLAYER MOTION"}
          </div>
          <p>{statusCopy[telemetry.status]}</p>
        </div>
        <span className={`live-telemetry-state state-${telemetry.status}`}>
          {telemetry.status.toUpperCase()}
        </span>
      </div>
      {telemetry.status !== "waiting" && (
        <>
          <div className="live-car-damage-meta">
            <span>{observationCount} admitted observations</span>
            <span>Session {safeText(telemetry.session_uid)}</span>
            <span>Player {safeInteger(telemetry.player_car_index)}</span>
            <span>Format {safeInteger(telemetry.packet_format)}</span>
            <span>Frame {safeInteger(telemetry.frame_identifier)}</span>
            <span>Source time {finiteNumber(telemetry.session_time_s)?.toFixed(2) ?? "—"} s</span>
          </div>
          <h3 className="live-subheading">World position · metres</h3>
          <div className="live-telemetry-grid">
            <LiveMetric label="WORLD X" value={motionComponent(position?.[0])} />
            <LiveMetric label="WORLD Y" value={motionComponent(position?.[1])} />
            <LiveMetric label="WORLD Z" value={motionComponent(position?.[2])} />
          </div>
          <h3 className="live-subheading">World velocity · m/s</h3>
          <div className="live-telemetry-grid">
            <LiveMetric label="VELOCITY X" value={motionComponent(velocity?.[0])} />
            <LiveMetric label="VELOCITY Y" value={motionComponent(velocity?.[1])} />
            <LiveMetric label="VELOCITY Z" value={motionComponent(velocity?.[2])} />
          </div>
          <p className="live-car-status-note">
            Source-reported world axes are shown as supplied; Y is not interpreted as altitude and no track position is inferred.
          </p>
          {invalidFields.length ? (
            <p className="live-car-status-invalid" role="status">
              Unavailable vectors: {invalidFields.join(", ")}.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

function liveMotionUnavailableReason(reason: string | null) {
  const reasons: Record<string, string> = {
    motion_adapter_unsupported: "The game continues. This Motion packet version is unsupported.",
    motion_decode_failed: "Motion values are hidden because this packet could not be decoded safely.",
    player_index_mismatch_in_frame: "Motion values are hidden because this frame does not identify the selected player.",
    player_index_out_of_range: "Motion values are hidden because the selected player is outside this packet's car records.",
    player_identity_unavailable: "Motion values are hidden because the selected player could not be identified in this frame.",
    conflicting_selected_player_motion_records: "Motion values are hidden because this frame contains conflicting records for the selected player.",
    receive_provenance_unavailable: "Motion values are hidden because receive-time evidence is incomplete.",
  };
  const message = reason && Object.hasOwn(reasons, reason) ? reasons[reason] : null;
  return typeof message === "string"
    ? message
    : "Player Motion is currently unavailable.";
}

function motionComponent(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(2)
    : "—";
}

function percentArrayValue(
  values: readonly (number | null)[] | null | undefined,
  index: number,
  integer: boolean,
) {
  if (!Array.isArray(values) || values.length !== 4) return "—";
  return percentValue(values[index], integer);
}

function percentValue(value: unknown, integer: boolean) {
  return typeof value === "number" &&
    Number.isFinite(value) &&
    value >= 0 &&
    value <= 100 &&
    (!integer || Number.isInteger(value))
    ? `${Number.isInteger(value) ? value : value.toFixed(1)}%`
    : "—";
}

function liveCarDamageUnavailableReason(reason: string | null) {
  if (reason === "receive_provenance_unavailable")
    return "The selected Damage packet has no receive-time provenance.";
  if (reason === "car_damage_decode_failed")
    return "The selected Car Damage packet could not be decoded.";
  if (reason === "conflicting_car_damage_packets")
    return "Selected-player Damage records conflict within this frame.";
  if (reason === "player_index_mismatch_in_frame")
    return "The selected-player identity is ambiguous for this frame.";
  if (reason === "operation_ended")
    return "The recording or replay has ended; live observations are cleared.";
  return reason ? reason.replaceAll("_", " ") : "Damage is unavailable for this frame.";
}

function liveCarSetupUnavailableReason(reason: string | null) {
  if (reason === "receive_provenance_unavailable")
    return "The selected Car Setups packet has no receive-time provenance.";
  if (reason === "car_setup_decode_failed")
    return "The selected Car Setups packet could not be decoded.";
  if (reason === "conflicting_car_setup_packets")
    return "Selected-player setup records or the next-pit wing request conflict within this frame.";
  if (reason === "player_index_mismatch_in_frame")
    return "The selected-player identity is ambiguous for this frame.";
  if (reason === "operation_ended")
    return "The recording or replay has ended; live observations are cleared.";
  return reason ? reason.replaceAll("_", " ") : "Car setup is unavailable for this frame.";
}

function setupInteger(value: number | null | undefined) {
  return Number.isInteger(value) && value !== null && value !== undefined
    ? String(value)
    : "—";
}

function setupPercent(value: number | null | undefined) {
  return Number.isInteger(value) && value !== null && value !== undefined
    ? `${value}%`
    : "—";
}

function setupFloat(value: number | null | undefined, suffix = "") {
  return typeof value === "number" && Number.isFinite(value)
    ? `${value.toFixed(2)}${suffix}`
    : "—";
}

function finiteNumber(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function safeInteger(value: unknown) {
  return Number.isSafeInteger(value) && (value as number) >= 0
    ? String(value)
    : "—";
}

function safeText(value: unknown) {
  return typeof value === "string" && value.length <= 64 ? value : "—";
}

function recordingStatus(
  recording: RecordingJobRecord | null,
  receiving: boolean,
) {
  if (!recording) return "Recording idle";
  if (recording.status === "starting") return "Preparing UDP recording";
  if (recording.status === "recording")
    return receiving ? "Recording telemetry" : "Listening for telemetry";
  if (recording.status === "stopping") return "Finalizing capture";
  if (recording.status === "complete") return "Capture finalized";
  if (recording.status === "failed") return "Recording failed";
  return "Recording interrupted";
}

function formatDuration(value: number) {
  const totalSeconds = Math.floor(value / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(totalSeconds % 60).padStart(2, "0")}`;
}

function numeric(values: Record<string, unknown>, key: string) {
  const value = values[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function label(value: unknown) {
  if (typeof value !== "string" || !value) return "mode unknown";
  return value.replaceAll("_", " ").toUpperCase();
}

function display(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : String(value);
}

function fixed(value: number | null | undefined, digits: number) {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : value.toFixed(digits);
}

function withUnit(
  value: number | null | undefined,
  unit: string,
  digits = 0,
  multiplier = 1,
) {
  return value === null || value === undefined
    ? "—"
    : `${(value * multiplier).toFixed(digits)} ${unit}`;
}

function formatLapClock(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value))
    return "—";
  const wholeSeconds = Math.floor(value / 1000);
  return `${Math.floor(wholeSeconds / 60)}:${String(wholeSeconds % 60).padStart(2, "0")}.${String(Math.floor(value % 1000)).padStart(3, "0")}`;
}

function formatAge(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "an unknown time";
  return `${(value / 1000).toFixed(1)} seconds`;
}

function pitStatus(value: number | null | undefined) {
  const labels = ["NONE", "PITTING", "PIT AREA"];
  return statusCode(value, labels);
}

function driverStatus(value: number | null | undefined) {
  const labels = ["GARAGE", "FLYING LAP", "IN LAP", "OUT LAP", "ON TRACK"];
  return statusCode(value, labels);
}

function statusCode(value: number | null | undefined, labels: string[]) {
  if (value === null || value === undefined) return "—";
  return `${value} · ${labels[value] ?? "UNKNOWN"}`;
}

function liveUnavailableReason(reason: string | null) {
  if (reason === "same_frame_car_telemetry_unavailable")
    return "Lap data is present, but matching player inputs are unavailable in this frame.";
  if (reason === "player_index_mismatch_in_frame")
    return "The player index differs between packets in this frame.";
  if (reason === "player_car_index_out_of_range")
    return "The packet identifies a player car index outside the supported range.";
  if (reason === "player_identity_conflicted_within_frame")
    return "Waiting for packets with a consistent player index.";
  if (reason === "car_telemetry_decode_failed")
    return "Car telemetry was malformed for this frame.";
  if (reason === "lap_data_decode_failed")
    return "Lap data was malformed for this frame.";
  return "The current player telemetry is unavailable.";
}

function liveCarStatusUnavailableReason(reason: string | null) {
  if (reason === "status_packet_missing")
    return "Lap Data is available, but no Car Status packet matched this frame.";
  if (reason === "player_index_mismatch")
    return "The Car Status packet belongs to a different player index.";
  if (reason === "status_packet_malformed_or_unsupported")
    return "Car Status was malformed or uses an unsupported packet version.";
  if (reason === "conflicting_status_packets")
    return "Conflicting Car Status updates were received for this frame.";
  if (reason === "same_frame_player_lap_missing")
    return "Car Status arrived without a valid same-frame player Lap Data packet.";
  if (reason === "receive_provenance_unavailable")
    return "Receive-time evidence for the selected Car Status frame is unavailable.";
  if (reason === "lap_data_decode_failed" || reason === "lap_data_adapter_unsupported")
    return "The same-frame player Lap Data packet is unavailable.";
  return "The current player Car Status is unavailable.";
}

function liveLapTimingUnavailableReason(reason: string | null) {
  if (reason === "receive_provenance_unavailable")
    return "Receive-time evidence for the selected Lap Data frame is unavailable.";
  if (reason === "lap_data_decode_failed")
    return "The selected player's Lap Data packet was malformed.";
  if (reason === "player_car_index_out_of_range")
    return "Lap Data identifies a player car outside the supported range.";
  if (reason === "conflicting_lap_data_packets")
    return "Conflicting selected-player Lap Data updates arrived in this frame.";
  if (reason === "flashback_boundary")
    return "Timing was cleared at a flashback boundary.";
  if (reason === "session_time_regression")
    return "Timing was cleared after a session clock regression.";
  if (reason === "event_evidence_unknown")
    return "Timing was cleared because the event boundary could not be identified.";
  return "The selected player's Lap Data timing is unavailable.";
}
