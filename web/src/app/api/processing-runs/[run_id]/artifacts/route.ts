import { NextResponse } from "next/server";
import { forwardLocalRequest } from "@/lib/local-proxy";

const allowedKeys = new Set(["kind", "limit", "offset"]);
const kinds = new Set(["all", "player_trace", "car_observation_chunk"]);

function unavailable(reason: string, status = 422) {
  return NextResponse.json(
    { api_version: "v1", status: "unavailable", data: null, reason },
    { status },
  );
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ run_id: string }> },
) {
  const { run_id: runId } = await params;
  if (!/^[a-f0-9]{64}$/.test(runId)) {
    return unavailable("processing_run_id_invalid");
  }

  const incoming = new URL(request.url).searchParams;
  const query: Record<string, string> = {};
  for (const [key, value] of incoming) {
    if (!allowedKeys.has(key)) {
      return unavailable("invalid_processing_run_artifact_query_parameter");
    }
    if (Object.hasOwn(query, key)) {
      return unavailable("invalid_processing_run_artifact_repeated_parameter");
    }
    query[key] = value;
  }

  const kind = query.kind ?? "all";
  const limit = query.limit ?? "50";
  const offset = query.offset ?? "0";
  if (!kinds.has(kind)) return unavailable("invalid_processing_run_artifact_kind");
  if (!/^[0-9]{1,3}$/.test(limit) || Number(limit) < 1 || Number(limit) > 50) {
    return unavailable("invalid_processing_run_artifact_limit");
  }
  if (
    !/^[0-9]{1,6}$/.test(offset) ||
    Number(offset) < 0 ||
    Number(offset) > 100_000
  ) {
    return unavailable("invalid_processing_run_artifact_offset");
  }

  const normalized = new URLSearchParams({ kind, limit, offset });
  try {
    const response = await forwardLocalRequest(
      `/api/v1/processing-runs/${runId}/artifacts?${normalized}`,
    );
    return NextResponse.json(await response.json(), { status: response.status });
  } catch {
    return unavailable("processing_run_artifact_service_unavailable", 503);
  }
}
