import type { ReactNode } from "react";
import { Bone } from "@/features/route-states/Skeleton";
import { CarIcon, FlagIcon, PitIcon, StatusGlyph } from "@/features/ui-foundation/icons";
import { compoundColor, stintBarLabel, stintSegments, stintTicks, type PitMarker } from "./overview-data";
import type { EventIcon, OverviewStint } from "./types";
import styles from "./SessionOverview.module.css";
import viewStyles from "./OverviewViews.module.css";

export function Unavailable({ children }: { children: ReactNode }) {
  return <p className={styles.unavailable}>{children}</p>;
}

/** Placeholder lines inside a real card frame while a deferred section loads. */
export function BoneLines({ rows = 3 }: { rows?: number }) {
  return (
    <div className={viewStyles.boneStack} aria-hidden="true">
      {Array.from({ length: rows }, (_, index) => (
        <Bone key={index} width={index % 2 ? "62%" : "88%"} height={12} />
      ))}
    </div>
  );
}

export function EventGlyph({ icon }: { icon: EventIcon }) {
  switch (icon) {
    case "ring-green":
      return <StatusGlyph tone="green" glyph="ring" size={14} />;
    case "ring-amber":
      return <StatusGlyph tone="amber" glyph="ring" size={14} />;
    case "alert-red":
      return <StatusGlyph tone="red" glyph="alert" size={14} />;
    case "alert-amber":
      return <StatusGlyph tone="amber" glyph="alert" size={14} />;
    case "flag":
      return (
        <span className={viewStyles.eventIcon}>
          <FlagIcon size={14} />
        </span>
      );
    case "pit":
      return (
        <span className={viewStyles.eventIcon}>
          <PitIcon size={14} />
        </span>
      );
    case "car":
      return (
        <span className={viewStyles.eventIcon}>
          <CarIcon size={14} />
        </span>
      );
    default:
      return <StatusGlyph tone="green" glyph="check" size={14} />;
  }
}

/** Legend, grid, stint bar and lap axis. Stints meet at each pit stop, marked by a dotted line. */
export function StintBar({
  stints,
  total,
  pits,
}: {
  stints: OverviewStint[];
  total: number;
  pits?: PitMarker[];
}) {
  const ticks = stintTicks(total);
  const span = Math.max(total - 1, 1);
  const position = (lap: number) => (total > 1 ? ((lap - 1) / span) * 100 : 0);
  const segments = stintSegments(stints, total, pits ?? []);
  const boundaries: { at: number; after: number }[] = [];
  let edge = 1;
  let previous: OverviewStint | null = null;
  for (const segment of segments) {
    if (segment.kind === "stint" && previous) boundaries.push({ at: edge, after: previous.endLap });
    if (segment.kind === "stint") previous = segment.stint;
    edge += segment.laps;
  }
  return (
    <>
      <div className={styles.stintLegend}>
        {stints.map((stint) => (
          <span key={stint.index} className={styles.key}>
            <i style={{ background: compoundColor(stint.compound), borderRadius: "50%" }} />
            {stint.label} ({stint.lapCount})
          </span>
        ))}
      </div>
      <div className={styles.stintTrack}>
        <div className={styles.stintGrid} aria-hidden="true">
          {ticks.map((tick) => (
            <span key={tick} style={{ left: `${position(tick)}%` }} />
          ))}
        </div>
        <div className={styles.stintBar} role="img" aria-label={stintBarLabel(stints, pits ?? [])}>
          {segments.map((segment) =>
            segment.kind === "stint" ? (
              <span
                key={`stint-${segment.stint.index}`}
                style={{ flexGrow: segment.laps || 0.0001, background: compoundColor(segment.stint.compound) }}
              />
            ) : (
              <span
                key={`gap-${segment.from}`}
                data-gap="true"
                style={{ flexGrow: segment.laps || 0.0001, background: "var(--grey-soft)" }}
              />
            ),
          )}
        </div>
        {boundaries.map((boundary) => (
          <span
            key={boundary.after}
            className={styles.stintPit}
            style={{ left: `${position(boundary.at)}%` }}
            role="img"
            aria-label={`Pit stop after lap ${boundary.after}`}
            title={`Pit stop after lap ${boundary.after}`}
          />
        ))}
        <div className={styles.stintAxis} aria-hidden="true">
          {ticks.map((tick) => (
            <span key={tick} style={{ left: `${position(tick)}%` }}>
              {tick}
            </span>
          ))}
        </div>
      </div>
    </>
  );
}
