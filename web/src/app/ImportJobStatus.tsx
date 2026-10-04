"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { ApiResponse, ImportJobRecord } from "@/lib/api";

export default function ImportJobStatus({
  initialJob,
  retryAvailable,
}: {
  initialJob: ImportJobRecord | null;
  retryAvailable: boolean | null;
}) {
  const [job, setJob] = useState(initialJob);
  const [unavailable, setUnavailable] = useState(false);
  const router = useRouter();

  useEffect(() => {
    if (!initialJob || isTerminal(initialJob.status)) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      try {
        const response = await fetch(`/api/import-jobs/${initialJob.job_id}`, {
          cache: "no-store",
        });
        const body = (await response.json()) as ApiResponse<ImportJobRecord>;
        if (!response.ok || !body.data) throw new Error("status unavailable");
        if (!active) return;
        setJob(body.data);
        setUnavailable(false);
        if (isTerminal(body.data.status)) {
          router.refresh();
          return;
        }
      } catch {
        if (active) setUnavailable(true);
      }
      if (active) timer = setTimeout(poll, 1000);
    };

    timer = setTimeout(poll, 500);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [initialJob, router]);

  if (!job) return null;

  const progress = job.progress;
  const percent =
    progress && progress.total_bytes > 0
      ? Math.min(
          100,
          Math.floor((progress.bytes_read / progress.total_bytes) * 100),
        )
      : 0;
  const result = job.result;
  const importedSessions = numericResult(result, "session_count");
  const importedAttempts = numericResult(result, "attempts");
  const importedCarObservations = numericResult(result, "car_observations");

  return (
    <section className="import-job panel" aria-live="polite">
      <div className="import-job-heading">
        <div>
          <span className="eyebrow">IMPORT JOB</span>
          <h3>
            {job.status === "complete"
              ? result?.already_imported === true
                ? "Recording already in archive"
                : "Recording added"
              : jobTitle(job)}
          </h3>
        </div>
        <span className={`job-badge job-${job.status}`}>
          {jobLabel(job.status)}
        </span>
      </div>
      {job.status === "queued" || job.status === "running" ? (
        <>
          <p className="import-job-copy">
            {job.status === "queued"
              ? "The local importer is waiting to start."
              : phaseDescription(progress?.phase ?? job.phase)}
          </p>
          {progress && (
            <>
              <div
                className="import-progress-track"
                role="progressbar"
                aria-label="Capture bytes read"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={percent}
              >
                <span style={{ width: `${percent}%` }} />
              </div>
              <div className="import-progress-meta">
                <span>{percent}% of capture read</span>
                <span>
                  {progress.packets_processed.toLocaleString()} packets
                </span>
              </div>
            </>
          )}
          {unavailable && (
            <p className="import-job-note">
              Progress is temporarily unavailable. This page will keep checking.
            </p>
          )}
        </>
      ) : job.status === "complete" ? (
        <p className="import-job-copy">
          {result?.already_imported === true
            ? "The capture checksum matches an existing completed import. The archive is up to date."
            : `${importedSessions ?? 0} session${importedSessions === 1 ? "" : "s"}, ${importedAttempts ?? 0} lap attempts, and ${importedCarObservations ?? 0} car-slot observations are now in the local archive.`}
        </p>
      ) : (
        <>
          <p className="import-job-copy">
            {job.status === "interrupted"
              ? "The API stopped before this import completed. Retry it to resume through the importer."
              : "The capture could not be imported. The existing archive remains available."}
          </p>
          {retryAvailable ? (
            <form action={`/api/import-jobs/${job.job_id}/retry`} method="post">
              <button className="import-button retry-button" type="submit">
                Retry import
              </button>
            </form>
          ) : retryAvailable === false ? (
            <p className="import-job-note">
              This recording file is unavailable. Restore it to the configured
              recordings folder and refresh before retrying.
            </p>
          ) : (
            <p className="import-job-note">
              Recording availability could not be checked. Refresh when the
              local catalog is available before retrying.
            </p>
          )}
        </>
      )}
    </section>
  );
}

function isTerminal(status: ImportJobRecord["status"]) {
  return ["complete", "failed", "interrupted"].includes(status);
}

function jobTitle(job: ImportJobRecord) {
  if (job.status === "queued") return "Import queued";
  if (job.status === "failed") return "Import needs attention";
  if (job.status === "interrupted") return "Import interrupted";
  return "Importing recording";
}

function jobLabel(status: ImportJobRecord["status"]) {
  return status.replaceAll("_", " ").toUpperCase();
}

function phaseDescription(phase: string) {
  if (phase === "hashing_capture") return "Checking capture integrity.";
  if (phase === "reading_packets")
    return "Reading and decoding captured packets.";
  if (phase === "writing_traces")
    return "Writing verified lap traces to the archive.";
  return "Processing the capture.";
}

function numericResult(result: Record<string, unknown> | null, key: string) {
  const value = result?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
