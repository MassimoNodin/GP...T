"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { classifyDrop } from "@/features/ui-foundation/Dropzone";
import { useToast } from "@/features/ui-foundation/Toast";
import {
  EMPTY_IMPORT_STATE,
  IMPORT_EXTENSION,
  MAX_IMPORT_FILES,
  failureText,
  fileProblem,
  importReducer,
  isPolled,
  jobIdOf,
  phaseFromJob,
  readJob,
  readJobAction,
  readQueuePositions,
  readUploadResult,
  rejectionMessage,
  sortNewestFirst,
  type FolderSource,
  type ImportItem,
  type ImportState,
} from "./import-state";

export type ImportDrawerSection = "recent" | "folder";

export type ImportsApi = {
  /** The engine answered; importing is possible. */
  enabled: boolean;
  /** Newest first. */
  items: ImportItem[];
  /** Captures in the recordings folder that were never imported. `null` when the folder could not be read. */
  folder: FolderSource[] | null;
  /** Never-imported captures in total (the list holds one page). */
  folderTotal: number;
  drawer: ImportDrawerSection | null;
  openDrawer: (section: ImportDrawerSection) => void;
  closeDrawer: () => void;
  pickFiles: () => void;
  startFiles: (files: File[]) => void;
  reject: (reason: "type" | "count") => void;
  importFromFolder: (source: FolderSource) => void;
  cancel: (key: string) => void;
  retry: (key: string) => void;
  dismiss: (key: string) => void;
  refreshLibrary: () => void;
};

const NOOP_API: ImportsApi = {
  enabled: false,
  items: [],
  folder: null,
  folderTotal: 0,
  drawer: null,
  openDrawer: () => {},
  closeDrawer: () => {},
  pickFiles: () => {},
  startFiles: () => {},
  reject: () => {},
  importFromFolder: () => {},
  cancel: () => {},
  retry: () => {},
  dismiss: () => {},
  refreshLibrary: () => {},
};

const ImportsContext = createContext<ImportsApi>(NOOP_API);

export function useImports(): ImportsApi {
  return useContext(ImportsContext);
}

type UploadHandle = { abort: () => void };

type UploadResult = { kind: "done"; captureId: string } | { kind: "failed"; reason: string } | { kind: "aborted" };

function uploadCapture(
  file: File,
  onProgress: (sent: number) => void,
  register: (handle: UploadHandle) => void,
): Promise<UploadResult> {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    register({ abort: () => xhr.abort() });
    xhr.open("POST", "/api/recording-sources/upload");
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("X-Capture-Upload-Filename", encodeURIComponent(file.name));
    xhr.upload.onprogress = (event) => onProgress(event.loaded);
    xhr.onload = () => {
      const outcome = readUploadResult(xhr.status, xhr.responseText);
      resolve(outcome.ok ? { kind: "done", captureId: outcome.captureId } : { kind: "failed", reason: outcome.reason });
    };
    xhr.onerror = () => {
      const outcome = readUploadResult(0, "");
      resolve({ kind: "failed", reason: outcome.ok ? "" : outcome.reason });
    };
    xhr.onabort = () => resolve({ kind: "aborted" });
    xhr.send(file);
  });
}

