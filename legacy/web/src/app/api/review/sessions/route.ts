import { isEvidenceId, proxyError, proxyGet } from "@/lib/proxy-json";

export const runtime = "nodejs";

const PAGE_LIMIT = 100;

/** One page of v2 sessions (id, lifecycle, context) for the browser's new-session watcher. */
export async function GET(request: Request) {
  const after = new URL(request.url).searchParams.get("after") ?? "";
  if (after && !isEvidenceId(after)) return proxyError(400, "invalid_cursor");
  const query = new URLSearchParams({ limit: String(PAGE_LIMIT), after });
  return proxyGet(request, `/api/v2/session-evidence/sessions?${query}`, { timeoutMs: 5000 });
}
