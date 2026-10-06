"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { appScreenHref } from "@/lib/navigation";

type UploadReceipt = {
  captureId: string;
  displayName: string;
  byteSize: number;
  sha256: string;
};

export default function RecordingUploadPanel({
  preservedQuery,
}: {
  preservedQuery: string;
}) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<UploadReceipt | null>(null);
  const [selectionHref, setSelectionHref] = useState<string | null>(null);
  const abortController = useRef<AbortController | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);

  useEffect(
    () => () => {
      abortController.current?.abort();
    },
    [],
  );

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || busy) return;
    if (!file.name.toLowerCase().endsWith(".f1ecap")) {
      setMessage("Choose a finished .f1ecap capture file.");
      return;
    }
    if (file.size < 1) {
      setMessage("The selected capture file is empty.");
      return;
    }

    const controller = new AbortController();
    abortController.current = controller;
    setBusy(true);
    setMessage(null);
    setReceipt(null);
    setSelectionHref(null);
    try {
      const response = await fetch("/api/recording-sources/upload", {
        method: "POST",
        headers: {
          "Content-Type": "application/octet-stream",
          "X-Capture-Upload-Filename": encodeURIComponent(file.name),
        },
        body: file,
        cache: "no-store",
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (!response.ok) {
        const reason = await responseReason(response);
        setMessage(uploadError(reason));
        return;
      }
      const body: unknown = await response.json();
      const parsed = readUploadReceipt(body, file.size);
      if (!parsed) {
        setMessage("The local service returned an invalid upload receipt.");
        return;
      }
      setReceipt(parsed);
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      const href = appScreenHref("recordings", preservedQuery, {
        recording_capture_id: parsed.captureId,
      });
      if (href) {
        router.replace(href, { scroll: false });
      } else {
        setSelectionHref(
          `/recordings?recording_capture_id=${encodeURIComponent(parsed.captureId)}`,
        );
      }
    } catch (error) {
      if (isAbortError(error)) {
        setMessage(
          "Cancellation requested. The capture may already have been published; refresh the capture list before retrying.",
        );
      } else {
        setMessage(
          "The upload outcome could not be confirmed. Refresh the capture list before retrying.",
        );
      }
    } finally {
      controller.abort();
      abortController.current = null;
      setBusy(false);
    }
  }

  return (
    <section className="recording-upload" aria-label="Upload a capture">
      <div>
        <h3>Upload a capture</h3>
        <p>
          Stream one <code>.f1ecap</code> file into the local recordings inbox.
          Uploading does not import or change the capture.
        </p>
      </div>
      <form onSubmit={upload}>
        <label>
          <span>CAPTURE FILE</span>
          <input
            ref={fileInput}
            type="file"
            accept=".f1ecap,application/octet-stream"
            disabled={busy}
            onChange={(event) => {
              setFile(event.currentTarget.files?.[0] ?? null);
              setMessage(null);
              setReceipt(null);
              setSelectionHref(null);
            }}
          />
        </label>
        {file ? (
          <p className="recording-upload-selected">
            {file.name} · {formatBytes(file.size)}
          </p>
        ) : null}
        <div className="recording-upload-actions">
          <button
            className="import-button"
            type="submit"
            disabled={!file || busy}
          >
            {busy ? "Uploading…" : "Upload capture"}
          </button>
          {busy ? (
            <button
              className="import-button secondary-import-button"
              type="button"
              onClick={() => abortController.current?.abort()}
            >
              Cancel upload
            </button>
          ) : null}
        </div>
      </form>
      {busy ? (
        <p className="recording-upload-note" role="status">
          Uploading to the configured local folder. The transfer limit is set by
          the local service; no progress is inferred from partial network data.
        </p>
      ) : null}
      {message ? (
        <p className="recording-upload-message" role="status">
          {message}
        </p>
      ) : null}
      {receipt ? (
        <div className="recording-upload-receipt" role="status">
          <strong>Capture uploaded: {receipt.displayName}</strong>
          <span>
            {formatBytes(receipt.byteSize)} · SHA-256 of transferred bytes:{" "}
            {receipt.sha256}
          </span>
          <span>
            The bounded capture header and transferred bytes were checked. This
            does not verify every packet, footer completeness, or telemetry
            quality. Import remains a separate action.
          </span>
          {selectionHref ? <a href={selectionHref}>Open this capture</a> : null}
        </div>
      ) : null}
    </section>
  );
}

function readUploadReceipt(
  value: unknown,
  expectedSize: number,
): UploadReceipt | null {
  if (
    typeof value !== "object" ||
    value === null ||
    !("api_version" in value) ||
    value.api_version !== "v1" ||
    !("status" in value) ||
    value.status !== "ok" ||
    !("data" in value) ||
    typeof value.data !== "object" ||
    value.data === null
  ) {
    return null;
  }
  const data = value.data;
  if (
    !("capture_id" in data) ||
    typeof data.capture_id !== "string" ||
    !/^[a-f0-9]{32}$/.test(data.capture_id) ||
    !("display_name" in data) ||
    typeof data.display_name !== "string" ||
    data.display_name.length < 1 ||
    data.display_name.length > 512 ||
    !("byte_size" in data) ||
    typeof data.byte_size !== "number" ||
    !Number.isSafeInteger(data.byte_size) ||
    data.byte_size !== expectedSize ||
    !("sha256" in data) ||
    typeof data.sha256 !== "string" ||
    !/^[a-f0-9]{64}$/.test(data.sha256) ||
    !("verification_scope" in data) ||
    data.verification_scope !== "capture_header_and_transferred_bytes"
  ) {
    return null;
  }
  return {
    captureId: data.capture_id,
    displayName: data.display_name,
    byteSize: data.byte_size,
    sha256: data.sha256,
  };
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

function uploadError(reason: string) {
  switch (reason) {
    case "recording_upload_publish_unavailable":
    case "recording_upload_publish_changed":
    case "recording_catalog_registration_invalid":
    case "recording_catalog_registration_failed":
      return "The upload outcome could not be confirmed. Refresh the capture list before retrying.";
    case "recording_upload_service_unavailable":
      return "The local upload service is unavailable. Check the local API and try again.";
    case "another_local_operation_is_in_progress":
      return "Finish or stop the active recording, import, or replay before uploading.";
    case "recording_upload_size_limit":
      return "The capture exceeds the upload size limit configured for the local service.";
    case "recording_upload_filename_invalid":
      return "Choose a file whose name ends in .f1ecap.";
    case "recording_upload_capture_header_invalid":
      return "The file does not have a supported F1 Engineer capture header.";
    case "recording_upload_stalled":
      return "The upload stopped receiving data for too long. Refresh the capture list to confirm whether it completed before retrying.";
    case "recording_upload_deadline_exceeded":
      return "The upload exceeded the local service time limit. Refresh the capture list to confirm whether it completed before retrying.";
    case "recording_upload_body_empty":
      return "The selected file is empty.";
    default:
      return `The upload failed (${reason}). Check the local service and retry.`;
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

function formatBytes(value: number) {
  if (value < 1_000_000) return `${(value / 1_000).toFixed(0)} kB`;
  return `${(value / 1_000_000).toFixed(1)} MB`;
}
