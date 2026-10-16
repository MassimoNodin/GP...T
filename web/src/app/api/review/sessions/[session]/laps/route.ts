import { isEvidenceId, proxyError, proxyGet } from "@/lib/proxy-json";

export const runtime = "nodejs";

// The backend scan has a 10 s budget; leave room for the response.
const TIMEOUT_MS = 14_000;

export async function GET(
  request: Request,
  { params }: { params: Promise<{ session: string }> },
) {
  const { session } = await params;
  if (!isEvidenceId(session)) return proxyError(404, "session_not_found");
  return proxyGet(
    request,
    `/api/v2/session-evidence/sessions/${encodeURIComponent(session)}/laps`,
    { timeoutMs: TIMEOUT_MS },
  );
}
