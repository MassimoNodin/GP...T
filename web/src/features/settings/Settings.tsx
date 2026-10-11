"use client";

import { useEffect, useState } from "react";
import {
  GamepadIcon,
  MicrophoneIcon,
  SpeakerIcon,
  StorageIcon,
} from "@/features/ui-foundation/icons";
import { Button, Card, Checkbox, Select, Tabs, Toggle } from "@/features/ui-foundation/primitives";
import { TrackMap } from "@/features/ui-foundation/TrackMap";
import type { SettingsModel } from "./load";
import styles from "./Settings.module.css";

type Section = "assistance" | "voice" | "connections" | "data" | "advanced";

const SECTIONS: Array<{ key: Section; label: string }> = [
  { key: "assistance", label: "Assistance" },
  { key: "voice", label: "Voice & Controls" },
  { key: "connections", label: "Connections" },
  { key: "data", label: "Data & Storage" },
  { key: "advanced", label: "Advanced Tools" },
];

const STORAGE_KEY = "race-engineer.settings.v1";

type Preferences = {
  topics: Record<string, boolean>;
  focusAreas: Record<string, boolean>;
  verbosity: string;
  interruption: string;
  depth: string;
  interpretations: boolean;
  activation: string;
  voice: string;
  speechRate: string;
  retention: string;
};

function defaults(model: SettingsModel): Preferences {
  return {
    topics: Object.fromEntries(model.topics),
    focusAreas: Object.fromEntries(model.focusAreas),
    verbosity: "Balanced",
    interruption: "Allow interruptions",
    depth: "Standard",
    interpretations: true,
    activation: "Push to talk",
    voice: "Engineer (default)",
    speechRate: "Normal",
    retention: "Keep everything",
  };
}

