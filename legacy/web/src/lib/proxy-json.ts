import {
  forwardLocalRequest,
  isTrustedLocalMutation,
  isTrustedLocalRead,
} from "@/lib/local-proxy";

// Shared JSON proxy for Route Handlers under `web/src/app/api/**`.
//
// - `proxyGet` checks `isTrustedLocalRead`, forwards without the control token
//   and bounds time and response size.
// - `proxyMutation` checks `isTrustedLocalMutation`, bounds the request body,
//   forwards with the control token (server-side only) and bounds the response.
// Every response is `Cache-Control: no-store`. Upstream status codes, JSON
// bodies and `ETag` pass through, so 409 conflicts stay visible to the caller.

export const DEFAULT_GET_TIMEOUT_MS = 10_000;
export const DEFAULT_MUTATION_TIMEOUT_MS = 30_000;
export const DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
export const DEFAULT_MAX_REQUEST_BYTES = 8 * 1024;

const EVIDENCE_ID = /^[A-Za-z0-9:_-]{1,128}$/;
const MODEL_ID = /^[A-Za-z0-9._:-]{1,128}$/;

/** v2 session and attempt revision ids. */
export function isEvidenceId(value: string): boolean {
  return EVIDENCE_ID.test(value);
}

/** Track model ids such as `monaco-2024`. */
export function isTrackModelId(value: string): boolean {
  return MODEL_ID.test(value);
}

const BASE_HEADERS = {
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
} as const;

/** Standard unavailable envelope, matching the local API's error shape. */
export function proxyError(status: number, reason: string): Response {
  return Response.json(
    { api_version: "v1", status: "unavailable", data: null, reason },
    { status, headers: BASE_HEADERS },
  );
}

/** Success envelope matching the local API's shape. */
export function proxyOk(status: number, data: unknown): Response {
  return Response.json(
    { api_version: "v1", status: "ok", data, reason: null },
    { status, headers: BASE_HEADERS },
  );
}

/**
 * True when the caller asked for JSON (`Accept: application/json`). Browser
 * form posts send `text/html,...` and keep the redirect behaviour.
 */
export function acceptsJson(request: Request): boolean {
  return (request.headers.get("accept") ?? "")
    .split(",")
    .some((part) => part.split(";", 1)[0].trim().toLowerCase() === "application/json");
}

export type BoundedRead =
  | { ok: true; bytes: Uint8Array }
  | { ok: false; reason: "limit" | "aborted" };

/**
 * Reads a stream into memory, giving up once `maximum` bytes are exceeded or
 * `signal` aborts. The stream is cancelled in both cases.
 */
export async function readBoundedBytes(
  stream: ReadableStream<Uint8Array> | null,
  maximum: number,
  signal?: AbortSignal,
): Promise<BoundedRead> {
  if (!stream) return { ok: true, bytes: new Uint8Array() };
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  const onAbort = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal?.addEventListener("abort", onAbort, { once: true });
  try {
    while (true) {
      if (signal?.aborted) return { ok: false, reason: "aborted" };
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maximum) {
        await reader.cancel().catch(() => undefined);
        return { ok: false, reason: "limit" };
      }
      chunks.push(value);
    }
  } catch {
    return { ok: false, reason: "aborted" };
  } finally {
    signal?.removeEventListener("abort", onAbort);
    try {
      reader.releaseLock();
    } catch {
      // The stream was cancelled while a read was pending.
    }
  }
  if (signal?.aborted) return { ok: false, reason: "aborted" };
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { ok: true, bytes };
}

function arrayBufferCopy(bytes: Uint8Array): ArrayBuffer {
  const buffer = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(buffer).set(bytes);
  return buffer;
}

type Deadline = {
  signal: AbortSignal;
  timedOut: () => boolean;
  clear: () => void;
};

