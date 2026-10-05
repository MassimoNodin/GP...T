"use client";

import { useEffect, useRef, useState } from "react";
import type { RecordingCatalogSourceRecord } from "@/lib/api";

type SaveFileHandle = {
  createWritable(): Promise<WritableStream<Uint8Array>>;
};

type SaveFilePickerWindow = Window & {
  showSaveFilePicker?: (options: {
    suggestedName: string;
  }) => Promise<SaveFileHandle>;
};

export default function RecordingDownloadButton({
  source,
}: {
  source: RecordingCatalogSourceRecord | null;
}) {
  const [busy, setBusy] = useState(false);
  const [supportsSavePicker, setSupportsSavePicker] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    setSupportsSavePicker(
      typeof (window as SaveFilePickerWindow).showSaveFilePicker === "function",
    );
    return () => controller.current?.abort();
  }, []);

  const available = Boolean(source?.available && source.download_version);

  async function download() {
    if (!source || !available || busy) return;
    setMessage(null);
    const url = `/api/recording-sources/${encodeURIComponent(source.capture_id)}/download?version=${encodeURIComponent(source.download_version)}`;
    const picker = (window as SaveFilePickerWindow).showSaveFilePicker;
    if (!picker) {
      setMessage(
        "This browser cannot safely save a streamed capture. Open GP...T in Chrome or Edge to download it.",
      );
      return;
    }

    const request = new AbortController();
    controller.current = request;
    setBusy(true);
    let responseBody: ReadableStream<Uint8Array> | null = null;
    let pipeStarted = false;
    try {
      const destination = await picker.call(window, {
        suggestedName: source.display_name,
      });
      const response = await fetch(url, {
        cache: "no-store",
        credentials: "same-origin",
        signal: request.signal,
      });
      if (!response.ok) {
        setMessage(downloadError(await responseReason(response)));
        return;
      }
      if (!response.body) {
        setMessage("The download request returned no file stream.");
        return;
      }
      responseBody = response.body;
      const writable = await destination.createWritable();
      pipeStarted = true;
      await responseBody.pipeTo(writable, { signal: request.signal });
      setMessage(
        "Download finished. This is an unverified file copy; integrity was not checked.",
      );
    } catch (error) {
      if (isAbortError(error)) {
        setMessage("Download cancelled.");
      } else {
        setMessage(
          "The download request failed. Check the local service and retry.",
        );
      }
    } finally {
      request.abort();
      if (responseBody && !pipeStarted && !responseBody.locked) {
        await responseBody.cancel().catch(() => undefined);
      }
      controller.current = null;
      setBusy(false);
    }
  }

  return (
    <div className="recording-download-control">
      <button
        className="import-button secondary-import-button"
        type="button"
        onClick={download}
        disabled={!available || busy || !supportsSavePicker}
      >
        {busy
          ? "Downloading…"
          : supportsSavePicker
            ? "Download capture"
            : "Download unavailable in this browser"}
      </button>
      {busy ? (
        <button
          className="import-button secondary-import-button"
          type="button"
          onClick={() => controller.current?.abort()}
        >
          Cancel download
        </button>
      ) : null}
      <span className="recording-download-note">
        Saves an unverified file copy; no checksum is checked.
      </span>
      {!supportsSavePicker ? (
        <p className="recording-download-message" role="status">
          This browser cannot safely save a streamed capture. Open GP...T in
          Chrome or Edge to download it.
        </p>
      ) : null}
      {message ? (
        <p className="recording-download-message" role="status">
          {message}
        </p>
      ) : null}
    </div>
  );
}

async function responseReason(response: Response) {
  try {
    const body: unknown = await response.json();
    if (
      typeof body === "object" &&
      body !== null &&
      "reason" in body &&
      typeof body.reason === "string"
    ) {
      return body.reason;
    }
  } catch {
    // Keep the HTTP status as the fallback when the error envelope is malformed.
  }
  return `http_${response.status}`;
}

function downloadError(reason: string) {
  switch (reason) {
    case "recording_source_unavailable":
      return "The selected capture file is unavailable. Refresh the catalog and try again.";
    case "recording_source_changed":
      return "The selected file changed since the catalog loaded. Refresh before downloading.";
    case "recording_download_size_limit":
      return "The capture exceeds the configured download size limit.";
    case "recording_source_busy":
      return "The capture is busy in another process. Try again shortly.";
    case "recording_download_limit_reached":
      return "Two capture downloads are already running. Try again shortly.";
    case "local_control_unauthorized":
      return "Local download authorization failed. Restart the local service.";
    default:
      return `The download request failed (${reason}). Check the local service and retry.`;
  }
}

function isAbortError(error: unknown) {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}
