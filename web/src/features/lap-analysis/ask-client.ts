import { answerView, askFailure, type AnswerLap, type AnswerView, type AskFailure } from "./answer";

// Builds and sends one engineer question. v2 laps go through the new proxy
// (`/api/review/sessions/{session}/ask`, revision ids); v1 laps through the
// existing `/api/engineer/ask` (attempt keys). Pure apart from `fetch`, which
// can be injected for tests.

/** The proxy's own deadline is 35 s; the client waits a little longer to see its 504. */
export const ASK_CLIENT_TIMEOUT_MS = 37_000;

export type AskLap = {
  id: string;
  lap: number;
  ms: number;
  attemptKey: string | null;
  revisionId: string | null;
};

export type AskContext = {
  source: "fixture" | "v1" | "v2";
  sessionKey: string;
  live: boolean;
  /** True when the engineer runtime reports `ready`; only then may a model answer. */
  runtimeReady: boolean;
  /** Raw v1 session type, for the v1 region comparison policy. */
  sessionType: string | null;
  /** The selected single region and its v1 track model, when there is one. */
  region: { modelId: string; revision: number; regionId: string } | null;
};

export type AskPlan =
  | { kind: "request"; version: "v1" | "v2"; url: string; body: Record<string, unknown> }
  | { kind: "blocked"; reason: string };

export const BLOCKED_COPY = "Questions need a recorded lap from the local engine.";

export function planAsk(
  context: AskContext,
  question: string,
  target: AskLap | null,
  reference: AskLap | null,
  allowMaterialization: boolean,
): AskPlan {
  if (!target) return { kind: "blocked", reason: "Select a lap first" };
  if (context.source === "v2" || target.revisionId) {
    if (!target.revisionId) return { kind: "blocked", reason: BLOCKED_COPY };
    return {
      kind: "request",
      version: "v2",
      url: `/api/review/sessions/${encodeURIComponent(context.sessionKey)}/ask`,
      body: {
        question,
        target: target.revisionId,
        reference: reference?.revisionId ?? null,
        allow_materialization: allowMaterialization,
        use_model: context.runtimeReady,
      },
    };
  }
  if (!target.attemptKey) return { kind: "blocked", reason: BLOCKED_COPY };
  const policy = context.sessionType === "time_trial" ? "time_trial" : "practice_qualifying";
  const selection =
    context.region && reference?.attemptKey
      ? {
          intent: "region_comparison",
          target_attempt_key: target.attemptKey,
          reference_attempt_key: reference.attemptKey,
          comparison_policy: policy,
          track_model_id: context.region.modelId,
          track_model_revision: context.region.revision,
          region_identifier: context.region.regionId,
        }
      : { intent: "attempt_summary", target_attempt_key: target.attemptKey };
  return { kind: "request", version: "v1", url: "/api/engineer/ask", body: { question, selection } };
}

export type AskOutcome =
  | { kind: "answer"; view: AnswerView }
  | { kind: "failure"; failure: AskFailure }
  | { kind: "cancelled" };

export function answerLaps(laps: readonly AskLap[]): AnswerLap[] {
  return laps.map((lap) => ({
    keys: [lap.id, lap.revisionId, lap.attemptKey].filter((key): key is string => typeof key === "string"),
    lap: lap.lap,
    ms: lap.ms,
  }));
}

function reasonOf(body: unknown): string | null {
  const reason = (body as { reason?: unknown } | null)?.reason;
  return typeof reason === "string" ? reason.slice(0, 128) : null;
}

export async function runAsk(
  plan: Extract<AskPlan, { kind: "request" }>,
  laps: readonly AskLap[],
  options: { fetchImpl?: typeof fetch; signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<AskOutcome> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, options.timeoutMs ?? ASK_CLIENT_TIMEOUT_MS);
  const onAbort = () => controller.abort();
  if (options.signal?.aborted) controller.abort();
  else options.signal?.addEventListener("abort", onAbort, { once: true });
  try {
    const response = await fetchImpl(plan.url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(plan.body),
      signal: controller.signal,
    });
    const body: unknown = await response.json().catch(() => null);
    if (response.ok) {
      const view = answerView(body, answerLaps(laps), plan.version);
      if (view) return { kind: "answer", view };
      return { kind: "failure", failure: askFailure(502, "upstream_response_invalid") };
    }
    return { kind: "failure", failure: askFailure(response.status, reasonOf(body)) };
  } catch {
    if (options.signal?.aborted && !timedOut) return { kind: "cancelled" };
    return { kind: "failure", failure: askFailure(null, null, timedOut) };
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", onAbort);
  }
}
