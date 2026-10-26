// Browser audio devices as the status tiles and the Voice & Controls tab see
// them. Pure derivation from what the browser reported, so a real machine never
// shows the reference "Shure MV7" and every unsupported API says so.

import type { VoiceOption } from "./audio";

export type MicPermission = "unknown" | "prompt" | "granted" | "denied";

export type MicDevice = { deviceId: string; label: string };

export type BrowserAudio = {
  /** False until the browser has been asked (server render and first client render). */
  checked: boolean;
  micSupported: boolean;
  permission: MicPermission;
  devices: MicDevice[];
  speechSupported: boolean;
  voices: VoiceOption[];
};

export const INITIAL_BROWSER_AUDIO: BrowserAudio = {
  checked: false,
  micSupported: true,
  permission: "unknown",
  devices: [],
  speechSupported: true,
  voices: [],
};

export type DeviceTone = "green-text" | "amber-text" | "grey-text" | "red-text";

export type DeviceTile = { name: string; state: string; tone: DeviceTone };

/** Fallback label when the browser hides device names. */
export function deviceLabel(device: MicDevice, index: number): string {
  const label = device.label.trim();
  return label || `Microphone ${index + 1}`;
}

/** The device the browser would use: the chosen one, else the first. */
export function selectedDevice(devices: readonly MicDevice[], deviceId: string | null): MicDevice | null {
  if (deviceId) {
    const match = devices.find((device) => device.deviceId === deviceId);
    if (match) return match;
  }
  return devices[0] ?? null;
}

export function microphoneTile(audio: BrowserAudio, deviceId: string | null = null): DeviceTile {
  if (!audio.checked) return { name: "Checking…", state: "Checking", tone: "grey-text" };
  if (!audio.micSupported) return { name: "Not supported", state: "Unavailable", tone: "grey-text" };
  if (audio.permission === "denied") return { name: "Access blocked", state: "Blocked", tone: "red-text" };
  if (audio.permission === "granted") {
    const device = selectedDevice(audio.devices, deviceId);
    if (!device) return { name: "No microphone found", state: "Not set up", tone: "amber-text" };
    const index = audio.devices.indexOf(device);
    return { name: deviceLabel(device, index), state: "Ready", tone: "green-text" };
  }
  return { name: "No microphone selected", state: "Not set up", tone: "amber-text" };
}

export function audioOutputTile(audio: BrowserAudio, voice: VoiceOption | null): DeviceTile {
  if (!audio.checked) return { name: "Checking…", state: "Checking", tone: "grey-text" };
  if (!audio.speechSupported) return { name: "Not supported", state: "Unavailable", tone: "grey-text" };
  if (!voice) return { name: "No local voice", state: "Not set up", tone: "amber-text" };
  return { name: voice.name, state: "Ready", tone: "green-text" };
}

export type MicrophoneStage =
  | "checking"
  | "unsupported"
  | "blocked"
  | "needs-access"
  | "no-device"
  | "ready";

/** Which of the Microphone card's states applies (plan 3.5.2). */
export function microphoneStage(audio: BrowserAudio): MicrophoneStage {
  if (!audio.checked) return "checking";
  if (!audio.micSupported) return "unsupported";
  if (audio.permission === "denied") return "blocked";
  if (audio.permission !== "granted") return "needs-access";
  return audio.devices.length === 0 ? "no-device" : "ready";
}

/** Maps a getUserMedia failure name to the permission it implies. */
export function permissionAfterError(errorName: string): { permission: MicPermission; noDevice: boolean } {
  if (errorName === "NotAllowedError" || errorName === "SecurityError" || errorName === "PermissionDeniedError") {
    return { permission: "denied", noDevice: false };
  }
  if (errorName === "NotFoundError" || errorName === "OverconstrainedError" || errorName === "DevicesNotFoundError") {
    return { permission: "granted", noDevice: true };
  }
  return { permission: "prompt", noDevice: false };
}
