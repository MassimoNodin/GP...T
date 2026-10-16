import type { StatusRow } from "@/features/live-status/map";
import { StatusGlyph, WaveformIcon } from "@/features/ui-foundation/icons";
import { Card } from "@/features/ui-foundation/primitives";
import { BROWSER_FOOTNOTE, type Activity, type ActivityWave } from "./system-rows";
import styles from "./DriveDiagnostics.module.css";

type Tone = StatusRow["tone"] | Activity["tone"];

function glyphFor(tone: Tone) {
  return tone === "green" ? "check" : tone === "grey" || tone === "blue" ? "dot" : "alert";
}

/** Three pulsing dots (SVG); the pulse runs only under no-preference motion. */
function TypingIndicator() {
  return (
    <svg className={styles.typing} width="22" height="8" viewBox="0 0 22 8" aria-hidden="true" focusable="false">
      {[3, 11, 19].map((cx, index) => (
        <circle key={cx} cx={cx} cy="4" r="2.4" fill="var(--blue)" style={{ animationDelay: `${index * 140}ms` }} />
      ))}
    </svg>
  );
}

function Wave({ wave }: { wave: ActivityWave }) {
  switch (wave.kind) {
    case "animated":
      return <WaveformIcon size={40} mode="animated" tone={wave.tone} />;
    case "static":
      return <WaveformIcon size={40} mode="static" tone={wave.tone} dim={wave.dim} />;
    case "flat":
      return <WaveformIcon size={40} mode="flat" tone={wave.tone} />;
    case "typing":
      return <TypingIndicator />;
    default:
      return null;
  }
}

export function SystemStatus({ rows, activity }: { rows: StatusRow[]; activity: Activity }) {
  return (
    <Card title="System Status" labelledBy="system-status" className={styles.systemCard}>
      <ul className={styles.systemList}>
        {rows.map((row) => (
          <li key={row.label} className={styles.systemRow}>
            <StatusGlyph tone={row.tone} glyph={glyphFor(row.tone)} size={14} />
            <span className={styles.systemLabel}>{row.label}</span>
            <span className={styles.systemStatus} data-tone={row.tone}>
              {row.status}
            </span>
            <span className={styles.systemDetail}>{row.detail}</span>
          </li>
        ))}
        <li className={styles.systemRow}>
          <StatusGlyph tone={activity.tone} glyph={glyphFor(activity.tone)} size={14} />
          <span className={styles.systemLabel}>Current Activity</span>
          <span className={styles.activity}>
            <span>{activity.text}</span>
            <Wave wave={activity.wave} />
          </span>
        </li>
      </ul>
      <p className={styles.systemFootnote}>{BROWSER_FOOTNOTE}</p>
    </Card>
  );
}
