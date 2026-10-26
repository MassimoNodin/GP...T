import { StatusGlyph } from "@/features/ui-foundation/icons";
import type { ChecklistStep } from "./connect-steps";
import styles from "./DriveDiagnostics.module.css";

export function ConnectChecklist({ steps }: { steps: ChecklistStep[] }) {
  const remaining = steps.filter((step) => !step.done).length;
  return (
    <section className={styles.checklist} aria-labelledby="connect-checklist">
      <h3 id="connect-checklist" className={styles.checklistTitle}>
        {remaining === 0 ? "Connected" : "Connect the game"}
      </h3>
      <ol className={styles.checklistSteps}>
        {steps.map((step) => (
          <li key={step.id} data-done={step.done || undefined}>
            <StatusGlyph tone={step.done ? "green" : "grey"} glyph={step.done ? "check" : "ring"} size={14} />
            <span>
              <span className={styles.checklistText}>
                {step.text}
                <span className="sr-only">{step.done ? " (done)" : " (to do)"}</span>
              </span>
              {step.hint && !step.done && <span className={styles.checklistHint}>{step.hint}</span>}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
