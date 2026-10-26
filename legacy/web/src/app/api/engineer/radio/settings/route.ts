import { proxyGet, proxyMutation } from "@/lib/proxy-json";
export const runtime = "nodejs";
export async function GET(request: Request) {
  return proxyGet(request, "/api/v2/engineer/radio/settings", { maxResponseBytes: 65536 });
}
export async function PUT(request: Request) {
  return proxyMutation(request, "/api/v2/engineer/radio/settings", {
    method: "PUT", body: "json", maxRequestBytes: 8192, maxResponseBytes: 65536, forwardHeaders: ["if-match"],
  });
}
