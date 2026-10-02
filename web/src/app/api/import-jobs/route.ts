import { NextResponse } from "next/server";
import {
  forwardImportRequest,
  isTrustedImportRequest,
} from "@/lib/import-proxy";

function redirectWith(request: Request, params: Record<string, string>) {
  const destination = new URL("/", request.url);
  for (const [key, value] of Object.entries(params)) {
    destination.searchParams.set(key, value);
  }
  return NextResponse.redirect(destination, 303);
}

export async function POST(request: Request) {
  if (!isTrustedImportRequest(request)) {
    return NextResponse.json(
      { error: "same_origin_request_required" },
      { status: 403 },
    );
  }
  const form = await request.formData();
  const captureId = form.get("capture_id");
  if (typeof captureId !== "string" || !/^[a-f0-9]{32}$/.test(captureId)) {
    return redirectWith(request, { import_error: "capture_unavailable" });
  }
  try {
    const response = await forwardImportRequest(
      "/api/v1/import-jobs",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ capture_id: captureId }),
      },
      true,
    );
    const result = (await response.json()) as {
      data?: { job_id?: string };
      reason?: string;
    };
    if (!response.ok || !result.data?.job_id) {
      return redirectWith(request, {
        import_error:
          response.status === 409 ? "busy" : (result.reason ?? "unavailable"),
      });
    }
    return redirectWith(request, { import_job_id: result.data.job_id });
  } catch {
    return redirectWith(request, { import_error: "unavailable" });
  }
}