function deadline(request: Request, timeoutMs: number): Deadline {
  const controller = new AbortController();
  let expired = false;
  const abortForClient = () => controller.abort(request.signal.reason);
  if (request.signal.aborted) controller.abort(request.signal.reason);
  else request.signal.addEventListener("abort", abortForClient, { once: true });
  const timer = setTimeout(() => {
    expired = true;
    controller.abort();
  }, timeoutMs);
  return {
    signal: controller.signal,
    timedOut: () => expired,
    clear: () => {
      clearTimeout(timer);
      request.signal.removeEventListener("abort", abortForClient);
    },
  };
}

function isJsonResponse(response: Response): boolean {
  return (response.headers.get("content-type") ?? "").toLowerCase().includes("json");
}

/** Forwards the upstream status, JSON body and ETag; bounds the body. */
async function relay(upstream: Response, maximum: number, limits: Deadline): Promise<Response> {
  const declared = upstream.headers.get("content-length");
  if (declared !== null && /^[0-9]{1,12}$/.test(declared) && Number(declared) > maximum) {
    await upstream.body?.cancel().catch(() => undefined);
    return proxyError(502, "upstream_response_limit_exceeded");
  }
  const result = await readBoundedBytes(upstream.body, maximum, limits.signal);
  if (!result.ok) {
    if (result.reason === "limit") return proxyError(502, "upstream_response_limit_exceeded");
    return limits.timedOut()
      ? proxyError(504, "upstream_deadline_exceeded")
      : proxyError(499, "client_closed_request");
  }
  if (!isJsonResponse(upstream)) return proxyError(502, "upstream_response_invalid");
  const headers = new Headers(BASE_HEADERS);
  headers.set("Content-Type", "application/json; charset=utf-8");
  const etag = upstream.headers.get("etag");
  if (etag) headers.set("ETag", etag);
  return new Response(arrayBufferCopy(result.bytes), { status: upstream.status, headers });
}

function failure(limits: Deadline): Response {
  if (limits.timedOut()) return proxyError(504, "upstream_deadline_exceeded");
  if (limits.signal.aborted) return proxyError(499, "client_closed_request");
  return proxyError(503, "local_engine_unavailable");
}

export type ImportJobAction = "create" | "cancel" | "retry";

/** Failure codes shared with the redirect branches of the import routes (`import_error`). */
export function importFailureReason(
  action: ImportJobAction,
  status: number,
  reason: string | null,
): string {
  if (action === "cancel") return status === 409 ? "job_not_waiting" : (reason ?? "unavailable");
  if (
    action === "create" &&
    (reason === "another_import_is_in_progress" ||
      reason === "another_local_operation_is_in_progress")
  ) {
    return "busy";
  }
  return reason ?? (status === 409 ? "busy" : "unavailable");
}

/**
 * JSON branch of the import-job routes (`Accept: application/json`). The caller
 * has already checked `isTrustedLocalMutation`. Success returns the upstream
 * job under `data`; failures use the same reason codes as the redirect
 * branch's `import_error`.
 */
