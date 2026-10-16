export type RadioSettings = {
  proactive_mode: string; answer_mode: string; intent_mode: string; tts_engine: string;
  voice: string | null; rate: number; volume: number; microphone: number | null;
  cue_output: number | null; cue_volume: number; capture_seconds: number;
};
export type RadioPresence = {
  connected: boolean; applied_revision?: string; tts_engine?: string; voices?: string[];
  devices?: { index: number; name: string; input: boolean; output: boolean }[];
};
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
