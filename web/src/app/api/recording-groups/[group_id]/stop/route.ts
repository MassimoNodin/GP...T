import { NextResponse } from "next/server";
import { forwardLocalRequest, isTrustedLocalMutation } from "@/lib/local-proxy";

export async function POST(request: Request, context: { params: Promise<{ group_id: string }> }) {
  if (!isTrustedLocalMutation(request)) {
    return NextResponse.json({ reason: "same_origin_request_required" }, { status: 403 });
  }
  const { group_id } = await context.params;
  if (!/^[a-f0-9]{32}$/.test(group_id)) {
    return NextResponse.json({ reason: "recording_group_unavailable" }, { status: 404 });
  }
  try {
    const response = await forwardLocalRequest(
      `/api/v1/recording-groups/${group_id}/stop`,
      { method: "POST" },
      true,
    );
    return NextResponse.json(await response.json(), { status: response.status });
  } catch {
    return NextResponse.json(
      { api_version: "v1", status: "unavailable", data: null, reason: "recording_service_unavailable" },
      { status: 503 },
    );
  }
}
