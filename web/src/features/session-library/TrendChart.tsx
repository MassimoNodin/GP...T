"use client";

import { chartHeight, useElementWidth } from "@/features/ui-foundation/chart-frame";
import styles from "./PersonalBests.module.css";

const FALLBACK_WIDTH = 260;
const PAD = 10;

export type TrendDot = { key: string; title: string; x: number; y: number; best: boolean };

/** Personal best trend drawn in real pixels; x and y are 0..1 (y = 0 is the fastest). */
export function TrendChart({ label, line, dots }: { label: string; line: Array<[number, number]>; dots: TrendDot[] }) {
  const [frame, frameWidth] = useElementWidth<HTMLDivElement>(FALLBACK_WIDTH);
  const W = Math.max(frameWidth, 120);
  const H = chartHeight(W, 70, 110);
  const toXY = ([x, y]: [number, number]) => [PAD + x * (W - 2 * PAD), PAD + y * (H - 2 * PAD)] as const;
  return (
    <div ref={frame} className={styles.trendFrame}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className={styles.trendChart} role="img" aria-label={label}>
        <polyline points={line.map((point) => toXY(point).join(",")).join(" ")} className={styles.trendLine} />
        {dots.map((dot) => {
          const [x, y] = toXY([dot.x, dot.y]);
          return (
            <circle key={dot.key} cx={x} cy={y} r={3} className={styles.trendPoint} data-best={dot.best || undefined}>
              <title>{dot.title}</title>
            </circle>
          );
        })}
      </svg>
    </div>
  );
}
