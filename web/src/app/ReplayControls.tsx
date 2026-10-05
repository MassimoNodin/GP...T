"use client";

import { useEffect, useState } from "react";
import type {
  ApiResponse,
  RecordingSourceRecord,
  ReplayRecord,
} from "@/lib/api";
import {
  LiveCarStatusPanel,
  LiveLapTimingPanel,
  LiveTelemetryPanel,
} from "./RecordingControls";

const activeStates = new Set([
  "starting",
  "playing",
  "pausing",
  "paused",
  "resuming",
  "stepping",
  "stopping",
]);
const replaySpeeds = [0.5, 1, 2, 4];

export default function ReplayControls({
  sources,
}: {
  sources: RecordingSourceRecord[];
}) {
  const availableSources = sources.filter((source) => source.available);
  const [captureId, setCaptureId] = useState(
    availableSources[0]?.capture_id ?? "",
  );
  const [speed, setSpeed] = useState(1);
  const [playback, setPlayback] = useState<ReplayRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const active = playback ? activeStates.has(playback.state) : false;

  useEffect(() => {
    let mounted = true;
    fetch("/api/replays/current", { cache: "no-store" })
      .then(async (response) => {
        const body = (await response.json()) as ApiResponse<ReplayRecord>;
        if (!response.ok || body.status !== "ok") {
          throw new Error(body.reason ?? "replay_status_unavailable");
        }
        if (mounted) setPlayback(body.data);
      })
      .catch(() => {
        if (mounted) setError("Replay status is temporarily unavailable.");
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!active) return;
    let mounted = true;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      try {
        const response = await fetch("/api/replays/current", {
          cache: "no-store",
        });
        const body = (await response.json()) as ApiResponse<ReplayRecord>;
        if (!response.ok || body.status !== "ok") {
          throw new Error(body.reason ?? "replay_status_unavailable");
        }
        if (!mounted) return;
        setPlayback(body.data);
        setError(null);
        if (!body.data || !activeStates.has(body.data.state)) return;
      } catch {
        if (mounted) setError("Replay status is temporarily unavailable.");
      }
      if (mounted) timer = setTimeout(poll, 250);
    };

    timer = setTimeout(poll, 250);
    return () => {
      mounted = false;
      clearTimeout(timer);
    };
  }, [active]);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/replays/start", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ capture_id: captureId, speed }),
      });
      const body = (await response.json()) as ApiResponse<ReplayRecord>;
      if (!response.ok || !body.data)
        throw new Error(body.reason ?? "replay_start_failed");
      setPlayback(body.data);
    } catch (caught) {
      setError(replayError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    if (!playback) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/replays/${playback.playback_id}/stop`,
        {
          method: "POST",
        },
      );
      const body = (await response.json()) as ApiResponse<ReplayRecord>;
      if (!response.ok || !body.data)
        throw new Error(body.reason ?? "replay_stop_failed");
      setPlayback(body.data);
    } catch (caught) {
      setError(replayError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function control(action: "pause" | "resume" | "step") {
    if (!playback) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/replays/${playback.playback_id}/${action}`,
        {
          method: "POST",
        },
      );
      const body = (await response.json()) as ApiResponse<ReplayRecord>;
      if (!response.ok || !body.data) {
        throw new Error(body.reason ?? `replay_${action}_failed`);
      }
      setPlayback(body.data);
    } catch (caught) {
      setError(replayError(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="replay-controls" aria-label="Diagnostic capture replay">
      <div className="replay-control-copy">
        <div className="recording-state-line">
          <span
            className={`recording-light ${active ? "recording-light-active" : ""}`}
          />
          <strong aria-live="polite">
            {playback ? replayStatus(playback.state) : "Diagnostic replay"}
          </strong>
          {playback && (
            <span className="recording-port">{playback.speed}×</span>
          )}
        </div>
        <p>
          {playback
            ? replayDescription(playback)
            : "Replay a catalog capture through the live monitors without starting the game or creating a new recording."}
        </p>
        {playback && (
          <div className="recording-metrics">
            <span>{formatDuration(playback.elapsed_ms)}</span>
            <span>
              {playback.datagrams_delivered.toLocaleString()} packets delivered
            </span>
            {playback.state === "completed" && (
              <span>
                source footer{" "}
                {playback.capture_complete ? "complete" : "incomplete"}
              </span>
            )}
            {playback.source_stable === false && (
              <span>source changed while reading</span>
            )}
          </div>
        )}
      </div>

      <div className="replay-control-actions">
        <label className="replay-select-label">
          <span>CAPTURE</span>
          <select
            value={captureId}
            onChange={(event) => setCaptureId(event.target.value)}
            disabled={active || busy || availableSources.length === 0}
            aria-label="Capture to replay"
          >
            {availableSources.map((source) => (
              <option key={source.capture_id} value={source.capture_id}>
                {source.display_name}
              </option>
            ))}
          </select>
        </label>
        <label className="replay-select-label replay-speed-label">
          <span>SPEED</span>
          <select
            value={speed}
            onChange={(event) => setSpeed(Number(event.target.value))}
            disabled={active || busy}
            aria-label="Replay speed"
          >
            {replaySpeeds.map((value) => (
              <option key={value} value={value}>
                {value}×
              </option>
            ))}
          </select>
        </label>
        {active ? (
          <>
            {playback?.state === "paused" ? (
              <>
                <button
                  className="import-button"
                  type="button"
                  onClick={() => control("step")}
                  disabled={busy}
                >
                  Step one packet
                </button>
                <button
                  className="import-button"
                  type="button"
                  onClick={() => control("resume")}
                  disabled={busy}
                >
                  Resume replay
                </button>
              </>
            ) : playback?.state === "playing" ? (
              <button
                className="import-button"
                type="button"
                onClick={() => control("pause")}
                disabled={busy}
              >
                Pause replay
              </button>
            ) : (
              <button className="import-button" type="button" disabled>
                {replayActionStatus(playback?.state)}
              </button>
            )}
            <button
              className="import-button stop-recording-button"
              type="button"
              onClick={stop}
              disabled={busy || playback?.state === "stopping"}
            >
              {playback?.state === "stopping" ? "Stopping…" : "Stop replay"}
            </button>
          </>
        ) : (
          <button
            className="import-button"
            type="button"
            onClick={start}
            disabled={busy || availableSources.length === 0 || !captureId}
          >
            Start paused
          </button>
        )}
      </div>

      {availableSources.length === 0 && (
        <p className="recording-control-alert" role="status">
          No available catalog captures can be replayed yet.
        </p>
      )}
      {error && (
        <p className="recording-control-alert" role="alert">
          {error}
        </p>
      )}
      {playback && (
        <>
          <LiveTelemetryPanel
            telemetry={playback.live_telemetry}
            sourceKind="replay"
          />
          <LiveCarStatusPanel
            telemetry={playback.live_car_status}
            sourceKind="replay"
          />
          <LiveLapTimingPanel
            telemetry={playback.live_lap_timing}
            sourceKind="replay"
          />
        </>
      )}
    </section>
  );
}

function replayStatus(state: ReplayRecord["state"]) {
  const labels: Record<ReplayRecord["state"], string> = {
    starting: "Preparing replay",
    playing: "Replaying capture",
    pausing: "Pausing replay",
    paused: "Replay paused",
    resuming: "Resuming replay",
    stepping: "Stepping one packet",
    stopping: "Stopping replay",
    stopped: "Replay stopped",
    completed: "Replay completed",
    failed: "Replay failed",
  };
  return labels[state];
}

function replayDescription(playback: ReplayRecord) {
  if (playback.state === "starting") {
    return `Opening ${playback.capture_name}. Replay will start paused so you can inspect packets one at a time.`;
  }
  if (playback.state === "playing") {
    return `${playback.capture_name} at ${playback.speed}×. Monitor freshness measures playback delivery recency, not original capture recency or quality.`;
  }
  if (playback.state === "pausing") {
    return "Waiting for the replay worker to reach a packet boundary. Delivery stops when the paused state is acknowledged.";
  }
  if (playback.state === "paused") {
    return `Paused after ${playback.datagrams_delivered.toLocaleString()} packets. Monitor freshness continues to age while replay is paused.`;
  }
  if (playback.state === "resuming")
    return "Resuming the original capture pacing without catching up across the pause.";
  if (playback.state === "stepping")
    return "Delivering one raw packet in capture order, then pausing again.";
  if (playback.state === "completed") {
    const footer =
      typeof playback.capture_completion?.status === "string"
        ? playback.capture_completion.status
        : playback.capture_complete
          ? "complete"
          : "incomplete or unavailable";
    return `Read ${playback.datagrams_delivered.toLocaleString()} packets from ${playback.capture_name}. The source capture footer reports ${footer}; playback does not change that evidence.`;
  }
  if (playback.state === "stopped") {
    return `Stopped after ${playback.datagrams_delivered.toLocaleString()} delivered packets. The source capture was left unchanged.`;
  }
  if (playback.state === "failed") {
    return `Replay failed${playback.failure_reason ? `: ${playback.failure_reason.replaceAll("_", " ")}` : "."} The source capture was left unchanged.`;
  }
  if (playback.state === "stopping")
    return "Stopping and closing the capture reader.";
  return `Opening ${playback.capture_name}. Playback will not create an import or recording.`;
}

function replayActionStatus(state: ReplayRecord["state"] | undefined) {
  if (state === "pausing") return "Waiting for pause…";
  if (state === "resuming") return "Resuming…";
  if (state === "stepping") return "Stepping…";
  if (state === "stopping") return "Stopping…";
  return "Preparing…";
}

function replayError(caught: unknown) {
  const reason =
    caught instanceof Error ? caught.message : "replay_request_failed";
  if (reason.includes("another_local_operation_is_in_progress")) {
    return "An import, recording, or replay operation is already in progress.";
  }
  if (reason.includes("capture_id_unavailable")) {
    return "That capture is no longer available in the local inbox.";
  }
  if (reason.includes("replay_controller_unavailable")) {
    return "The local replay service is unavailable. Check the API and refresh.";
  }
  if (reason.includes("replay_speed_unsupported")) {
    return "Choose a supported replay speed.";
  }
  if (reason.includes("replay_not_paused")) {
    return "Wait for replay to pause before stepping or resuming.";
  }
  if (reason.includes("replay_step_in_progress")) {
    return "Wait for the current packet step to finish before stepping again.";
  }
  if (reason.includes("replay_not_playing")) {
    return "Replay must be playing before it can be paused.";
  }
  return "The replay request could not be completed.";
}

function formatDuration(value: number) {
  const totalSeconds = Math.floor(value / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(totalSeconds % 60).padStart(2, "0")}`;
}
