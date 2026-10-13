import { proxyGet, proxyMutation } from "@/lib/proxy-json";

export const runtime = "nodejs";

// The backend accepts at most 8 KiB and replies with the stored document.
const MAX_REQUEST_BYTES = 8 * 1024;
const MAX_RESPONSE_BYTES = 64 * 1024;

export async function GET(request: Request) {
  return proxyGet(request, "/api/v1/engineer/preferences", {
    timeoutMs: 5_000,
    maxResponseBytes: MAX_RESPONSE_BYTES,
  });
}

/** Forwards the complete document and `If-Match`; returns the `ETag` and 409 conflicts unchanged. */
export async function PUT(request: Request) {
  return proxyMutation(request, "/api/v1/engineer/preferences", {
    method: "PUT",
    body: "json",
    timeoutMs: 10_000,
    maxRequestBytes: MAX_REQUEST_BYTES,
    maxResponseBytes: MAX_RESPONSE_BYTES,
    forwardHeaders: ["if-match"],
  });
}
