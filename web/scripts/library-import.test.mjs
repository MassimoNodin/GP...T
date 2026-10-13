import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const typescript = require("typescript");
const featuresRoot = new URL("../src/features/", import.meta.url);

function transpile(source, fileName) {
  return typescript.transpileModule(source, {
    fileName,
    compilerOptions: {
      module: typescript.ModuleKind.ESNext,
      target: typescript.ScriptTarget.ES2022,
    },
  }).outputText;
}

function toDataUrl(code) {
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}

/** Transpiles a source file and points the listed import specifiers at data URLs. */
async function urlOf(relativePath, imports = {}) {
  let code = transpile(await readFile(new URL(relativePath, featuresRoot), "utf8"), relativePath);
  for (const [specifier, url] of Object.entries(imports)) {
    code = code.replaceAll(`from "${specifier}"`, `from ${JSON.stringify(url)}`);
  }
  return toDataUrl(code);
}

const reasonsUrl = await urlOf("review-data/reasons.ts");
const formatUrl = await urlOf("review-data/format.ts");
const evidenceUrl = await urlOf("review-data/session-evidence.ts", { "./format": formatUrl });
const fixturesUrl = await urlOf("review-data/fixtures.ts");
const importStateUrl = await urlOf("session-library/imports/import-state.ts", {
  "@/features/review-data/reasons": reasonsUrl,
});
const rowsUrl = await urlOf("session-library/library-rows.ts", {
  "@/features/review-data/session-evidence": evidenceUrl,
  "@/features/review-data/format": formatUrl,
  "@/features/review-data/fixtures": fixturesUrl,
});

const state = await import(importStateUrl);
const rows = await import(rowsUrl);

/* ----------------------------------------------------------------- helpers */

const NOW = Date.parse("2026-10-12T12:00:00Z");
const JOB = "a".repeat(32);
const CAPTURE = "b".repeat(32);

function run(actions, start = state.EMPTY_IMPORT_STATE) {
  return actions.reduce((current, action) => state.importReducer(current, action), start);
}

const add = (overrides = {}) => ({
  type: "add",
  key: "k1",
  fileName: "monaco.f1ecap",
  bytes: 1000,
  source: "upload",
  at: 1,
  ...overrides,
});

const phase = (current, key = "k1") => current.items.find((item) => item.key === key)?.phase;
const item = (current, key = "k1") => current.items.find((entry) => entry.key === key);

/* ----------------------------------------------------------------- reducer */

test("an uploaded capture moves through upload, queue, processing and complete", () => {
  let current = run([add()]);
  assert.deepEqual(phase(current), { kind: "uploading", sent: 0 });

  current = run([{ type: "uploadProgress", key: "k1", sent: 420, at: 2 }], current);
  assert.equal(state.badgeOf(item(current)).label, "Uploading 42%");
  assert.equal(state.badgeOf(item(current)).tone, "blue");
  assert.equal(state.progressOf(item(current)), 42);

  current = run([{ type: "uploaded", key: "k1", captureId: CAPTURE, at: 3 }], current);
  assert.deepEqual(phase(current), { kind: "queuing" });
  assert.equal(item(current).captureId, CAPTURE);
  assert.equal(state.badgeOf(item(current)).label, "Starting import");

  current = run([{ type: "jobUpdate", key: "k1", phase: { kind: "queued", jobId: JOB, position: 2 }, at: 4 }], current);
  assert.equal(state.badgeOf(item(current)).label, "Queued (2nd)");
  assert.equal(state.badgeOf(item(current)).tone, "grey");
  assert.equal(state.canCancel(item(current)), true);

  current = run(
    [{ type: "jobUpdate", key: "k1", phase: { kind: "processing", jobId: JOB, percent: 68, stage: "Reading packets" }, at: 5 }],
    current,
  );
  assert.equal(state.badgeOf(item(current)).label, "Processing 68%");
  assert.equal(state.noteOf(item(current)), "Reading packets");
  assert.equal(state.canCancel(item(current)), false, "running jobs cannot be cancelled");

  current = run(
    [{ type: "jobUpdate", key: "k1", phase: { kind: "complete", jobId: JOB, sessionHref: "/review/abc" }, at: 6 }],
    current,
  );
  assert.equal(phase(current).kind, "complete");
  assert.deepEqual(state.listItems(current.items), [], "finished imports leave the list");
  assert.equal(state.sortNewestFirst(current.items).length, 1);
});

test("upload progress only moves forward and ignores other phases", () => {
  let current = run([add(), { type: "uploadProgress", key: "k1", sent: 600, at: 2 }]);
  current = run([{ type: "uploadProgress", key: "k1", sent: 300, at: 3 }], current);
  assert.equal(phase(current).sent, 600);
  current = run([{ type: "uploadProgress", key: "k1", sent: 9000, at: 4 }], current);
  assert.equal(phase(current).sent, 1000, "clamped to the file size");
  current = run([{ type: "uploaded", key: "k1", captureId: CAPTURE, at: 5 }, { type: "uploadProgress", key: "k1", sent: 1, at: 6 }], current);
  assert.deepEqual(phase(current), { kind: "queuing" });
});

