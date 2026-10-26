import { isEvidenceId, proxyError, proxyMutation } from "@/lib/proxy-json";

export const runtime = "nodejs";

// A materialised lap can carry thousands of samples; the backend caps one read
// at 32 MiB of encoded records.
const MAX_RESPONSE_BYTES = 40 * 1024 * 1024;
const TIMEOUT_MS = 45_000;

export async function POST(
  request: Request,
  { params }: { params: Promise<{ session: string; revision: string }> },
) {
  const { session, revision } = await params;
  if (!isEvidenceId(session)) return proxyError(404, "session_not_found");
  if (!isEvidenceId(revision)) return proxyError(404, "attempt_trace_unavailable");
  return proxyMutation(
    request,
    `/api/v2/session-evidence/sessions/${encodeURIComponent(session)}/attempts/${encodeURIComponent(revision)}/materialize`,
    {
      method: "POST",
      body: "none",
      timeoutMs: TIMEOUT_MS,
      maxResponseBytes: MAX_RESPONSE_BYTES,
    },
  );
}
