import { proxyGet } from "@/lib/proxy-json";

export const runtime = "nodejs";

export async function GET(request: Request) {
  return proxyGet(request, "/api/v1/track-models", { timeoutMs: 8_000 });
}
