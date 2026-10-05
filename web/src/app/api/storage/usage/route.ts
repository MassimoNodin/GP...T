import { NextResponse } from "next/server";
import { forwardLocalRequest } from "@/lib/local-proxy";

export async function GET() {
  try {
    const response = await forwardLocalRequest("/api/v1/storage/usage");
    const body = await response.json();
    return NextResponse.json(body, { status: response.status });
  } catch {
    return NextResponse.json(
      {
        api_version: "v1",
        status: "unavailable",
        data: null,
        reason: "storage_service_unavailable",
      },
      { status: 503 },
    );
  }
}
