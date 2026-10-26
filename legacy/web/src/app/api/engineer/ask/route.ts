import { NextResponse } from "next/server";
import { forwardLocalRequest, isTrustedLocalMutation } from "@/lib/local-proxy";

const MAX_REQUEST_BYTES = 8 * 1024;
const MAX_RESPONSE_BYTES = 40 * 1024;
const REQUEST_DEADLINE_MS = 60_000;

function arrayBufferCopy(bytes: Uint8Array): ArrayBuffer {
  const buffer = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(buffer).set(bytes);
  return buffer;
}

function unavailable(reason: string, status: number) {
  return NextResponse.json(
    { api_version: "v1", status: "unavailable", data: null, reason },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

async function readBounded(
  stream: ReadableStream<Uint8Array> | null,
  maximum: number,
  signal: AbortSignal,
): Promise<Uint8Array | null> {
  if (!stream) return new Uint8Array();
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  let onAbort: (() => void) | null = null;
  try {
    while (true) {
      if (signal.aborted) return null;
      const readResult = await Promise.race([
        reader.read(),
        new Promise<null>((resolve) => {
          onAbort = () => {
            void reader.cancel();
            resolve(null);
          };
          signal.addEventListener("abort", onAbort, { once: true });
        }),
      ]);
      if (readResult === null) return null;
      signal.removeEventListener("abort", onAbort!);
      onAbort = null;
      const { done, value } = readResult;
      if (done) break;
      size += value.byteLength;
      if (size > maximum) {
        try {
          await reader.cancel();
        } catch {
          // The request size remains authoritative after a disconnect.
        }
        return null;
      }
      chunks.push(value);
    }
  } finally {
    if (onAbort) signal.removeEventListener("abort", onAbort);
    reader.releaseLock();
  }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}

export async function POST(request: Request) {
  if (!isTrustedLocalMutation(request))
    return unavailable("same_origin_request_required", 403);
  const controller = new AbortController();
  const abortForClient = () => controller.abort(request.signal.reason);
  request.signal.addEventListener("abort", abortForClient, { once: true });
  const deadline = setTimeout(() => controller.abort(), REQUEST_DEADLINE_MS);
  const contentLength = request.headers.get("content-length");
  if (contentLength !== null) {
    if (!/^[0-9]{1,10}$/.test(contentLength)) {
      clearTimeout(deadline);
      request.signal.removeEventListener("abort", abortForClient);
      return unavailable("invalid_content_length", 400);
    }
    if (Number(contentLength) > MAX_REQUEST_BYTES) {
      clearTimeout(deadline);
      request.signal.removeEventListener("abort", abortForClient);
      return unavailable("engineer_ask_request_limit_exceeded", 413);
    }
  }
  try {
    const body = await readBounded(
      request.body,
      MAX_REQUEST_BYTES,
      controller.signal,
    );
    if (!body) {
      return unavailable(
        controller.signal.aborted
          ? "engineer_ask_deadline_exceeded"
          : "engineer_ask_request_limit_exceeded",
        controller.signal.aborted ? 504 : 413,
      );
    }
    const response = await forwardLocalRequest(
      "/api/v1/engineer/ask",
      {
        method: "POST",
        body: arrayBufferCopy(body),
        headers: { "content-type": "application/json" },
        signal: controller.signal,
      },
      true,
    );
    const result = await readBounded(
      response.body,
      MAX_RESPONSE_BYTES,
      controller.signal,
    );
    if (!result)
      return unavailable("engineer_ask_response_limit_exceeded", 502);
    return new NextResponse(arrayBufferCopy(result), {
      status: response.status,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return unavailable(
      controller.signal.aborted
        ? "engineer_ask_deadline_exceeded"
        : "engineer_ask_service_unavailable",
      controller.signal.aborted ? 504 : 503,
    );
  } finally {
    clearTimeout(deadline);
    request.signal.removeEventListener("abort", abortForClient);
  }
}
