import type { DeviceTile } from "./devices";

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
  const settings = revision === 0 && presence.connected && presence.settings ? presence.settings : stored;
  return { ...settings, voice: settings.voice ?? (settings.tts_engine === "edge" ? DEFAULT_RADIO_VOICE : null) };
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

export const DEFAULT_RADIO_VOICE = "en-AU-WilliamNeural";
export const EDGE_RADIO_VOICES = [DEFAULT_RADIO_VOICE, "en-AU-NatashaNeural"];

export function radioVoiceLabel(voice: string): string {
  if (voice === DEFAULT_RADIO_VOICE) return "William — English (Australia), neural (default)";
  if (voice === "en-AU-NatashaNeural") return "Natasha — English (Australia), neural";
  return voice;
}

export function radioVoiceChoices(engine: string, presence: RadioPresence, selected: string | null): string[] {
  return [...new Set([
    ...(engine === "edge" ? EDGE_RADIO_VOICES : []),
    ...(presence.tts_engine === engine ? presence.voices ?? [] : []),
    ...(selected ? [selected] : []),
  ])];
}

export function radioMicrophoneTile(presence: RadioPresence): DeviceTile {
  if (!presence.connected) return { name: "Not reported", state: "Radio not connected", tone: "grey-text" };
  const index = presence.settings?.microphone;
  const device = presence.devices?.find((item) => item.input && item.index === index);
  if (index !== null && index !== undefined && presence.devices && !device) {
    return { name: "Device " + index, state: "Unavailable on radio", tone: "red-text" };
  }
  if (index == null && presence.devices && !presence.devices.some((item) => item.input)) {
    return { name: "No radio input devices", state: "Unavailable on radio", tone: "red-text" };
  }
  return { name: device?.name ?? (index == null ? "Radio system default" : "Device " + index), state: "Radio connected", tone: "green-text" };
}

export function radioVoiceTile(presence: RadioPresence): DeviceTile {
  if (!presence.connected) return { name: "Not reported", state: "Radio not connected", tone: "grey-text" };
  const engine = presence.settings?.tts_engine ?? presence.tts_engine;
  const voice = presence.settings?.voice ?? (engine === "edge" ? DEFAULT_RADIO_VOICE : "Radio system default");
  return { name: radioVoiceLabel(voice), state: "Radio connected", tone: "green-text" };
}
