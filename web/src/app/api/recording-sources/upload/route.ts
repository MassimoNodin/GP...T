import { forwardLocalRequest, isTrustedLocalMutation } from "@/lib/local-proxy";

export const runtime = "nodejs";

function unavailable(status: number, reason: string) {
  return Response.json(
    { api_version: "v1", status: "unavailable", data: null, reason },
    {
      status,
      headers: {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    },
  );
}

export async function POST(request: Request) {
  if (!isTrustedLocalMutation(request)) {
    return unavailable(403, "same_origin_request_required");
  }
  const contentType =
    request.headers
      .get("content-type")
      ?.split(";", 1)[0]
      ?.trim()
      .toLowerCase() ?? "";
  if (contentType !== "application/octet-stream" || !request.body) {
    return unavailable(415, "recording_upload_content_type_invalid");
  }
  const encodedFilename = request.headers.get("x-capture-upload-filename");
  if (
    !encodedFilename ||
    encodedFilename.length > 2_048 ||
    !/^[\x21-\x7e]+$/.test(encodedFilename)
  ) {
    return unavailable(422, "recording_upload_filename_invalid");
  }

  const headers = new Headers({
    "Content-Type": "application/octet-stream",
    "X-Capture-Upload-Filename": encodedFilename,
  });
  const contentLength = request.headers.get("content-length");
  if (contentLength !== null) headers.set("Content-Length", contentLength);

  let upstream: Response;
  try {
    upstream = await forwardLocalRequest(
      "/api/v1/recording-sources/upload",
      {
        method: "POST",
        headers,
        body: request.body,
        signal: request.signal,
        duplex: "half",
      } as RequestInit,
      true,
    );
  } catch {
    return unavailable(503, "recording_upload_service_unavailable");
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
