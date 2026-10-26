import { InfoIcon, StatusGlyph } from "@/features/ui-foundation/icons";
import type { RadioRow } from "./radio";
import styles from "./DriveDiagnostics.module.css";

function glyphTone(row: RadioRow) {
  return row.kind === "issue" ? "red" : row.kind === "question" ? "blue" : "green";
}

/**
 * Event Details in reference order: header, Message, Trigger, Evidence /
 * Context, Output, Timings, Evidence Limitations. Sections without content
 * are omitted, never shown as empty headings.
 */
export function EventDetails({ row }: { row: RadioRow }) {
  const sections: Array<[string, string | string[]]> = [
    ["Message", row.text],
    ["Trigger", row.trigger],
    ["Evidence / Context", row.evidence],
    ["Output", row.output],
    ["Timings", row.timings],
  ];
  const visible = sections.filter(([, body]) => (Array.isArray(body) ? body.length > 0 : body.trim() !== ""));
  return (
    <div aria-live="polite">
      <div className={styles.detailHead}>
        <StatusGlyph tone={glyphTone(row)} glyph="check" size={14} />
        <div>
          <div className={styles.detailTitle}>{row.title}</div>
          <div className={styles.detailTime}>{row.timestamp}</div>
        </div>
        <span className={styles.outcomeBadge} data-tone={row.outcomeTone}>
          {row.outcome}
        </span>
      </div>
      {visible.map(([label, body]) => (
        <div key={label} className={styles.detailSection}>
          <div className={styles.detailLabel}>{label}</div>
          {Array.isArray(body) ? (
            <ul className={styles.bullets}>
              {body.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : (
            <p className={styles.detailBody}>{body}</p>
          )}
        </div>
      ))}
      {row.limitations && (
        <div className={styles.detailSection}>
          <div className={styles.detailLabel}>
            Evidence Limitations <InfoIcon size={13} />
          </div>
          <p className={styles.detailBody}>{row.limitations}</p>
        </div>
      )}
    </div>
  );
}
