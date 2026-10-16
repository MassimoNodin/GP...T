"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { localVoiceOptions } from "./audio";
import {
  INITIAL_BROWSER_AUDIO,
  permissionAfterError,
  type BrowserAudio,
  type MicDevice,
  type MicPermission,
} from "./devices";

function micApisAvailable(): boolean {
  return (
    typeof navigator !== "undefined" &&
    Boolean(navigator.mediaDevices) &&
    typeof navigator.mediaDevices.getUserMedia === "function" &&
    typeof navigator.mediaDevices.enumerateDevices === "function"
  );
}

async function readBrowserAudio(grantedHint: boolean): Promise<BrowserAudio> {
  const micSupported = micApisAvailable();
  const speechSupported = typeof window !== "undefined" && "speechSynthesis" in window;

  let permission: MicPermission = "unknown";
  if (micSupported && typeof navigator.permissions?.query === "function") {
    try {
      const status = await navigator.permissions.query({ name: "microphone" as PermissionName });
      permission = status.state === "granted" || status.state === "denied" || status.state === "prompt" ? status.state : "unknown";
    } catch {
      // Safari and some Firefox versions cannot query the microphone permission.
    }
  }

  let inputs: MicDevice[] = [];
  let labelled = false;
  if (micSupported) {
    try {
      const list = await navigator.mediaDevices.enumerateDevices();
      const audioInputs = list.filter((device) => device.kind === "audioinput" && device.deviceId !== "communications");
      labelled = audioInputs.some((device) => device.label.trim() !== "");
      inputs = audioInputs.map((device) => ({ deviceId: device.deviceId, label: device.label }));
    } catch {
      inputs = [];
    }
  }
  // Without the permissions API, readable device names are the evidence of a grant.
  if (permission === "unknown" && (labelled || grantedHint)) permission = "granted";
  if (permission === "prompt" && grantedHint) permission = "granted";

  let voices: BrowserAudio["voices"] = [];
  if (speechSupported) {
    try {
      voices = localVoiceOptions(window.speechSynthesis.getVoices());
    } catch {
      voices = [];
    }
  }

  return {
    checked: true,
    micSupported,
    permission,
    devices: permission === "granted" ? inputs : [],
    speechSupported,
    voices,
  };
}

/**
 * What the browser reports about the microphone and local speech voices. The
 * first render matches the server render (`checked: false`); the browser is
 * asked after hydration. No audio stream is opened here.
 */
export function useBrowserAudio() {
  const [audio, setAudio] = useState<BrowserAudio>(INITIAL_BROWSER_AUDIO);
  const alive = useRef(true);

  const refresh = useCallback(async (grantedHint = false) => {
    const next = await readBrowserAudio(grantedHint);
    if (alive.current) setAudio(next);
  }, []);

  useEffect(() => {
    alive.current = true;
    let permissionStatus: PermissionStatus | null = null;
    const onChange = () => void refresh();
    void (async () => {
      await refresh();
      if (micApisAvailable() && typeof navigator.permissions?.query === "function") {
        try {
          permissionStatus = await navigator.permissions.query({ name: "microphone" as PermissionName });
          if (alive.current) permissionStatus.addEventListener("change", onChange);
          else permissionStatus = null;
        } catch {
          permissionStatus = null;
        }
      }
    })();
    if (micApisAvailable()) navigator.mediaDevices.addEventListener?.("devicechange", onChange);
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.addEventListener?.("voiceschanged", onChange);
    }
    return () => {
      alive.current = false;
      permissionStatus?.removeEventListener("change", onChange);
      if (micApisAvailable()) navigator.mediaDevices.removeEventListener?.("devicechange", onChange);
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.removeEventListener?.("voiceschanged", onChange);
      }
    };
  }, [refresh]);

  /** Asks the browser for microphone permission once; stops the stream immediately. */
  const requestAccess = useCallback(async (): Promise<"granted" | "denied" | "no-device" | "failed"> => {
    if (!micApisAvailable()) return "failed";
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      for (const track of stream.getTracks()) track.stop();
      await refresh(true);
      return "granted";
    } catch (error) {
      const name = error instanceof DOMException ? error.name : "";
      const outcome = permissionAfterError(name);
      if (alive.current) {
        setAudio((current) => ({
          ...current,
          permission: outcome.permission,
          devices: outcome.noDevice ? [] : current.devices,
        }));
      }
      if (outcome.permission === "denied") return "denied";
      return outcome.noDevice ? "no-device" : "failed";
    }
  }, [refresh]);

  return { audio, requestAccess, refresh };
}
