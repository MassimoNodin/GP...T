import type { ReactElement } from "react";

/**
 * Shared grid for charts whose x axis is lap number: a light line at every
 * lap, a stronger line and a label at major laps. Laps sit in the middle of
 * their cell (the axis runs half a lap beyond the first and last lap), so dots
 * never touch the plot edge and each one sits on its own line.
 */

/** Gridline colours: minor lines are a soft border tone, major lines one step stronger. */
export const GRID_MINOR = "var(--border)";
export const GRID_MAJOR = "var(--border-strong)";

/** Minimum horizontal room for a label so neighbouring numbers never touch. */
const LABEL_ROOM = 26;
/** Below this spacing, per-lap lines turn into a haze, so only labelled laps get a line. */
const MIN_LINE_ROOM = 4;

export type LapScale = {
  /** Axis start and end in lap units (half a lap beyond the first and last lap). */
  lo: number;
  hi: number;
  /** Pixel position of a lap, clamped to the plot. */
  x: (lap: number) => number;
  /** Pixels per lap. */
  perLap: number;
};

export function lapScale(firstLap: number, lastLap: number, left: number, width: number): LapScale {
  const lo = firstLap - 0.5;
  const hi = Math.max(lastLap, firstLap) + 0.5;
  const perLap = width / (hi - lo);
  const x = (lap: number) => left + (Math.min(Math.max(lap, lo), hi) - lo) * perLap;
  return { lo, hi, x, perLap };
}

export type LapGridLine = { lap: number; major: boolean; label: boolean };

/** Lines for every lap in the range; labels thin out (1, 2, 5, 10...) as the plot narrows. */
export function lapGridLines(firstLap: number, lastLap: number, perLap: number): LapGridLine[] {
  const labelStep = [1, 2, 5, 10, 20, 50, 100].find((step) => step * perLap >= LABEL_ROOM) ?? 100;
  const majorStep = labelStep === 1 ? 5 : labelStep === 2 ? 10 : labelStep * 2;
  const lines: LapGridLine[] = [];
  for (let lap = firstLap; lap <= lastLap; lap += 1) {
    const label = lap % labelStep === 0 || (labelStep === 1 && lap === firstLap);
    const major = lap % majorStep === 0;
    if (label || perLap >= MIN_LINE_ROOM) lines.push({ lap, major, label });
  }
  return lines;
}

/** Vertical lap gridlines and their labels; draw it beneath tints, pit lines and data. */
export function LapGrid({
  scale,
  firstLap,
  lastLap,
  top,
  bottom,
  labelY,
  labelClassName,
}: {
  scale: LapScale;
  firstLap: number;
  lastLap: number;
  top: number;
  bottom: number;
  labelY: number;
  labelClassName: string;
}): ReactElement {
  return (
    <g aria-hidden="true" pointerEvents="none" data-lap-grid="true">
      {lapGridLines(firstLap, lastLap, scale.perLap).map((line) => (
        <g key={line.lap}>
          <line
            x1={scale.x(line.lap)}
            x2={scale.x(line.lap)}
            y1={top}
            y2={bottom}
            stroke={line.major ? GRID_MAJOR : GRID_MINOR}
          />
          {line.label && (
            <text x={scale.x(line.lap)} y={labelY} textAnchor="middle" className={labelClassName}>
              {line.lap}
            </text>
          )}
        </g>
      ))}
    </g>
  );
}
