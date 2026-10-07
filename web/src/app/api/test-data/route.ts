import { generateTestData } from "@/lib/test-data-fixtures";
import { isTestDataScenario } from "@/lib/test-data";

export const runtime = "nodejs";
const headers = {
  "Cache-Control": "no-store",
  "X-F1-Data-Source": "synthetic",
};
function unavailable(reason: string, status = 422) {
  return Response.json(
    { api_version: "v1", status: "unavailable", data: null, reason },
    { status, headers },
  );
}

export function GET(request: Request) {
  const query = new URL(request.url).searchParams;
  const allowed = new Set([
    "scenario",
    "session",
    "target",
    "reference",
    "tick",
  ]);
  for (const key of query.keys()) {
    if (
      !allowed.has(key) ||
      query.getAll(key).length !== 1 ||
      query.get(key)!.length > 64
    )
      return unavailable("Invalid or repeated test-data parameter.");
  }
  const scenario = query.get("scenario") ?? "populated";
  if (!isTestDataScenario(scenario))
    return unavailable("Unknown test-data scenario.");
  const tickText = query.get("tick") ?? "0";
  if (!/^\d{1,4}$/.test(tickText) || Number(tickText) > 3600)
    return unavailable("Test tick must be an integer from 0 to 3600.");
  try {
    const snapshot = generateTestData({
      scenario,
      tick: Number(tickText),
      session: query.get("session") ?? undefined,
      target: query.get("target") ?? undefined,
      reference: query.get("reference") ?? undefined,
    });
    if (scenario === "unavailable")
      return unavailable("Simulated test-data source unavailable.", 503);
    return Response.json(
      { api_version: "v1", status: "ok", data: snapshot, reason: null },
      { headers },
    );
  } catch (cause) {
    return unavailable(
      cause instanceof Error ? cause.message : "Test selection unavailable.",
    );
  }
}
