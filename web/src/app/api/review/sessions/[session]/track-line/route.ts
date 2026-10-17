import { fetchEvidenceAttempts } from "@/features/review-data/backend";
import { evidenceLaps } from "@/features/review-data/session-evidence";
import { downsamplePath } from "@/features/ui-foundation/measured-track";
import { fetchEvidenceTrace } from "@/features/lap-analysis/trace-data";
import type { TracePath } from "@/features/lap-analysis/lap-telemetry";
import { isTrustedLocalRead } from "@/lib/local-proxy";
import { isEvidenceId, proxyError, proxyOk } from "@/lib/proxy-json";

export const runtime = "nodejs";

// Measured circuit line for the overview and Drive maps: the car positions of
// a representative clean lap (fastest valid lap with a materialised trace),
// thinned to a few hundred points. Read through the same readers Lap Analysis uses.

const CANDIDATES = 3;
const CACHE_LIMIT = 32;
const cache = new Map<string, TracePath>();

export async function GET(
  request: Request,
  { params }: { params: Promise<{ session: string }> },
) {
  if (!isTrustedLocalRead(request)) return proxyError(403, "untrusted_origin");
  const { session } = await params;
  if (!isEvidenceId(session)) return proxyError(404, "session_not_found");
  const cached = cache.get(session);
  if (cached) return proxyOk(200, { path: cached });

  const result = await fetchEvidenceAttempts(session);
  if (!result) return proxyError(503, "unreachable");
  const laps = evidenceLaps(result.attempts)
    .filter((lap) => lap.valid && !lap.pit && lap.startObserved !== false && lap.traceAvailable)
    .sort((a, b) => a.ms - b.ms)
    .slice(0, CANDIDATES);
  if (laps.length === 0) return proxyError(404, "no_position_data");

  let busy = false;
  for (const lap of laps) {
    const trace = await fetchEvidenceTrace(session, lap.revisionId);
    if (trace.state === "unavailable") {
      busy ||= trace.code?.includes("busy") ?? false;
      continue;
    }
    if (!trace.path) continue;
    const path = downsamplePath(trace.path);
    if (cache.size >= CACHE_LIMIT) cache.clear();
    cache.set(session, path);
    return proxyOk(200, { path });
  }
  return busy ? proxyError(409, "comparison_read_budget_busy") : proxyError(404, "no_position_data");
}
