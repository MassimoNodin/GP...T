import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

export async function upstreamHeaders(
  initial?: HeadersInit,
  needsControlToken = false,
): Promise<Headers> {
  const headers = new Headers(initial);
  const apiTokenFile = process.env.F1_ENGINEER_API_TOKEN_FILE;
  if (apiTokenFile !== undefined || needsControlToken) {
    const tokenPath =
      apiTokenFile ??
      process.env.F1_ENGINEER_CONTROL_TOKEN_FILE ??
      resolve(process.cwd(), "..", "data", ".f1-engineer-control-token");
    const token = (
      await readFile(/*turbopackIgnore: true*/ tokenPath, "utf8")
    ).trim();
    if (token.length < 32 || token.length > 4096 || /\s/.test(token)) {
      throw new Error("API authorization is unavailable.");
    }
    headers.set("authorization", `Bearer ${token}`);
  }
  return headers;
}
