import Link from "next/link";
import { Card } from "@/features/ui-foundation/primitives";
import { StatusGlyph } from "@/features/ui-foundation/icons";
import { EmptyState } from "@/features/ui-foundation/states";
import { TrackMap } from "@/features/ui-foundation/TrackMap";
import { TrackPhoto } from "@/features/ui-foundation/TrackPhoto";
import type { DriveContext } from "./load";
import styles from "./DriveDiagnostics.module.css";

/**
 * Session Context card. Three states: a session (live or last), the
 * reference design while the engine is unreachable, and "No session yet" when
 * the engine answers with no usable session.
 */
export function SessionContext({
  context,
  photo,
  live,
}: {
  context: DriveContext;
  photo: string | null;
  /** True while packets are arriving (red "Live game data" pill). */
  live: boolean;
}) {
  if (context.kind === "empty") {
    return (
      <Card title="Session Context" labelledBy="session-context" className={styles.contextCard}>
        <EmptyState
          icon={<TrackMap track="generic" className={styles.emptyMap} strokeWidth={2.4} />}
          title="No session yet"
          body="Start a session in F1 with UDP telemetry sent to this computer on port 20777."
          action={
            <Link href="/settings?section=connections" className={styles.textLink}>
              Connection settings
            </Link>
          }
        />
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
        <TrackMap track={context.track} className={styles.contextMap} strokeWidth={2.4} />
      </div>
      <div className={styles.contextBody}>
        <TrackPhoto src={photo} alt={`${context.title} circuit`} className={styles.contextPhoto} />
        <dl className={styles.facts}>
          {context.facts.map(([label, value]) => (
            <div key={label} className={styles.fact}>
              <dt>{label}:</dt>
              <dd>{value}</dd>
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
        <p className={styles.contextNote}>
          <Link href={`/review/${encodeURIComponent(context.sessionId)}`} className={styles.textLink}>
            Open in Review
          </Link>
        </p>
      )}
    </Card>
  );
}
