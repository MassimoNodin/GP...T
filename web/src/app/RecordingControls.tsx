"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type {
  ApiResponse,
  LiveCarStatusRecord,
  LiveLapTimingRecord,
  LiveTelemetryRecord,
  RecordingJobRecord,
} from "@/lib/api";

const activeStatuses = new Set(["starting", "recording", "stopping"]);

export default function RecordingControls({
  initialRecording,
  initialError,
}: {
  initialRecording: RecordingJobRecord | null;
  initialError: boolean;
}) {
  const [recording, setRecording] = useState(initialRecording);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();
  const active = recording ? activeStatuses.has(recording.status) : false;

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
        </>
      )}
    </div>
  );
}

function LiveTelemetryPanel({ telemetry }: { telemetry: LiveTelemetryRecord }) {
  const statusCopy: Record<LiveTelemetryRecord["status"], string> = {
    waiting: "Waiting for a synchronized player frame.",
    fresh: "Player telemetry is updating from the current recording.",
    stale: `Last player frame was ${formatAge(telemetry.age_ms)} ago.`,
    unsupported:
      "Recording continues. The live view does not support this packet format yet.",
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
          <div className="eyebrow">LIVE PLAYER TELEMETRY</div>
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
    </section>
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

function LiveCarStatusPanel({ telemetry }: { telemetry: LiveCarStatusRecord }) {
  const statusCopy: Record<LiveCarStatusRecord["status"], string> = {
    waiting: "Waiting for same-frame player Lap Data and Car Status.",
    fresh: "Car Status is matched to the current player frame.",
    stale: `Last matched Car Status frame was ${formatAge(telemetry.age_ms)} ago.`,
    unsupported:
      "Recording continues. This Car Status packet version is unsupported.",
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
          <div className="eyebrow">LIVE CAR STATUS</div>
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

function LiveLapTimingPanel({ telemetry }: { telemetry: LiveLapTimingRecord }) {
  const statusCopy: Record<LiveLapTimingRecord["status"], string> = {
    waiting: "Waiting for selected-player Lap Data.",
    fresh: "Game-reported lap timing is updating for the selected player.",
    stale: `Last Lap Data timing update was ${formatAge(telemetry.age_ms)} ago.`,
    unsupported:
      "Recording continues. This Lap Data packet version is unsupported.",
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
          <div className="eyebrow">LIVE REPORTED LAP TIMING</div>
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
