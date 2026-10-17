import Link from "next/link";
import { Card } from "@/features/ui-foundation/primitives";
import { StatusGlyph } from "@/features/ui-foundation/icons";
import { EmptyState } from "@/features/ui-foundation/states";
import { MeasuredTrackMap } from "@/features/ui-foundation/MeasuredTrackMap";
import { TrackMap } from "@/features/ui-foundation/TrackMap";
import { TrackPhoto } from "@/features/ui-foundation/TrackPhoto";
import { UnitText } from "@/features/ui-foundation/units";
import { StatTile } from "@/features/ui-foundation/StatTile";
import { ConnectChecklist } from "./ConnectChecklist";
import type { ChecklistStep } from "./connect-steps";
import type { DriveContext, SessionStats } from "./load";
import styles from "./DriveDiagnostics.module.css";

/**
 * Session Context card. Three states: a session (live or last), the
 * reference design while the engine is unreachable, and "No session yet" when
 * the engine answers with no usable session. While no game data arrives, a
 * checklist shows what is left to connect, and a past session shows its
 * headline numbers.
 */
export function SessionContext({
  context,
  photo,
  live,
  checklist = null,
}: {
  context: DriveContext;
  photo: string | null;
  /** True while packets are arriving (red "Live game data" pill). */
  live: boolean;
  /** Steps to get the game connected; null hides the checklist. */
  checklist?: ChecklistStep[] | null;
}) {
  if (context.kind === "empty") {
    return (
      <Card title="Session Context" labelledBy="session-context" className={styles.contextCard}>
        <EmptyState
          icon={<TrackMap track="generic" className={styles.emptyMap} strokeWidth={2.4} />}
          title="No session yet"
          body="Your sessions appear here as soon as the game sends telemetry."
          action={
            <Link href="/settings?section=connections" className={styles.textLink}>
              Connection settings
            </Link>
          }
        />
        {checklist && <ConnectChecklist steps={checklist} />}
      </Card>
    );
  }

  return (
    <Card title="Session Context" labelledBy="session-context" className={styles.contextCard}>
      <div className={styles.contextHead}>
        <div>
          <div className={styles.contextTitle}>{context.title}</div>
          <div className={styles.contextSub}>
            {context.subtitle.map((part, index) => (
              <span key={part}>
                {index > 0 && <span className={styles.dot}>•</span>}
                {part}
              </span>
            ))}
          </div>
        </div>
        <MeasuredTrackMap
          sessionId={context.kind === "session" ? context.sessionId : null}
          track={context.track}
          className={styles.contextMap}
          strokeWidth={2.4}
        />
      </div>
      <div className={styles.contextBody}>
        <TrackPhoto src={photo} alt={`${context.title} circuit`} className={styles.contextPhoto} />
        <dl className={styles.facts}>
          {context.facts.map(([label, value]) => (
            <div key={label} className={styles.fact}>
              <dt>{label}:</dt>
              <dd>
                <UnitText>{value}</UnitText>
              </dd>
            </div>
          ))}
        </dl>
      </div>
      <div className={styles.liveWrap}>
        <span className={styles.livePill} data-live={live}>
          <StatusGlyph tone={live ? "red" : "grey"} glyph="dot" size={14} />
          {live ? "Live game data" : "No live data"}
        </span>
      </div>
      {context.kind === "session" && !live && (
        <>
          {context.stats && <LastSessionStats stats={context.stats} />}
          <p className={styles.contextNote}>
            <Link href={`/review/${encodeURIComponent(context.sessionId)}`} className={styles.textLink}>
              Open in Review
            </Link>
            {context.stats?.analysisHref && (
              <>
                <span className={styles.dot} aria-hidden="true">
                  •
                </span>
                <Link href={context.stats.analysisHref} className={styles.textLink}>
                  Analyse best lap
                </Link>
              </>
            )}
          </p>
        </>
      )}
      {checklist && <ConnectChecklist steps={checklist} />}
    </Card>
  );
}

function LastSessionStats({ stats }: { stats: SessionStats }) {
  return (
    <div className={styles.lastStats} aria-label="Last session summary" role="group">
      <StatTile label="Laps" value={stats.laps} sub={stats.invalid ? `${stats.invalid} invalid` : undefined} />
      <StatTile
        label="Best lap"
        value={stats.best?.time ?? "—"}
        sub={stats.best ? `Lap ${stats.best.lap}` : "No timed laps"}
      />
      <StatTile label="Time driven" value={stats.timeDriven ?? "—"} />
    </div>
  );
}
