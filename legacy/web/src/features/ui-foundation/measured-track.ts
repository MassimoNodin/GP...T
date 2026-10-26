// Pure helpers for the measured track map: parse, thin and draw the car
// positions of one lap (world x/z in metres) as a single SVG path.

import { projectPath } from "@/features/lap-analysis/track-geometry";
import type { TracePath } from "@/features/lap-analysis/lap-telemetry";

export const MEASURED_MAP_POINTS = 220;
const VIEW_WIDTH = 120;
const VIEW_HEIGHT = 80;
const VIEW_PADDING = 8;

/** Keeps about `target` points spread evenly along the lap, always ending on the last one. */
export function downsamplePath(path: TracePath, target = MEASURED_MAP_POINTS): TracePath {
  if (path.length <= target) return path.map(([d, x, z]) => [Math.round(d), Math.round(x * 10) / 10, Math.round(z * 10) / 10]);
  const step = (path.length - 1) / (target - 1);
  const kept: TracePath = [];
  for (let i = 0; i < target; i += 1) {
    const [d, x, z] = path[Math.round(i * step)];
    kept.push([Math.round(d), Math.round(x * 10) / 10, Math.round(z * 10) / 10]);
  }
  return kept;
}

/** Validates the route's `{ path }` body: finite [distance, x, z] triples, enough to draw a line. */
export function parseTrackLine(body: unknown): TracePath | null {
  const data = (body as { data?: { path?: unknown } } | null)?.data;
  const path = data?.path;
  if (!Array.isArray(path) || path.length < 20) return null;
  const out: TracePath = [];
  for (const item of path) {
    if (!Array.isArray(item) || item.length < 3 || !item.slice(0, 3).every((v) => typeof v === "number" && Number.isFinite(v))) return null;
    out.push([item[0], item[1], item[2]]);
  }
  return out;
}

/** SVG path data (120x80 viewBox, aspect ratio kept) for the measured line. */
export function measuredPathData(path: TracePath): string {
  const points = projectPath(path, VIEW_WIDTH, VIEW_HEIGHT, VIEW_PADDING);
  return points.map(([, x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`).join("");
}
