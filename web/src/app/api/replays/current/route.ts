import { NextResponse } from "next/server";
import { forwardLocalRequest } from "@/lib/local-proxy";

export async function GET() {
  try {
    const response = await forwardLocalRequest("/api/v1/replays/current");
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
