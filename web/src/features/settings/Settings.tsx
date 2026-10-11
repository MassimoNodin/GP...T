"use client";

import { useEffect, useState } from "react";
import {
  GamepadIcon,
  MicrophoneIcon,
  SpeakerIcon,
  StorageIcon,
} from "@/features/ui-foundation/icons";
import { Badge, Button, Card, Checkbox, Select, Tabs, Toggle } from "@/features/ui-foundation/primitives";
import { TrackMap } from "@/features/ui-foundation/TrackMap";
import type { DisplayRow } from "./display";
import type { SettingsModel } from "./load";
import {
  ACTIVATION_OPTIONS,
  PREFERENCES_KEY,
  SPEECH_RATE_OPTIONS,
  VOICE_OPTIONS,
  defaultPreferences,
  preferencesEqual,
  restorePreferences,
  type Preferences,
} from "./preferences";
import styles from "./Settings.module.css";

type Section = "assistance" | "voice" | "connections" | "data" | "advanced";
type Notice = "applied" | "failed" | "unreadable" | null;

const SECTIONS: Array<{ key: Section; label: string }> = [
  { key: "assistance", label: "Assistance" },
  { key: "voice", label: "Voice & Controls" },
  { key: "connections", label: "Connections" },
  { key: "data", label: "Data & Storage" },
  { key: "advanced", label: "Advanced Tools" },
];