test("cancel during upload is terminal until retried; late events are ignored", () => {
  let current = run([add(), { type: "uploadProgress", key: "k1", sent: 10, at: 2 }, { type: "cancelled", key: "k1", at: 3 }]);
  assert.deepEqual(phase(current), { kind: "cancelled", jobId: null });
  assert.equal(state.badgeOf(item(current)).label, "Cancelled");
  assert.equal(state.canRetry(item(current)), false, "no job id to retry: the user adds the file again");

  current = run([{ type: "uploaded", key: "k1", captureId: CAPTURE, at: 4 }, { type: "uploadProgress", key: "k1", sent: 500, at: 5 }], current);
  assert.equal(phase(current).kind, "cancelled");

  current = run([{ type: "retrying", key: "k1", at: 6 }], current);
  assert.deepEqual(phase(current), { kind: "uploading", sent: 0 }, "retry without a capture id uploads again");
});

test("failures keep their reason and the job id; retry resumes at the right step", () => {
  let current = run([
    add(),
    { type: "uploaded", key: "k1", captureId: CAPTURE, at: 2 },
    { type: "jobUpdate", key: "k1", phase: { kind: "queued", jobId: JOB, position: 1 }, at: 3 },
    { type: "jobUpdate", key: "k1", phase: { kind: "failed", jobId: JOB, reason: "Import failed: Capture corrupt", retryable: true }, at: 4 },
  ]);
  assert.equal(state.badgeOf(item(current)).label, "Import failed");
  assert.equal(state.badgeOf(item(current)).tone, "red");
  assert.equal(state.noteOf(item(current)), "Import failed: Capture corrupt");
  assert.equal(state.canRetry(item(current)), true);

  current = run([{ type: "retrying", key: "k1", at: 5 }], current);
  assert.deepEqual(phase(current), { kind: "queuing" }, "the capture is on the engine, so only queueing repeats");
  assert.equal(state.jobIdOf(item(current)), JOB, "the job id survives the transient phase");

  current = run([{ type: "fail", key: "k1", reason: "The local engine is unavailable.", retryable: true, at: 6 }], current);
  assert.deepEqual(phase(current), { kind: "failed", jobId: JOB, reason: "The local engine is unavailable.", retryable: true });

  current = run([{ type: "jobUpdate", key: "k1", phase: { kind: "queued", jobId: JOB, position: null }, at: 7 }], current);
  assert.equal(phase(current).kind, "failed", "a failed row is only revived by an explicit retry");
});

test("late polls never undo a terminal state or switch jobs", () => {
  const base = run([
    add(),
    { type: "uploaded", key: "k1", captureId: CAPTURE, at: 2 },
    { type: "jobUpdate", key: "k1", phase: { kind: "queued", jobId: JOB, position: 1 }, at: 3 },
  ]);
  const other = run([{ type: "jobUpdate", key: "k1", phase: { kind: "processing", jobId: "c".repeat(32), percent: 5, stage: "x" }, at: 4 }], base);
  assert.deepEqual(phase(other), phase(base), "another job id is ignored");

  const cancelled = run([{ type: "jobUpdate", key: "k1", phase: { kind: "cancelled", jobId: JOB }, at: 5 }], base);
  const revived = run([{ type: "jobUpdate", key: "k1", phase: { kind: "processing", jobId: JOB, percent: 9, stage: "x" }, at: 6 }], cancelled);
  assert.equal(phase(revived).kind, "cancelled");
  assert.equal(state.canRetry(item(cancelled)), true, "a cancelled job the engine knows can be retried");

  const done = run([{ type: "fail", key: "k1", reason: "late", retryable: true, at: 7 }], run([
    { type: "jobUpdate", key: "k1", phase: { kind: "complete", jobId: JOB, sessionHref: null }, at: 6 },
  ], base));
  assert.equal(phase(done).kind, "complete", "complete rows are not failed afterwards");
});

test("dismissed rows stay dismissed and restore does not duplicate known work", () => {
  const restored = state.restoreImports(
    [
      { capture_id: CAPTURE, display_name: "x.f1ecap", byte_size: 5, modified_at_utc: "2026-10-12T11:00:00Z", latest_job_id: JOB, latest_job_status: "queued", available: true },
    ],
    NOW,
  ).items;
  assert.equal(restored.length, 1);

  let current = run([{ type: "restore", items: restored }]);
  assert.equal(current.items.length, 1);
  current = run([{ type: "restore", items: restored }], current);
  assert.equal(current.items.length, 1, "restoring twice adds nothing");

  const local = run([add({ key: "u1", fileName: "x.f1ecap" }), { type: "uploaded", key: "u1", captureId: CAPTURE, at: 2 }]);
  assert.equal(run([{ type: "restore", items: restored }], local).items.length, 1, "same capture already tracked in this tab");

  current = run([{ type: "dismiss", key: restored[0].key }], current);
  assert.equal(current.items.length, 0);
  current = run([{ type: "restore", items: restored }], current);
  assert.equal(current.items.length, 0, "a dismissed item is not resurrected by a refresh");
});

