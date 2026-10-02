import { NextResponse } from "next/server";
import { forwardLocalRequest, isTrustedLocalMutation } from "@/lib/local-proxy";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ job_id: string }> },
) {
  const { job_id } = await params;
  if (!isTrustedLocalMutation(request)) {
    return NextResponse.json(
      { error: "same_origin_request_required" },
      { status: 403 },
    );
  }
  if (!/^[a-f0-9]{32}$/.test(job_id)) {
    return NextResponse.redirect(
      new URL("/?import_error=job_unavailable", request.url),
      303,
    );
  }
  try {
    const response = await forwardLocalRequest(
      `/api/v1/import-jobs/${job_id}/retry`,
      { method: "POST" },
      true,
    );
    const result = (await response.json()) as {
      data?: { job_id?: string };
      reason?: string;
    };
    if (!response.ok || !result.data?.job_id) {
      const destination = new URL("/", request.url);
      destination.searchParams.set(
        "import_error",
        response.status === 409 ? "busy" : (result.reason ?? "unavailable"),
      );
      return NextResponse.redirect(destination, 303);
    }
    const destination = new URL("/", request.url);
    destination.searchParams.set("import_job_id", result.data.job_id);
    return NextResponse.redirect(destination, 303);
  } catch {
    return NextResponse.redirect(
      new URL("/?import_error=unavailable", request.url),
      303,
    );
  }
}
