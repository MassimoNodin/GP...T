import { proxyError, proxyGet } from "@/lib/proxy-json";

export const runtime = "nodejs";

const ALLOWED = new Set([
  "limit",
  "offset",
  "q",
  "latest_job_status",
  "availability",
  "selected_capture_id",
]);

export async function GET(request: Request) {
  const query = new URL(request.url).searchParams;
  const upstream = new URLSearchParams();
  for (const [key, value] of query) {
    // The engine validates values and rejects repeated keys; unknown keys never leave the proxy.
    if (!ALLOWED.has(key) || value.length > 128) {
      return proxyError(422, "query_parameter");
    }
    upstream.append(key, value);
  }
  const suffix = upstream.size ? `?${upstream}` : "";
  return proxyGet(request, `/api/v1/recording-sources/page${suffix}`, {
    timeoutMs: 8_000,
  });
}