/* ----------------------------------------------------- mappings and parsing */

test("percent comes from bytes read over total bytes and needs both", () => {
  assert.equal(state.percentOf({ bytesRead: 680, totalBytes: 1000 }), 68);
  assert.equal(state.percentOf({ bytesRead: 1, totalBytes: 3 }), 33);
  assert.equal(state.percentOf({ bytesRead: 0, totalBytes: 1000 }), null);
  assert.equal(state.percentOf({ bytesRead: 10, totalBytes: 0 }), null);
  assert.equal(state.percentOf({ bytesRead: 2000, totalBytes: 1000 }), 100);
  assert.equal(state.percentOf(null), null);
});

test("job envelopes map to row phases, with stage names", () => {
  const job = (overrides) => ({
    status: "ok",
    data: { job_id: JOB, status: "running", phase: "reading_packets", progress: { phase: "reading_packets", packets_processed: 5, bytes_read: 250, total_bytes: 1000 }, failure_reason: null, result: null, ...overrides },
  });
  const running = state.readJob(job({}));
  assert.deepEqual(state.phaseFromJob(running, null), { kind: "processing", jobId: JOB, percent: 25, stage: "Reading packets" });

  const hashing = state.readJob(job({ phase: "hashing_capture", progress: null }));
  assert.deepEqual(state.phaseFromJob(hashing, null), { kind: "processing", jobId: JOB, percent: null, stage: "Checking the capture" });
  assert.equal(state.badgeOf({ phase: state.phaseFromJob(hashing, null), bytes: 1 }).label, "Processing");

  const queued = state.readJob(job({ status: "queued", phase: "queued" }));
  assert.deepEqual(state.phaseFromJob(queued, 3), { kind: "queued", jobId: JOB, position: 3 });

  const complete = state.readJob(job({ status: "complete", phase: "complete", result: { session_key: "abc/def" } }));
  assert.deepEqual(state.phaseFromJob(complete, null), { kind: "complete", jobId: JOB, sessionHref: "/review/abc%2Fdef" });
  const noKey = state.readJob(job({ status: "complete", phase: "complete", result: { run_id: "r" } }));
  assert.equal(state.phaseFromJob(noKey, null).sessionHref, null);

  const failed = state.readJob(job({ status: "failed", phase: "failed", failure_reason: "capture_unavailable" }));
  assert.deepEqual(state.phaseFromJob(failed, null), {
    kind: "failed",
    jobId: JOB,
    reason: "Import failed: The uploaded capture could not be found.",
    retryable: true,
  });
  const failedNoReason = state.readJob(job({ status: "failed", phase: "failed" }));
  assert.equal(state.phaseFromJob(failedNoReason, null).reason, "Import failed.");

  const interrupted = state.readJob(job({ status: "interrupted", phase: "interrupted" }));
  assert.match(state.phaseFromJob(interrupted, null).reason, /interrupted/i);
  assert.equal(state.phaseFromJob(interrupted, null).retryable, true);

  const cancelled = state.readJob(job({ status: "cancelled", phase: "cancelled" }));
  assert.deepEqual(state.phaseFromJob(cancelled, null), { kind: "cancelled", jobId: JOB });

  assert.equal(state.readJob({ data: { job_id: JOB, status: "mystery" } }), null);
  assert.equal(state.readJob(null), null);
  assert.equal(state.stageLabel("some_new_phase"), "Some new phase");
});

test("queue positions are read from the waiting list", () => {
  const positions = state.readQueuePositions({
    data: { waiting_jobs: [{ job_id: "x".repeat(32), queue_position: 1 }, { job_id: JOB, queue_position: 2 }] },
  });
  assert.equal(positions.get(JOB), 2);
  assert.equal(state.readQueuePositions({ data: null }).size, 0);
});

test("upload, queue, cancel and retry responses become reason messages", () => {
  assert.deepEqual(state.readUploadResult(200, JSON.stringify({ status: "ok", data: { capture_id: CAPTURE } })), { ok: true, captureId: CAPTURE });
  assert.deepEqual(state.readUploadResult(415, JSON.stringify({ status: "unavailable", reason: "recording_upload_content_type_invalid" })), {
    ok: false,
    reason: "Only .f1ecap capture files can be imported.",
  });
  assert.deepEqual(state.readUploadResult(422, JSON.stringify({ reason: "recording_upload_filename_invalid" })), {
    ok: false,
    reason: "The file name could not be used. Rename the file and try again.",
  });
  assert.equal(state.readUploadResult(503, JSON.stringify({ reason: "recording_upload_service_unavailable" })).reason, "The local engine is unavailable.");
  assert.equal(state.readUploadResult(0, "").reason, "The local engine is unavailable.", "network failure");
  assert.equal(state.readUploadResult(200, "not json").ok, false);

  const created = { api_version: "v1", status: "ok", data: { job_id: JOB, status: "queued", phase: "queued" } };
  const outcome = state.readJobAction(202, JSON.stringify(created));
  assert.equal(outcome.ok, true);
  assert.equal(outcome.job.jobId, JOB);

  assert.equal(state.readJobAction(409, JSON.stringify({ status: "unavailable", reason: "busy" })).reason, "Another import or recording is running. The capture was queued.");
  assert.equal(state.readJobAction(422, JSON.stringify({ reason: "capture_unavailable" })).reason, "The uploaded capture could not be found.");
  assert.equal(state.readJobAction(409, JSON.stringify({ reason: "job_not_waiting" })).reason, "The import has already started and cannot be cancelled.");
  assert.equal(state.readJobAction(500, "").reason, "The local engine is unavailable.");
  assert.equal(state.readJobAction(400, "").reason, "The import could not be started.");
});

