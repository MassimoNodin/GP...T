import { NextResponse } from "next/server";
import { forwardLocalRequest, isTrustedLocalMutation } from "@/lib/local-proxy";
import {
  acceptsJson,
  proxyError,
  proxyImportJob,
  readBoundedBytes,
} from "@/lib/proxy-json";
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

const MAX_JSON_REQUEST_BYTES = 4 * 1024;

// JSON branch (`Accept: application/json`): accepts a JSON or form body and
// answers with a JSON envelope instead of a redirect.
async function queueImportJson(request: Request) {
  let captureId: unknown;
  let queueIfBusy = false;
  try {
    const contentType = request.headers
      .get("content-type")
      ?.split(";", 1)[0]
      ?.trim()
      .toLowerCase();
    if (contentType === "application/json") {
      const read = await readBoundedBytes(
        request.body,
        MAX_JSON_REQUEST_BYTES,
        request.signal,
      );
      if (!read.ok) return proxyError(413, "request_body_limit_exceeded");
      const parsed: unknown = JSON.parse(new TextDecoder().decode(read.bytes));
      const record =
        parsed !== null && typeof parsed === "object"
          ? (parsed as Record<string, unknown>)
          : {};
      captureId = record.capture_id;
      queueIfBusy = record.queue_if_busy === true || record.queue_if_busy === "true";
    } else {
      const form = await request.formData();
      captureId = form.get("capture_id");
      queueIfBusy = form.get("queue_if_busy") === "true";
    }
  } catch {
    return proxyError(400, "request_body_invalid");
  }
  if (typeof captureId !== "string" || !/^[a-f0-9]{32}$/.test(captureId)) {
    return proxyError(422, "capture_unavailable");
  }
  return proxyImportJob("create", "/api/v1/import-jobs", {
    capture_id: captureId,
    queue_if_busy: queueIfBusy,
  });
}

export async function POST(request: Request) {
  const wantsJson = acceptsJson(request);
  if (!isTrustedLocalMutation(request)) {
    return wantsJson
      ? proxyError(403, "same_origin_request_required")
      : NextResponse.json(
          { error: "same_origin_request_required" },
          { status: 403 },
        );
  }
  if (wantsJson) return queueImportJson(request);
  const form = await request.formData();
  const returnTo = appScreenFrom(form.get("return_to"));
  const preservedQuery = sanitizeAppStateQuery(
    String(form.get("preserved_query") ?? ""),
  );
  const queueIfBusy = form.get("queue_if_busy") === "true";
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
        body: JSON.stringify({
          capture_id: captureId,
          queue_if_busy: queueIfBusy,
        }),
      },
      true,
    );
    const result = (await response.json()) as {
      data?: { job_id?: string };
      reason?: string;
    };
    if (!response.ok || !result.data?.job_id) {
      const busy =
        result.reason === "another_import_is_in_progress" ||
        result.reason === "another_local_operation_is_in_progress";
      return redirectWith(request, returnTo, preservedQuery, {
        import_error: busy
          ? "busy"
          : (result.reason ??
            (response.status === 409 ? "busy" : "unavailable")),
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
