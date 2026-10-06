"use client";

import { useEffect, useState } from "react";
import type { ImportQueueRecord } from "@/lib/api";
import { readImportQueueEnvelope } from "@/lib/import-queue";
import type { ImportReturnScreen } from "@/lib/navigation";

const identifierPattern = /^[a-f0-9]{32}$/;
const reservations = new Set([
  "idle",
  "recording",
  "import",
  "replay",
  "upload",
]);

export default function ImportQueue({
  returnTo,
  preservedQuery,
}: {
  returnTo: ImportReturnScreen;
  preservedQuery: string;
}) {
  const [queue, setQueue] = useState<ImportQueueRecord | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let request: AbortController | null = null;

    const poll = async () => {
      const controller = new AbortController();
      request = controller;
      const deadline = setTimeout(() => controller.abort(), 5000);
      try {
        const response = await fetch("/api/import-jobs/queue", {
          cache: "no-store",
          signal: controller.signal,
        });
        const body: unknown = await response.json();
        const parsed = readImportQueueEnvelope(response.ok, body);
        if (!parsed) throw new Error("queue unavailable");
        if (!active) return;
        setQueue(parsed);
        setUnavailable(false);
      } catch {
        if (active) {
          setQueue(null);
          setUnavailable(true);
        }
      } finally {
        clearTimeout(deadline);
        if (request === controller) request = null;
      }
      if (active) timer = setTimeout(poll, 1200);
    };

    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
      request?.abort();
    };
  }, []);

  return (
    <section className="import-queue panel" aria-live="polite">
      <div className="import-job-heading">
        <div>
          <span className="eyebrow">IMPORT QUEUE</span>
          <h3>Waiting for the local service</h3>
        </div>
        <span className="job-badge">
          {queue ? `${queue.waiting_count} WAITING` : "CHECKING"}
        </span>
      </div>
      {unavailable || !queue ? (
        <p
          className="import-job-note"
          role={unavailable ? "status" : undefined}
        >
          The import queue is temporarily unavailable. The recording catalog
          remains usable.
        </p>
      ) : (
        <>
          <p className="import-job-copy">
            {reservationCopy(queue.blocking_reservation)}
          </p>
          {queue.running_job && (
            <div className="import-queue-row import-queue-running">
              <span>Import running · {shortId(queue.running_job.job_id)}</span>
              <span>{queue.running_job.phase.replaceAll("_", " ")}</span>
            </div>
          )}
          {queue.waiting_jobs.length === 0 ? (
            <p className="import-job-note">No imports are waiting.</p>
          ) : (
            <ol className="import-queue-list">
              {queue.waiting_jobs.map((job) => (
                <li className="import-queue-row" key={job.job_id}>
                  <span>
                    {job.queue_position}. Import · {shortId(job.job_id)}
                  </span>
                  <form
                    action={`/api/import-jobs/${job.job_id}/cancel`}
                    method="post"
                  >
                    <input type="hidden" name="return_to" value={returnTo} />
                    <input
                      type="hidden"
                      name="preserved_query"
                      value={preservedQuery}
                    />
                    <button
                      className="queue-cancel-button"
                      type="submit"
                      aria-label={`Cancel queued import at position ${job.queue_position}`}
                    >
                      Cancel
                    </button>
                  </form>
                </li>
              ))}
            </ol>
          )}
          {queue.waiting_count >= 16 && (
            <p className="import-job-note">The waiting queue is full.</p>
          )}
        </>
      )}
    </section>
  );
}

function reservationCopy(
  reservation: ImportQueueRecord["blocking_reservation"],
) {
  if (reservation === "idle")
    return "The importer is ready for the next waiting capture.";
  if (reservation === "recording")
    return "Recording owns the service; imports will start after it stops.";
  if (reservation === "replay")
    return "Replay owns the service; imports will start after it stops.";
  if (reservation === "upload")
    return "A capture upload owns the service; imports will start after it finishes.";
  return "An import currently owns the service.";
}

function shortId(value: string) {
  return value.slice(0, 8);
}