test("file and drop rejections use the copy from the plan", () => {
  assert.equal(state.fileProblem({ name: "lap.F1ECAP" }), null);
  assert.equal(state.fileProblem({ name: "notes.txt" }), "Only .f1ecap capture files can be imported.");
  assert.equal(state.rejectionMessage("type"), "Only .f1ecap capture files can be imported.");
  assert.equal(state.rejectionMessage("count"), "Import up to 5 files at a time.");
  assert.equal(state.MAX_IMPORT_FILES, 5);
});

test("status badges follow the plan's strings", () => {
  const make = (phaseValue, bytes = 100) => ({ key: "k", fileName: "a", bytes, phase: phaseValue, captureId: null, lastJobId: null, source: "upload", startedAt: 0, updatedAt: 0 });
  assert.equal(state.badgeOf(make({ kind: "queued", jobId: JOB, position: null })).label, "Queued");
  assert.equal(state.badgeOf(make({ kind: "queued", jobId: JOB, position: 1 })).label, "Queued (1st)");
  assert.equal(state.badgeOf(make({ kind: "queued", jobId: JOB, position: 3 })).label, "Queued (3rd)");
  assert.equal(state.badgeOf(make({ kind: "queued", jobId: JOB, position: 12 })).label, "Queued (12th)");
  assert.equal(state.badgeOf(make({ kind: "processing", jobId: JOB, percent: null, stage: "x" })).label, "Processing");
  assert.equal(state.badgeOf(make({ kind: "failed", jobId: null, reason: "r", retryable: true })).label, "Import failed");
  assert.equal(state.formatBytes(482 * 1024 * 1024), "482 MB");
  assert.equal(state.formatBytes(1.2 * 1024 ** 3), "1.2 GB");
});

/* ------------------------------------------------------------------ restore */

test("restore maps queued, running and recent failures to rows and never-imported captures to the folder list", () => {
  const source = (id, status, modified, extra = {}) => ({
    capture_id: id.repeat(32).slice(0, 32),
    display_name: `${id}.f1ecap`,
    byte_size: 2048,
    modified_at_utc: modified,
    latest_job_id: status === null ? null : id.repeat(32).slice(0, 32),
    latest_job_status: status,
    available: true,
    ...extra,
  });
  const result = state.restoreImports(
    [
      source("1", "queued", "2026-10-12T11:59:00Z"),
      source("2", "running", "2026-10-12T11:58:00Z"),
      source("3", "failed", "2026-10-12T08:00:00Z"),
      source("4", "interrupted", "2026-10-11T13:00:00Z"),
      source("5", "failed", "2026-10-10T08:00:00Z"),
      source("6", "complete", "2026-10-12T08:00:00Z"),
      source("7", null, "2026-10-12T07:00:00Z"),
      source("8", null, "2026-10-12T07:00:00Z", { available: false }),
      source("9", "cancelled", "2026-10-12T07:00:00Z"),
    ],
    NOW,
  );
  const byName = Object.fromEntries(result.items.map((entry) => [entry.fileName, entry.phase]));
  assert.equal(byName["1.f1ecap"].kind, "queued");
  assert.equal(byName["2.f1ecap"].kind, "processing");
  assert.equal(byName["2.f1ecap"].percent, null);
  assert.equal(byName["3.f1ecap"].kind, "failed");
  assert.equal(byName["4.f1ecap"].kind, "failed");
  assert.match(byName["4.f1ecap"].reason, /interrupted/i);
  assert.equal(byName["5.f1ecap"], undefined, "failures older than 24 hours are not restored");
  assert.equal(byName["6.f1ecap"], undefined, "complete imports are real rows");
  assert.equal(byName["9.f1ecap"], undefined);
  assert.equal(result.items.length, 4);
  assert.deepEqual(result.folder.map((entry) => entry.name), ["7.f1ecap"], "only available, never-imported captures");
  assert.ok(result.items.every((entry) => entry.source === "restored" && entry.captureId !== null));
});

/* -------------------------------------------------------------- library rows */

const lap = (n, ms, extra = {}) => ({ id: String(n), lap: n, attempt: 1, ms, valid: true, pit: false, startObserved: true, sectors: null, revisionId: `r${n}`, traceAvailable: false, ...extra });

