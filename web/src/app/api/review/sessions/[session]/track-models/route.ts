import { isEvidenceId, proxyError, proxyGet } from "@/lib/proxy-json";

export const runtime = "nodejs";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ session: string }> },
) {
  const { session } = await params;
  if (!isEvidenceId(session)) return proxyError(404, "session_not_found");
  return proxyGet(
    request,
    `/api/v2/session-evidence/sessions/${encodeURIComponent(session)}/track-models`,
    { timeoutMs: 8_000 },
  );
}
