import { NextResponse } from "next/server";
import { forwardLocalRequest } from "@/lib/local-proxy";

export async function GET() {
  try {
    const response = await forwardLocalRequest("/api/v1/recording-groups/current");
    return NextResponse.json(await response.json(), { status: response.status });
  } catch {
    return NextResponse.json(
      { api_version: "v1", status: "unavailable", data: null, reason: "recording_service_unavailable" },
      { status: 503 },
    );
  }
}
