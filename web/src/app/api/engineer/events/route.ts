import { proxyError, proxyGet } from "@/lib/proxy-json";

export const runtime = "nodejs";

const MAX_LIMIT = 200;

export async function GET(request: Request) {
  const query = new URL(request.url).searchParams;
  const upstream = new URLSearchParams();
  for (const key of new Set(query.keys())) {
    if (
      !["after", "limit", "session_id"].includes(key) ||
      query.getAll(key).length !== 1
    ) {
      return proxyError(422, "query_parameter_invalid");
    }
  }
  const after = query.get("after");
  if (after !== null) {
    if (!/^[0-9]{1,18}$/.test(after)) {
      return proxyError(422, "query_parameter_invalid");
    }
    upstream.set("after", after);
  }
  const limit = query.get("limit");
  if (limit !== null) {
    if (
      !/^[0-9]{1,3}$/.test(limit) ||
      Number(limit) < 1 ||
      Number(limit) > MAX_LIMIT
    ) {
      return proxyError(422, "query_parameter_invalid");
    }
    upstream.set("limit", limit);
  }
  const sessionId = query.get("session_id");
  if (sessionId !== null) {
    if (!/^[A-Za-z0-9:_-]{1,128}$/.test(sessionId)) {
      return proxyError(422, "query_parameter_invalid");
    }
    upstream.set("session_id", sessionId);
  }
  const suffix = upstream.size ? `?${upstream}` : "";
  return proxyGet(request, `/api/v2/engineer/events${suffix}`, {
    timeoutMs: 6_000,
  });
}
