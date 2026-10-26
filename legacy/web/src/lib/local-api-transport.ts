const LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "::1"];

// Matches the engineer JSON routes. This bounds header and body completion
// for the JSON helpers only. Upload and download streams do not use it.
export const JSON_API_DEADLINE_MS = 60_000;

export function assertLoopbackHttpUrl(value: string): URL {
  const parsed = new URL(value);
  const hostname = parsed.hostname.replace(/^\[|\]$/g, "");
  if (parsed.protocol !== "http:" || !LOOPBACK_HOSTS.includes(hostname)) {
    throw new Error("The local proxy only accepts a loopback API URL.");
  }
  return parsed;
}
