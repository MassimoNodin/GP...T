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
    const body: unknown = await request.json();
    if (
      typeof body !== "object" || body === null || Array.isArray(body) ||
      !Number.isSafeInteger((body as Record<string, unknown>).expected_revision) ||
      !Number.isInteger((body as Record<string, unknown>).expected_revision) ||
      typeof (body as Record<string, unknown>).expected_recording_id !== "string" ||
      !/^[a-f0-9]{32}$/.test((body as Record<string, string>).expected_recording_id)
    ) {
      return NextResponse.json({ reason: "recording_group_transition_invalid" }, { status: 422 });
    }
    const response = await forwardLocalRequest(
      `/api/v1/recording-groups/${group_id}/pause`,
      { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) },
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
