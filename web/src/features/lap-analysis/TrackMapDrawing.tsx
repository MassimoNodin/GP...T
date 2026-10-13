"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { trackOutlinePath } from "@/features/ui-foundation/TrackMap";
import type { Heat, RegionChoice } from "./regions";
import type { TracePath } from "./lap-telemetry";
import { MAP_HEIGHT, MAP_WIDTH, outlineDash, pathSegment, pointAt, pointsAttribute, projectPath } from "./track-geometry";
import styles from "./Views.module.css";

const HEAT_COLOR: Record<Heat | "neutral", string> = {
  gain: "var(--gain)",
  loss: "var(--loss)",
  even: "var(--muted)",
  none: "var(--grey-soft)",
  neutral: "var(--muted)",
};

export type MapRegion = { choice: RegionChoice; heat: Heat | "neutral"; label: string };

/**
 * Circuit drawing with the track-model regions on it. Draws the measured line
 * when the laps carried positions, else the schematic outline (approximate).
 * Segments mirror the accessible region list and are hidden from assistive tech.
 */
export function TrackMapDrawing({
  trackKey,
  path,
  lapLengthM,
  regions,
  selectedId,
  onSelect,
  title,
  className,
  strokeWidth = 5,
  focusId = null,
  cursor = null,
}: {
  trackKey: string;
  path: TracePath | null;
  lapLengthM: number | null;
  regions: MapRegion[];
  selectedId: string | null;
  onSelect?: (id: string) => void;
  title: string;
  className?: string;
  strokeWidth?: number;
  /** Zoom the measured line to this region (no effect on the schematic outline). */
  focusId?: string | null;
  /** Lap distance of the chart cursor, drawn as a dot on the line. */
  cursor?: number | null;
}) {
  const projected = useMemo(() => (path ? projectPath(path) : null), [path]);
  const outline = trackOutlinePath(trackKey);
  const length = lapLengthM ?? (path ? path[path.length - 1][0] : null);
  const drawn = regions.filter((region) => !region.choice.complex);
  const selected = regions.find((region) => region.choice.id === selectedId) ?? null;
  const interactive = !!onSelect;
  const outlineRef = useRef<SVGPathElement>(null);
  const [outlinePoint, setOutlinePoint] = useState<[number, number] | null>(null);
  const measuredPoint = projected && cursor !== null ? pointAt(projected, cursor) : null;
  // The schematic outline has no distances, so place the dot by its share of the lap.
  useEffect(() => {
    const element = outlineRef.current;
    if (projected || !element || cursor === null || !length) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setOutlinePoint(null);
      return;
    }
    const share = Math.min(1, Math.max(0, cursor / length));
    const point = element.getPointAtLength(element.getTotalLength() * share);
    setOutlinePoint([point.x, point.y]);
  }, [cursor, length, projected]);
  const cursorPoint = measuredPoint ?? outlinePoint;
  const focus = projected && focusId ? regions.find((region) => region.choice.id === focusId) : null;
  const box = (() => {
    if (!projected || !focus) return null;
    const points = pathSegment(projected, focus.choice.range.from, focus.choice.range.to);
    if (points.length < 2) return null;
    const xs = points.map((p) => p[1]);
    const ys = points.map((p) => p[2]);
    const pad = 8;
    const x0 = Math.min(...xs) - pad;
    const y0 = Math.min(...ys) - pad;
    const w = Math.max(Math.max(...xs) - Math.min(...xs) + 2 * pad, MAP_WIDTH / 4);
    const h = Math.max(Math.max(...ys) - Math.min(...ys) + 2 * pad, MAP_HEIGHT / 4);
    const scale = Math.max(w / MAP_WIDTH, h / MAP_HEIGHT);
    return `${x0} ${y0} ${MAP_WIDTH * scale} ${MAP_HEIGHT * scale}`;
  })();

  return (
    <svg
      className={[styles.mapSvg, className].filter(Boolean).join(" ")}
      viewBox={box ?? `0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
      fill="none"
      strokeLinejoin="round"
      strokeLinecap="round"
      role="img"
      aria-label={title}
      focusable="false"
    >
      {projected ? (
        <>
          <polyline points={pointsAttribute(projected)} stroke="currentColor" strokeWidth="1.2" vectorEffect="non-scaling-stroke" opacity="0.55" />
          <circle cx={projected[0][1]} cy={projected[0][2]} r="1.6" fill="var(--text)" stroke="var(--surface)" strokeWidth="0.6" />
        </>
      ) : (
        <path ref={outlineRef} d={outline} stroke="currentColor" strokeWidth="1.2" vectorEffect="non-scaling-stroke" opacity="0.55" />
      )}
      {length &&
        [...drawn, ...(selected && selected.choice.complex ? [selected] : [])].map((region) => {
          const { from, to } = region.choice.range;
          const isSelected = region.choice.id === selectedId;
          const color = HEAT_COLOR[region.heat];
          const common = {
            stroke: color,
            strokeWidth: isSelected ? strokeWidth + 2 : strokeWidth,
            vectorEffect: "non-scaling-stroke" as const,
            "aria-hidden": true,
            className: interactive ? styles.mapSegment : undefined,
            onClick: interactive ? () => onSelect?.(region.choice.id) : undefined,
          };
          const halo = isSelected ? { stroke: "var(--surface)", strokeWidth: strokeWidth + 5, vectorEffect: "non-scaling-stroke" as const } : null;
          if (projected) {
            const segment = pathSegment(projected, from, to);
            if (segment.length < 2) return null;
            return (
              <g key={region.choice.id}>
                {halo && <polyline points={pointsAttribute(segment)} {...halo} />}
                <polyline points={pointsAttribute(segment)} {...common}>
                  <title>{region.label}</title>
                </polyline>
              </g>
            );
          }
          const dash = outlineDash(from, to, length);
          if (!dash) return null;
          return (
            <g key={region.choice.id}>
              {halo && <path d={outline} pathLength={1} strokeDasharray={dash.dasharray} strokeDashoffset={dash.dashoffset} {...halo} />}
              <path d={outline} pathLength={1} strokeDasharray={dash.dasharray} strokeDashoffset={dash.dashoffset} {...common}>
                <title>{region.label}</title>
              </path>
            </g>
          );
        })}
      {cursorPoint && (
        <circle
          className={styles.mapCursor}
          cx={cursorPoint[0]}
          cy={cursorPoint[1]}
          r={box ? 1.4 : 2.2}
          aria-hidden="true"
        />
      )}
    </svg>
  );
}