export async function proxyImportJob(
  action: ImportJobAction,
  path: string,
  body?: Record<string, unknown>,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DEFAULT_MUTATION_TIMEOUT_MS);
  try {
    const upstream = await forwardLocalRequest(
      path,
      {
        method: "POST",
        headers: body ? { "content-type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      },
      true,
    );
    const read = await readBoundedBytes(upstream.body, 64 * 1024, controller.signal);
    let result: { data?: { job_id?: unknown } | null; reason?: unknown } = {};
    if (read.ok) {
      try {
        const parsed: unknown = JSON.parse(new TextDecoder().decode(read.bytes));
        if (parsed !== null && typeof parsed === "object") result = parsed as typeof result;
      } catch {
        result = {};
      }
    }
    const jobId = result.data?.job_id;
    if (upstream.ok && read.ok && typeof jobId === "string" && jobId !== "") {
      return proxyOk(upstream.status, result.data);
    }
    const reason = typeof result.reason === "string" ? result.reason.slice(0, 128) : null;
    return proxyError(
      upstream.ok ? 502 : upstream.status,
      importFailureReason(action, upstream.status, reason),
    );
  } catch {
    return proxyError(503, "unavailable");
  } finally {
    clearTimeout(timer);
  }
}

export type ProxyGetOptions = {
  timeoutMs?: number;
  maxResponseBytes?: number;
};

/**
 * Same-origin JSON read. `path` must already be a validated, fully built local
 * API path (including any query string).
 */
export async function proxyGet(
  request: Request,
  path: string,
  options: ProxyGetOptions = {},
): Promise<Response> {
  if (!isTrustedLocalRead(request)) return proxyError(403, "same_origin_request_required");
  const limits = deadline(request, options.timeoutMs ?? DEFAULT_GET_TIMEOUT_MS);
  try {
    const upstream = await forwardLocalRequest(path, {
      method: "GET",
      headers: { accept: "application/json" },
      signal: limits.signal,
    });
    return await relay(upstream, options.maxResponseBytes ?? DEFAULT_MAX_RESPONSE_BYTES, limits);
  } catch {
    return failure(limits);
  } finally {
    limits.clear();
  }
}

export type ProxyMutationOptions = {
  method?: "POST" | "PUT" | "DELETE";
  /** `json`: forward the bounded JSON request body. `none`: send no body. */
  body?: "json" | "none";
  /** A fixed deadline, or a function of the request body (e.g. longer for future-lap questions). */
  timeoutMs?: number | ((body: Uint8Array | null) => number);
  maxRequestBytes?: number;
  maxResponseBytes?: number;
  /** Request headers copied upstream (lower-case), e.g. `if-match`. */
  forwardHeaders?: readonly string[];
};

const SAFE_HEADER_VALUE = /^[\x20-\x7e]{1,64}$/;

/** Same-origin mutation: bounded body in, control token added server-side, bounded body out. */
export async function proxyMutation(
  request: Request,
  path: string,
  options: ProxyMutationOptions = {},
): Promise<Response> {
  if (!isTrustedLocalMutation(request)) return proxyError(403, "same_origin_request_required");
  const mode = options.body ?? "none";
  const maxRequest = options.maxRequestBytes ?? DEFAULT_MAX_REQUEST_BYTES;

  const headers = new Headers();
  for (const name of options.forwardHeaders ?? []) {
    const value = request.headers.get(name);
    if (value === null) continue;
    if (!SAFE_HEADER_VALUE.test(value)) return proxyError(422, "request_invalid");
    headers.set(name, value);
  }

  let body: Uint8Array | null = null;
  const fixedTimeout = typeof options.timeoutMs === "number" ? options.timeoutMs : undefined;
  const limits = deadline(request, fixedTimeout ?? DEFAULT_MUTATION_TIMEOUT_MS);
  try {
    if (mode === "json") {
      const contentType = request.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase();
      if (contentType !== "application/json") return proxyError(415, "request_content_type_invalid");
      const declared = request.headers.get("content-length");
      if (declared !== null) {
        if (!/^[0-9]{1,10}$/.test(declared)) return proxyError(400, "request_body_invalid");
        if (Number(declared) > maxRequest) return proxyError(413, "request_body_limit_exceeded");
      }
      const read = await readBoundedBytes(request.body, maxRequest, limits.signal);
      if (!read.ok) {
        if (read.reason === "limit") return proxyError(413, "request_body_limit_exceeded");
        return limits.timedOut()
          ? proxyError(504, "upstream_deadline_exceeded")
          : proxyError(499, "client_closed_request");
      }
      if (read.bytes.byteLength === 0) return proxyError(400, "request_body_invalid");
      body = read.bytes;
      headers.set("content-type", "application/json");
    }

    // A function deadline is applied once the body is known; restart the timer.
    let active = limits;
    if (typeof options.timeoutMs === "function") {
      limits.clear();
      active = deadline(request, options.timeoutMs(body));
    }
    try {
      const upstream = await forwardLocalRequest(
        path,
        {
          method: options.method ?? "POST",
          headers,
          body: body ? arrayBufferCopy(body) : undefined,
          signal: active.signal,
        },
        true,
      );
      return await relay(upstream, options.maxResponseBytes ?? DEFAULT_MAX_RESPONSE_BYTES, active);
    } catch {
      return failure(active);
    } finally {
      active.clear();
    }
  } finally {
    limits.clear();
  }
}
