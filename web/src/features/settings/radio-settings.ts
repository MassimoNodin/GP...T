export type RadioSettings = {
  proactive_mode: string; answer_mode: string; intent_mode: string; tts_engine: string;
  voice: string | null; rate: number; volume: number; microphone: number | null;
  cue_output: number | null; cue_volume: number; capture_seconds: number;
};
export type RadioPresence = {
  connected: boolean; applied_revision?: string; tts_engine?: string; voices?: string[];
  devices?: { index: number; name: string; input: boolean; output: boolean }[];
  /** The values the running radio uses; reported so an unsaved form never resets them. */
  settings?: RadioSettings;
};

/**
 * The form to show. Before anything is saved (revision 0) the radio runs on its
 * command-line values, so start from what it reports instead of the stored defaults;
 * otherwise saving one field would silently change the rest.
 */
export function initialRadioForm(stored: RadioSettings, revision: number, presence: RadioPresence): RadioSettings {
  return revision === 0 && presence.connected && presence.settings ? { ...presence.settings } : stored;
}
export function radioStatus(presence: RadioPresence, revision: number | null): string {
  if (!presence.connected) return "Radio not connected \u2014 changes apply when it connects";
  const applied = presence.applied_revision?.split(":")[1];
  return applied === String(revision)
    ? `Radio connected \u00b7 settings applied (rev ${revision})`
    : "Radio connected \u00b7 waiting for settings to apply";
}
export function browserMaySpeak(enabled: boolean, radioConnected: boolean): boolean {
  return enabled && !radioConnected;
}
