import { isEvidenceId, proxyError, proxyMutation } from "@/lib/proxy-json";

export const runtime = "nodejs";

const MAX_REQUEST_BYTES = 8 * 1024;
const MAX_RESPONSE_BYTES = 256 * 1024;
// Ordinary questions use the engine's 30 s service deadline.
const ASK_TIMEOUT_MS = 35_000;
// Questions about a future lap (`binding_id`) may wait up to 270 s upstream.
const FUTURE_LAP_TIMEOUT_MS = 285_000;

function deadlineFor(body: Uint8Array | null): number {
  if (!body) return ASK_TIMEOUT_MS;
  try {
    const parsed: unknown = JSON.parse(new TextDecoder().decode(body));
    const bindingId =
      parsed !== null && typeof parsed === "object"
        ? (parsed as Record<string, unknown>).binding_id
        : undefined;
    return typeof bindingId === "string" ? FUTURE_LAP_TIMEOUT_MS : ASK_TIMEOUT_MS;
  } catch {
    return ASK_TIMEOUT_MS;
  }
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ session: string }> },
) {
  const { session } = await params;
  if (!isEvidenceId(session)) return proxyError(404, "session_not_found");
  return proxyMutation(
    request,
    `/api/v2/session-evidence/sessions/${encodeURIComponent(session)}/ask`,
    {
      method: "POST",
      body: "json",
      maxRequestBytes: MAX_REQUEST_BYTES,
      maxResponseBytes: MAX_RESPONSE_BYTES,
      timeoutMs: deadlineFor,
    },
  );
}
