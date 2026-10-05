import { NextResponse } from "next/server";
import { forwardLocalRequest, isTrustedLocalMutation } from "@/lib/local-proxy";

export async function POST(
  request: Request,
  context: { params: Promise<{ playback_id: string }> },
) {
  if (!isTrustedLocalMutation(request)) {
    return NextResponse.json(
      { reason: "same_origin_request_required" },
      { status: 403 },
    );
  }
  const { playback_id } = await context.params;
  if (!/^[a-f0-9]{32}$/.test(playback_id)) {
    return NextResponse.json(
      { status: "unavailable", data: null, reason: "playback_unavailable" },
      { status: 404 },
    );
  }
  try {
    const response = await forwardLocalRequest(
      `/api/v1/replays/${playback_id}/step`,
      { method: "POST" },
      true,
    );
    const body = await response.json();
    return NextResponse.json(body, { status: response.status });
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
