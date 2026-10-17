import { proxyGet, proxyMutation } from "@/lib/proxy-json";

export const runtime = "nodejs";

export async function GET(request: Request) {
  return proxyGet(request, "/api/v1/engineer/speech", { timeoutMs: 10_000, maxResponseBytes: 64 * 1024 });
}

export async function PUT(request: Request) {
  return proxyMutation(request, "/api/v1/engineer/speech", {
    method: "PUT", body: "json", timeoutMs: 10_000,
    maxRequestBytes: 2048, maxResponseBytes: 64 * 1024, forwardHeaders: ["if-match"],
  });
}
