import { proxyGet } from "@/lib/proxy-json";
export const runtime = "nodejs";
export async function GET(request: Request) {
  return proxyGet(request, "/api/v2/engineer/radio/presence", { timeoutMs: 3000, maxResponseBytes: 65536 });
}
