import { humanReason } from "@/features/review-data/reasons";

// Pure state for the Session Library import flow. Nothing here touches the
// network or React, so the reducer and the mappings are tested directly
// (`scripts/library-import.test.mjs`).

export const MAX_IMPORT_FILES = 5;
export const IMPORT_EXTENSION = ".f1ecap";
export const RESTORE_FAILED_WINDOW_MS = 24 * 60 * 60 * 1000;

export type ImportPhase =
  | { kind: "uploading"; sent: number }
  | { kind: "queuing" }
  | { kind: "queued"; jobId: string; position: number | null }
  | { kind: "processing"; jobId: string; percent: number | null; stage: string }
  | { kind: "complete"; jobId: string; sessionHref: string | null }
  | { kind: "failed"; jobId: string | null; reason: string; retryable: boolean }
  | { kind: "cancelled"; jobId: string | null };

export type ImportItem = {
  /** Local unique id for the row. */
  key: string;
  fileName: string;
  bytes: number;
  phase: ImportPhase;
  /** Id of the uploaded capture once the engine has it. */
  captureId: string | null;
  /** Last engine job this row owned; survives a retry's transient `queuing` phase. */
  lastJobId: string | null;
  source: "upload" | "folder" | "restored";
  startedAt: number;
  updatedAt: number;
};

export type ImportState = {
  items: ImportItem[];
  /** Keys the user dismissed; reload-restored items are never resurrected for them. */
  dismissed: string[];
};

export const EMPTY_IMPORT_STATE: ImportState = { items: [], dismissed: [] };

export type ImportAction =
  | {
      type: "add";
      key: string;
      fileName: string;
      bytes: number;
      source: ImportItem["source"];
      captureId?: string | null;
      /** Start in `queuing` (capture already uploaded) instead of `uploading`. */
      queuing?: boolean;
      at: number;
    }
  | { type: "uploadProgress"; key: string; sent: number; at: number }
  | { type: "uploaded"; key: string; captureId: string; at: number }
  | { type: "jobUpdate"; key: string; phase: ImportPhase; at: number }
  | { type: "fail"; key: string; reason: string; retryable: boolean; at: number }
  | { type: "cancelled"; key: string; at: number }
  | { type: "retrying"; key: string; at: number }
  | { type: "dismiss"; key: string }
  | { type: "restore"; items: ImportItem[] };

const TERMINAL = new Set<ImportPhase["kind"]>(["complete", "failed", "cancelled"]);

export function isTerminal(item: ImportItem): boolean {
  return TERMINAL.has(item.phase.kind);
}

export function isActive(item: ImportItem): boolean {
  return !isTerminal(item);
}

/** Phases that the poller follows (the engine owns a job for them). */
export function isPolled(item: ImportItem): boolean {
  return item.phase.kind === "queued" || item.phase.kind === "processing";
}

export function jobIdOf(item: ImportItem): string | null {
  const phase = item.phase;
  return "jobId" in phase ? phase.jobId : item.lastJobId;
}

function patch(state: ImportState, key: string, change: (item: ImportItem) => ImportItem | null): ImportState {
  let touched = false;
  const items = state.items.map((item) => {
    if (item.key !== key) return item;
    const next = change(item);
    if (next === null) return item;
    touched = true;
    return next;
  });
  return touched ? { ...state, items } : state;
}