test("highlights name the best lap and count invalid and pit laps", () => {
  const laps = [lap(1, 95000, { pit: true }), lap(7, 92104), lap(8, 90000, { valid: false }), lap(9, 93000, { valid: false }), lap(10, 94000, { valid: false, pit: true })];
  assert.deepEqual(rows.highlightLines(laps), ["Best 1:32.104 (lap 7)", "3 invalid · 2 pit laps"]);
  assert.deepEqual(rows.highlightLines([lap(1, 90000)]), ["Best 1:30.000 (lap 1)"]);
  assert.deepEqual(rows.highlightLines([lap(1, 90000, { pit: true })]), ["Best 1:30.000 (lap 1)", "1 pit lap"]);
  assert.deepEqual(rows.highlightLines([]), ["No timed laps"]);
  assert.deepEqual(rows.highlightLines([lap(1, 90000, { valid: false })]), ["No valid timed lap", "1 invalid"]);
});

test("compare pair is the two fastest valid laps", () => {
  assert.deepEqual(rows.comparePair([lap(1, 95000), lap(2, 91000), lap(3, 93000), lap(4, 80000, { valid: false })]), { target: "3", reference: "2" });
  assert.equal(rows.comparePair([lap(1, 95000)]), null);
  assert.equal(rows.comparePair([lap(1, 95000), lap(2, 91000, { valid: false })]), null);
});

test("v2 dates use the cached summary date, else the placeholder with the short id", () => {
  assert.deepEqual(rows.sessionDate("3f2a9c1b00112233", "2024-06-04T15:00:00Z"), { text: "4 Jun 2024, 15:00", known: true });
  assert.deepEqual(rows.sessionDate("3f2a9c1b00112233", null), { text: "Date not recorded • #3f2a9c1b", known: false });
  assert.equal(rows.sessionDate("3f2a9c1b00112233", "not a date").known, false);
});

test("mapLimit bounds concurrency and keeps order", async () => {
  let active = 0;
  let peak = 0;
  const output = await rows.mapLimit(Array.from({ length: 25 }, (_, index) => index), 4, async (value) => {
    active += 1;
    peak = Math.max(peak, active);
    await new Promise((resolve) => setTimeout(resolve, 2));
    active -= 1;
    return value * 2;
  });
  assert.equal(peak, 4);
  assert.deepEqual(output, Array.from({ length: 25 }, (_, index) => index * 2));
  assert.deepEqual(await rows.mapLimit([], 4, async () => 1), []);
});

const row = (overrides) => ({
  key: "k",
  title: "Monaco Grand Prix",
  track: "monaco",
  type: { label: "Race", tone: "green" },
  source: { label: "Game", tone: "blue" },
  date: "4 Jun 2024, 15:00",
  meta: ["F1 24"],
  laps: 10,
  notes: "",
  status: { label: "Complete", tone: "green" },
  href: "/review/k",
  photo: null,
  kind: "v2",
  highlights: [],
  dateKnown: true,
  detailed: true,
  compare: null,
  download: null,
  ...overrides,
});

test("filters apply before pagination, which shows 20 at a time", () => {
  const many = Array.from({ length: 63 }, (_, index) => row({ key: `s${index}`, title: index % 3 === 0 ? "Imola" : "Monaco Grand Prix", highlights: index === 5 ? ["Best 1:32.104 (lap 7)"] : [] }));
  const first = rows.paginate(many, 20);
  assert.equal(first.visible.length, 20);
  assert.equal(first.countLine, "Showing 20 of 63 sessions");
  assert.equal(first.more, "Show 20 more");
  assert.equal(first.remaining, 43);
  const third = rows.paginate(many, 60);
  assert.equal(third.more, "Show 3 more");
  const all = rows.paginate(many, 80);
  assert.equal(all.visible.length, 63);
  assert.equal(all.remaining, 0);
  assert.equal(rows.paginate([row({})], 20).countLine, "Showing 1 of 1 session");

  const imola = rows.filterRows(many, { ...rows.NO_FILTERS, track: "Imola" });
  assert.equal(imola.length, 21);
  assert.equal(rows.paginate(imola, 20).countLine, "Showing 20 of 21 sessions");
  assert.equal(rows.filterRows(many, { ...rows.NO_FILTERS, query: "1:32.104" }).length, 1, "search covers derived highlights");
  assert.equal(rows.filterRows(many, { ...rows.NO_FILTERS, query: "nothing like this" }).length, 0);
  assert.equal(rows.filterRows(many, { ...rows.NO_FILTERS, oldestFirst: true })[0].key, "s62");
  assert.equal(rows.filtersActive(rows.NO_FILTERS), false);
  assert.equal(rows.filtersActive({ ...rows.NO_FILTERS, status: "Complete" }), true);
  assert.equal(rows.filtersActive({ ...rows.NO_FILTERS, oldestFirst: true }), false, "sort order is not a filter");
});

/* ---------------------------------------------------------------------- load */

