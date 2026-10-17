import { proxyMutation } from "@/lib/proxy-json";
export const runtime = "nodejs";
export async function PUT(request: Request) {
  return proxyMutation(request, "/api/v2/engineer/radio/presence/refresh", {
    method: "PUT", body: "json", maxRequestBytes: 64, maxResponseBytes: 1024,
  });
}
