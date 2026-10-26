import { NextResponse } from "next/server";
import { forwardLocalRequest, isTrustedLocalMutation } from "@/lib/local-proxy";

export async function POST(request: Request) {
  if (!isTrustedLocalMutation(request)) {
    return NextResponse.json(
      { reason: "same_origin_request_required" },
      { status: 403 },
    );
  }
  try {
    const body = await request.text();
    const response = await forwardLocalRequest(
      "/api/v1/replays/start",
      {
        method: "POST",
        body,
        headers: { "content-type": "application/json" },
      },
      true,
    );
    const payload = await response.json();
    return NextResponse.json(payload, { status: response.status });
  } catch {
    return NextResponse.json(
      {
        status: "unavailable",
        data: null,
        reason: "replay_service_unavailable",
      },
      { status: 503 },
    );
  }
}
