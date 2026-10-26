import { envelopeReason } from "@/features/review-data/parse-utils";
import { humanReason } from "@/features/review-data/reasons";

// Browser side of the deferred sections (plan 3.0.1): when the server loader's
// budget ran out, the card fetches the same data through the proxy route. A
// 409 busy answer retries twice (1 s, then 3 s) before the section shows its
// unavailable state. The hook lives in `use-deferred-read.ts`.

export const BUSY_RETRY_DELAYS_MS = [1000, 3000] as const;

export type DeferredOutcome<R> =
  | { kind: "ok"; value: R }
  | { kind: "busy"; reason: string }
  | { kind: "unavailable"; reason: string };

/** Classifies one proxy response: parsed value, engine busy, or unavailable (with plain-language reason). */
export function interpretDeferredResponse<R>(
  status: number,
  body: unknown,
  parse: (body: unknown) => R | null,
): DeferredOutcome<R> {
  const code = envelopeReason(body);
  if (status === 200) {
    const value = parse(body);
    return value === null
      ? { kind: "unavailable", reason: humanReason("upstream_response_invalid") }
      : { kind: "ok", value };
  }
  if (status === 409 && code !== null && code.includes("busy")) {
    return { kind: "busy", reason: humanReason(code) };
  }
  return { kind: "unavailable", reason: humanReason(code) || "This section is not available." };
}
