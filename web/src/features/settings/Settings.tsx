"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { formToDoc, type AssistanceForm } from "@/features/review-data/preferences";
import { ConfirmDialog } from "@/features/ui-foundation/Drawer";
import { Tabs } from "@/features/ui-foundation/primitives";
import { useToast } from "@/features/ui-foundation/Toast";
import { AdvancedPanel } from "./AdvancedPanel";
import { AssistancePanel } from "./AssistancePanel";
import { ConnectionsPanel } from "./ConnectionsPanel";
import { DataPanel } from "./DataPanel";
import {
  assistanceView,
  initPrefsState,
  isDirty,
  planMigration,
  prefsReducer,
  type AlertAction,
} from "./engine-preferences";
import type { SettingsModel } from "./load";
import { PREFERENCES_KEY, parseLegacyPreferences } from "./preferences";
import { getPreferences, putPreferences } from "./preferences-client";
import { SECTIONS, TAB_ID_PREFIX, panelId, searchWithSection, type Section } from "./sections";
import { StatusTiles } from "./StatusTiles";
import { VoicePanel } from "./VoicePanel";
import { radioMicrophoneTile, radioVoiceTile } from "./radio-settings";
import { useRadioPresence } from "./useRadioPresence";
import styles from "./Settings.module.css";
import tabStyles from "./SettingsTabs.module.css";

function removeLegacy() {
  try {
    window.localStorage.removeItem(PREFERENCES_KEY);
  } catch {
    // Storage may be blocked; the banner simply stays dismissed for this visit.
  }
}

export function Settings({ model, initialSection }: { model: SettingsModel; initialSection: Section }) {
  const [section, setSection] = useState<Section>(initialSection);
  const [seenInitial, setSeenInitial] = useState<Section>(initialSection);
  // A link to /settings?section=… while this screen is open changes the prop.
  if (seenInitial !== initialSection) {
    setSeenInitial(initialSection);
    setSection(initialSection);
  }
  const sectionRef = useRef(section);
  useEffect(() => {
    sectionRef.current = section;
  }, [section]);

  const toast = useToast();
  const [prefs, dispatch] = useReducer(prefsReducer, model.preferences, initPrefsState);
  const view = assistanceView(prefs);
  const dirty = isDirty(prefs);

  const [migration, setMigration] = useState<AssistanceForm | null>(null);
  const adoptedMigration = useRef(false);
  const [confirmOverwrite, setConfirmOverwrite] = useState(false);

  const presence = useRadioPresence();

  const changeSection = useCallback((next: Section) => {
    setSection(next);
    // Keep the address in step with the tab without asking the server to render again.
    window.history.replaceState(null, "", `${window.location.pathname}${searchWithSection(window.location.search, next)}`);
  }, []);

  // One-time migration of the retired browser-local copy. Restored after
  // hydration so the server render and the first client render stay identical.
  useEffect(() => {
    let raw: string | null = null;
    try {
      raw = window.localStorage.getItem(PREFERENCES_KEY);
    } catch {
      return;
    }
    const plan = planMigration(parseLegacyPreferences(model, raw), prefs.saved, prefs.access);
    if (plan.state === "identical") removeLegacy();
    // eslint-disable-next-line react-hooks/set-state-in-effect
    else if (plan.state === "offer") setMigration(plan.form);
    // Runs once for the preferences the server rendered.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // "Changes Applied" shows for two seconds.
  useEffect(() => {
    if (prefs.phase !== "saved") return;
    const timer = window.setTimeout(() => dispatch({ type: "settle" }), 2000);
    return () => window.clearTimeout(timer);
  }, [prefs.phase]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function save(overwriteRevision: number | null) {
    dispatch({ type: "saving" });
    const doc = formToDoc(prefs.form, prefs.savedDoc);
    const result = await putPreferences(doc, overwriteRevision ?? prefs.revision);
    dispatch({ type: "result", result });
    if (result.ok && adoptedMigration.current) {
      adoptedMigration.current = false;
      removeLegacy();
    }
    // Inline status is the primary channel; a toast only reaches a user who left the tab.
    if (sectionRef.current !== "assistance") {
      if (result.ok) {
        toast.push({ tone: "success", title: "Settings saved", body: "The next engineer call uses these settings." });
      } else if (result.kind !== "conflict") {
        toast.push({ tone: "error", title: "Settings were not saved", body: "Open the Assistance tab for details." });
      } else {
        toast.push({ tone: "warning", title: "Settings changed elsewhere", body: "Open the Assistance tab to resolve it." });
      }
    }
  }

  function apply() {
    // Applying over a known conflict is an explicit overwrite.
    if (prefs.failure?.kind === "conflict") setConfirmOverwrite(true);
    else void save(null);
  }

  async function onAlertAction(action: AlertAction) {
    if (action === "overwrite") {
      setConfirmOverwrite(true);
      return;
    }
    const latest = await getPreferences();
    if (latest.state === "ok") dispatch({ type: "adopt", stored: latest.stored });
    else toast.push({ tone: "error", title: "Latest settings could not be loaded", body: "Reload the page and try again." });
  }

  const overwriteRevision = prefs.failure?.kind === "conflict" ? prefs.failure.currentRevision : null;

  const microphone = radioMicrophoneTile(presence);
  const output = radioVoiceTile(presence);

  return (
    <>
      <Tabs
        items={SECTIONS}
        value={section}
        onChange={changeSection}
        label="Settings sections"
        className={styles.tabs}
        idPrefix={TAB_ID_PREFIX}
        controls={panelId}
      />

      {SECTIONS.map(({ key }) => (
        <div
          key={key}
          id={panelId(key)}
          role="tabpanel"
          aria-labelledby={`${TAB_ID_PREFIX}-${key}`}
          className={tabStyles.panel}
          tabIndex={0}
          hidden={key !== section}
        >
          {key === section && key === "assistance" && (
            <AssistancePanel
              model={model}
              state={prefs}
              view={view}
              onEdit={(form) => dispatch({ type: "edit", form })}
              onApply={apply}
              onDiscard={() => dispatch({ type: "discard" })}
              onAlertAction={(action) => void onAlertAction(action)}
              migration={migration}
              onUseMigration={() => {
                if (migration) dispatch({ type: "edit", form: migration });
                adoptedMigration.current = true;
                setMigration(null);
              }}
              onDismissMigration={() => {
                removeLegacy();
                setMigration(null);
              }}
            />
          )}
          {key === section && key === "voice" && (
            <VoicePanel />
          )}
          {key === section && key === "connections" && <ConnectionsPanel model={model} />}
          {key === section && key === "data" && <DataPanel storage={model.storage} />}
          {key === section && key === "advanced" && <AdvancedPanel />}
        </div>
      ))}

      <StatusTiles model={model} microphone={microphone} audio={output} showRuntimeChip={section !== "connections"} />

      <ConfirmDialog
        open={confirmOverwrite}
        title="Replace the saved settings with yours?"
        body="Another window saved different settings. Your choices will replace them."
        confirmLabel="Replace"
        onCancel={() => setConfirmOverwrite(false)}
        onConfirm={() => {
          setConfirmOverwrite(false);
          void save(overwriteRevision);
        }}
      />
    </>
  );
}
