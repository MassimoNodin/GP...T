import { proxyGet, proxyMutation } from "@/lib/proxy-json";

export const runtime = "nodejs";
const path = "/api/v1/secrets/openai-api-key";

export async function GET(request: Request) {
  return proxyGet(request, path, { timeoutMs: 5_000, maxResponseBytes: 4096 });
}

export async function PUT(request: Request) {
  return proxyMutation(request, path, {
    method: "PUT", body: "json", timeoutMs: 10_000, maxRequestBytes: 2048, maxResponseBytes: 4096,
  });
}

export async function DELETE(request: Request) {
  return proxyMutation(request, path, { method: "DELETE", body: "none", timeoutMs: 5_000, maxResponseBytes: 4096 });
}