export function Settings({ model }: { model: SettingsModel }) {
  const [section, setSection] = useState<Section>("assistance");
  const [source] = useState(model);
  const [initial] = useState(() => defaultPreferences(model));
  const [prefs, setPrefs] = useState<Preferences>(initial);
  const [saved, setSaved] = useState<Preferences>(initial);
  const [notice, setNotice] = useState<Notice>(null);

  // Preferences live in this browser only, so they are restored after hydration
  // to keep the server render and the first client render identical.
  useEffect(() => {
    let raw: string | null = null;
    try {
      raw = window.localStorage.getItem(PREFERENCES_KEY);
    } catch {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setNotice("unreadable");
      return;
    }
    if (raw === null) return;
    const stored = restorePreferences(source, raw);
    setPrefs(stored);
    setSaved(stored);
  }, [source]);

  const dirty = !preferencesEqual(prefs, saved);
  const update = <K extends keyof Preferences>(key: K, value: Preferences[K]) => {
    setNotice(null);
    setPrefs((current) => ({ ...current, [key]: value }));
  };

  function apply() {
    try {
      window.localStorage.setItem(PREFERENCES_KEY, JSON.stringify(prefs));
      setSaved(prefs);
      setNotice("applied");
    } catch {
      setNotice("failed");
    }
  }

  const applyNote =
    notice === "failed"
      ? "Settings could not be saved in this browser. Check that site storage is allowed and try again."
      : notice === "unreadable"
        ? "Saved settings could not be read from this browser. Showing defaults."
        : notice === "applied" && !dirty
          ? "Saved in this browser only. The engineer runtime does not read these preferences yet."
          : "Most settings apply immediately. Some connection settings require a restart.";

  const applyRow = (
    <div className={styles.applyRow}>
      <Button variant="primary" className={styles.apply} onClick={apply} disabled={!dirty}>
        {notice === "applied" && !dirty ? "Changes Applied" : "Apply Changes"}
      </Button>
      <p className={styles.applyNote} role="status" data-tone={notice === "failed" ? "red" : undefined}>
        {applyNote}
      </p>
    </div>
  );

  const { runtime, receiver, storage } = model;

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
                {ACTIVATION_OPTIONS.map((option) => (
                  <option key={option}>{option}</option>
                ))}
              </Select>
            </Field>
            <Field label="Engineer Voice" help="Voice used for spoken responses.">
              <Select value={prefs.voice} onChange={(event) => update("voice", event.target.value)} aria-label="Engineer Voice">
                {VOICE_OPTIONS.map((option) => (
                  <option key={option}>{option}</option>
                ))}
              </Select>
            </Field>
            <Field label="Speech Rate" help="Speaking speed for responses.">
              <Select value={prefs.speechRate} onChange={(event) => update("speechRate", event.target.value)} aria-label="Speech Rate">
                {SPEECH_RATE_OPTIONS.map((option) => (
                  <option key={option}>{option}</option>
                ))}
              </Select>
            </Field>
          </Card>
          <div className={styles.rightCol}>
            <Card title="Devices" labelledBy="voice-devices">
              <p className={styles.cardDesc}>Audio devices used by the engineer runtime.</p>
              <KeyValues
                rows={[
                  { label: "Microphone", value: model.status.microphone.name },
                  { label: "Audio Output", value: model.status.audio.name },
                ]}
              />
              <p className={styles.footnote}>Device names are reference values. The engine does not report audio devices yet.</p>
            </Card>
            {applyRow}
          </div>
        </div>
      )}

      {section === "connections" && (
        <div className={styles.grid}>
          <Card title="Game Telemetry" labelledBy="game-telemetry">
            <p className={styles.cardDesc}>Set the game&apos;s UDP telemetry destination to this computer.</p>
            <KeyValues rows={receiver.rows} />
            <p className={styles.footnote}>
              {receiver.available
                ? receiver.detail
                : "Telemetry details could not be read because the local engine did not answer."}
            </p>
          </Card>
          <div className={styles.rightCol}>
            <Card title="Engineer Runtime" labelledBy="engineer-runtime" aside={<RuntimeChip model={model} />}>
              <p className={styles.cardDesc}>The runtime that answers engineer questions.</p>
              <KeyValues rows={runtime.rows} />
            </Card>
            <Card title="Local Engine" labelledBy="local-engine">
              <p className={styles.cardDesc}>The processing service this dashboard reads from.</p>
              <KeyValues
                rows={[
                  {
                    label: "Connection",
                    value: model.engineReachable ? "Connected" : "Unavailable",
                    tone: model.engineReachable ? "green" : "red",
                  },
                  {
                    label: "Changes",
                    value: "The receiver address and port are set when starting the engine and require a restart.",
                  },
                ]}
              />
            </Card>
          </div>
        </div>
      )}

      {section === "data" && (
        <div className={styles.grid}>
          <Card title="Storage" labelledBy="storage-usage">
            <p className={styles.cardDesc}>Space used by recorded sessions and analysis data.</p>
            {storage ? (
              <>
                <div className={styles.storageSummary}>
                  <div className={styles.storageUsed}>{storage.used}</div>
                  <div className={styles.help}>
                    {storage.available}
                    {storage.capacity ? ` · ${storage.capacity}` : ""}
                  </div>
                </div>
                {storage.volumeShare !== null && (
                  <div className={styles.meter} aria-hidden="true">
                    <span style={{ width: barWidth(storage.volumeShare) }} />
                  </div>
                )}
                <ul className={styles.scopes}>
                  {storage.scopes.map((scope) => (
                    <li key={scope.label} className={styles.scope} data-unavailable={scope.unavailable || undefined}>
                      <div className={styles.scopeHead}>
                        <span>{scope.label}</span>
                        <span className={styles.scopeValue}>{scope.value}</span>
                      </div>
                      {scope.share !== null && (
                        <div className={styles.meter} data-small="" aria-hidden="true">
                          <span style={{ width: barWidth(scope.share) }} />
                        </div>
                      )}
                      {scope.detail && !scope.unavailable && <div className={styles.scopeDetail}>{scope.detail}</div>}
                    </li>
                  ))}
                </ul>
                <p className={styles.footnote}>
                  {storage.complete ? "" : "Some locations could not be measured, so the total is a lower bound. "}
                  {storage.note}
                </p>
              </>
            ) : (
              <p className={styles.unavailable}>
                Storage usage is unavailable. Start the local engine to measure recorded sessions.
              </p>
            )}
          </Card>
          <Card title="Retention" labelledBy="retention">
            <p className={styles.cardDesc}>How long recorded sessions are kept.</p>
            <KeyValues
              rows={[
                { label: "Policy", value: "None configured" },
                {
                  label: "Deletion",
                  value:
                    "Nothing on this screen deletes data. Recordings stay in the recordings folder until you remove them yourself.",
                },
              ]}
            />
          </Card>
        </div>
      )}

      {section === "advanced" && (
        <div className={styles.grid}>
          <Card title="Diagnostic Tools" labelledBy="diagnostic-tools">
            <p className={styles.cardDesc}>Replay and capture tools run from the command line so they never compete with the live receiver.</p>
            <dl className={styles.kv}>
              <div>
                <dt>Check a capture</dt>
                <dd>
                  <code>f1-engineer inspect &lt;capture.f1ecap&gt;</code>
                </dd>
              </div>
              <div>
                <dt>Validate a replay</dt>
                <dd>
                  <code>f1-engineer replay-udp &lt;capture.f1ecap&gt; --host &lt;receiver-ip&gt; --dry-run</code>
                  <span className={styles.cmdNote}>
                    Sends nothing. Use <code>--confirm-idle</code> instead of <code>--dry-run</code> to send, with F1 off.
                  </span>
                </dd>
              </div>
              <div>
                <dt>Manual acquisition</dt>
                <dd>
                  Start the engine with <code>f1-engineer api --manual-acquisition</code> for diagnostic recording controls.
                </dd>
              </div>
              <div>
                <dt>Receiver address</dt>
                <dd>
                  <code>f1-engineer api --udp-host &lt;ip&gt; --udp-port &lt;port&gt;</code>
                </dd>
              </div>
            </dl>
            <p className={styles.footnote}>
              Run these with <code>uv run --extra app</code> or from the project&apos;s virtual environment. See README.md for details.
            </p>
          </Card>
          <Card title="Track Models" labelledBy="track-models">
            <p className={styles.cardDesc}>Circuit models used for regions and corner analysis.</p>
            <KeyValues
              rows={[
                { label: "Version", value: model.status.trackModels.version },
                { label: "Status", value: model.status.trackModels.state },
              ]}
            />
            <p className={styles.footnote}>Reference values. The engine does not report track model status here yet.</p>
          </Card>
        </div>
      )}

      <Card
        title="Current Configuration & Status"
        labelledBy="current-configuration"
        className={styles.statusCard}
        aside={section === "connections" ? undefined : <RuntimeChip model={model} />}
      >
        <ul className={styles.tiles}>
          <li className={styles.tile}>
            <MicrophoneIcon size={30} strokeWidth={1.4} className={styles.tileIcon} />
            <div>
              <div className={styles.tileLabel}>Microphone</div>
              <div className={styles.tileName}>{model.status.microphone.name}</div>
              <div className={styles.tileState} data-tone={model.engineReachable ? "green" : "grey"}>
                <i /> {model.status.microphone.state}
              </div>
            </div>
          </li>
          <li className={styles.tile}>
            <SpeakerIcon size={30} strokeWidth={1.4} className={styles.tileIcon} />
            <div>
              <div className={styles.tileLabel}>Audio Output</div>
              <div className={styles.tileName}>{model.status.audio.name}</div>
              <div className={styles.tileState} data-tone={model.engineReachable ? "green" : "grey"}>
                <i /> {model.status.audio.state}
              </div>
            </div>
          </li>
          <li className={styles.tile}>
            <GamepadIcon size={30} strokeWidth={1.4} className={styles.tileIcon} />
            <div>
              <div className={styles.tileLabel}>Game Connection</div>
              <div className={styles.tileName}>{model.game.name}</div>
              <div className={styles.tileState} data-tone={model.game.tone}>
                {model.game.state}
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

/** Engineer runtime status sits in a card header so the five reference tiles stay intact. */
function RuntimeChip({ model }: { model: SettingsModel }) {
  return (
    <span className={styles.chip}>
      <Badge tone={model.runtime.tone}>Engineer: {model.runtime.statusLabel}</Badge>
    </span>
  );
}

function barWidth(share: number) {
  const clamped = Math.min(1, Math.max(0, share));
  return clamped === 0 ? "0" : `${Math.max(1, clamped * 100).toFixed(1)}%`;
}

function KeyValues({ rows }: { rows: DisplayRow[] }) {
  return (
    <dl className={styles.kv}>
      {rows.map((row) => (
        <div key={row.label}>
          <dt>{row.label}</dt>
          <dd data-tone={row.tone}>{row.value}</dd>
        </div>
      ))}
    </dl>
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
