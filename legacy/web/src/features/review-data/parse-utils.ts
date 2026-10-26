// Small defensive helpers shared by the review-data parsers. Nothing performs
// I/O, so every parser built on them can be tested with captured payloads.

export function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function integer(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) ? value : null;
}

export function text(value: unknown, maxLength = 512): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.slice(0, maxLength) : null;
}

export function bool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

/** The `data` member of a `{ data: ... }` API envelope, or null. */
export function envelopeData(body: unknown): unknown {
  return asRecord(body)?.data ?? null;
}

/** The `reason` member of an unavailable envelope, or null. */
export function envelopeReason(body: unknown): string | null {
  return text(asRecord(body)?.reason, 128);
}
