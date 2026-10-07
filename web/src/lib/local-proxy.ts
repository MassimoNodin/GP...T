import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { assertLoopbackHttpUrl } from "./local-api-transport";

export function isTrustedLocalMutation(request: Request) {
  const configuredOrigin = process.env.F1_ENGINEER_WEB_ORIGIN;
  const allowedOrigins = new Set([
    "http://127.0.0.1:3000",
    "http://localhost:3000",
  ]);
  if (configuredOrigin) {
    try {
      const configured = new URL(configuredOrigin);
      allowedOrigins.add(configured.origin);
      if (["127.0.0.1", "localhost", "[::1]"].includes(configured.hostname)) {
        for (const hostname of ["127.0.0.1", "localhost", "[::1]"]) {
          const alias = new URL(configured.origin);
          alias.hostname = hostname;
          allowedOrigins.add(alias.origin);
        }
      }
    } catch {
      return false;
    }
  }
  return (
    allowedOrigins.has(request.headers.get("origin") ?? "") &&
    request.headers.get("sec-fetch-site") === "same-origin"
  );
}

export function isTrustedLocalRead(request: Request) {
  if (request.headers.get("sec-fetch-site") !== "same-origin") return false;
  try {
    const requestUrl = new URL(request.url);
    const hostname = requestUrl.hostname.replace(/^\[|\]$/g, "");
    if (
      !["http:", "https:"].includes(requestUrl.protocol) ||
      !["127.0.0.1", "localhost", "::1"].includes(hostname)
    ) {
      return false;
    }
    const originHeader = request.headers.get("origin");
    if (originHeader !== null && originHeader !== requestUrl.origin)
      return false;

    const configuredOrigin = process.env.F1_ENGINEER_WEB_ORIGIN;
    if (!configuredOrigin) return true;
    const configured = new URL(configuredOrigin);
    const allowedOrigins = new Set([configured.origin]);
    if (["127.0.0.1", "localhost", "[::1]"].includes(configured.hostname)) {
      for (const host of ["127.0.0.1", "localhost", "[::1]"]) {
        const alias = new URL(configured.origin);
        alias.hostname = host;
        allowedOrigins.add(alias.origin);
      }
    }
    return allowedOrigins.has(requestUrl.origin);
  } catch {
    return false;
  }
}

export async function forwardLocalRequest(
  path: string,
  init: RequestInit = {},
  needsControlToken = false,
) {
  const parsed = assertLoopbackHttpUrl(
    process.env.F1_ENGINEER_API_URL ?? "http://127.0.0.1:8765",
  );
  const url = `${parsed.origin}${path}`;
  assertLoopbackHttpUrl(url);
  const headers = new Headers(init.headers);
  if (needsControlToken) {
    const tokenPath =
      process.env.F1_ENGINEER_CONTROL_TOKEN_FILE ??
      resolve(process.cwd(), "..", "data", ".f1-engineer-control-token");
    const token = (
      await readFile(/*turbopackIgnore: true*/ tokenPath, "utf8")
    ).trim();
    if (token.length < 32)
      throw new Error("Local recording control is unavailable.");
    headers.set("authorization", `Bearer ${token}`);
  }
  return fetch(url, {
    ...init,
    headers,
    cache: "no-store",
    redirect: "error",
  });
}
