import type { TracePath } from "./lap-telemetry";

// Geometry for the measured track line: world x/z positions fitted into the
// map's viewBox and cut into region segments by lap distance. Pure.

export const MAP_WIDTH = 120;
export const MAP_HEIGHT = 80;
export const MAP_PADDING = 7;

export type ProjectedPoint = [distance: number, x: number, y: number];

/** Fits the positions into the viewBox, keeping the aspect ratio and centring the line. */
export function projectPath(path: TracePath, width = MAP_WIDTH, height = MAP_HEIGHT, padding = MAP_PADDING): ProjectedPoint[] {
  if (path.length === 0) return [];
  let minX = Infinity;
  let maxX = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;
  for (const [, x, z] of path) {
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minZ = Math.min(minZ, z);
    maxZ = Math.max(maxZ, z);
  }
  const spanX = Math.max(maxX - minX, 1e-6);
  const spanZ = Math.max(maxZ - minZ, 1e-6);
  const scale = Math.min((width - 2 * padding) / spanX, (height - 2 * padding) / spanZ);
  const offsetX = (width - spanX * scale) / 2;
  const offsetY = (height - spanZ * scale) / 2;
  return path.map(([d, x, z]) => [d, offsetX + (x - minX) * scale, offsetY + (z - minZ) * scale]);
}

/** Points whose lap distance lies in [from, to]; fewer than two means no segment. */
export function pathSegment(points: readonly ProjectedPoint[], from: number, to: number): ProjectedPoint[] {
  return points.filter(([d]) => d >= from && d <= to);
}

export function pointsAttribute(points: readonly ProjectedPoint[]): string {
  return points.map(([, x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
}

/**
 * Dash settings that draw the stretch [from, to] of a schematic outline whose
 * path length is normalised to 1 (`pathLength="1"`).
 */
export function outlineDash(from: number, to: number, lapLength: number): { dasharray: string; dashoffset: number } | null {
  if (!(lapLength > 0) || !(to > from)) return null;
  const start = Math.min(1, Math.max(0, from / lapLength));
  const length = Math.min(1 - start, Math.max(0, (to - from) / lapLength));
  if (length <= 0) return null;
  return { dasharray: `${length.toFixed(5)} 2`, dashoffset: -start };
}