export function importReducer(state: ImportState, action: ImportAction): ImportState {
  switch (action.type) {
    case "add": {
      if (state.items.some((item) => item.key === action.key)) return state;
      const item: ImportItem = {
        key: action.key,
        fileName: action.fileName,
        bytes: action.bytes,
        phase: action.queuing ? { kind: "queuing" } : { kind: "uploading", sent: 0 },
        captureId: action.captureId ?? null,
        lastJobId: null,
        source: action.source,
        startedAt: action.at,
        updatedAt: action.at,
      };
      return { ...state, items: [item, ...state.items] };
    }
    case "uploadProgress":
      return patch(state, action.key, (item) =>
        item.phase.kind === "uploading"
          ? { ...item, phase: { kind: "uploading", sent: Math.max(item.phase.sent, Math.min(action.sent, item.bytes)) }, updatedAt: action.at }
          : null,
      );
    case "uploaded":
      return patch(state, action.key, (item) =>
        item.phase.kind === "uploading"
          ? { ...item, captureId: action.captureId, phase: { kind: "queuing" }, updatedAt: action.at }
          : null,
      );
    case "jobUpdate":
      return patch(state, action.key, (item) => {
        // Only follow a job the item already owns; late polls never undo a terminal state.
        if (item.phase.kind === "queuing") {
          return "jobId" in action.phase
            ? { ...item, phase: action.phase, lastJobId: action.phase.jobId, updatedAt: action.at }
            : null;
        }
        if (!isPolled(item)) return null;
        const owned = jobIdOf(item);
        if ("jobId" in action.phase && action.phase.jobId !== owned) return null;
        return {
          ...item,
          phase: action.phase,
          lastJobId: "jobId" in action.phase ? action.phase.jobId : item.lastJobId,
          updatedAt: action.at,
        };
      });
    case "fail":
      return patch(state, action.key, (item) =>
        item.phase.kind === "complete" || item.phase.kind === "cancelled"
          ? null
          : {
              ...item,
              phase: { kind: "failed", jobId: jobIdOf(item), reason: action.reason, retryable: action.retryable },
              updatedAt: action.at,
            },
      );
    case "cancelled":
      return patch(state, action.key, (item) =>
        isTerminal(item) ? null : { ...item, phase: { kind: "cancelled", jobId: jobIdOf(item) }, updatedAt: action.at },
      );
    case "retrying":
      return patch(state, action.key, (item) => {
        if (item.phase.kind !== "failed" && item.phase.kind !== "cancelled") return null;
        // With the capture on the engine only the queueing step repeats.
        const phase: ImportPhase = item.captureId !== null ? { kind: "queuing" } : { kind: "uploading", sent: 0 };
        return { ...item, phase, lastJobId: jobIdOf(item), updatedAt: action.at };
      });
    case "dismiss":
      return {
        items: state.items.filter((item) => item.key !== action.key),
        dismissed: state.dismissed.includes(action.key) ? state.dismissed : [...state.dismissed, action.key],
      };
    case "restore": {
      const known = new Set(state.items.map((item) => item.key));
      const jobs = new Set(state.items.map(jobIdOf).filter((id): id is string => id !== null));
      const captures = new Set(state.items.map((item) => item.captureId).filter((id): id is string => id !== null));
      const fresh = action.items.filter((item) => {
        if (known.has(item.key) || state.dismissed.includes(item.key)) return false;
        if (item.captureId !== null && captures.has(item.captureId)) return false;
        const job = jobIdOf(item);
        return job === null || !jobs.has(job);
      });
      return fresh.length === 0 ? state : { ...state, items: [...state.items, ...fresh] };
    }
  }
}

/* ------------------------------------------------------------ presentation */

export type ImportTone = "blue" | "grey" | "red" | "green";

