import { NextResponse } from "next/server";
import { forwardLocalRequest, isTrustedLocalMutation } from "@/lib/local-proxy";

export async function POST(request: Request) {
  if (!isTrustedLocalMutation(request)) {
    return NextResponse.json({ reason: "same_origin_request_required" }, { status: 403 });
  }
  try {
    const response = await forwardLocalRequest("/api/v1/recording-groups/start", { method: "POST" }, true);
    return NextResponse.json(await response.json(), { status: response.status });
  } catch {
    return NextResponse.json(
      { api_version: "v1", status: "unavailable", data: null, reason: "recording_service_unavailable" },
      { status: 503 },
    );
  }
}