export function Settings({ model }: { model: SettingsModel }) {
  const [section, setSection] = useState<Section>("assistance");
  const [prefs, setPrefs] = useState<Preferences>(() => defaults(model));
  const [saved, setSaved] = useState<Preferences>(() => defaults(model));
  const [notice, setNotice] = useState<"applied" | "failed" | null>(null);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const stored = { ...defaults(model), ...(JSON.parse(raw) as Partial<Preferences>) };
      // Restoring browser-local preferences after hydration.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPrefs(stored);
      setSaved(stored);
    } catch {
      /* ignore unreadable preferences */
    }
  }, [model]);

  const dirty = JSON.stringify(prefs) !== JSON.stringify(saved);
  const update = <K extends keyof Preferences>(key: K, value: Preferences[K]) => {
    setNotice(null);
    setPrefs((current) => ({ ...current, [key]: value }));
  };

  function apply() {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
      setSaved(prefs);
      setNotice("applied");
    } catch {
      setNotice("failed");
    }
  }

  const applyRow = (
    <div className={styles.applyRow}>
      <Button variant="primary" className={styles.apply} onClick={apply} disabled={!dirty && notice !== "failed"}>
        {notice === "applied" && !dirty ? "Changes Applied" : "Apply Changes"}
      </Button>
      <p className={styles.applyNote} role="status">
        {notice === "failed"
          ? "Settings could not be saved in this browser."
          : "Most settings apply immediately. Some connection settings require a restart."}
      </p>
    </div>
  );

  return (
    <>
      <Tabs items={SECTIONS} value={section} onChange={setSection} label="Settings sections" className={styles.tabs} />

      {section === "assistance" && (
        <div className={styles.grid}>
          <Card title="Live Assistance" labelledBy="live-assistance" className={styles.liveCard}>
            <p className={styles.cardDesc}>Configure how the engineer behaves during driving sessions.</p>
            <fieldset className={styles.fieldset}>
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
              <Select value={prefs.verbosity} onChange={(event) => update("verbosity", event.target.value)} aria-label="Verbosity">
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
                <Select value={prefs.depth} onChange={(event) => update("depth", event.target.value)} aria-label="Analysis Depth">
                  {model.depth.map((option) => (
                    <option key={option}>{option}</option>
                  ))}
                </Select>
              </Field>
              <div className={styles.toggleRow}>
                <div>
                  <div className={styles.label}>Include Interpretations</div>
                  <p className={styles.help}>Include AI interpretations alongside measured data.</p>
                </div>
                <Toggle
                  label="Include Interpretations"
                  checked={prefs.interpretations}
                  onChange={(value) => update("interpretations", value)}
                />
              </div>
              <fieldset className={styles.fieldset}>
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
            {applyRow}
          </div>
        </div>
      )}

      {section === "voice" && (
        <div className={styles.grid}>
          <Card title="Voice" labelledBy="voice-settings">
            <p className={styles.cardDesc}>How the engineer listens and speaks while you drive.</p>
            <Field label="Question Activation" help="How the microphone starts listening for a question.">
              <Select value={prefs.activation} onChange={(event) => update("activation", event.target.value)} aria-label="Question Activation">
                <option>Push to talk</option>
                <option>Voice activation</option>
              </Select>
            </Field>
            <Field label="Engineer Voice" help="Voice used for spoken responses.">
              <Select value={prefs.voice} onChange={(event) => update("voice", event.target.value)} aria-label="Engineer Voice">
                <option>Engineer (default)</option>
                <option>Calm</option>
                <option>Energetic</option>
              </Select>
            </Field>
            <Field label="Speech Rate" help="Speaking speed for responses.">
              <Select value={prefs.speechRate} onChange={(event) => update("speechRate", event.target.value)} aria-label="Speech Rate">
                <option>Slow</option>
                <option>Normal</option>
                <option>Fast</option>
              </Select>
            </Field>
          </Card>
          <div className={styles.rightCol}>
            <Card title="Devices" labelledBy="voice-devices">
              <p className={styles.cardDesc}>Audio devices used by the engineer runtime.</p>
              <dl className={styles.kv}>
                <div>
                  <dt>Microphone</dt>
                  <dd>{model.status.microphone.name}</dd>
                </div>
                <div>
                  <dt>Audio Output</dt>
                  <dd>{model.status.audio.name}</dd>
                </div>
              </dl>
            </Card>
            {applyRow}
          </div>
        </div>
      )}

      {section === "connections" && (
        <div className={styles.grid}>
          <Card title="Game Telemetry" labelledBy="game-telemetry">
            <p className={styles.cardDesc}>Set the game&apos;s UDP telemetry destination to this computer.</p>
            <dl className={styles.kv}>
              <div>
                <dt>UDP Port</dt>
                <dd>20777</dd>
              </div>
              <div>
                <dt>Receiver</dt>
                <dd data-tone={model.telemetry?.tone}>{model.telemetry ? model.telemetry.state : "Local engine unavailable"}</dd>
              </div>
              <div>
                <dt>Status</dt>
                <dd>{model.telemetry ? model.telemetry.detail : "Start the local engine to receive telemetry."}</dd>
              </div>
            </dl>
          </Card>
          <Card title="Local Engine" labelledBy="local-engine">
            <p className={styles.cardDesc}>The processing service this dashboard reads from.</p>
            <dl className={styles.kv}>
              <div>
                <dt>Connection</dt>
                <dd data-tone={model.telemetry ? "green" : "red"}>{model.telemetry ? "Connected" : "Unavailable"}</dd>
              </div>
              <div>
                <dt>Changes</dt>
                <dd>Connection settings are configured when starting the engine and require a restart.</dd>
              </div>
            </dl>
          </Card>
        </div>
      )}

      {section === "data" && (
        <div className={styles.grid}>
          <Card title="Storage" labelledBy="storage-usage">
            <p className={styles.cardDesc}>Space used by recorded sessions and analysis data.</p>
            <dl className={styles.kv}>
              {(model.storageScopes ?? [
                { label: "Total used", value: model.status.storage.used.replace(" used", "") },
                { label: "Available", value: model.status.storage.available.replace(" available", "") },
              ]).map((scope) => (
                <div key={scope.label}>
                  <dt>{scope.label}</dt>
                  <dd>{scope.value}</dd>
                </div>
              ))}
            </dl>
          </Card>
          <div className={styles.rightCol}>
            <Card title="Retention" labelledBy="retention">
              <p className={styles.cardDesc}>Recorded sessions are never deleted automatically.</p>
              <Field label="Keep Sessions" help="Sessions are kept until you remove them.">
                <Select value={prefs.retention} onChange={(event) => update("retention", event.target.value)} aria-label="Keep Sessions">
                  <option>Keep everything</option>
                </Select>
              </Field>
            </Card>
            {applyRow}
          </div>
        </div>
      )}

      {section === "advanced" && (
        <div className={styles.grid}>
          <Card title="Diagnostic Tools" labelledBy="diagnostic-tools">
            <p className={styles.cardDesc}>Replay and capture tools run from the command line so they never compete with the live receiver.</p>
            <dl className={styles.kv}>
              <div>
                <dt>Replay a capture</dt>
                <dd>
                  <code>f1-engineer replay-udp &lt;capture.f1ecap&gt; --dry-run</code>
                </dd>
              </div>
              <div>
                <dt>Manual acquisition</dt>
                <dd>
                  Start the engine with <code>--manual-acquisition</code> for diagnostic recording controls.
                </dd>
              </div>
            </dl>
          </Card>
          <Card title="Track Models" labelledBy="track-models">
            <p className={styles.cardDesc}>Circuit models used for regions and corner analysis.</p>
            <dl className={styles.kv}>
              <div>
                <dt>Version</dt>
                <dd>{model.status.trackModels.version}</dd>
              </div>
              <div>
                <dt>Status</dt>
                <dd>{model.status.trackModels.state}</dd>
              </div>
            </dl>
          </Card>
        </div>
      )}

      <Card title="Current Configuration & Status" labelledBy="current-configuration" className={styles.statusCard}>
        <ul className={styles.tiles}>
          <li className={styles.tile}>
            <MicrophoneIcon size={30} strokeWidth={1.4} className={styles.tileIcon} />
            <div>
              <div className={styles.tileLabel}>Microphone</div>
              <div className={styles.tileName}>{model.status.microphone.name}</div>
              <div className={styles.tileState} data-tone="green">
                <i /> {model.status.microphone.state}
              </div>
            </div>
          </li>
          <li className={styles.tile}>
            <SpeakerIcon size={30} strokeWidth={1.4} className={styles.tileIcon} />
            <div>
              <div className={styles.tileLabel}>Audio Output</div>
              <div className={styles.tileName}>{model.status.audio.name}</div>
              <div className={styles.tileState} data-tone="green">
                <i /> {model.status.audio.state}
              </div>
            </div>
          </li>
          <li className={styles.tile}>
            <GamepadIcon size={30} strokeWidth={1.4} className={styles.tileIcon} />
            <div>
              <div className={styles.tileLabel}>Game Connection</div>
              <div className={styles.tileName}>{model.status.game.name}</div>
              <div className={styles.tileState} data-tone={model.status.game.state.startsWith("Connected") ? "green-text" : "amber-text"}>
                {model.status.game.state}
              </div>
            </div>
          </li>
          <li className={styles.tile}>
            <StorageIcon size={30} strokeWidth={1.4} className={styles.tileIcon} />
            <div>
              <div className={styles.tileLabel}>Storage</div>
              <div className={styles.tileName}>{model.status.storage.used}</div>
              <div className={styles.tileState}>{model.status.storage.available}</div>
            </div>
          </li>
          <li className={styles.tile}>
            <TrackMap track="monaco" className={styles.tileMap} strokeWidth={1.6} />
            <div>
              <div className={styles.tileLabel}>Track Models</div>
              <div className={styles.tileName}>{model.status.trackModels.version}</div>
              <span className={styles.tileBadge}>{model.status.trackModels.state}</span>
            </div>
          </li>
        </ul>
      </Card>
    </>
  );
}

function Field({ label, help, children }: { label: string; help: string; children: React.ReactNode }) {
  return (
    <div className={styles.field}>
      <div className={styles.label}>{label}</div>
      {children}
      <p className={styles.help}>{help}</p>
    </div>
  );
}
