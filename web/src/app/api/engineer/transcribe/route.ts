import { NextResponse } from "next/server";
import {
  forwardLocalRequest,
  isTrustedLocalMutation,
  isTrustedLocalRead,
} from "@/lib/local-proxy";

const MAX_REQUEST_BYTES = 512 * 1024;
const MAX_RESPONSE_BYTES = 16 * 1024;
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
          // The byte bound remains authoritative if the client disconnects.
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

async function forwardStatus(request: Request) {
  if (!isTrustedLocalRead(request))
    return unavailable("same_origin_request_required", 403);
  if (new URL(request.url).search)
    return unavailable("engineer_speech_query_not_allowed", 400);
  const controller = new AbortController();
  const abortForClient = () => controller.abort(request.signal.reason);
  request.signal.addEventListener("abort", abortForClient, { once: true });
  const deadline = setTimeout(() => controller.abort(), 5_000);
  try {
    const response = await forwardLocalRequest("/api/v1/engineer/transcribe", {
      signal: controller.signal,
    });
    const body = await readBounded(
      response.body,
      MAX_RESPONSE_BYTES,
      controller.signal,
    );
    if (!body)
      return unavailable("engineer_speech_status_response_limit_exceeded", 502);
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
        ? "engineer_speech_status_deadline_exceeded"
        : "engineer_speech_status_unavailable",
      controller.signal.aborted ? 504 : 503,
    );
  } finally {
    clearTimeout(deadline);
    request.signal.removeEventListener("abort", abortForClient);
  }
}

export async function GET(request: Request) {
  return forwardStatus(request);
}

export async function POST(request: Request) {
  if (!isTrustedLocalMutation(request))
    return unavailable("same_origin_request_required", 403);
  if (new URL(request.url).search)
    return unavailable("engineer_speech_query_not_allowed", 400);
  const contentType = request.headers
    .get("content-type")
    ?.split(";", 1)[0]
    .trim()
    .toLowerCase();
  if (contentType !== "audio/wav")
    return unavailable("engineer_speech_wav_required", 415);
  const requestId = request.headers.get("x-request-id") ?? "";
  const audioSha256 = request.headers.get("x-audio-sha256") ?? "";
  if (
    !/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(
      requestId,
    ) ||
    !/^[a-f0-9]{64}$/.test(audioSha256)
  ) {
    return unavailable("engineer_speech_correlation_invalid", 422);
  }
  const contentLength = request.headers.get("content-length");
  if (contentLength !== null) {
    if (!/^[0-9]{1,10}$/.test(contentLength))
      return unavailable("engineer_speech_content_length_invalid", 400);
    if (Number(contentLength) > MAX_REQUEST_BYTES)
      return unavailable("engineer_speech_audio_limit_exceeded", 413);
  }

  const controller = new AbortController();
  const abortForClient = () => controller.abort(request.signal.reason);
  request.signal.addEventListener("abort", abortForClient, { once: true });
  const deadline = setTimeout(() => controller.abort(), REQUEST_DEADLINE_MS);
  try {
    const body = await readBounded(
      request.body,
      MAX_REQUEST_BYTES,
      controller.signal,
    );
    if (!body) {
      return unavailable(
        controller.signal.aborted
          ? "engineer_speech_deadline_exceeded"
          : "engineer_speech_audio_limit_exceeded",
        controller.signal.aborted ? 504 : 413,
      );
    }
    const response = await forwardLocalRequest(
      "/api/v1/engineer/transcribe",
      {
        method: "POST",
        body: arrayBufferCopy(body),
        headers: {
          "content-type": "audio/wav",
          "x-request-id": requestId,
          "x-audio-sha256": audioSha256,
        },
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
      return unavailable("engineer_speech_response_limit_exceeded", 502);
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
        ? "engineer_speech_deadline_exceeded"
        : "engineer_speech_unavailable",
      controller.signal.aborted ? 504 : 503,
    );
  } finally {
    clearTimeout(deadline);
    request.signal.removeEventListener("abort", abortForClient);
  }
}
