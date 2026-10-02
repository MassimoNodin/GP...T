import { NextResponse } from "next/server";
import { forwardLocalRequest, isTrustedLocalMutation } from "@/lib/local-proxy";

export async function POST(
  request: Request,
  context: { params: Promise<{ recording_id: string }> },
) {
  if (!isTrustedLocalMutation(request)) {
    return NextResponse.json(
      { reason: "same_origin_request_required" },
      { status: 403 },
    );
  }
  const { recording_id } = await context.params;
  if (!/^[a-f0-9]{32}$/.test(recording_id)) {
    return NextResponse.json(
      { status: "unavailable", data: null, reason: "recording_unavailable" },
      { status: 404 },
    );
  }
  try {
    const response = await forwardLocalRequest(
      `/api/v1/recordings/${recording_id}/stop`,
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
        reason: "recording_service_unavailable",
      },
      { status: 503 },
    );
  }
}
