import type {
  ApiResponse,
  ImportJobRecord,
  RecordingGroupRecord,
  RecordingJobRecord,
  RecordingSourceRecord,
} from "@/lib/api";
import { preservedAppStateQuery, type AppSearchParams } from "@/lib/navigation";
import { requestApi } from "@/lib/api";
import AppHeader from "../AppHeader";
import RecordingInbox from "../RecordingInbox";

export default async function RecordingsPage({
  searchParams,
}: {
  searchParams: Promise<AppSearchParams>;
}) {
  const params = await searchParams;
  const preservedQuery = preservedAppStateQuery(params);
  const importJobId = firstParam(params.import_job_id);
  const requestedJob = Boolean(importJobId);
  const [sourcesResponse, recordingResponse, groupResponse, loadedJobResponse] =
    await Promise.all([
      requestApi<RecordingSourceRecord[]>("/api/v1/recording-sources"),
      requestApi<RecordingJobRecord>("/api/v1/recordings/current"),
      requestApi<RecordingGroupRecord>("/api/v1/recording-groups/current"),
      importJobId && /^[a-f0-9]{32}$/.test(importJobId)
        ? requestApi<ImportJobRecord>(
            `/api/v1/import-jobs/${encodeURIComponent(importJobId)}`,
          )
        : Promise.resolve(null),
    ]);
  const jobResponse: ApiResponse<ImportJobRecord> | null = requestedJob
    ? (loadedJobResponse ?? {
        api_version: "v1",
        status: "unavailable",
        data: null,
        reason: "import_job_status_unavailable",
      })
    : null;

  return (
    <div className="app-shell">
      <AppHeader active="recordings" preservedQuery={preservedQuery} />
      <main className="page-content" id="main-content" tabIndex={-1}>
        <section className="recordings-page-intro">
          <div className="eyebrow">RECORDINGS / CAPTURE AND REPLAY</div>
          <h1>
            Capture.
            <br />
            <span>Review the evidence.</span>
          </h1>
          <p>
            Start a local telemetry capture, import a finished recording, or
            replay an available capture. Recording, import, and replay share the
            local service reservation.
          </p>
        </section>
        <RecordingInbox
          sourcesResponse={sourcesResponse}
          recordingResponse={recordingResponse}
          groupResponse={groupResponse}
          jobResponse={jobResponse}
          importError={firstParam(params.import_error)}
          returnTo="recordings"
          preservedQuery={preservedQuery}
        />
      </main>
      <footer className="footer-bar">
        <span>
          GP...T <b>·</b> LOCAL FIRST
        </span>
        <span>Recording and replay controls · Local telemetry only</span>
      </footer>
    </div>
  );
}

function firstParam(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