const backendStub = toDataUrl(`
  const config = () => globalThis.__libraryStub;
  export const fetchSessions = async () => config().sessions;
  export const fetchEvidenceSessions = async () => config().evidence;
  export const fetchAcquisition = async () => config().acquisition;
  export const cachedSummaryDates = () => config().dates ?? new Map();
  export const fetchEvidenceAttempts = async (id) => {
    const c = config();
    c.calls.push(id);
    c.active += 1;
    c.peak = Math.max(c.peak, c.active);
    await new Promise((resolve) => setTimeout(resolve, 1));
    c.active -= 1;
    return c.attempts(id);
  };
  export const fetchRecordingSources = async (query = {}) => config().sources(query);
  export const telemetryStatus = (status) =>
    status.state === "receiving" ? { tone: "green", label: "Telemetry live" } : { tone: "amber", label: "Waiting for game" };
  export const telemetryFooter = (status, fallback) => ({ tone: "green", label: "Telemetry live", lines: [fallback.lines[0], "Ready"] });
`);
const trackMapStub = toDataUrl(`export const trackKeyFromName = (name) => (name ? String(name).toLowerCase().split(" ")[0] : "generic");`);
const photoStub = toDataUrl(`export const trackPhotoUrl = () => null;`);
const load = await import(
  await urlOf("session-library/load.ts", {
    "@/features/review-data/backend": backendStub,
    "@/features/review-data/session-evidence": evidenceUrl,
    "@/features/review-data/fixtures": fixturesUrl,
    "@/features/review-data/format": formatUrl,
    "@/features/ui-foundation/TrackMap": trackMapStub,
    "@/features/ui-foundation/track-photo-url": photoStub,
    "./imports/import-state": importStateUrl,
    "./library-rows": rowsUrl,
  })
);
const fixtures = await import(fixturesUrl);

const RECEIVING = { state: "receiving" };
const CONTEXT = JSON.stringify({ track_name: "Jeddah", session_type: "practice_1", packet_format: 2025, weather_name: "clear", air_temperature_c: 26 });

function configure(overrides = {}) {
  globalThis.__libraryStub = {
    sessions: [],
    evidence: [],
    acquisition: RECEIVING,
    dates: new Map(),
    calls: [],
    active: 0,
    peak: 0,
    attempts: () => ({ attempts: [], truncated: false }),
    sources: () => null,
    ...overrides,
  };
  return globalThis.__libraryStub;
}

const evidenceSession = (id, extra = {}) => ({
  id,
  generation: "g",
  uid: "1",
  lifecycle: "ended",
  acquisition: "receiving",
  occurrence: 1,
  context: { trackName: "Jeddah", sessionType: "practice_1", weatherName: "clear", airTemperatureC: 26, trackTemperatureC: null, packetFormat: 2025, ruleSet: null, trackLengthM: null, totalLaps: null },
  ...extra,
});

const attempt = (n, ms, sequence, extra = {}) => ({
  id: `rev${n}`,
  role: "player",
  publishedSequence: sequence,
  attemptNumber: 1,
  lapNumber: n,
  lapTimeMs: ms,
  disposition: "completed",
  gameValid: true,
  pitEncountered: false,
  startObserved: true,
  superseded: false,
  traceAvailable: false,
  context: null,
  ...extra,
});

test("engine reachable with zero sessions is an empty library, not the fixture", async () => {
  configure();
  const model = await load.loadLibrary(NOW);
  assert.equal(model.mode, "live");
  assert.equal(model.live, true);
  assert.deepEqual(model.sessions, []);
  assert.equal(model.sessionsUnreadable, false);
  assert.equal(model.status.label, "Telemetry live");
  assert.ok(!model.sessions.some((entry) => entry.title === "Monaco Grand Prix"));
});

test("an unreachable engine shows the reference rows with a red Backend unavailable pill", async () => {
  configure({ sessions: null, evidence: null, acquisition: null });
  const model = await load.loadLibrary(NOW);
  assert.equal(model.mode, "reference");
  assert.equal(model.live, false);
  assert.deepEqual(model.status, { tone: "red", label: "Backend unavailable" });
  assert.notEqual(model.status.label, fixtures.shellFixture.telemetryLive.label);
  assert.equal(model.footer.label, "Engine offline");
  assert.deepEqual(model.footer.lines, ["Start the local engine", ""]);
  assert.equal(model.sessions.length, fixtures.libraryFixture.sessions.length);
  assert.ok(model.sessions.every((entry) => entry.kind === "reference"));
  assert.equal(model.header.items[0], "Local engine offline", "no 'Connected to F1 24' claim while offline");
  assert.deepEqual(model.imports.restored, []);
});

test("status unreadable but lists readable reports Backend unavailable, never Telemetry live", async () => {
  configure({ acquisition: null, evidence: [evidenceSession("aaaa1111")], attempts: () => ({ attempts: [attempt(1, 90000, 5)], truncated: false }) });
  const model = await load.loadLibrary(NOW);
  assert.equal(model.mode, "live");
  assert.deepEqual(model.status, { tone: "red", label: "Backend unavailable" });
  assert.equal(model.sessions.length, 1);
});

