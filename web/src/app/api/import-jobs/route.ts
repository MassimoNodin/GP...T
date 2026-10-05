import { NextResponse } from "next/server";
import { forwardLocalRequest, isTrustedLocalMutation } from "@/lib/local-proxy";
import {
  appScreenFrom,
  appScreenPath,
  sanitizeAppStateQuery,
  type AppScreen,
} from "@/lib/navigation";

function redirectWith(
  request: Request,
  screen: AppScreen,
  preservedQuery: string,
  params: Record<string, string>,
) {
  const destination = new URL(appScreenPath(screen), request.url);
  const state = new URLSearchParams(sanitizeAppStateQuery(preservedQuery));
  state.forEach((value, key) => destination.searchParams.append(key, value));
  if (Object.hasOwn(params, "import_job_id")) {
    destination.searchParams.delete("import_error");
  }
  if (Object.hasOwn(params, "import_error")) {
    destination.searchParams.delete("import_job_id");
  }
  for (const [key, value] of Object.entries(params)) {
    destination.searchParams.set(key, value);
  }
  return NextResponse.redirect(destination, 303);
}

export async function POST(request: Request) {
  if (!isTrustedLocalMutation(request)) {
    return NextResponse.json(
      { error: "same_origin_request_required" },
      { status: 403 },
    );
  }
  const form = await request.formData();
  const returnTo = appScreenFrom(form.get("return_to"));
  const preservedQuery = sanitizeAppStateQuery(
    String(form.get("preserved_query") ?? ""),
  );
  const captureId = form.get("capture_id");
  if (typeof captureId !== "string" || !/^[a-f0-9]{32}$/.test(captureId)) {
    return redirectWith(request, returnTo, preservedQuery, {
      import_error: "capture_unavailable",
    });
  }
  try {
    const response = await forwardLocalRequest(
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
      return redirectWith(request, returnTo, preservedQuery, {
        import_error:
          response.status === 409 ? "busy" : (result.reason ?? "unavailable"),
      });
    }
    return redirectWith(request, returnTo, preservedQuery, {
      import_job_id: result.data.job_id,
    });
  } catch {
    return redirectWith(request, returnTo, preservedQuery, {
      import_error: "unavailable",
    });
  }
}
