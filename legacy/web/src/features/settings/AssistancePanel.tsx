"use client";

import type { AssistanceForm } from "@/features/review-data/preferences";
import { InlineAlert } from "@/features/ui-foundation/states";
import { Button, Card, Checkbox, Select, Toggle } from "@/features/ui-foundation/primitives";
import type { AlertAction, AssistanceView, PrefsState } from "./engine-preferences";
import type { SettingsModel } from "./load";
import { Field } from "./shared";
import styles from "./Settings.module.css";
import tabStyles from "./SettingsTabs.module.css";

export type AssistancePanelProps = {
  model: SettingsModel;
  state: PrefsState;
  view: AssistanceView;
  onEdit: (form: AssistanceForm) => void;
  onApply: () => void;
  onDiscard: () => void;
  onAlertAction: (action: AlertAction) => void;
  /** A different browser-local copy of the settings, offered once. */
  migration: AssistanceForm | null;
  onUseMigration: () => void;
  onDismissMigration: () => void;
};

export function AssistancePanel({
  model,
  state,
  view,
  onEdit,
  onApply,
  onDiscard,
  onAlertAction,
  migration,
  onUseMigration,
  onDismissMigration,
}: AssistancePanelProps) {
  const prefs = state.form;
  const disabled = view.controlsDisabled;
  const update = <K extends keyof AssistanceForm>(key: K, value: AssistanceForm[K]) =>
    onEdit({ ...prefs, [key]: value });

  const alert = view.alert;
  return (
    <>
      {migration && (
        <div className={tabStyles.banner}>
          <InlineAlert
            tone="info"
            actions={
              <>
                <Button variant="outline-blue" onClick={onUseMigration}>
                  Use these settings
                </Button>
                <Button variant="muted" onClick={onDismissMigration}>
                  Dismiss
                </Button>
              </>
            }
          >
            Settings from an earlier version are saved in this browser.
          </InlineAlert>
        </div>
      )}
      <div className={styles.grid}>
        <Card title="Live Assistance" labelledBy="live-assistance" className={styles.liveCard}>
          <p className={styles.cardDesc}>Configure how the engineer behaves during driving sessions.</p>
          <fieldset className={styles.fieldset} disabled={disabled}>
            <legend className={styles.label}>Assistance Topics</legend>
            <div className={styles.checks}>
              {model.topics.map(([label]) => (
                <Checkbox
                  key={label}
                  label={label}
                  checked={prefs.topics[label] ?? false}
                  onChange={(checked) => update("topics", { ...prefs.topics, [label]: checked })}
                />
              ))}
            </div>
          </fieldset>
          <Field label="Verbosity" help="Controls the length and detail of responses.">
            <Select
              value={prefs.verbosity}
              onChange={(event) => update("verbosity", event.target.value)}
              aria-label="Verbosity"
              disabled={disabled}
            >
              {model.verbosity.map((option) => (
                <option key={option}>{option}</option>
              ))}
            </Select>
          </Field>
          <Field label="Interruption Behaviour" help="Higher priority events can interrupt responses.">
            <Select
              value={prefs.interruption}
              onChange={(event) => update("interruption", event.target.value)}
              aria-label="Interruption Behaviour"
              disabled={disabled}
            >
              {model.interruption.map((option) => (
                <option key={option}>{option}</option>
              ))}
            </Select>
          </Field>
        </Card>

        <div className={styles.rightCol}>
          <Card title="Review Assistance" labelledBy="review-assistance" className={styles.reviewCard}>
            <p className={styles.cardDesc}>Configure expert analysis for session review.</p>
            <Field label="Analysis Depth" help="Depth of analysis when reviewing sessions.">
              <Select
                value={prefs.depth}
                onChange={(event) => update("depth", event.target.value)}
                aria-label="Analysis Depth"
                disabled={disabled}
              >
                {model.depth.map((option) => (
                  <option key={option}>{option}</option>
                ))}
              </Select>
            </Field>
            <fieldset className={styles.toggleRow} disabled={disabled}>
              <div>
                <div className={styles.label}>Include Interpretations</div>
                <p className={styles.help}>Include AI interpretations alongside measured data.</p>
              </div>
              <Toggle
                label="Include Interpretations"
                checked={prefs.interpretations}
                onChange={(value) => update("interpretations", value)}
              />
            </fieldset>
            <fieldset className={styles.fieldset} disabled={disabled}>
              <legend className={styles.label}>Focus Areas</legend>
              <div className={styles.checks}>
                {model.focusAreas.map(([label]) => (
                  <Checkbox
                    key={label}
                    label={label}
                    checked={prefs.focusAreas[label] ?? false}
                    onChange={(checked) => update("focusAreas", { ...prefs.focusAreas, [label]: checked })}
                  />
                ))}
              </div>
            </fieldset>
          </Card>
          {alert && (
            <InlineAlert
              tone={alert.tone}
              title={alert.title}
              actions={
                alert.actions.length > 0 ? (
                  <>
                    {alert.actions.includes("load-latest") && (
                      <Button variant="muted" onClick={() => onAlertAction("load-latest")}>
                        Load latest
                      </Button>
                    )}
                    {alert.actions.includes("overwrite") && (
                      <Button variant="outline-blue" onClick={() => onAlertAction("overwrite")}>
                        Overwrite
                      </Button>
                    )}
                  </>
                ) : undefined
              }
            >
              {alert.text}
            </InlineAlert>
          )}
          <div className={styles.applyRow}>
            <Button
              variant="primary"
              className={styles.apply}
              onClick={onApply}
              disabled={view.applyDisabled}
              aria-busy={view.busy || undefined}
            >
              {view.applyLabel}
            </Button>
            {view.showDiscard && (
              <Button variant="muted" onClick={onDiscard}>
                Discard
              </Button>
            )}
            <p className={styles.applyNote} role="status" data-tone={view.noteTone ?? undefined}>
              {view.note}
            </p>
          </div>
        </div>
      </div>
    </>
  );
}
