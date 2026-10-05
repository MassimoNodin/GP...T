import type {
  ApiResponse,
  ImportJobRecord,
  RecordingGroupRecord,
  RecordingJobRecord,
  RecordingSourcePageRecord,
} from "@/lib/api";
import ImportJobStatus from "./ImportJobStatus";
import ReplayControls from "./ReplayControls";
import RecordingControls from "./RecordingControls";
import {
  appScreenHref,
  isSelectionTransferBlocked,
  type ImportReturnScreen,
} from "@/lib/navigation";

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
  catalogFilters,
  recordingResponse,
  groupResponse,
  jobResponse,
  importError,
  returnTo = "dashboard",
  preservedQuery = "",
}: {
  sourcesResponse: ApiResponse<RecordingSourcePageRecord> | null;
  catalogFilters: {
    query: string;
    latestJobStatus: string;
    availability: string;
    offset: string;
    selectedCaptureId: string;
  };
  recordingResponse: ApiResponse<RecordingJobRecord> | null;
  groupResponse: ApiResponse<RecordingGroupRecord> | null;
  jobResponse: ApiResponse<ImportJobRecord> | null;
  importError?: string;
  returnTo?: ImportReturnScreen;
  preservedQuery?: string;
}) {
  const catalog = sourcesResponse?.data ?? null;
  const sources = catalog?.items ?? [];
  const selectedSource = catalog?.selected_capture ?? null;
  const selectedCaptureId =
    catalogFilters.selectedCaptureId ||
    catalog?.filters.selected_capture_id ||
    "";
  const outsidePage = selectedSource
    ? !sources.some((source) => source.capture_id === selectedSource.capture_id)
    : false;
  const displaySources = outsidePage ? [...sources, selectedSource!] : sources;
  const available = sourcesResponse?.status === "ok" && catalog !== null;
  const job = jobResponse?.data ?? null;
  const queryValue = catalogFilters.query;
  const latestJobStatus = [
    "all",
    "none",
    "queued",
    "running",
    "complete",
    "failed",
    "interrupted",
  ].includes(catalogFilters.latestJobStatus)
    ? catalogFilters.latestJobStatus
    : "all";
  const availabilityFilter = ["all", "available", "missing"].includes(
    catalogFilters.availability,
  )
    ? catalogFilters.availability
    : "all";
  const preservedEntries = Array.from(
    new URLSearchParams(preservedQuery).entries(),
  ).filter(([key]) => !recordingCatalogKeys.has(key));
  const selectedCaptureQuery = selectedCaptureId ? (
    <input
      type="hidden"
      name="recording_capture_id"
      value={selectedCaptureId}
    />
  ) : null;
  const pageOffset = catalog?.offset ?? parseOffset(catalogFilters.offset);
  const pageLimit = catalog?.limit ?? 25;
  const visibleFrom =
    catalog && catalog.total_count > 0
      ? Math.min(catalog.offset + 1, catalog.total_count)
      : 0;
  const visibleThrough = catalog
    ? Math.min(catalog.offset + sources.length, catalog.total_count)
    : 0;

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
        <span className="count-pill">
          {available ? catalog.total_count.toLocaleString() : "—"}
        </span>
      </div>

      <RecordingControls
        initialRecording={recordingResponse?.data ?? null}
        initialGroup={groupResponse?.data ?? null}
        initialError={!recordingResponse || recordingResponse.status !== "ok"}
        preservedQuery={preservedQuery}
      />
      <ReplayControls
        sources={sources}
        selectedSource={selectedSource}
        selectedCaptureId={selectedCaptureId}
        preservedQuery={preservedQuery}
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
      ) : (
        <>
          <form
            action="/recordings"
            method="get"
            className="recording-catalog-filters"
            aria-label="Filter the recording catalog"
          >
            {preservedEntries.map(([key, value], index) => (
              <input
                key={`${key}-${index}`}
                type="hidden"
                name={key}
                value={value}
              />
            ))}
            {selectedCaptureQuery}
            <label>
              <span>SEARCH CAPTURES</span>
              <input
                name="recording_q"
                type="search"
                maxLength={128}
                value={queryValue}
                placeholder="Filename or capture ID"
              />
            </label>
            <label>
              <span>LAST IMPORT</span>
              <select name="recording_import_status" value={latestJobStatus}>
                <option value="all">All statuses</option>
                <option value="none">No import job</option>
                <option value="queued">Queued</option>
                <option value="running">Running</option>
                <option value="complete">Complete</option>
                <option value="failed">Failed</option>
                <option value="interrupted">Interrupted</option>
              </select>
            </label>
            <label>
              <span>FILE</span>
              <select name="recording_availability" value={availabilityFilter}>
                <option value="all">All files</option>
                <option value="available">Available</option>
                <option value="missing">Missing</option>
              </select>
            </label>
            <button className="import-button" type="submit">
              Apply filters
            </button>
            <a
              className="import-button secondary-import-button"
              href={clearCatalogHref(preservedQuery)}
            >
              Clear filters
            </a>
          </form>
          {catalog.total_count === 0 ? (
            <p className="inbox-empty">
              {queryValue ||
              latestJobStatus !== "all" ||
              availabilityFilter !== "all" ? (
                "No captures match these filters."
              ) : (
                <>
                  No captures found. The default folder is{" "}
                  <code>recordings/</code>.
                </>
              )}
            </p>
          ) : null}
          {displaySources.length > 0 ? (
            <div className="inbox-list">
              {displaySources.map((source) => {
                const selectedOutsidePage =
                  outsidePage && source.capture_id === selectedCaptureId;
                return (
                  <div
                    className={`inbox-item ${source.capture_id === selectedCaptureId ? "inbox-item-selected" : ""}`}
                    key={source.capture_id}
                  >
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
                        {selectedOutsidePage
                          ? " · selected outside this page"
                          : ""}
                      </span>
                    </div>
                    <div className="inbox-actions">
                      {source.latest_job_id && (
                        <a
                          className="import-button secondary-import-button"
                          href={
                            appScreenHref(returnTo, preservedQuery, {
                              import_job_id: source.latest_job_id,
                            }) ?? undefined
                          }
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
                          href={
                            appScreenHref(
                              "sessions",
                              isSelectionTransferBlocked(preservedQuery)
                                ? ""
                                : preservedQuery,
                              { run_id: source.latest_job_run_id },
                            ) ?? undefined
                          }
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
                          <input
                            type="hidden"
                            name="return_to"
                            value={returnTo}
                          />
                          <input
                            type="hidden"
                            name="preserved_query"
                            value={preservedQuery}
                          />
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
                          <input
                            type="hidden"
                            name="return_to"
                            value={returnTo}
                          />
                          <input
                            type="hidden"
                            name="preserved_query"
                            value={preservedQuery}
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
                );
              })}
            </div>
          ) : null}
          <div
            className="recording-catalog-pagination"
            aria-label="Recording catalog pages"
          >
            <span>
              Showing {visibleFrom.toLocaleString()}–
              {visibleThrough.toLocaleString()} of{" "}
              {catalog.total_count.toLocaleString()}
            </span>
            <div>
              {pageOffset === 0 ? (
                <span
                  className="import-button secondary-import-button"
                  aria-disabled="true"
                >
                  Previous
                </span>
              ) : (
                <a
                  className="import-button secondary-import-button"
                  href={catalogHref(
                    preservedQuery,
                    Math.max(0, pageOffset - pageLimit),
                  )}
                >
                  Previous
                </a>
              )}
              {!catalog.has_more ? (
                <span
                  className="import-button secondary-import-button"
                  aria-disabled="true"
                >
                  Next
                </span>
              ) : (
                <a
                  className="import-button secondary-import-button"
                  href={catalogHref(preservedQuery, pageOffset + pageLimit)}
                >
                  Next
                </a>
              )}
            </div>
          </div>
        </>
      )}

      {jobResponse?.status === "unavailable" && (
        <p className="import-job-note">
          The requested import status is unavailable. The recording catalog
          remains usable.
        </p>
      )}
      <ImportJobStatus
        initialJob={job}
        returnTo={returnTo}
        preservedQuery={preservedQuery}
        retryAvailable={
          job
            ? sourcesResponse?.status === "ok"
              ? (displaySources.find(
                  (source) => source.capture_id === job.capture_id,
                )?.available ?? null)
              : null
            : null
        }
      />
    </section>
  );
}

const recordingCatalogKeys = new Set([
  "recording_q",
  "recording_import_status",
  "recording_availability",
  "recording_offset",
  "recording_capture_id",
]);

const recordingCatalogFilterKeys = [
  "recording_q",
  "recording_import_status",
  "recording_availability",
  "recording_offset",
];

function catalogHref(preservedQuery: string, offset: number) {
  const query = new URLSearchParams(preservedQuery);
  query.delete("recording_offset");
  if (offset > 0) query.set("recording_offset", String(offset));
  const encoded = query.toString();
  return encoded ? `/recordings?${encoded}` : "/recordings";
}

function clearCatalogHref(preservedQuery: string) {
  const query = new URLSearchParams(preservedQuery);
  for (const key of recordingCatalogFilterKeys) query.delete(key);
  const encoded = query.toString();
  return encoded ? `/recordings?${encoded}` : "/recordings";
}

function parseOffset(value: string) {
  if (!/^(0|[1-9][0-9]{0,5})$/.test(value)) return 0;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed <= 100_000 ? parsed : 0;
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
