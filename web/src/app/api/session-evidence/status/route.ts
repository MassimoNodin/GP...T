import { NextResponse } from "next/server";
import { forwardLocalRequest, isTrustedLocalRead } from "@/lib/local-proxy";

export async function GET(request: Request) {
  const headers = { "Cache-Control": "no-store" };
  if (!isTrustedLocalRead(request)) {
    return NextResponse.json(
      { state: "unavailable" },
      { status: 403, headers },
    );
  }
  try {
    const response = await forwardLocalRequest(
      "/api/v2/session-evidence/status",
      {
        signal: AbortSignal.timeout(4500),
      },
    );
    const body: unknown = await response.json();
    return NextResponse.json(body, { status: response.status, headers });
  } catch {
    return NextResponse.json(
      { state: "unavailable" },
      { status: 503, headers },
    );
  }
}
