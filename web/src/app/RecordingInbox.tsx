import type {
  ApiResponse,
  ImportJobRecord,
  RecordingJobRecord,
  RecordingSourceRecord,
} from "@/lib/api";
import ImportJobStatus from "./ImportJobStatus";
import RecordingControls from "./RecordingControls";

const importErrorText: Record<string, string> = {
  busy: "Another recording is importing. Wait for it to finish, then try again.",
  capture_unavailable: "That recording is no longer available in the inbox.",
  job_unavailable: "That import job is no longer available.",
  unavailable:
    "The local import service is unavailable. Check the API and try again.",
  import_control_not_authorized:
    "Local import authorization is unavailable. Restart the API and dashboard.",
};

export default function RecordingInbox({
  sourcesResponse,
  recordingResponse,
  jobResponse,
  importError,
}: {
  sourcesResponse: ApiResponse<RecordingSourceRecord[]> | null;
  recordingResponse: ApiResponse<RecordingJobRecord> | null;
  jobResponse: ApiResponse<ImportJobRecord> | null;
  importError?: string;
}) {
  const sources = sourcesResponse?.data ?? [];
  const available = sourcesResponse?.status === "ok";
  const job = jobResponse?.data ?? null;

  return (
    <section className="recording-inbox panel">
      <div className="inbox-heading">
        <div>
          <div className="eyebrow">LOCAL RECORDING INBOX</div>
          <h2>Add a capture to the archive</h2>
          <p>
            Copy a finished <code>.f1ecap</code> recording into the configured
            recordings folder, then refresh this page.
          </p>
        </div>
        <span className="count-pill">{available ? sources.length : "—"}</span>
      </div>

      <RecordingControls
        initialRecording={recordingResponse?.data ?? null}
        initialError={!recordingResponse || recordingResponse.status !== "ok"}
      />

      {importError && (
        <p className="import-alert" role="alert">
          {importErrorText[importError] ??
            "The import could not be started. Check the local API and try again."}
        </p>
      )}

      {!available ? (
        <p className="inbox-empty">
          The local recording catalog is unavailable. Check that the API is
          running, then refresh.
        </p>
      ) : sources.length === 0 ? (
        <p className="inbox-empty">
          No finished captures found. The default folder is{" "}
          <code>recordings/</code>.
        </p>
      ) : (
        <div className="inbox-list">
          {sources.map((source) => (
            <div className="inbox-item" key={source.capture_id}>
              <div className="inbox-file">
                <strong title={source.display_name}>
                  {source.display_name}
                </strong>
                <span>
                  {formatBytes(source.byte_size)} · changed{" "}
                  {formatDate(source.modified_at_utc)}
                  {source.latest_job_status
                    ? ` · last import ${source.latest_job_status}`
                    : ""}
                  {!source.available ? " · file unavailable" : ""}
                </span>
              </div>
              <div className="inbox-actions">
                {source.latest_job_id && (
                  <a
                    className="import-button secondary-import-button"
                    href={`/?import_job_id=${encodeURIComponent(source.latest_job_id)}`}
                  >
                    {source.latest_job_status === "queued" ||
                    source.latest_job_status === "running"
                      ? "View progress"
                      : "View latest job"}
                  </a>
                )}
                {source.latest_job_status === "complete" &&
                source.latest_job_run_id ? (
                  <a
                    className="import-button secondary-import-button"
                    href={`/?run_id=${encodeURIComponent(source.latest_job_run_id)}`}
                  >
                    Run evidence ↗
                  </a>
                ) : null}
                {source.available &&
                source.latest_job_id &&
                (source.latest_job_status === "failed" ||
                  source.latest_job_status === "interrupted") ? (
                  <form
                    action={`/api/import-jobs/${source.latest_job_id}/retry`}
                    method="post"
                  >
                    <button className="import-button" type="submit">
                      Retry import
                    </button>
                  </form>
                ) : source.available &&
                  source.latest_job_status !== "queued" &&
                  source.latest_job_status !== "running" ? (
                  <form action="/api/import-jobs" method="post">
                    <input
                      type="hidden"
                      name="capture_id"
                      value={source.capture_id}
                    />
                    <button className="import-button" type="submit">
                      {source.latest_job_status === "complete"
                        ? "Check for updates"
                        : "Import recording"}
                    </button>
                  </form>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      )}

      {jobResponse?.status === "unavailable" && (
        <p className="import-job-note">
          The requested import status is unavailable. The recording catalog
          remains usable.
        </p>
      )}
      <ImportJobStatus
        initialJob={job}
        retryAvailable={
          job
            ? sourcesResponse?.status === "ok"
              ? (sources.find((source) => source.capture_id === job.capture_id)
                  ?.available ?? null)
              : null
            : null
        }
      />
    </section>
  );
}

function formatBytes(value: number) {
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "time unknown"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}
