import { isTrackModelId, proxyError, proxyGet } from "@/lib/proxy-json";

export const runtime = "nodejs";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ model_id: string }> },
) {
  const { model_id: modelId } = await params;
  if (!isTrackModelId(modelId)) {
    return proxyError(404, "unknown_track_model_revision");
  }
  const query = new URL(request.url).searchParams;
  const keys = [...query.keys()];
  const revisions = query.getAll("revision");
  if (keys.some((key) => key !== "revision") || revisions.length !== 1) {
    return proxyError(422, "track_model_revision_required");
  }
  if (!/^[1-9][0-9]{0,8}$/.test(revisions[0])) {
    return proxyError(422, "track_model_revision_invalid");
  }
  return proxyGet(
    request,
    `/api/v1/track-models/${encodeURIComponent(modelId)}/regions?revision=${revisions[0]}`,
    { timeoutMs: 8_000 },
  );
}