async function postJson(path: string, body?: Record<string, unknown>): Promise<{ status: number; text: string }> {
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: body
        ? { Accept: "application/json", "Content-Type": "application/json" }
        : { Accept: "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    return { status: response.status, text: await response.text() };
  } catch {
    return { status: 0, text: "" };
  }
}

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `import-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function stem(fileName: string): string {
  return fileName.toLowerCase().endsWith(IMPORT_EXTENSION) ? fileName.slice(0, -IMPORT_EXTENSION.length) : fileName;
}

const POLL_VISIBLE_MS = 1000;
const POLL_HIDDEN_MS = 2000;

export function ImportProvider({
  enabled,
  restored,
  folder,
  folderTotal,
  children,
}: {
  enabled: boolean;
  restored: ImportItem[];
  folder: FolderSource[] | null;
  folderTotal: number;
  children: ReactNode;
}) {
  const router = useRouter();
  const toast = useToast();
  const [state, dispatch] = useReducer(importReducer, undefined, (): ImportState => ({
    ...EMPTY_IMPORT_STATE,
    items: restored,
  }));
  const [drawer, setDrawer] = useState<ImportDrawerSection | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const files = useRef(new Map<string, File>());
  const uploads = useRef(new Map<string, UploadHandle>());
  const finished = useRef(new Set<string>());
  const itemsRef = useRef<ImportItem[]>(state.items);
  const toastRef = useRef(toast);
  const routerRef = useRef(router);

  useEffect(() => {
    itemsRef.current = state.items;
    toastRef.current = toast;
    routerRef.current = router;
  });

  // A refreshed server model may know about jobs this tab has not seen yet.
  useEffect(() => {
    dispatch({ type: "restore", items: restored });
  }, [restored]);

  const queueCapture = useCallback(async (key: string, captureId: string) => {
    const result = await postJson("/api/import-jobs", { capture_id: captureId, queue_if_busy: true });
    const outcome = readJobAction(result.status, result.text);
    if (!outcome.ok) {
      dispatch({ type: "fail", key, reason: outcome.reason, retryable: true, at: Date.now() });
      return;
    }
    dispatch({ type: "jobUpdate", key, phase: phaseFromJob(outcome.job, null), at: Date.now() });
  }, []);

  const runUpload = useCallback(
    async (key: string, file: File) => {
      const result = await uploadCapture(
        file,
        (sent) => dispatch({ type: "uploadProgress", key, sent, at: Date.now() }),
        (handle) => uploads.current.set(key, handle),
      );
      uploads.current.delete(key);
      if (result.kind === "aborted") {
        dispatch({ type: "cancelled", key, at: Date.now() });
        return;
      }
      if (result.kind === "failed") {
        dispatch({ type: "fail", key, reason: result.reason, retryable: true, at: Date.now() });
        return;
      }
      dispatch({ type: "uploaded", key, captureId: result.captureId, at: Date.now() });
      await queueCapture(key, result.captureId);
    },
    [queueCapture],
  );

  const startFiles = useCallback(
    (picked: File[]) => {
      if (!enabled) return;
      const outcome = classifyDrop(picked, [IMPORT_EXTENSION], MAX_IMPORT_FILES);
      if (!outcome.ok) {
        toast.push({ tone: "error", title: rejectionMessage(outcome.reason) });
        return;
      }
      for (const file of outcome.files) {
        const key = newKey();
        const problem = fileProblem(file);
        const at = Date.now();
        dispatch({ type: "add", key, fileName: file.name, bytes: file.size, source: "upload", at });
        if (problem) {
          dispatch({ type: "fail", key, reason: problem, retryable: false, at });
          continue;
        }
        files.current.set(key, file);
        void runUpload(key, file);
      }
    },
    [enabled, runUpload, toast],
  );

  const importFromFolder = useCallback(
    (source: FolderSource) => {
      if (!enabled) return;
      const key = `folder:${source.captureId}`;
      dispatch({
        type: "add",
        key,
        fileName: source.name,
        bytes: source.bytes,
        source: "folder",
        captureId: source.captureId,
        queuing: true,
        at: Date.now(),
      });
      void queueCapture(key, source.captureId);
    },
    [enabled, queueCapture],
  );

  const cancel = useCallback((key: string) => {
    const item = itemsRef.current.find((entry) => entry.key === key);
    if (!item) return;
    if (item.phase.kind === "uploading") {
      uploads.current.get(key)?.abort();
      return;
    }
    const jobId = jobIdOf(item);
    if (item.phase.kind !== "queued" || jobId === null) return;
    void postJson(`/api/import-jobs/${jobId}/cancel`).then((result) => {
      const outcome = readJobAction(result.status, result.text);
      if (outcome.ok) {
        dispatch({ type: "jobUpdate", key, phase: phaseFromJob(outcome.job, null), at: Date.now() });
      } else {
        // The engine may already have started the job; the poller shows the real state.
        toastRef.current.push({ tone: "error", title: "Could not cancel the import", body: outcome.reason });
      }
    });
  }, []);

  const retry = useCallback(
    (key: string) => {
      const item = itemsRef.current.find((entry) => entry.key === key);
      if (!item || (item.phase.kind !== "failed" && item.phase.kind !== "cancelled")) return;
      const jobId = jobIdOf(item);
      const file = files.current.get(key) ?? null;
      if (jobId === null && item.captureId === null && file === null) return;
      finished.current.delete(key);
      dispatch({ type: "retrying", key, at: Date.now() });
      if (jobId !== null) {
        void postJson(`/api/import-jobs/${jobId}/retry`).then((result) => {
          const outcome = readJobAction(result.status, result.text);
          if (outcome.ok) {
            dispatch({ type: "jobUpdate", key, phase: phaseFromJob(outcome.job, null), at: Date.now() });
          } else {
            dispatch({ type: "fail", key, reason: outcome.reason, retryable: true, at: Date.now() });
          }
        });
      } else if (item.captureId !== null) {
        void queueCapture(key, item.captureId);
      } else if (file) {
        void runUpload(key, file);
      }
    },
    [queueCapture, runUpload],
  );

  const dismiss = useCallback((key: string) => {
    files.current.delete(key);
    dispatch({ type: "dismiss", key });
  }, []);

  const refreshLibrary = useCallback(() => router.refresh(), [router]);

  // Follow queued and running jobs until they reach a terminal status.
  const pollKey = state.items
    .filter(isPolled)
    .map((item) => `${item.key}:${jobIdOf(item)}`)
    .join("|");
  useEffect(() => {
    if (pollKey === "") return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function pollOne(item: ImportItem, positions: Map<string, number>) {
      const jobId = jobIdOf(item);
      if (jobId === null) return;
      let response: Response;
      try {
        response = await fetch(`/api/import-jobs/${jobId}`, { headers: { Accept: "application/json" }, cache: "no-store" });
      } catch {
        return; // transient: keep following the job
      }
      if (stopped) return;
      if (response.status === 404) {
        dispatch({
          type: "fail",
          key: item.key,
          reason: failureText("import_job_unavailable"),
          retryable: false,
          at: Date.now(),
        });
        return;
      }
      if (!response.ok) return;
      const job = readJob(await response.json().catch(() => null));
      if (!job || job.jobId !== jobId || stopped) return;
      const phase = phaseFromJob(job, positions.get(jobId) ?? null);
      dispatch({ type: "jobUpdate", key: item.key, phase, at: Date.now() });
      if (finished.current.has(item.key)) return;
      if (phase.kind === "complete") {
        finished.current.add(item.key);
        toastRef.current.push({
          tone: "success",
          title: `${stem(item.fileName)} imported`,
          action: phase.sessionHref ? { label: "Open session", href: phase.sessionHref } : undefined,
        });
        routerRef.current.refresh();
      } else if (phase.kind === "failed") {
        finished.current.add(item.key);
        toastRef.current.push({ tone: "error", title: "Import failed", body: phase.reason });
      }
    }

    async function tick() {
      const polled = itemsRef.current.filter(isPolled);
      if (polled.length > 0) {
        let positions = new Map<string, number>();
        if (polled.some((item) => item.phase.kind === "queued")) {
          try {
            const response = await fetch("/api/import-jobs/queue", { cache: "no-store" });
            if (response.ok) positions = readQueuePositions(await response.json().catch(() => null));
          } catch {
            positions = new Map();
          }
        }
        await Promise.all(polled.map((item) => pollOne(item, positions)));
      }
      if (!stopped) timer = setTimeout(tick, document.hidden ? POLL_HIDDEN_MS : POLL_VISIBLE_MS);
    }

    timer = setTimeout(tick, 0);
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [pollKey]);

  const items = useMemo(() => sortNewestFirst(state.items), [state.items]);
  const api = useMemo<ImportsApi>(
    () => ({
      enabled,
      items,
      folder,
      folderTotal,
      drawer,
      openDrawer: setDrawer,
      closeDrawer: () => setDrawer(null),
      pickFiles: () => input.current?.click(),
      startFiles,
      reject: (reason) => toast.push({ tone: "error", title: rejectionMessage(reason) }),
      importFromFolder,
      cancel,
      retry,
      dismiss,
      refreshLibrary,
    }),
    [enabled, items, folder, folderTotal, drawer, startFiles, toast, importFromFolder, cancel, retry, dismiss, refreshLibrary],
  );

  return (
    <ImportsContext.Provider value={api}>
      {children}
      <input
        ref={input}
        type="file"
        accept={IMPORT_EXTENSION}
        multiple
        hidden
        tabIndex={-1}
        aria-label="Choose .f1ecap capture files"
        onChange={(event) => {
          const picked = Array.from(event.target.files ?? []);
          event.target.value = "";
          if (picked.length > 0) startFiles(picked);
        }}
      />
    </ImportsContext.Provider>
  );
}