test("both session lists unreadable while the engine answers is flagged, not shown as empty", async () => {
  configure({ sessions: null, evidence: null });
  const model = await load.loadLibrary(NOW);
  assert.equal(model.mode, "live");
  assert.equal(model.sessionsUnreadable, true);
  assert.deepEqual(model.sessions, []);
});

test("one unreadable source keeps the other's rows and adds a warning", async () => {
  configure({ sessions: null, evidence: [evidenceSession("aaaa1111")], attempts: () => ({ attempts: [attempt(1, 90000, 5)], truncated: false }) });
  const model = await load.loadLibrary(NOW);
  assert.equal(model.sessions.length, 1);
  assert.equal(model.warnings.length, 1);
  assert.match(model.warnings[0], /Imported captures could not be read/);
});

test("v2 rows get highlights, compare pair, cached dates and journal-recency order", async () => {
  configure({
    evidence: [evidenceSession("aaaa1111bbbb2222"), evidenceSession("cccc3333dddd4444")],
    dates: new Map([["cccc3333dddd4444", "2024-06-04T15:00:00Z"]]),
    attempts: (id) =>
      id.startsWith("aaaa")
        ? { attempts: [attempt(1, 95000, 10), attempt(2, 92104, 11, { gameValid: false }), attempt(3, 91000, 12, { pitEncountered: true })], truncated: false }
        : { attempts: [attempt(1, 90000, 50), attempt(2, 89000, 51)], truncated: false },
  });
  const model = await load.loadLibrary(NOW);
  assert.deepEqual(model.sessions.map((entry) => entry.key), ["cccc3333dddd4444", "aaaa1111bbbb2222"], "newest journal position first");
  const [newer, older] = model.sessions;
  assert.equal(newer.date, "4 Jun 2024, 15:00");
  assert.equal(newer.dateKnown, true);
  assert.equal(older.date, "Date not recorded • #aaaa1111");
  assert.equal(older.dateKnown, false);
  assert.deepEqual(newer.highlights, ["Best 1:29.000 (lap 2)"]);
  assert.deepEqual(older.highlights, ["Best 1:31.000 (lap 3)", "1 invalid · 1 pit lap"]);
  assert.deepEqual(newer.compare, { target: "1", reference: "2" });
  assert.equal(newer.laps, 2);
  assert.equal(newer.kind, "v2");
  assert.equal(newer.href, "/review/cccc3333dddd4444");
  assert.match(newer.notes, /Best 1:29\.000/, "highlights are searchable");
});

test("a session without timed laps says so", async () => {
  configure({ evidence: [evidenceSession("aaaa1111")], attempts: () => ({ attempts: [], truncated: false }) });
  const [only] = (await load.loadLibrary(NOW)).sessions;
  assert.deepEqual(only.highlights, ["No timed laps"]);
  assert.equal(only.compare, null);
});

test("cold-cache attempt reads are capped and bounded in concurrency", async () => {
  const ids = Array.from({ length: 150 }, (_, index) => `s${String(index).padStart(4, "0")}`);
  const stub = configure({
    evidence: ids.map((id) => evidenceSession(id)),
    attempts: (id) => ({ attempts: [attempt(1, 90000, Number(id.slice(1)))], truncated: false }),
  });
  const model = await load.loadLibrary(NOW);
  assert.equal(stub.calls.length, rows.ATTEMPT_READ_CAP);
  assert.ok(stub.peak <= rows.ATTEMPT_READ_CONCURRENCY, `peak ${stub.peak} exceeds ${rows.ATTEMPT_READ_CONCURRENCY}`);
  assert.equal(model.sessions.length, 150, "every session is still listed");
  const detailed = model.sessions.filter((entry) => entry.detailed);
  const unread = model.sessions.filter((entry) => !entry.detailed);
  assert.equal(detailed.length, rows.ATTEMPT_READ_CAP);
  assert.equal(unread.length, 70);
  assert.ok(model.sessions.slice(0, detailed.length).every((entry) => entry.detailed), "read rows come first");
  assert.ok(unread.every((entry) => entry.href && entry.laps === 0 && entry.highlights.length === 1));
});

