import { NextResponse } from "next/server";
import { forwardLocalRequest, isTrustedLocalRead } from "@/lib/local-proxy";

const MAX_RESPONSE_BYTES = 8 * 1024;
const REQUEST_DEADLINE_MS = 5_000;

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

async function readBounded(stream: ReadableStream<Uint8Array> | null) {
  if (!stream) return null;
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_RESPONSE_BYTES) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
  } finally {
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

export async function GET(request: Request) {
  if (!isTrustedLocalRead(request)) {
    return unavailable("same_origin_request_required", 403);
  }
  const controller = new AbortController();
  const abortForClient = () => controller.abort(request.signal.reason);
  request.signal.addEventListener("abort", abortForClient, { once: true });
  const deadline = setTimeout(() => controller.abort(), REQUEST_DEADLINE_MS);
  try {
    const response = await forwardLocalRequest("/api/v1/engineer/runtime", {
      signal: controller.signal,
    });
    const declared = response.headers.get("content-length");
    if (
      declared !== null &&
      (!/^[0-9]{1,8}$/.test(declared) || Number(declared) > MAX_RESPONSE_BYTES)
    ) {
      await response.body?.cancel();
      return unavailable("engineer_runtime_response_limit_exceeded", 502);
    }
    const body = await readBounded(response.body);
    if (!body)
      return unavailable("engineer_runtime_response_limit_exceeded", 502);
    return new NextResponse(arrayBufferCopy(body), {
      status: response.status,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return unavailable(
      controller.signal.aborted
        ? "engineer_runtime_deadline_exceeded"
        : "engineer_runtime_unavailable",
      controller.signal.aborted ? 504 : 503,
    );
  } finally {
    clearTimeout(deadline);
    request.signal.removeEventListener("abort", abortForClient);
  }
}