export function ordinal(value: number): string {
  const mod100 = value % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${value}th`;
  switch (value % 10) {
    case 1:
      return `${value}st`;
    case 2:
      return `${value}nd`;
    case 3:
      return `${value}rd`;
    default:
      return `${value}th`;
  }
}

/** Whole percent of an upload, clamped to 0..99 until the engine has the file. */
export function uploadPercent(sent: number, total: number): number | null {
  if (!Number.isFinite(sent) || !Number.isFinite(total) || total <= 0) return null;
  return Math.max(0, Math.min(100, Math.round((sent / total) * 100)));
}

/** `bytes_read / total_bytes` of a running job as a whole percent; null when unknown. */
export function percentOf(progress: { bytesRead: number; totalBytes: number } | null): number | null {
  if (!progress) return null;
  const { bytesRead, totalBytes } = progress;
  if (!Number.isFinite(bytesRead) || !Number.isFinite(totalBytes) || bytesRead <= 0 || totalBytes <= 0) return null;
  return Math.max(0, Math.min(100, Math.round((bytesRead / totalBytes) * 100)));
}

const STAGES: Record<string, string> = {
  queued: "Waiting for its turn",
  starting: "Starting",
  hashing_capture: "Checking the capture",
  reading_packets: "Reading packets",
  writing_traces: "Saving laps and traces",
  complete: "Finishing",
};

/** Human stage line for a job phase code. */
export function stageLabel(phase: string | null | undefined): string {
  if (typeof phase !== "string" || phase === "") return "Processing";
  if (Object.hasOwn(STAGES, phase)) return STAGES[phase];
  const words = phase.replace(/[_\s]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1).toLowerCase() : "Processing";
}

export function badgeOf(item: ImportItem): { tone: ImportTone; label: string } {
  const phase = item.phase;
  switch (phase.kind) {
    case "uploading": {
      const percent = uploadPercent(phase.sent, item.bytes);
      return { tone: "blue", label: percent === null ? "Uploading" : `Uploading ${percent}%` };
    }
    case "queuing":
      return { tone: "blue", label: "Starting import" };
    case "queued":
      return { tone: "grey", label: phase.position === null ? "Queued" : `Queued (${ordinal(phase.position)})` };
    case "processing":
      return { tone: "blue", label: phase.percent === null ? "Processing" : `Processing ${phase.percent}%` };
    case "complete":
      return { tone: "green", label: "Complete" };
    case "failed":
      return { tone: "red", label: "Import failed" };
    case "cancelled":
      return { tone: "grey", label: "Cancelled" };
  }
}

/** Notes column text: the stage while running, the reason after a failure. */
export function noteOf(item: ImportItem): string {
  const phase = item.phase;
  switch (phase.kind) {
    case "uploading":
      return "Uploading the capture to the local engine";
    case "queuing":
      return "Adding the capture to the import queue";
    case "queued":
      return "Waiting for the current import or recording to finish";
    case "processing":
      return phase.stage;
    case "complete":
      return "Imported";
    case "failed":
      return phase.reason;
    case "cancelled":
      return "The import was cancelled";
  }
}

/** Progress for the bar under the badge: null is indeterminate, undefined means no bar. */
export function progressOf(item: ImportItem): number | null | undefined {
  const phase = item.phase;
  switch (phase.kind) {
    case "uploading":
      return uploadPercent(phase.sent, item.bytes);
    case "queuing":
      return null;
    case "queued":
      return null;
    case "processing":
      return phase.percent;
    default:
      return undefined;
  }
}

export function canCancel(item: ImportItem): boolean {
  return item.phase.kind === "uploading" || item.phase.kind === "queued";
}

/** Retry is offered after a failure, and after a cancelled job the engine still knows. */
export function canRetry(item: ImportItem): boolean {
  const phase = item.phase;
  if (phase.kind === "failed") return phase.retryable;
  if (phase.kind === "cancelled") return phase.jobId !== null;
  return false;
}

/** Rows shown in the list: everything not finished. Finished imports show as real rows. */
export function listItems(items: ImportItem[]): ImportItem[] {
  return items.filter((item) => item.phase.kind !== "complete");
}

export function sortNewestFirst(items: ImportItem[]): ImportItem[] {
  return [...items].sort((a, b) => b.updatedAt - a.updatedAt);
}

/* --------------------------------------------------------- file validation */

/** Message for a file the importer cannot take, or null when it is acceptable. */
export function fileProblem(file: { name: string }): string | null {
  return file.name.toLowerCase().endsWith(IMPORT_EXTENSION) ? null : humanReason("recording_upload_content_type_invalid");
}

export function rejectionMessage(reason: "type" | "count"): string {
  return reason === "type"
    ? "Only .f1ecap capture files can be imported."
    : `Import up to ${MAX_IMPORT_FILES} files at a time.`;
}

/* ------------------------------------------------------- response parsing */

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export type JobSnapshot = {
  jobId: string;
  status: "queued" | "running" | "complete" | "failed" | "interrupted" | "cancelled";
  phase: string;
  progress: { bytesRead: number; totalBytes: number } | null;
  failureReason: string | null;
  sessionKey: string | null;
};

const JOB_STATUSES = new Set(["queued", "running", "complete", "failed", "interrupted", "cancelled"]);

/** Reads an import job from an API envelope (`{ data: job }`) or from a bare job. */
export function readJob(body: unknown): JobSnapshot | null {
  const root = record(body);
  const job = record(root?.data) ?? (root && "job_id" in root ? root : null);
  if (!job) return null;
  const jobId = job.job_id;
  const status = job.status;
  if (typeof jobId !== "string" || jobId === "" || typeof status !== "string" || !JOB_STATUSES.has(status)) return null;
  const progress = record(job.progress);
  const bytesRead = finiteNumber(progress?.bytes_read);
  const totalBytes = finiteNumber(progress?.total_bytes);
  const result = record(job.result);
  return {
    jobId,
    status: status as JobSnapshot["status"],
    phase: typeof job.phase === "string" ? job.phase : "",
    progress: bytesRead !== null && totalBytes !== null ? { bytesRead, totalBytes } : null,
    failureReason: typeof job.failure_reason === "string" && job.failure_reason !== "" ? job.failure_reason.slice(0, 128) : null,
    sessionKey: typeof result?.session_key === "string" && result.session_key !== "" ? result.session_key : null,
  };
}

/** Queue positions (`job id -> 1-based position`) from `GET /api/import-jobs/queue`. */
export function readQueuePositions(body: unknown): Map<string, number> {
  const positions = new Map<string, number>();
  const waiting = record(record(body)?.data)?.waiting_jobs;
  if (!Array.isArray(waiting)) return positions;
  waiting.forEach((entry, index) => {
    const job = record(entry);
    if (typeof job?.job_id !== "string") return;
    positions.set(job.job_id, finiteNumber(job.queue_position) ?? index + 1);
  });
  return positions;
}

export function failureText(reason: string | null): string {
  const text = reason ? humanReason(reason) : "";
  return text ? `Import failed: ${text}` : "Import failed.";
}

/** Maps an engine job to the phase of a row. */
export function phaseFromJob(job: JobSnapshot, position: number | null): ImportPhase {
  switch (job.status) {
    case "queued":
      return { kind: "queued", jobId: job.jobId, position };
    case "running":
      return { kind: "processing", jobId: job.jobId, percent: percentOf(job.progress), stage: stageLabel(job.phase) };
    case "complete":
      return {
        kind: "complete",
        jobId: job.jobId,
        sessionHref: job.sessionKey ? `/review/${encodeURIComponent(job.sessionKey)}` : null,
      };
    case "failed":
      return { kind: "failed", jobId: job.jobId, reason: failureText(job.failureReason), retryable: true };
    case "interrupted":
      return { kind: "failed", jobId: job.jobId, reason: "The import was interrupted. Retry to run it again.", retryable: true };
    case "cancelled":
      return { kind: "cancelled", jobId: job.jobId };
  }
}

export type UploadOutcome = { ok: true; captureId: string } | { ok: false; reason: string };

/** Reads the upload route's answer into a capture id or a human message. */
export function readUploadResult(httpStatus: number, text: string): UploadOutcome {
  let body: unknown = null;
  try {
    body = JSON.parse(text);
  } catch {
    body = null;
  }
  const root = record(body);
  const captureId = record(root?.data)?.capture_id;
  if (httpStatus >= 200 && httpStatus < 300 && root?.status === "ok" && typeof captureId === "string" && captureId !== "") {
    return { ok: true, captureId };
  }
  const code = typeof root?.reason === "string" ? root.reason : null;
  if (code) return { ok: false, reason: humanReason(code) };
  return { ok: false, reason: humanReason(httpStatus === 0 || httpStatus >= 500 ? "recording_upload_service_unavailable" : "unavailable") };
}

export type QueueOutcome = { ok: true; job: JobSnapshot } | { ok: false; reason: string };

/** Reads the JSON branch of the import-job routes (create, cancel, retry). */
export function readJobAction(httpStatus: number, text: string): QueueOutcome {
  let body: unknown = null;
  try {
    body = JSON.parse(text);
  } catch {
    body = null;
  }
  const root = record(body);
  const job = readJob(body);
  if (httpStatus >= 200 && httpStatus < 300 && root?.status === "ok" && job) return { ok: true, job };
  const code = typeof root?.reason === "string" ? root.reason : null;
  return { ok: false, reason: humanReason(code ?? (httpStatus === 0 || httpStatus >= 500 ? "local_engine_unavailable" : "unavailable")) };
}

/* ------------------------------------------------------- restore on reload */

/** One capture file in the recordings folder, as the loader reads it. */
export type FolderSource = {
  captureId: string;
  name: string;
  bytes: number;
  modifiedAtUtc: string;
};

/** The subset of a recording-source record the restore mapping needs. */
export type SourceLike = {
  capture_id: string;
  display_name: string;
  byte_size: number;
  modified_at_utc: string;
  latest_job_id: string | null;
  latest_job_status: string | null;
  available: boolean;
};

export type RestoredImports = {
  items: ImportItem[];
  folder: FolderSource[];
};

/**
 * Turns recording sources into in-flight rows (queued or running jobs),
 * failed rows (failed or interrupted within the last 24 hours) and the list of
 * captures that were never imported.
 */
export function restoreImports(sources: SourceLike[], now: number): RestoredImports {
  const items: ImportItem[] = [];
  const folder: FolderSource[] = [];
  for (const source of sources) {
    const modified = Date.parse(source.modified_at_utc);
    const base = {
      key: `restored:${source.capture_id}`,
      fileName: source.display_name,
      bytes: source.byte_size,
      captureId: source.capture_id,
      lastJobId: source.latest_job_id,
      source: "restored" as const,
      startedAt: Number.isFinite(modified) ? modified : now,
      updatedAt: Number.isFinite(modified) ? modified : now,
    };
    const jobId = source.latest_job_id;
    switch (source.latest_job_status) {
      case "queued":
        if (jobId) items.push({ ...base, phase: { kind: "queued", jobId, position: null } });
        break;
      case "running":
        if (jobId) items.push({ ...base, phase: { kind: "processing", jobId, percent: null, stage: "Processing" } });
        break;
      case "failed":
      case "interrupted":
        if (jobId && Number.isFinite(modified) && now - modified <= RESTORE_FAILED_WINDOW_MS) {
          items.push({
            ...base,
            phase: {
              kind: "failed",
              jobId,
              reason:
                source.latest_job_status === "failed"
                  ? "Import failed. Retry to run it again."
                  : "The import was interrupted. Retry to run it again.",
              retryable: true,
            },
          });
        }
        break;
      case null:
        if (source.available) {
          folder.push({
            captureId: source.capture_id,
            name: source.display_name,
            bytes: source.byte_size,
            modifiedAtUtc: source.modified_at_utc,
          });
        }
        break;
      default:
        break;
    }
  }
  return { items, folder };
}

/** "482 MB", "1.2 GB", "640 KB". */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}
