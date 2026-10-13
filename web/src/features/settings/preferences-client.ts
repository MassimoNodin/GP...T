"use client";

import {
  ifMatchHeader,
  parsePreferencesRead,
  parsePreferencesSaveResult,
  preferencesRequestBody,
  type PreferencesDoc,
  type PreferencesRead,
  type PreferencesSaveResult,
} from "@/features/review-data/preferences";

const ENDPOINT = "/api/engineer/preferences";

async function readBody(response: Response): Promise<unknown> {
  try {
    return (await response.json()) as unknown;
  } catch {
    return null;
  }
}

/**
 * Saves the document through the same-origin proxy. `revision` becomes the
 * `If-Match` precondition; pass null to save unconditionally (used to replace a
 * saved file that could not be read). Never throws.
 */
export async function putPreferences(
  doc: PreferencesDoc,
  revision: number | null,
  signal?: AbortSignal,
): Promise<PreferencesSaveResult> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (revision !== null) headers["if-match"] = ifMatchHeader(revision);
  try {
    const response = await fetch(ENDPOINT, {
      method: "PUT",
      headers,
      body: preferencesRequestBody(doc),
      cache: "no-store",
      signal,
    });
    return parsePreferencesSaveResult(response.status, await readBody(response), response.headers.get("etag"));
  } catch {
    return { ok: false, kind: "unreachable", reason: "local_engine_unavailable" };
  }
}

/** Reads the saved document ("Load latest"). Never throws. */
export async function getPreferences(signal?: AbortSignal): Promise<PreferencesRead> {
  try {
    const response = await fetch(ENDPOINT, { cache: "no-store", signal });
    return parsePreferencesRead(response.status, await readBody(response));
  } catch {
    return { state: "unreachable" };
  }
}
