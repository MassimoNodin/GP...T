import { existsSync } from "node:fs";
import path from "node:path";

const extensions = ["jpg", "jpeg", "png", "webp"] as const;
const cache = new Map<string, string | null>();

/** Resolve /tracks/<key>.<ext> from web/public when the user has supplied one. */
export function trackPhotoUrl(trackKey: string): string | null {
  if (!/^[a-z0-9-]{1,40}$/.test(trackKey)) return null;
  if (process.env.NODE_ENV === "production" && cache.has(trackKey))
    return cache.get(trackKey) ?? null;
  let found: string | null = null;
  for (const extension of extensions) {
    const file = path.join(process.cwd(), "public", "tracks", `${trackKey}.${extension}`);
    if (existsSync(file)) {
      found = `/tracks/${trackKey}.${extension}`;
      break;
    }
  }
  cache.set(trackKey, found);
  return found;
}
