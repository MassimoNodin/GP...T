import { forwardLocalRequest, isTrustedLocalRead } from "@/lib/local-proxy";

const captureIdPattern = /^[a-f0-9]{32}$/;
const versionPattern = /^[a-f0-9]{64}$/;
const maxDownloadBytes = 16 * 1024 ** 3;

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

export async function GET(
  request: Request,
  { params }: { params: Promise<{ capture_id: string }> },
) {
  if (!isTrustedLocalRead(request)) {
    return unavailable(403, "same_origin_request_required");
  }
  if (request.headers.has("range")) {
    return unavailable(416, "recording_download_range_unsupported");
  }

  const { capture_id: captureId } = await params;
  if (!captureIdPattern.test(captureId)) {
    return unavailable(422, "recording_download_capture_id_invalid");
  }
  const query = new URL(request.url).searchParams;
  if ([...query.keys()].some((key) => key !== "version")) {
    return unavailable(422, "recording_download_query_parameter_invalid");
  }
  const versions = query.getAll("version");
  if (versions.length !== 1) {
    return unavailable(422, "recording_download_version_invalid");
  }
  const version = versions[0];
  if (!versionPattern.test(version)) {
    return unavailable(422, "recording_download_version_invalid");
  }

  let upstream: Response;
  try {
    upstream = await forwardLocalRequest(
      `/api/v1/recording-sources/${captureId}/download?version=${encodeURIComponent(version)}`,
      { signal: request.signal },
      true,
    );
  } catch {
    return unavailable(503, "recording_download_service_unavailable");
  }

  if (!upstream.ok) {
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    });
  }

  const disposition = upstream.headers.get("content-disposition");
  const rawLength = upstream.headers.get("content-length");
  const byteLength =
    rawLength && /^[0-9]{1,11}$/.test(rawLength) ? Number(rawLength) : null;
  if (
    !disposition ||
    disposition.length > 4096 ||
    !/^attachment; filename="capture\.f1ecap"; filename\*=UTF-8''[A-Za-z0-9!#$&+\-.^_`|~%]*$/.test(
      disposition,
    ) ||
    byteLength === null ||
    !Number.isSafeInteger(byteLength) ||
    byteLength < 0 ||
    byteLength > maxDownloadBytes ||
    !upstream.body
  ) {
    await upstream.body?.cancel();
    return unavailable(502, "recording_download_response_invalid");
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "application/octet-stream",
      "Content-Disposition": disposition,
      "Content-Length": String(byteLength),
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
