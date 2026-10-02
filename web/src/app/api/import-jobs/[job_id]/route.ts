import { NextResponse } from "next/server";
import { forwardLocalRequest } from "@/lib/local-proxy";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ job_id: string }> },
) {
  const { job_id } = await params;
  if (!/^[a-f0-9]{32}$/.test(job_id)) {
    return NextResponse.json(
      { error: "import_job_unavailable" },
      { status: 404 },
    );
  }
  try {
    const response = await forwardLocalRequest(`/api/v1/import-jobs/${job_id}`);
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    return NextResponse.json(
      { error: "import_job_unavailable" },
      { status: 503 },
    );
  }
}
