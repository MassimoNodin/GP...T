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
  let form: FormData | null = null;
  try {
    form = await request.formData();
  } catch {
    form = null;
  }
  const returnTo = appScreenFrom(form?.get("return_to") ?? null);
  const preservedQuery = sanitizeAppStateQuery(
    String(form?.get("preserved_query") ?? ""),
  );
  if (!/^[a-f0-9]{32}$/.test(job_id)) {
    return redirectWith(request, returnTo, preservedQuery, {
      import_error: "job_unavailable",
    });
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
      return redirectWith(request, returnTo, preservedQuery, {
        import_error:
          result.reason ??
          (response.status === 409 ? "busy" : "unavailable"),
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
