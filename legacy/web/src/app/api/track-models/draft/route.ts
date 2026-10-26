import { NextResponse } from "next/server";
import { forwardLocalRequest, isTrustedLocalMutation } from "@/lib/local-proxy";

const MAX_BODY_BYTES = 64 * 1024;

function bodyLimitResponse() {
  return NextResponse.json(
    {
      status: "unavailable",
      data: null,
      reason: "draft_model_request_size_limit_exceeded",
    },
    { status: 413 },
  );
}

export async function POST(request: Request) {
  if (!isTrustedLocalMutation(request)) {
    return NextResponse.json(
      {
        status: "unavailable",
        data: null,
        reason: "same_origin_request_required",
      },
      { status: 403 },
    );
  }
  const declaredLengthHeader = request.headers.get("content-length");
  if (declaredLengthHeader !== null) {
    const declaredLength = Number(declaredLengthHeader);
    if (!Number.isSafeInteger(declaredLength) || declaredLength < 0) {
      return NextResponse.json(
        { status: "unavailable", data: null, reason: "invalid_content_length" },
        { status: 400 },
      );
    }
    if (declaredLength > MAX_BODY_BYTES) return bodyLimitResponse();
  }
  try {
    const reader = request.body?.getReader();
    const chunks: Uint8Array[] = [];
    let bodyByteLength = 0;
    try {
      while (reader) {
        const { done, value } = await reader.read();
        if (done) break;
        if (bodyByteLength + value.byteLength > MAX_BODY_BYTES) {
          try {
            await reader.cancel();
          } catch {
            // Keep the size-limit response even when the client has disconnected.
          }
          return bodyLimitResponse();
        }
        chunks.push(value);
        bodyByteLength += value.byteLength;
      }
    } finally {
      reader?.releaseLock();
    }
    const body = new Uint8Array(bodyByteLength);
    let offset = 0;
    for (const chunk of chunks) {
      body.set(chunk, offset);
      offset += chunk.byteLength;
    }
    const response = await forwardLocalRequest("/api/v1/track-models/draft", {
      method: "POST",
      body,
      headers: { "content-type": "application/json" },
    });
    return NextResponse.json(await response.json(), {
      status: response.status,
    });
  } catch {
    return NextResponse.json(
      {
        status: "unavailable",
        data: null,
        reason: "draft_model_service_unavailable",
      },
      { status: 503 },
    );
  }
}