test("imported v1 rows link to their capture and restored imports come from the catalog", async () => {
  const run = "f".repeat(64);
  const sources = (query) => {
    const page = (items) => ({ items, total_count: items.length, limit: 50, offset: 0, has_more: false });
    const base = { byte_size: 4096, modified_at_utc: "2026-10-12T11:30:00Z", latest_job_run_id: null, available: true, download_version: "d".repeat(64) };
    switch (query.latestJobStatus) {
      case "queued":
        return page([{ ...base, capture_id: "1".repeat(32), display_name: "waiting.f1ecap", latest_job_id: "1".repeat(32), latest_job_status: "queued" }]);
      case "failed":
        return page([{ ...base, capture_id: "2".repeat(32), display_name: "broken.f1ecap", latest_job_id: "2".repeat(32), latest_job_status: "failed" }]);
      case "none":
        return page([{ ...base, capture_id: "3".repeat(32), display_name: "fresh.f1ecap", latest_job_id: null, latest_job_status: null }]);
      case "complete":
        return page([{ ...base, capture_id: "4".repeat(32), display_name: "done.f1ecap", latest_job_id: "4".repeat(32), latest_job_status: "complete", latest_job_run_id: run }]);
      default:
        return page([]);
    }
  };
  configure({
    sessions: [
      { session_key: "k1", run_id: run, session_uid: "1", packet_format: 2024, context: { track_name: "Monaco", session_type: "race", weather_name: "clear" }, run_status: "complete", capture_quality: null, capture_sha256: "s", pipeline_version: "p", started_at_utc: "2024-06-04T15:00:00Z", finished_at_utc: null, lap_attempts: 78 },
      { session_key: "k2", run_id: "e".repeat(64), session_uid: "2", packet_format: 2024, context: null, run_status: "partial", capture_quality: null, capture_sha256: "t", pipeline_version: "p", started_at_utc: "2024-06-03T15:00:00Z", finished_at_utc: null, lap_attempts: 0 },
    ],
    sources,
  });
  const model = await load.loadLibrary(NOW);
  const [first, second] = model.sessions;
  assert.equal(first.kind, "v1");
  assert.deepEqual(first.download, { captureId: "4".repeat(32), version: "d".repeat(64) });
  assert.equal(second.download, null);
  assert.deepEqual(second.highlights, ["No timed laps"]);
  assert.equal(first.date, "4 Jun 2024, 15:00");

  assert.deepEqual(model.imports.restored.map((entry) => [entry.fileName, entry.phase.kind]).sort(), [["broken.f1ecap", "failed"], ["waiting.f1ecap", "queued"]]);
  assert.deepEqual(model.imports.folder.map((entry) => entry.name), ["fresh.f1ecap"]);
  assert.equal(model.imports.folderTotal, 1);
});

test("unreadable recordings catalog leaves the folder list unknown, not empty", async () => {
  configure({ evidence: [evidenceSession("aaaa1111")] });
  const model = await load.loadLibrary(NOW);
  assert.equal(model.imports.folder, null);
  assert.deepEqual(model.imports.restored, []);
});

/* ------------------------------------------------------------ source checks */

const libraryRoot = new URL("session-library/", featuresRoot);

test("the import button is built on SplitButton and Menu items, with no hand-rolled menu", async () => {
  const source = await readFile(new URL("ImportSessionButton.tsx", libraryRoot), "utf8");
  assert.match(source, /<SplitButton/);
  assert.match(source, /menuLabel="More import options"/);
  assert.doesNotMatch(source, /role="menu"/);
  assert.match(source, /Import capture file \(\.f1ecap\)/);
  assert.match(source, /Import from recordings folder/);
  assert.match(source, /Refresh library/);
});

test("the row menu offers only actions the backend supports", async () => {
  const source = await readFile(new URL("SessionRowMenu.tsx", libraryRoot), "utf8");
  for (const label of ["Open overview", "Open lap analysis", "Compare best laps", "Copy session ID", "Download capture"]) {
    assert.ok(source.includes(label), `missing ${label}`);
  }
  assert.doesNotMatch(source, />\s*(Delete|Rename|Edit notes|Add note)/i);
});

test("new library CSS uses tokens, and library UI uses SVG icons from icons.tsx only", async () => {
  const css = await readFile(new URL("imports/imports.module.css", libraryRoot), "utf8");
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/, "raw hex colour in imports.module.css");
  const forbidden = /[←-⇿⌀-⏿■-➿⤀-⥿\u{1f000}-\u{1faff}️]/u;
  for (const file of ["SessionLibrary.tsx", "SessionRowMenu.tsx", "ImportSessionButton.tsx", "imports/ImportRow.tsx", "imports/ImportActivityDrawer.tsx", "imports/ImportProvider.tsx"]) {
    const source = await readFile(new URL(file, libraryRoot), "utf8");
    assert.doesNotMatch(source, forbidden, `${file} contains an icon-like character`);
    assert.doesNotMatch(source, /<svg[\s>]/, `${file} declares its own svg; use icons.tsx`);
  }
});

test("the library page is wired to the loader and the empty state copy matches the plan", async () => {
  const library = await readFile(new URL("SessionLibrary.tsx", libraryRoot), "utf8");
  assert.match(library, /No sessions yet/);
  assert.match(library, /Drive with telemetry enabled, or import a \.f1ecap capture\./);
  assert.match(library, /No sessions match these filters\./);
  assert.match(library, /Clear filters/);
  assert.match(library, /Drop \.f1ecap captures to import/);
  assert.match(library, /Reference data/);
  assert.match(library, /The local engine is not reachable, so example sessions from the reference design are shown\. Start the engine to see your own data\./);
  const loader = await readFile(new URL("load.ts", libraryRoot), "utf8");
  assert.doesNotMatch(loader, /shellFixture\.telemetryLive/, "the unreachable fallback must not be the green pill");
});
