import type {
  ApiResponse,
  ImportJobRecord,
  RecordingGroupRecord,
  RecordingJobRecord,
  RecordingSourcePageRecord,
} from "@/lib/api";
import { preservedAppStateQuery, type AppSearchParams } from "@/lib/navigation";
import { requestApi } from "@/lib/api";
import { readRecordingSourcePage } from "@/lib/recording-catalog";
import AppHeader from "../AppHeader";
import { testDataRoute } from "../TestDataRoute";
import RecordingInbox from "../RecordingInbox";
import ReferenceScreen from "../ReferenceScreens";

export default async function RecordingsPage({
  searchParams,
}: {
  searchParams: Promise<AppSearchParams>;
}) {
  const params = await searchParams;
  const syntheticPage = testDataRoute("recordings", params);
  if (syntheticPage) return syntheticPage;
  const preservedQuery = preservedAppStateQuery(params);
  const importJobId = firstParam(params.import_job_id);
  const requestedJob = Boolean(importJobId);
  const catalogApiPath = recordingCatalogApiPath(params);
  const catalogFilters = {
    query: firstParam(params.recording_q) ?? "",
    latestJobStatus: firstParam(params.recording_import_status) ?? "all",
    availability: firstParam(params.recording_availability) ?? "all",
    offset: firstParam(params.recording_offset) ?? "0",
    selectedCaptureId: firstParam(params.recording_capture_id) ?? "",
  };
  const [
    loadedCatalogResponse,
    recordingResponse,
    groupResponse,
    loadedJobResponse,
  ] = await Promise.all([
    catalogApiPath
      ? requestApi<unknown>(catalogApiPath)
      : Promise.resolve(null),
    requestApi<RecordingJobRecord>("/api/v1/recordings/current"),
    requestApi<RecordingGroupRecord>("/api/v1/recording-groups/current"),
    importJobId && /^[a-f0-9]{32}$/.test(importJobId)
      ? requestApi<ImportJobRecord>(
          `/api/v1/import-jobs/${encodeURIComponent(importJobId)}`,
        )
      : Promise.resolve(null),
  ]);
  const sourcesResponse = readCatalogResponse(
    loadedCatalogResponse,
    catalogFilters,
  );
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
      <main className="page-content reference-page-content" id="main-content" tabIndex={-1}>
        <ReferenceScreen screen="recordings" preservedQuery={preservedQuery} />
        <details className="ref-workflow-details" id="recording-workflow">
          <summary id="recording-controls-trigger">Open recording, import, and replay controls</summary>
          <div className="ref-workflow-content">
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
          catalogFilters={catalogFilters}
          recordingResponse={recordingResponse}
          groupResponse={groupResponse}
          jobResponse={jobResponse}
          importError={firstParam(params.import_error)}
          returnTo="recordings"
          preservedQuery={preservedQuery}
        />
          </div>
        </details>
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

function recordingCatalogApiPath(params: AppSearchParams): string | null {
  const routeToApi = [
    ["recording_q", "q"],
    ["recording_import_status", "latest_job_status"],
    ["recording_availability", "availability"],
    ["recording_offset", "offset"],
    ["recording_capture_id", "selected_capture_id"],
  ] as const;
  const query = new URLSearchParams();
  for (const [routeKey, apiKey] of routeToApi) {
    const value = params[routeKey];
    const entries =
      value === undefined ? [] : Array.isArray(value) ? value : [value];
    if (entries.length > 8 || entries.some((entry) => entry.length > 512)) {
      return null;
    }
    for (const entry of entries) query.append(apiKey, entry);
  }
  const encoded = query.toString();
  if (encoded.length > 4096) return null;
  return encoded
    ? `/api/v1/recording-sources/page?${encoded}`
    : "/api/v1/recording-sources/page";
}

function readCatalogResponse(
  response: ApiResponse<unknown> | null,
  expected: {
    query: string;
    latestJobStatus: string;
    availability: string;
    offset: string;
    selectedCaptureId: string;
  },
): ApiResponse<RecordingSourcePageRecord> | null {
  if (response === null) return null;
  if (response.status === "unavailable") {
    return { ...response, data: null };
  }
  const data = readRecordingSourcePage(response.data);
  return data && recordingCatalogPageMatches(data, expected)
    ? { ...response, data }
    : {
        api_version: "v1",
        status: "unavailable",
        data: null,
        reason: "recording_catalog_response_invalid",
      };
}

function recordingCatalogPageMatches(
  data: RecordingSourcePageRecord,
  expected: {
    query: string;
    latestJobStatus: string;
    availability: string;
    offset: string;
    selectedCaptureId: string;
  },
) {
  if (!/^(0|[0-9]{1,6})$/.test(expected.offset)) return false;
  const offset = Number(expected.offset);
  if (!Number.isSafeInteger(offset) || offset > 100_000) return false;
  return (
    data.limit === 25 &&
    data.offset === offset &&
    data.filters.query === expected.query &&
    data.filters.latest_job_status === expected.latestJobStatus &&
    data.filters.availability === expected.availability &&
    data.filters.selected_capture_id === (expected.selectedCaptureId || null)
  );
}

function firstParam(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
