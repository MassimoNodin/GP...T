"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { ApiResponse, RecordingJobRecord } from "@/lib/api";

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
      if (mounted) timer = setTimeout(poll, 1000);
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
    <div className="recording-controls" aria-live="polite">
      <div className="recording-control-copy">
        <div className="recording-state-line">
          <span
            className={`recording-light ${active ? "recording-light-active" : ""}`}
          />
          <strong>{recordingStatus(recording, isReceiving)}</strong>
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
    </div>
  );
}

function recordingStatus(
  recording: RecordingJobRecord | null,
  receiving: boolean,
) {
  if (!recording) return "Recording idle";
  if (recording.status === "starting") return "Starting UDP listener";
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
